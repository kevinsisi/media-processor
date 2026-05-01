"""M4 analysis pipeline orchestrator.

Runs the four steps in sequence (stt → scene → motion → coverage) for a
single Asset, persists per-step status, and stays partial-success-friendly:
a failure in one step records ``failed:{reason}`` for that step and
continues to the next step. The job exits successfully so RQ does not
retry the whole pipeline behind our backs.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from media_processor.api.config import settings
from media_processor.core.db import async_session_maker
from media_processor.models import (
    Asset,
    AssetStatus,
    AssetTag,
    AssetTranscript,
    Script,
    ScriptCoverage,
)
from media_processor.services import camera_motion, scene_tagging, script_coverage, whisper_stt

logger = logging.getLogger(__name__)


VALID_STEPS = ("stt", "scene", "motion", "coverage")
STEP_TIMEOUT_S = 30 * 60  # 30 min per step

# Max retry-able exceptions that map to a known reason token.
# Anything else falls back to model-error:{exception_class_name}.
_KNOWN_REASONS: dict[type[Exception], str] = {
    whisper_stt.WhisperUnavailableError: "gpu-unavailable",
    scene_tagging.SceneQuotaExhaustedError: "quota-exhausted",
    script_coverage.ScriptCoverageQuotaError: "quota-exhausted",
    script_coverage.ScriptCoverageMissingScriptError: "missing-script",
}

_GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"


def _failure_reason(exc: Exception) -> str:
    for cls, token in _KNOWN_REASONS.items():
        if isinstance(exc, cls):
            return f"failed:{token}"
    return f"failed:model-error:{type(exc).__name__}"


def _api_keys() -> tuple[str, ...]:
    return tuple(k.strip() for k in settings.llm_api_keys.split(",") if k.strip())


async def _load_asset(session: AsyncSession, asset_id: int) -> Asset:
    asset = (
        await session.execute(
            select(Asset).where(Asset.id == asset_id).options(selectinload(Asset.tags))
        )
    ).scalar_one_or_none()
    if asset is None:
        raise RuntimeError(f"asset {asset_id} not found")
    return asset


async def _load_script_body(session: AsyncSession, project_id: int) -> str:
    script = (
        await session.execute(select(Script).where(Script.project_id == project_id))
    ).scalar_one_or_none()
    if script is None:
        return ""
    return script.body or ""


async def _set_step_state(
    session: AsyncSession, asset_id: int, step: str, value: str
) -> None:
    """Read-modify-write the analysis_steps_json blob for ``asset_id``."""
    asset = await session.get(Asset, asset_id)
    if asset is None:
        return
    blob: dict[str, str] = dict(asset.analysis_steps_json or {})
    blob[step] = value
    asset.analysis_steps_json = blob
    await session.commit()


async def _initial_step_blob(
    session: AsyncSession,
    asset_id: int,
    requested_steps: tuple[str, ...],
) -> None:
    """Set assets.status='analyzing' and seed analysis_steps_json for requested steps."""
    asset = await session.get(Asset, asset_id)
    if asset is None:
        raise RuntimeError(f"asset {asset_id} not found")
    blob: dict[str, str] = dict(asset.analysis_steps_json or {})
    for step in requested_steps:
        blob[step] = "pending"
    asset.analysis_steps_json = blob
    asset.status = AssetStatus.ANALYZING.value
    await session.commit()


async def _finalise_status(
    session: AsyncSession, asset_id: int, requested_steps: tuple[str, ...]
) -> None:
    asset = await session.get(Asset, asset_id)
    if asset is None:
        return
    blob: dict[str, str] = dict(asset.analysis_steps_json or {})
    requested_states = [blob.get(s, "pending") for s in requested_steps]
    has_failure = any(state.startswith("failed:") for state in requested_states)
    has_pending_or_running = any(
        state in {"pending", "running"} for state in requested_states
    )
    if has_pending_or_running:
        # Should not happen after a normal pipeline run — leave status alone.
        return
    if has_failure and all(state.startswith("failed:") for state in requested_states):
        asset.status = AssetStatus.ANALYSIS_FAILED.value
    else:
        # Partial success counts as analyzed — operator can re-run failed steps.
        asset.status = AssetStatus.ANALYZED.value
    await session.commit()


# ---------- step runners ----------


async def _run_stt(
    session: AsyncSession,
    asset: Asset,
    *,
    force: bool,
) -> str:
    """Run STT step. Returns the new state token for the step."""
    existing = (
        await session.execute(
            select(AssetTranscript).where(AssetTranscript.asset_id == asset.id)
        )
    ).scalar_one_or_none()
    if existing is not None and existing.edited and not force:
        logger.info(
            "asset %d transcript edited=true, skipping STT (force=False)", asset.id
        )
        return "done"

    media_path = Path(asset.file_path)
    result = await asyncio.to_thread(whisper_stt.transcribe, media_path)
    segments_json = [
        {"idx": s.idx, "start_ms": s.start_ms, "end_ms": s.end_ms, "text": s.text}
        for s in result.segments
    ]

    if existing is None:
        row = AssetTranscript(
            asset_id=asset.id,
            language=result.language,
            model=result.model,
            transcript_text=result.transcript_text,
            segments_json=segments_json,
            edited=False,
        )
        session.add(row)
    else:
        existing.language = result.language
        existing.model = result.model
        existing.transcript_text = result.transcript_text
        existing.segments_json = segments_json
        existing.edited = False
        existing.updated_at = datetime.now(timezone.utc)
    await session.commit()
    return "done"


async def _run_scene(
    session: AsyncSession,
    asset: Asset,
    *,
    force: bool,
) -> str:
    api_keys = _api_keys()
    if not api_keys:
        raise scene_tagging.SceneTaggingError("LLM_API_KEYS not configured")

    scratch = Path(settings.analysis_dir) / str(asset.id) / "scene"
    media_path = Path(asset.file_path)
    result = await scene_tagging.classify_asset(
        media_path,
        asset.duration_ms,
        api_keys=api_keys,
        model=settings.llm_model,
        base_url=_GEMINI_BASE_URL,
        timeout_s=settings.llm_timeout_s,
        interval_ms=settings.scene_sample_interval_ms,
        scratch_dir=scratch,
    )

    if force:
        await session.execute(
            delete(AssetTag)
            .where(AssetTag.asset_id == asset.id)
            .where(AssetTag.tag_type == "scene")
            .where(AssetTag.source_model.like("gemini-vision-%"))
        )
        await session.commit()

    for tag_name, mean_conf in result.tags:
        # Insert; rely on the unique constraint to no-op on duplicates if
        # the user re-runs without force.
        existing = (
            await session.execute(
                select(AssetTag)
                .where(AssetTag.asset_id == asset.id)
                .where(AssetTag.tag_type == "scene")
                .where(AssetTag.tag_name == tag_name)
                .where(AssetTag.source_model == result.model)
            )
        ).scalar_one_or_none()
        if existing is None:
            session.add(
                AssetTag(
                    asset_id=asset.id,
                    tag_type="scene",
                    tag_name=tag_name,
                    confidence=float(mean_conf),
                    source_model=result.model,
                    time_ranges_ms=None,
                )
            )
        else:
            existing.confidence = float(mean_conf)
    await session.commit()
    return "done"


async def _run_motion(
    session: AsyncSession,
    asset: Asset,
    *,
    force: bool,
) -> str:
    scratch = Path(settings.analysis_dir) / str(asset.id) / "motion"
    media_path = Path(asset.file_path)
    segments = await asyncio.to_thread(camera_motion.detect_motion, media_path, scratch)

    source_model = "opencv-optical-flow"
    if force:
        await session.execute(
            delete(AssetTag)
            .where(AssetTag.asset_id == asset.id)
            .where(AssetTag.tag_type == "motion")
            .where(AssetTag.source_model == source_model)
        )
        await session.commit()

    for seg in segments:
        # Each merged motion window becomes its own row with time_ranges_ms.
        # The unique constraint is (asset_id, tag_type, tag_name, source_model)
        # so multiple windows of the same class collapse to one row whose
        # time_ranges_ms grows; merge into a single existing row by appending.
        existing = (
            await session.execute(
                select(AssetTag)
                .where(AssetTag.asset_id == asset.id)
                .where(AssetTag.tag_type == "motion")
                .where(AssetTag.tag_name == seg.motion_type)
                .where(AssetTag.source_model == source_model)
            )
        ).scalar_one_or_none()
        new_range = [seg.start_ms, seg.end_ms]
        if existing is None:
            session.add(
                AssetTag(
                    asset_id=asset.id,
                    tag_type="motion",
                    tag_name=seg.motion_type,
                    confidence=1.0,
                    source_model=source_model,
                    time_ranges_ms=[new_range],
                )
            )
        else:
            ranges = list(existing.time_ranges_ms or [])
            ranges.append(new_range)
            existing.time_ranges_ms = ranges
    await session.commit()
    return "done"


async def _run_coverage(session: AsyncSession, asset: Asset) -> str:
    api_keys = _api_keys()
    if not api_keys:
        raise script_coverage.ScriptCoverageError("LLM_API_KEYS not configured")

    transcript = (
        await session.execute(
            select(AssetTranscript).where(AssetTranscript.asset_id == asset.id)
        )
    ).scalar_one_or_none()
    if transcript is None or not transcript.segments_json:
        raise script_coverage.ScriptCoverageError("transcript missing for coverage")

    script_row = (
        await session.execute(select(Script).where(Script.project_id == asset.project_id))
    ).scalar_one_or_none()
    if script_row is None or not (script_row.body or "").strip():
        raise script_coverage.ScriptCoverageMissingScriptError("project script empty")

    segments_in = [
        script_coverage.TranscriptSegmentInput(
            idx=int(s["idx"]),
            start_ms=int(s["start_ms"]),
            end_ms=int(s["end_ms"]),
            text=str(s["text"]),
        )
        for s in transcript.segments_json
    ]
    result = await script_coverage.compare(
        script_body=script_row.body,
        segments=segments_in,
        api_keys=api_keys,
        model=settings.llm_model,
        base_url=_GEMINI_BASE_URL,
        timeout_s=settings.llm_timeout_s,
    )

    # Replace any existing row.
    await session.execute(delete(ScriptCoverage).where(ScriptCoverage.asset_id == asset.id))
    session.add(
        ScriptCoverage(
            asset_id=asset.id,
            script_id=script_row.id,
            model=result.model,
            scripted_segment_count=result.scripted_segment_count,
            total_segment_count=result.total_segment_count,
            coverage_ratio_by_count=result.coverage_ratio_by_count,
            coverage_ratio_by_duration_ms=result.coverage_ratio_by_duration_ms,
            match_details_json=[
                {
                    "transcript_idx": m.transcript_idx,
                    "classification": m.classification,
                    "confidence": m.confidence,
                    "matched_script_excerpt": m.matched_script_excerpt,
                }
                for m in result.matches
            ],
        )
    )
    await session.commit()
    return "done"


# ---------- public entry point ----------


async def run_pipeline(
    asset_id: int,
    *,
    steps: list[str] | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Run the analysis pipeline for one Asset and return a small summary dict.

    The orchestrator owns DB session lifecycle. On exception inside a step,
    the failure is recorded in ``analysis_steps_json`` and the pipeline
    continues to the next step.
    """
    requested = tuple(steps) if steps else VALID_STEPS
    unknown = [s for s in requested if s not in VALID_STEPS]
    if unknown:
        raise ValueError(f"unknown analysis steps: {unknown}")

    summary: dict[str, str] = {}
    async with async_session_maker() as session:
        await _initial_step_blob(session, asset_id, requested)

    # Each step runs in its own session so a SQL error in one step does not
    # poison subsequent steps' transactions.
    for step in requested:
        async with async_session_maker() as session:
            await _set_step_state(session, asset_id, step, "running")
            try:
                async with async_session_maker() as work_session:
                    asset = await _load_asset(work_session, asset_id)
                    new_state = await asyncio.wait_for(
                        _dispatch(step, work_session, asset, force=force),
                        timeout=STEP_TIMEOUT_S,
                    )
            except asyncio.TimeoutError:
                logger.warning("step %r timed out for asset %d", step, asset_id)
                new_state = "failed:timeout"
            except Exception as exc:  # noqa: BLE001 — record and continue.
                logger.exception("step %r failed for asset %d", step, asset_id)
                new_state = _failure_reason(exc)
            await _set_step_state(session, asset_id, step, new_state)
            summary[step] = new_state

    async with async_session_maker() as session:
        await _finalise_status(session, asset_id, requested)

    return {"asset_id": asset_id, "steps": summary}


async def _dispatch(
    step: str,
    session: AsyncSession,
    asset: Asset,
    *,
    force: bool,
) -> str:
    if step == "stt":
        return await _run_stt(session, asset, force=force)
    if step == "scene":
        return await _run_scene(session, asset, force=force)
    if step == "motion":
        return await _run_motion(session, asset, force=force)
    if step == "coverage":
        return await _run_coverage(session, asset)
    raise ValueError(f"unknown step: {step}")


__all__ = ["VALID_STEPS", "run_pipeline"]
