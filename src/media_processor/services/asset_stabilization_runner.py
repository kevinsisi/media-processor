"""Worker-side runner for v0.40.0 asset-level stabilization."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from contextlib import suppress
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select

from media_processor.api.config import settings
from media_processor.core.db import async_session_maker
from media_processor.models import Asset
from media_processor.services import asset_variants
from media_processor.services.queue import enqueue_asset_analysis

logger = logging.getLogger(__name__)


def _enqueue_analysis_after_stabilization(asset_id: int, *, force: bool) -> None:
    """Best-effort analysis enqueue after stabilization terminal state.

    Failure must not surface to the caller — the operator can retry via
    POST /assets/{id}/analyze.
    """
    try:
        enqueue_asset_analysis(asset_id, force=force)
        logger.info(
            "run_asset_stabilization: enqueued analysis for asset %d (force=%s)",
            asset_id,
            force,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "run_asset_stabilization: failed to enqueue analysis for asset %d: %s"
            " — operator can retry via POST /analyze",
            asset_id,
            exc,
        )


def _tracking_intent_key(asset: Asset) -> str:
    custom_roi = asset.custom_roi_json if isinstance(asset.custom_roi_json, dict) else {}
    point_origin = (
        asset.point_tracking_origin if isinstance(asset.point_tracking_origin, dict) else {}
    )
    point_track = asset.point_tracking_json if isinstance(asset.point_tracking_json, dict) else {}
    tracking_blob = asset.tracking_json if isinstance(asset.tracking_json, dict) else {}
    payload = json.dumps(
        {
            "active_asset_variant": asset_variants.active_variant(asset),
            "tracked_object_index": asset.tracked_object_index,
            "point_tracking_status": asset.point_tracking_status,
            "point_tracking_origin": point_origin,
            "point_tracking_json": point_track,
            "custom_roi_init": custom_roi.get("init"),
            "custom_roi_json": custom_roi,
            "tracking_json": tracking_blob,
        },
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _candidate_path(dst: Path) -> Path:
    return dst.with_name(f"{dst.stem}.{uuid4().hex}.candidate{dst.suffix}")


def _discard_candidate(path: Path) -> None:
    with suppress(OSError):
        path.unlink(missing_ok=True)


def _discard_previous_derivative(path: str | None, raw_path: str, keep_path: Path) -> None:
    if not path:
        return
    previous = Path(path)
    if previous == Path(raw_path) or previous == keep_path:
        return
    with suppress(OSError):
        previous.unlink(missing_ok=True)


async def run_asset_stabilization(asset_id: int, *, force: bool = False) -> dict[str, str | int]:
    async with async_session_maker() as session:
        asset = (
            await session.execute(select(Asset).where(Asset.id == asset_id))
        ).scalar_one_or_none()
        if asset is None:
            logger.warning("run_asset_stabilization: asset %d not found", asset_id)
            return {"asset_id": asset_id, "status": "missing"}
        if asset.tracked_object_index == -4 and asset.point_tracking_status == "pending":
            asset.stabilization_status = asset_variants.STABILIZATION_NOT_STARTED
            asset.stabilization_error = "waiting for point tracking before stabilization"
            await session.commit()
            return {"asset_id": asset_id, "status": "waiting_point_tracking"}
        existing_done_path = Path(asset.stabilized_path) if asset.stabilized_path else None
        existing_done_valid = (
            not force
            and asset_variants.stabilization_status(asset) == asset_variants.STABILIZATION_DONE
            and existing_done_path is not None
            and existing_done_path.is_file()
        )
        if existing_done_valid and asset.tracked_object_index is not None:
            return {"asset_id": asset_id, "status": "done"}
        asset.stabilization_status = asset_variants.STABILIZATION_RUNNING
        previous_stabilization_error = getattr(asset, "stabilization_error", None)
        asset.stabilization_error = None
        dst = asset_variants.stabilized_path_for_asset(asset)
        previous_stabilized_path = getattr(asset, "stabilized_path", None)
        asset.stabilized_path = str(dst)
        intent_key = _tracking_intent_key(asset)
        await session.commit()
        src = asset_variants.selected_media_path(asset)
        if src == dst or asset_variants.active_variant(asset) == asset_variants.STABILIZED_VARIANT:
            # A forced rerun can be requested while the stabilized variant is
            # active. Avoid reading and replacing the same file in one ffmpeg
            # operation; regenerate from immutable raw in that case.
            src = Path(asset.file_path)
        raw_path = str(asset.file_path)
        scratch = Path(settings.analysis_dir) / "asset_stabilization" / str(asset_id)
        asset_snapshot = asset
        tracked_object_index = asset.tracked_object_index

    candidate_dst = _candidate_path(dst)
    preflight_estimate: asset_variants.StabilizationNeedEstimate | None = None
    if not force and tracked_object_index is None:
        preflight_estimate = await asyncio.to_thread(
            asset_variants.estimate_stabilization_need, src
        )
        if not preflight_estimate.should_stabilize:
            logger.info(
                "run_asset_stabilization: asset %d skipped low-jitter source before auto tracking (%s)",
                asset_id,
                preflight_estimate.reason,
            )
            async with async_session_maker() as session:
                row = await session.get(Asset, asset_id)
                if row is None:
                    return {"asset_id": asset_id, "status": "missing_after"}
                if _tracking_intent_key(row) != intent_key:
                    return {"asset_id": asset_id, "status": "stale_intent"}
                row.active_asset_variant = asset_variants.RAW_VARIANT
                row.stabilized_path = None
                row.stabilization_status = asset_variants.STABILIZATION_SKIPPED
                row.stabilization_error = f"low-jitter source skipped: {preflight_estimate.reason}"
                await session.commit()
            _discard_previous_derivative(previous_stabilized_path, raw_path, candidate_dst)
            _enqueue_analysis_after_stabilization(asset_id, force=force)
            return {"asset_id": asset_id, "status": asset_variants.STABILIZATION_SKIPPED}
        if existing_done_valid:
            async with async_session_maker() as session:
                row = await session.get(Asset, asset_id)
                if row is None:
                    return {"asset_id": asset_id, "status": "missing_after"}
                if _tracking_intent_key(row) != intent_key:
                    return {"asset_id": asset_id, "status": "stale_intent"}
                row.stabilized_path = str(existing_done_path)
                row.stabilization_status = asset_variants.STABILIZATION_DONE
                row.stabilization_error = previous_stabilization_error
                await session.commit()
            return {"asset_id": asset_id, "status": "done"}

    tracking_error: str | None
    try:
        tracking_result = await asyncio.to_thread(
            asset_variants.stabilize_source_from_tracking,
            asset_snapshot,
            src,
            candidate_dst,
            scratch,
        )
    except Exception as exc:  # noqa: BLE001 — fall back to vidstab/preflight below.
        logger.exception(
            "run_asset_stabilization: asset %d tracking-based stabilization failed; falling back",
            asset_id,
        )
        tracking_result = None
        tracking_error = f"tracking stabilization failed: {type(exc).__name__}: {exc}"
        _discard_candidate(candidate_dst)
    else:
        tracking_error = None
    if tracking_result is not None:
        async with async_session_maker() as session:
            row = await session.get(Asset, asset_id)
            if row is None:
                _discard_candidate(candidate_dst)
                return {"asset_id": asset_id, "status": "missing_after"}
            if _tracking_intent_key(row) != intent_key:
                _discard_candidate(candidate_dst)
                return {"asset_id": asset_id, "status": "stale_intent"}
            replaced_stabilized_path = getattr(row, "stabilized_path", None)
            row.active_asset_variant = asset_variants.STABILIZED_VARIANT
            row.stabilized_path = str(candidate_dst)
            row.stabilization_status = asset_variants.STABILIZATION_DONE
            row.stabilization_error = None
            row.stabilization_mode = "tracking"
            row.stabilization_metrics_json = {
                "mode": tracking_result.mode,
                "point_count": tracking_result.point_count,
                "crop_w": tracking_result.crop_w,
                "crop_h": tracking_result.crop_h,
            }
            await session.commit()
        _discard_previous_derivative(previous_stabilized_path, raw_path, candidate_dst)
        _discard_previous_derivative(replaced_stabilized_path, raw_path, candidate_dst)
        _enqueue_analysis_after_stabilization(asset_id, force=force)
        return {"asset_id": asset_id, "status": "done"}

    if tracking_error or not force:
        if preflight_estimate is None:
            preflight_estimate = await asyncio.to_thread(
                asset_variants.estimate_stabilization_need, src
            )
        estimate = preflight_estimate
        if not estimate.should_stabilize:
            logger.info(
                "run_asset_stabilization: asset %d skipped low-jitter source (%s)",
                asset_id,
                estimate.reason,
            )
            async with async_session_maker() as session:
                row = await session.get(Asset, asset_id)
                if row is None:
                    return {"asset_id": asset_id, "status": "missing_after"}
                if _tracking_intent_key(row) != intent_key:
                    return {"asset_id": asset_id, "status": "stale_intent"}
                row.active_asset_variant = asset_variants.RAW_VARIANT
                row.stabilized_path = None
                row.stabilization_status = asset_variants.STABILIZATION_SKIPPED
                detail = f"low-jitter source skipped: {estimate.reason}"
                row.stabilization_error = (
                    f"{tracking_error}; {detail}" if tracking_error else detail
                )
                await session.commit()
            _discard_previous_derivative(previous_stabilized_path, raw_path, candidate_dst)
            _enqueue_analysis_after_stabilization(asset_id, force=force)
            return {"asset_id": asset_id, "status": asset_variants.STABILIZATION_SKIPPED}

    try:
        await asyncio.to_thread(asset_variants.stabilize_source, src, candidate_dst, scratch)
    except Exception as exc:  # noqa: BLE001 — persist terminal state for the UI.
        logger.exception("run_asset_stabilization: asset %d failed", asset_id)
        _discard_candidate(candidate_dst)
        async with async_session_maker() as session:
            row = await session.get(Asset, asset_id)
            if row is None:
                return {"asset_id": asset_id, "status": "missing_after"}
            if _tracking_intent_key(row) != intent_key:
                return {"asset_id": asset_id, "status": "stale_intent"}
            row.active_asset_variant = asset_variants.RAW_VARIANT
            row.stabilization_status = asset_variants.STABILIZATION_FAILED
            row.stabilization_error = f"{type(exc).__name__}: {exc}"
            await session.commit()
        _enqueue_analysis_after_stabilization(asset_id, force=force)
        return {"asset_id": asset_id, "status": "failed"}

    async with async_session_maker() as session:
        row = await session.get(Asset, asset_id)
        if row is None:
            _discard_candidate(candidate_dst)
            return {"asset_id": asset_id, "status": "missing_after"}
        if _tracking_intent_key(row) != intent_key:
            _discard_candidate(candidate_dst)
            return {"asset_id": asset_id, "status": "stale_intent"}
        replaced_stabilized_path = getattr(row, "stabilized_path", None)
        row.active_asset_variant = asset_variants.STABILIZED_VARIANT
        row.stabilized_path = str(candidate_dst)
        row.stabilization_status = asset_variants.STABILIZATION_DONE
        row.stabilization_error = tracking_error
        row.stabilization_mode = "vidstab"
        row.stabilization_metrics_json = None
        await session.commit()
    _discard_previous_derivative(previous_stabilized_path, raw_path, candidate_dst)
    _discard_previous_derivative(replaced_stabilized_path, raw_path, candidate_dst)
    _enqueue_analysis_after_stabilization(asset_id, force=force)
    return {"asset_id": asset_id, "status": "done"}


__all__ = ["run_asset_stabilization"]
