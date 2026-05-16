"""Worker-side runner for v0.40.0 asset-level stabilization."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from sqlalchemy import select

from media_processor.api.config import settings
from media_processor.core.db import async_session_maker
from media_processor.models import Asset
from media_processor.services import asset_variants

logger = logging.getLogger(__name__)


async def run_asset_stabilization(asset_id: int, *, force: bool = False) -> dict[str, str | int]:
    async with async_session_maker() as session:
        asset = (
            await session.execute(select(Asset).where(Asset.id == asset_id))
        ).scalar_one_or_none()
        if asset is None:
            logger.warning("run_asset_stabilization: asset %d not found", asset_id)
            return {"asset_id": asset_id, "status": "missing"}
        if (
            not force
            and asset_variants.stabilization_status(asset) == asset_variants.STABILIZATION_DONE
            and asset.stabilized_path
            and Path(asset.stabilized_path).is_file()
        ):
            return {"asset_id": asset_id, "status": "done"}
        asset.stabilization_status = asset_variants.STABILIZATION_RUNNING
        asset.stabilization_error = None
        dst = asset_variants.stabilized_path_for_asset(asset)
        asset.stabilized_path = str(dst)
        await session.commit()
        src = asset_variants.selected_media_path(asset)
        if src == dst:
            # A forced rerun can be requested while the stabilized variant is
            # active. Avoid reading and replacing the same file in one ffmpeg
            # operation; regenerate from immutable raw in that case.
            src = Path(asset.file_path)
        scratch = Path(settings.analysis_dir) / "asset_stabilization" / str(asset_id)

    tracking_error: str | None
    try:
        tracking_result = await asyncio.to_thread(
            asset_variants.stabilize_source_from_tracking,
            asset,
            src,
            dst,
            scratch,
        )
    except Exception as exc:  # noqa: BLE001 — fall back to vidstab/preflight below.
        logger.exception(
            "run_asset_stabilization: asset %d tracking-based stabilization failed; falling back",
            asset_id,
        )
        tracking_result = None
        tracking_error = f"tracking stabilization failed: {type(exc).__name__}: {exc}"
    else:
        tracking_error = None
    if tracking_result is not None:
        async with async_session_maker() as session:
            row = await session.get(Asset, asset_id)
            if row is None:
                return {"asset_id": asset_id, "status": "missing_after"}
            row.stabilized_path = str(dst)
            row.stabilization_status = asset_variants.STABILIZATION_DONE
            row.stabilization_error = (
                f"tracking-based stabilization ({tracking_result.mode}); "
                f"points={tracking_result.point_count}; "
                f"crop={tracking_result.crop_w}x{tracking_result.crop_h}"
            )
            await session.commit()
        return {"asset_id": asset_id, "status": "done"}

    if not force:
        estimate = await asyncio.to_thread(asset_variants.estimate_stabilization_need, src)
        if not estimate.should_stabilize:
            logger.info(
                "run_asset_stabilization: asset %d skipped low-jitter source (%s)",
                asset_id,
                estimate.reason,
            )
            async with async_session_maker() as session:
                row = await session.get(Asset, asset_id)
                if row is not None:
                    row.stabilized_path = None
                    row.stabilization_status = asset_variants.STABILIZATION_SKIPPED
                    detail = f"low-jitter source skipped: {estimate.reason}"
                    row.stabilization_error = (
                        f"{tracking_error}; {detail}" if tracking_error else detail
                    )
                    await session.commit()
            return {"asset_id": asset_id, "status": asset_variants.STABILIZATION_SKIPPED}

    try:
        await asyncio.to_thread(asset_variants.stabilize_source, src, dst, scratch)
    except Exception as exc:  # noqa: BLE001 — persist terminal state for the UI.
        logger.exception("run_asset_stabilization: asset %d failed", asset_id)
        async with async_session_maker() as session:
            row = await session.get(Asset, asset_id)
            if row is not None:
                row.stabilization_status = asset_variants.STABILIZATION_FAILED
                row.stabilization_error = f"{type(exc).__name__}: {exc}"
                await session.commit()
        return {"asset_id": asset_id, "status": "failed"}

    async with async_session_maker() as session:
        row = await session.get(Asset, asset_id)
        if row is None:
            return {"asset_id": asset_id, "status": "missing_after"}
        row.stabilized_path = str(dst)
        row.stabilization_status = asset_variants.STABILIZATION_DONE
        row.stabilization_error = None
        await session.commit()
    return {"asset_id": asset_id, "status": "done"}


__all__ = ["run_asset_stabilization"]
