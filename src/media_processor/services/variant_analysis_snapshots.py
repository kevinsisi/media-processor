"""Persist and restore per-source-variant asset analysis results."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from media_processor.models import Asset, AssetTag, AssetTranscript, Script, ScriptCoverage
from media_processor.models.enums import AssetStatus
from media_processor.services import asset_variants

SCHEMA_VERSION = "asset.variant-analysis.v1"
VARIANT_DEPENDENT_TAG_TYPES = ("scene", "motion", "emotion")


def _json_obj(value: Any) -> Any:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, list):
        return list(value)
    return value


async def save_variant_analysis_snapshot(session: AsyncSession, asset: Asset) -> None:
    """Store the current active variant's analysis rows in ``assets`` JSON.

    This is intentionally DB persistence, not an in-process cache: switching
    raw/stabilized variants must survive page reloads, worker restarts, and
    deployments without repeatedly burning GPU / Gemini quota.
    """
    variant = asset_variants.active_variant(asset)
    tags = (
        (
            await session.execute(
                select(AssetTag)
                .where(AssetTag.asset_id == asset.id)
                .where(AssetTag.tag_type.in_(VARIANT_DEPENDENT_TAG_TYPES))
            )
        )
        .scalars()
        .all()
    )
    transcript = (
        await session.execute(select(AssetTranscript).where(AssetTranscript.asset_id == asset.id))
    ).scalar_one_or_none()
    coverage = (
        await session.execute(select(ScriptCoverage).where(ScriptCoverage.asset_id == asset.id))
    ).scalar_one_or_none()

    store = dict(asset.variant_analysis_json or {})
    store[variant] = {
        "schema_version": SCHEMA_VERSION,
        "saved_at": datetime.now(UTC).isoformat(),
        "status": asset.status,
        "analysis_steps_json": _json_obj(asset.analysis_steps_json),
        "tracking_json": _json_obj(asset.tracking_json),
        "tracked_object_index": asset.tracked_object_index,
        "custom_roi_json": _json_obj(asset.custom_roi_json),
        "point_tracking_json": _json_obj(asset.point_tracking_json),
        "point_tracking_origin": _json_obj(asset.point_tracking_origin),
        "point_tracking_status": asset.point_tracking_status,
        "point_tracking_error": asset.point_tracking_error,
        "tags": [
            {
                "tag_type": t.tag_type,
                "tag_name": t.tag_name,
                "confidence": float(t.confidence),
                "source_model": t.source_model,
                "time_ranges_ms": _json_obj(t.time_ranges_ms),
            }
            for t in tags
        ],
        "transcript": (
            {
                "language": transcript.language,
                "model": transcript.model,
                "transcript_text": transcript.transcript_text,
                "segments_json": _json_obj(transcript.segments_json),
                "edited": bool(transcript.edited),
            }
            if transcript is not None
            else None
        ),
        "coverage": (
            {
                "script_id": coverage.script_id,
                "model": coverage.model,
                "scripted_segment_count": coverage.scripted_segment_count,
                "total_segment_count": coverage.total_segment_count,
                "coverage_ratio_by_count": coverage.coverage_ratio_by_count,
                "coverage_ratio_by_duration_ms": coverage.coverage_ratio_by_duration_ms,
                "match_details_json": _json_obj(coverage.match_details_json),
            }
            if coverage is not None
            else None
        ),
    }
    asset.variant_analysis_json = store


async def clear_variant_dependent_state(session: AsyncSession, asset: Asset) -> None:
    """Clear rows/columns tied to the currently selected source video."""
    await session.execute(
        delete(AssetTag)
        .where(AssetTag.asset_id == asset.id)
        .where(AssetTag.tag_type.in_(VARIANT_DEPENDENT_TAG_TYPES))
    )
    await session.execute(delete(AssetTranscript).where(AssetTranscript.asset_id == asset.id))
    await session.execute(delete(ScriptCoverage).where(ScriptCoverage.asset_id == asset.id))
    asset.analysis_steps_json = None
    asset.status = AssetStatus.PENDING.value
    asset.tracking_json = None
    asset.tracked_object_index = None
    asset.custom_roi_json = None
    asset.point_tracking_json = None
    asset.point_tracking_origin = None
    asset.point_tracking_status = None
    asset.point_tracking_error = None


async def restore_variant_analysis_snapshot(
    session: AsyncSession,
    asset: Asset,
    variant: asset_variants.AssetVariant,
) -> bool:
    """Restore ``variant`` analysis from DB JSON. Returns False when absent."""
    store = asset.variant_analysis_json if isinstance(asset.variant_analysis_json, dict) else {}
    snapshot = store.get(variant)
    if not isinstance(snapshot, dict) or snapshot.get("schema_version") != SCHEMA_VERSION:
        return False

    await clear_variant_dependent_state(session, asset)
    asset.status = str(snapshot.get("status") or AssetStatus.ANALYZED.value)
    asset.analysis_steps_json = snapshot.get("analysis_steps_json")
    asset.tracking_json = snapshot.get("tracking_json")
    asset.tracked_object_index = snapshot.get("tracked_object_index")
    asset.custom_roi_json = snapshot.get("custom_roi_json")
    asset.point_tracking_json = snapshot.get("point_tracking_json")
    asset.point_tracking_origin = snapshot.get("point_tracking_origin")
    asset.point_tracking_status = snapshot.get("point_tracking_status")
    asset.point_tracking_error = snapshot.get("point_tracking_error")

    for row in snapshot.get("tags") or []:
        if not isinstance(row, dict):
            continue
        session.add(
            AssetTag(
                asset_id=asset.id,
                tag_type=str(row.get("tag_type") or ""),
                tag_name=str(row.get("tag_name") or ""),
                confidence=float(row.get("confidence") or 0.0),
                source_model=str(row.get("source_model") or "snapshot"),
                time_ranges_ms=row.get("time_ranges_ms"),
            )
        )

    transcript = snapshot.get("transcript")
    if isinstance(transcript, dict):
        session.add(
            AssetTranscript(
                asset_id=asset.id,
                language=str(transcript.get("language") or "zh-Hant"),
                model=str(transcript.get("model") or "snapshot"),
                transcript_text=str(transcript.get("transcript_text") or ""),
                segments_json=transcript.get("segments_json") or [],
                edited=bool(transcript.get("edited", False)),
            )
        )

    coverage = snapshot.get("coverage")
    if isinstance(coverage, dict):
        script_id = coverage.get("script_id")
        if isinstance(script_id, int) and await session.get(Script, script_id) is not None:
            session.add(
                ScriptCoverage(
                    asset_id=asset.id,
                    script_id=script_id,
                    model=str(coverage.get("model") or "snapshot"),
                    scripted_segment_count=int(coverage.get("scripted_segment_count") or 0),
                    total_segment_count=int(coverage.get("total_segment_count") or 0),
                    coverage_ratio_by_count=float(coverage.get("coverage_ratio_by_count") or 0.0),
                    coverage_ratio_by_duration_ms=float(
                        coverage.get("coverage_ratio_by_duration_ms") or 0.0
                    ),
                    match_details_json=coverage.get("match_details_json") or [],
                )
            )
    return True
