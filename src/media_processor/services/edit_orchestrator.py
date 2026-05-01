"""M5 — auto-edit orchestrator.

Coordinates the four stages: Gemini cut plan → DB write (Draft +
DraftSegments) → ffmpeg cut + concat → SRT build + subtitle burn-in.
Each stage flips ``Draft.progress_steps_json[stage]`` between
``pending | running | done | failed:{reason}`` so the UI can poll the
existing ``/drafts/{id}`` endpoint and watch progress.

The orchestrator owns the DB session lifecycle. RQ's worker just calls
:func:`run_render` via ``asyncio.run`` — it never sees the session.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from media_processor.api.config import settings
from media_processor.core.db import async_session_maker
from media_processor.models import (
    Asset,
    AssetTranscript,
    Draft,
    DraftSegment,
    DraftStatus,
    EditStep,
    Project,
)
from media_processor.profile.loader import ProfileSpec, load_profile
from media_processor.services import edit_planner, subtitles, video_renderer
from media_processor.services.edit_planner import CutPlan
from media_processor.services.settings_store import get_llm_api_keys

logger = logging.getLogger(__name__)


_STAGES: tuple[str, ...] = (
    EditStep.PLAN.value,
    EditStep.CUT.value,
    EditStep.CONCAT.value,
    EditStep.SUBTITLES.value,
)
_GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"


def _initial_progress() -> dict[str, str]:
    return dict.fromkeys(_STAGES, "pending")


def _failure_reason(exc: Exception) -> str:
    """Map known exceptions to a stable reason token; fall back to class name."""
    if isinstance(exc, edit_planner.EditPlanQuotaError):
        return "failed:quota-exhausted"
    if isinstance(exc, edit_planner.EditPlanInvalidError):
        return "failed:invalid-plan"
    if isinstance(exc, edit_planner.EditPlanEmptyError):
        return "failed:no-content"
    if isinstance(exc, video_renderer.VideoRenderTimeoutError):
        return "failed:timeout"
    if isinstance(exc, video_renderer.FFmpegMissingError):
        return "failed:ffmpeg-missing"
    if isinstance(exc, video_renderer.VideoRenderError):
        return "failed:render-error"
    return f"failed:model-error:{type(exc).__name__}"


# ---------- DB helpers ----------


async def _next_draft_version(session: AsyncSession, project_id: int) -> int:
    current = await session.scalar(
        select(func.max(Draft.version)).where(Draft.project_id == project_id)
    )
    return int(current or 0) + 1


@dataclass
class _DraftHandle:
    draft_id: int
    profile_name: str
    target_aspect: str
    version: int


async def _create_draft_row(
    project: Project,
) -> _DraftHandle:
    async with async_session_maker() as session:
        version = await _next_draft_version(session, project.id)
        draft = Draft(
            project_id=project.id,
            profile_name=project.profile_name,
            version=version,
            status=DraftStatus.PROCESSING.value,
            progress_steps_json=_initial_progress(),
        )
        session.add(draft)
        await session.commit()
        await session.refresh(draft)
        return _DraftHandle(
            draft_id=draft.id,
            profile_name=project.profile_name,
            target_aspect=project.target_aspect_ratio,
            version=version,
        )


async def _adopt_draft_row(project: Project, draft_id: int) -> _DraftHandle:
    """Look up a draft created by the API endpoint and re-flag it as in
    progress. The worker takes over from here, so anything stale (a leftover
    progress map from a force-retry) gets reset."""
    async with async_session_maker() as session:
        draft = await session.get(Draft, draft_id)
        if draft is None:
            raise RuntimeError(
                f"draft {draft_id} not found (was it deleted between enqueue and dequeue?)"
            )
        if draft.project_id != project.id:
            raise RuntimeError(
                f"draft {draft_id} belongs to project {draft.project_id}, not {project.id}"
            )
        draft.status = DraftStatus.PROCESSING.value
        draft.progress_steps_json = _initial_progress()
        draft.prompt_feedback = None
        await session.commit()
        await session.refresh(draft)
        return _DraftHandle(
            draft_id=draft.id,
            profile_name=project.profile_name,
            target_aspect=project.target_aspect_ratio,
            version=draft.version,
        )


async def _set_stage_state(draft_id: int, stage: str, value: str) -> None:
    async with async_session_maker() as session:
        draft = await session.get(Draft, draft_id)
        if draft is None:
            return
        blob: dict[str, str] = dict(draft.progress_steps_json or {})
        blob[stage] = value
        draft.progress_steps_json = blob
        await session.commit()


async def _persist_plan(handle: _DraftHandle, plan: CutPlan) -> None:
    """Write ``Draft.cut_plan_json`` plus a row per CutPlanSegment."""
    async with async_session_maker() as session:
        draft = await session.get(Draft, handle.draft_id)
        if draft is None:
            raise RuntimeError(f"draft {handle.draft_id} disappeared")
        draft.cut_plan_json = edit_planner.serialise_plan(plan)
        cursor_ms = 0
        # Replace any leftover segments from a prior run (force).
        await session.execute(
            delete(DraftSegment).where(DraftSegment.draft_id == handle.draft_id)
        )
        for cut in plan.segments:
            duration = cut.asset_end_ms - cut.asset_start_ms
            session.add(
                DraftSegment(
                    draft_id=handle.draft_id,
                    order=cut.order,
                    asset_id=cut.asset_id,
                    asset_start_ms=cut.asset_start_ms,
                    asset_end_ms=cut.asset_end_ms,
                    on_timeline_start_ms=cursor_ms,
                    on_timeline_end_ms=cursor_ms + max(1, duration),
                    source_kind=cut.source_kind,
                    plan_reason=cut.reason,
                )
            )
            cursor_ms += max(1, duration)
        if plan.used_fallback and plan.fallback_reason:
            draft.prompt_feedback = plan.fallback_reason
        await session.commit()


async def _mark_failed(draft_id: int, message: str) -> None:
    async with async_session_maker() as session:
        draft = await session.get(Draft, draft_id)
        if draft is None:
            return
        draft.status = DraftStatus.FAILED.value
        draft.prompt_feedback = (
            (draft.prompt_feedback or "") + f"\n[render-failed] {message}"
        ).strip()
        await session.commit()


async def _mark_ready(
    draft_id: int,
    *,
    output_path: Path,
    srt_path: Path | None,
) -> None:
    async with async_session_maker() as session:
        draft = await session.get(Draft, draft_id)
        if draft is None:
            return
        draft.status = DraftStatus.READY_FOR_REVIEW.value
        draft.mp4_preview_path = str(output_path)
        if srt_path is not None and srt_path.is_file():
            draft.subtitle_path = str(srt_path)
        await session.commit()


async def _gather_render_inputs(
    project_id: int,
) -> tuple[Project, dict[int, Path], dict[int, AssetTranscript]]:
    async with async_session_maker() as session:
        project = await session.get(Project, project_id)
        if project is None:
            raise RuntimeError(f"project {project_id} not found")
        assets = (
            (await session.execute(select(Asset).where(Asset.project_id == project_id)))
            .scalars()
            .all()
        )
        asset_paths = {a.id: Path(a.file_path) for a in assets}
        tx_rows = (
            (
                await session.execute(
                    select(AssetTranscript).where(
                        AssetTranscript.asset_id.in_(asset_paths.keys())
                    )
                )
            )
            .scalars()
            .all()
        )
        transcripts = {t.asset_id: t for t in tx_rows}
        return project, asset_paths, transcripts


# ---------- Plan stage ----------


def _try_load_profile(profile_name: str) -> ProfileSpec | None:
    """Best-effort profile load — orchestrator falls back to defaults on miss."""
    try:
        path = Path(settings.profiles_dir) / f"{profile_name}.yaml"
        if not path.is_file():
            logger.warning("profile %r not found at %s", profile_name, path)
            return None
        return load_profile(path)
    except Exception as exc:  # noqa: BLE001 — fall back to defaults.
        logger.warning("profile %r failed to load: %s", profile_name, exc)
        return None


async def _plan_stage(project_id: int, target_duration_ms: int) -> CutPlan:
    """Run the Gemini planner with key-pool + fallback."""
    async with async_session_maker() as session:
        api_keys = await get_llm_api_keys(session)

    fallback_reason: str | None = None
    if api_keys:
        try:
            async with async_session_maker() as session:
                return await edit_planner.plan(
                    project_id,
                    session,
                    api_keys=api_keys,
                    model=settings.llm_model,
                    base_url=_GEMINI_BASE_URL,
                    timeout_s=settings.llm_timeout_s,
                    target_duration_ms=target_duration_ms,
                )
        except edit_planner.EditPlanEmptyError:
            raise
        except edit_planner.EditPlanError as exc:
            logger.warning("edit-planner failed; falling back to heuristic: %s", exc)
            fallback_reason = f"gemini failed ({type(exc).__name__}); used heuristic"
    else:
        fallback_reason = "no LLM_API_KEYS configured; used heuristic"

    async with async_session_maker() as session:
        return await edit_planner.heuristic_fallback(
            project_id,
            session,
            target_duration_ms=target_duration_ms,
            fallback_reason=fallback_reason or "fallback",
        )


# ---------- Public entry point ----------


def _output_paths(project_id: int, version: int) -> tuple[Path, Path]:
    base = Path(settings.drafts_dir) / str(project_id)
    return (base / f"v{version}.mp4", base / f"v{version}.srt")


async def run_render(
    project_id: int, *, draft_id: int | None = None, force: bool = False
) -> dict[str, Any]:
    """Run the full M5 pipeline for ``project_id`` and return a summary.

    When ``draft_id`` is given the API endpoint already created the row and
    the worker just adopts it (the common path now). When ``draft_id`` is
    ``None`` (legacy / direct invocation) the orchestrator reserves a fresh
    row so existing tooling keeps working.

    The return value is for RQ's job-result store; the UI polls
    ``GET /drafts/{id}`` and the orchestrator keeps that row in sync.
    """
    # Load the project up-front so we know the draft will have something
    # to attach to before we reserve a draft id.
    async with async_session_maker() as session:
        project = await session.get(Project, project_id)
        if project is None:
            raise RuntimeError(f"project {project_id} not found")

    if draft_id is None:
        handle = await _create_draft_row(project)
    else:
        handle = await _adopt_draft_row(project, draft_id)
    summary: dict[str, Any] = {
        "draft_id": handle.draft_id,
        "version": handle.version,
        "stages": _initial_progress(),
    }

    profile_spec = _try_load_profile(handle.profile_name)
    target_duration_ms = (
        profile_spec.editing_rules.target_duration_ms
        if profile_spec is not None
        else edit_planner.DEFAULT_TARGET_DURATION_MS
    )

    # Stage 1 — plan.
    await _set_stage_state(handle.draft_id, EditStep.PLAN.value, "running")
    try:
        plan = await _plan_stage(project_id, target_duration_ms)
        await _persist_plan(handle, plan)
    except Exception as exc:  # noqa: BLE001 — record + abort.
        reason = _failure_reason(exc)
        logger.exception("plan stage failed for project %d", project_id)
        await _set_stage_state(handle.draft_id, EditStep.PLAN.value, reason)
        await _mark_failed(handle.draft_id, f"plan: {exc}")
        summary["stages"][EditStep.PLAN.value] = reason
        return summary
    await _set_stage_state(handle.draft_id, EditStep.PLAN.value, "done")
    summary["stages"][EditStep.PLAN.value] = "done"

    # Build SRT side-output BEFORE rendering so we can pass it into
    # the burn-in stage. Subtitle generation is pure-Python and cheap.
    project, asset_paths, transcripts = await _gather_render_inputs(project_id)
    srt_text = subtitles.build_srt(plan, transcripts)
    output_path, srt_path = _output_paths(project_id, handle.version)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if srt_text:
        srt_path.write_text(srt_text, encoding="utf-8")
    else:
        # No subtitles for this draft; remove any stale file from a
        # prior render at the same version.
        if srt_path.is_file():
            srt_path.unlink()

    scratch_dir = Path(settings.analysis_dir) / "edits"
    scratch_dir.mkdir(parents=True, exist_ok=True)

    # Stages 2 + 3 + 4 — cut / concat / subtitles. The renderer batches
    # these so the on_progress callback is the only progress hook.
    progress_state = {"cut": "pending", "concat": "pending", "subtitles": "pending"}

    async def update_state(stage: str, value: str) -> None:
        progress_state[stage] = value
        await _set_stage_state(handle.draft_id, stage, value)
        summary["stages"][stage] = value

    await update_state(EditStep.CUT.value, "running")

    # The renderer's progress callback is sync; we shuttle stage transitions
    # through asyncio.run_coroutine_threadsafe so the worker process can
    # update the row from inside a thread (the renderer calls subprocess
    # under asyncio.to_thread).
    loop = asyncio.get_running_loop()

    def _sync_progress(stage: str, done: int, total: int) -> None:
        if stage == "cut" and done < total:
            return  # only flip terminal state
        # Map the renderer's three buckets to our stage names + done state.
        if stage == "cut":
            asyncio.run_coroutine_threadsafe(
                update_state(EditStep.CUT.value, "done"), loop
            ).result(timeout=10)
            asyncio.run_coroutine_threadsafe(
                update_state(EditStep.CONCAT.value, "running"), loop
            ).result(timeout=10)
        elif stage == "concat":
            asyncio.run_coroutine_threadsafe(
                update_state(EditStep.CONCAT.value, "done"), loop
            ).result(timeout=10)
            asyncio.run_coroutine_threadsafe(
                update_state(EditStep.SUBTITLES.value, "running"), loop
            ).result(timeout=10)
        elif stage == "subtitles":
            asyncio.run_coroutine_threadsafe(
                update_state(EditStep.SUBTITLES.value, "done"), loop
            ).result(timeout=10)

    try:
        result = await asyncio.to_thread(
            video_renderer.render,
            plan,
            draft_id=handle.draft_id,
            target_aspect=handle.target_aspect,
            asset_paths=asset_paths,
            output_path=output_path,
            srt_path=srt_path if srt_text else None,
            scratch_dir=scratch_dir,
            on_progress=_sync_progress,
        )
    except Exception as exc:  # noqa: BLE001 — record + mark failed.
        reason = _failure_reason(exc)
        logger.exception("render stages failed for draft %d", handle.draft_id)
        # Find the first non-done stage and attribute the failure to it.
        for stage in (EditStep.CUT.value, EditStep.CONCAT.value, EditStep.SUBTITLES.value):
            if progress_state.get(stage) != "done":
                await _set_stage_state(handle.draft_id, stage, reason)
                summary["stages"][stage] = reason
                break
        await _mark_failed(handle.draft_id, f"render: {exc}")
        return summary

    await _mark_ready(
        handle.draft_id,
        output_path=result.output_path,
        srt_path=srt_path if srt_text else None,
    )
    video_renderer.cleanup_intermediates(result.intermediate_dir)
    return summary


__all__ = ["run_render"]
