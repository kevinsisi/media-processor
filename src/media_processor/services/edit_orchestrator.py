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
import os
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
    SubtitleCueRow,
)
from media_processor.profile.loader import ProfileSpec, load_profile
from media_processor.services import (
    asset_variants,
    beat_sync,
    bgm_mixer,
    edit_planner,
    smart_camera_planner,
    subtitles,
    video_renderer,
)
from media_processor.services.edit_planner import CutPlan
from media_processor.services.settings_store import get_llm_api_keys

logger = logging.getLogger(__name__)


_STAGES: tuple[str, ...] = (
    EditStep.PLAN.value,
    EditStep.CUT.value,
    EditStep.STABILIZE.value,
    EditStep.CONCAT.value,
    EditStep.SUBTITLES.value,
    EditStep.BGM.value,
)
_GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"

# Bounds on the auto-computed target render length. Mirrors the API's
# 10–300 s validation on EditTriggerRequest.target_duration_seconds so
# manual overrides and auto picks land in the same envelope.
_AUTO_TARGET_MIN_MS = 60_000
_AUTO_TARGET_MAX_MS = 180_000
_USER_TARGET_MIN_MS = 10_000
_USER_TARGET_MAX_MS = 300_000


def _compute_auto_target_ms(profile_target_ms: int, total_source_ms: int, asset_count: int) -> int:
    """Pick a target render length from the available source material.

    Profiles default to 30 s, fine for a 1–2 min shoot. With a lot of
    source (>5 min, many clips) the planner should produce a longer reel
    so a meaningful share of the footage shows up in the cut. Returns
    the profile setting unchanged for small shoots.
    """
    if total_source_ms < 300_000:
        return profile_target_ms
    source_based = max(_AUTO_TARGET_MIN_MS, min(_AUTO_TARGET_MAX_MS, total_source_ms // 10))
    asset_floor = max(_AUTO_TARGET_MIN_MS, (asset_count // 2) * 5_000)
    dynamic = max(source_based, asset_floor)
    return max(profile_target_ms, dynamic)


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


async def _claim_pending_draft(
    project: Project,
) -> _DraftHandle:
    """Claim the latest ``pending`` draft for the project and flip it to
    ``processing``. The API creates the row synchronously when the user
    triggers an edit, so by the time the worker runs there should always
    be a pending row to claim. As a defensive fallback we create one if
    none exists (e.g. legacy in-flight RQ jobs from before this change)."""
    async with async_session_maker() as session:
        pending = (
            await session.execute(
                select(Draft)
                .where(Draft.project_id == project.id)
                .where(Draft.status == DraftStatus.PENDING.value)
                .order_by(Draft.version.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if pending is not None:
            pending.status = DraftStatus.PROCESSING.value
            if not pending.progress_steps_json:
                pending.progress_steps_json = _initial_progress()
            await session.commit()
            await session.refresh(pending)
            return _DraftHandle(
                draft_id=pending.id,
                profile_name=project.profile_name,
                target_aspect=project.target_aspect_ratio,
                version=pending.version,
            )
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
        if draft.status != DraftStatus.PENDING.value:
            raise RuntimeError(
                f"stale render job ignored for draft {draft_id}: status is {draft.status!r}"
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
        if draft.status not in (DraftStatus.PENDING.value, DraftStatus.PROCESSING.value):
            return
        blob: dict[str, str] = dict(draft.progress_steps_json or {})
        blob[stage] = value
        draft.progress_steps_json = blob
        await session.commit()


async def _load_segment_volumes(draft_id: int) -> list[bgm_mixer.SegmentVolume]:
    """Pull per-DraftSegment voice/bgm volume overrides for the mixer.

    Times are in *output timeline* seconds (matches what SRT cues use).
    Returns an empty list when the draft has no segments yet, e.g. the
    pre-render path before _persist_plan ran.
    """
    async with async_session_maker() as session:
        rows = (
            (
                await session.execute(
                    select(DraftSegment)
                    .where(DraftSegment.draft_id == draft_id)
                    .order_by(DraftSegment.order)
                )
            )
            .scalars()
            .all()
        )
    out: list[bgm_mixer.SegmentVolume] = []
    for r in rows:
        # v0.24.0 — explicit None-check on ``voice_volume``. The pre-fix
        # form was ``float(getattr(r, "voice_volume", 1.0) or 1.0)``,
        # which silently turned ``voice_volume = 0`` into ``1.0``
        # because ``0 or 1.0`` evaluates to ``1.0`` in Python (0 is
        # falsy). That made every "mute this segment" override at
        # 0 % a no-op — the user's most natural way to silence a clip
        # was the one value the loader rejected.
        raw_vv = getattr(r, "voice_volume", None)
        raw_bv = getattr(r, "bgm_volume", None)
        out.append(
            bgm_mixer.SegmentVolume(
                start_s=(r.on_timeline_start_ms or 0) / 1000.0,
                end_s=(r.on_timeline_end_ms or 0) / 1000.0,
                voice_volume=float(raw_vv) if raw_vv is not None else 1.0,
                bgm_volume=float(raw_bv) if raw_bv is not None else None,
            )
        )
    return out


async def _snapshot_draft_bgm_path(draft_id: int, project_bgm_path: str | None) -> str | None:
    """Return the BGM path this draft should mix against.

    First render: if the draft has no ``bgm_path`` recorded yet, copy the
    project's current ``bgm_path`` onto it and return that. Subsequent
    renders return whatever was snapshotted earlier — the project's
    current BGM is ignored, so a freshly-generated AI track on the
    project doesn't silently overwrite an older draft's soundtrack.
    Returns ``None`` when neither side has a BGM (the BGM stage no-ops).
    """
    async with async_session_maker() as session:
        draft = await session.get(Draft, draft_id)
        if draft is None:
            return project_bgm_path
        if draft.status not in (DraftStatus.PENDING.value, DraftStatus.PROCESSING.value):
            return draft.bgm_path
        if draft.bgm_path is None and project_bgm_path:
            draft.bgm_path = project_bgm_path
            await session.commit()
        return draft.bgm_path


def _resolve_smart_camera_flag(
    project: Project,
    override: bool | None,
) -> bool:
    """Pick the effective AI Smart Camera flag.

    Priority (highest first):
      1. Explicit ``override`` kwarg — populated from the
         ``EditTriggerRequest.smart_camera`` body field by the API
         endpoint. Distinguishes "user toggled it off for this run"
         from "user didn't touch the toggle".
      2. ``Project.smart_camera_enabled`` — the persistent project
         toggle the user flipped on the project edit page.

    v0.24.0 ``voice_volume = 0`` taught us not to use ``or`` on
    nullable booleans — a body value of ``False`` is meaningful
    (explicit opt-out) and must be preserved.
    """
    if override is not None:
        return bool(override)
    return bool(getattr(project, "smart_camera_enabled", False))


async def _restore_plan_blob(handle: _DraftHandle, plan: CutPlan) -> None:
    """Re-serialise a plan back onto ``Draft.cut_plan_json`` after the
    smart-camera stage decorated each segment. Doesn't touch
    ``DraftSegment`` rows because the segment shape (asset / span /
    order) is unchanged — only the ``smart_camera_json`` decoration
    inside the JSON blob differs.
    """
    async with async_session_maker() as session:
        draft = await session.get(Draft, handle.draft_id)
        if draft is None:
            return
        if draft.status not in (DraftStatus.PENDING.value, DraftStatus.PROCESSING.value):
            return
        draft.cut_plan_json = edit_planner.serialise_plan(plan)
        await session.commit()


def _plan_needs_smart_camera(plan: CutPlan) -> bool:
    """True when at least one cut lacks a current smart-camera directive."""
    for seg in plan.segments:
        blob = getattr(seg, "smart_camera_json", None)
        if not isinstance(blob, dict):
            return True
        if blob.get("schema_version") != smart_camera_planner.SMART_CAMERA_SCHEMA_VERSION:
            return True
    return False


def _should_run_smart_camera_stage(
    *,
    smart_camera_active: bool,
    skip_plan: bool,
    plan: CutPlan,
) -> bool:
    """Decide whether this render should call Gemini Vision for camera moves.

    Fresh renders always need the stage when the feature is active. Skip-plan
    re-renders also need it when the stored plan predates Smart Camera or was
    regenerated from timeline rows, because those blobs have no directives for
    the renderer to apply.
    """
    if not smart_camera_active or not plan.segments:
        return False
    return not skip_plan or _plan_needs_smart_camera(plan)


async def _persist_plan(
    handle: _DraftHandle,
    plan: CutPlan,
    *,
    initial_voice_volume: float = 1.0,
) -> None:
    """Write ``Draft.cut_plan_json`` plus a row per CutPlanSegment."""
    # Pull per-asset secondary translations once so each cut can be
    # snapshot with its joined English text. Same getattr-with-default
    # safety pattern as tracking_json so a degraded host (column missing)
    # quietly skips the snapshot.
    asset_ids = sorted({c.asset_id for c in plan.segments})
    secondary_by_asset: dict[int, list[dict[str, Any]]] = {}
    if asset_ids:
        async with async_session_maker() as session:
            rows = (
                (await session.execute(select(Asset).where(Asset.id.in_(asset_ids))))
                .scalars()
                .all()
            )
            for row in rows:
                segs = getattr(row, "subtitle_secondary_segments_json", None)
                if isinstance(segs, list) and segs:
                    secondary_by_asset[row.id] = list(segs)

    async with async_session_maker() as session:
        draft = await session.get(Draft, handle.draft_id)
        if draft is None:
            raise RuntimeError(f"draft {handle.draft_id} disappeared")
        if draft.status != DraftStatus.PROCESSING.value:
            logger.info(
                "draft %d no longer processing (%s); skipping stale plan persist",
                handle.draft_id,
                draft.status,
            )
            return
        draft.cut_plan_json = edit_planner.serialise_plan(plan)
        cursor_ms = 0
        # Replace any leftover segments from a prior run (force).
        await session.execute(delete(DraftSegment).where(DraftSegment.draft_id == handle.draft_id))
        for cut in plan.segments:
            duration = cut.asset_end_ms - cut.asset_start_ms
            secondary_text = subtitles.secondary_text_for_cut(
                cut,
                secondary_by_asset.get(cut.asset_id),
            )
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
                    voice_volume=initial_voice_volume,
                    subtitle_secondary_text=secondary_text,
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
        if draft.status not in (DraftStatus.PENDING.value, DraftStatus.PROCESSING.value):
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
        if draft.status != DraftStatus.PROCESSING.value:
            return
        draft.status = DraftStatus.READY_FOR_REVIEW.value
        draft.mp4_preview_path = str(output_path)
        if srt_path is not None and srt_path.is_file():
            draft.subtitle_path = str(srt_path)
        await session.commit()


async def _gather_render_inputs(
    project_id: int,
) -> tuple[
    Project,
    dict[int, Path],
    dict[int, AssetTranscript],
    dict[int, dict[str, Any]],
    dict[int, int | None],
    dict[int, dict[str, Any]],
    dict[int, dict[str, Any]],
    dict[int, list[dict[str, Any]]],
    set[int],
]:
    async with async_session_maker() as session:
        project = await session.get(Project, project_id)
        if project is None:
            raise RuntimeError(f"project {project_id} not found")
        assets = (
            (await session.execute(select(Asset).where(Asset.project_id == project_id)))
            .scalars()
            .all()
        )
        asset_paths = {a.id: asset_variants.selected_media_path(a) for a in assets}
        stabilized_asset_ids = {
            a.id
            for a in assets
            if asset_variants.active_variant(a) == asset_variants.STABILIZED_VARIANT
            and asset_variants.stabilization_status(a) == asset_variants.STABILIZATION_DONE
        }
        # v0.16 — tracking_json. v0.17 added per-asset ``tracked_object_index``
        # + ``custom_roi_json`` so the user can pick a non-dominant
        # object (or draw a custom ROI) on the analysis page.
        # ``getattr(..., None)`` keeps this resilient if the column is
        # missing on a degraded host — the planner / cut stage still
        # ships, just without auto-reframe.
        tracking_by_asset: dict[int, dict[str, Any]] = {}
        tracking_target_by_asset: dict[int, int | None] = {}
        custom_roi_by_asset: dict[int, dict[str, Any]] = {}
        # v0.23 — point_tracking_json (LK pixel-precise track) per
        # asset. Loaded under the same gate as custom_roi: only when
        # the column is non-empty so the renderer's dispatch can
        # cleanly pick between -4 (point) / -1 (custom_roi) / ≥0
        # (YOLO) sentinels.
        point_track_by_asset: dict[int, dict[str, Any]] = {}
        # v0.18 — secondary-language translation segments per asset.
        secondary_segments_by_asset: dict[int, list[dict[str, Any]]] = {}
        for a in assets:
            tracked_idx = getattr(a, "tracked_object_index", None)
            if tracked_idx is not None:
                tracking_target_by_asset[a.id] = int(tracked_idx)
            custom = getattr(a, "custom_roi_json", None)
            if isinstance(custom, dict) and custom.get("frames"):
                custom_roi_by_asset[a.id] = dict(custom)
            point_track = getattr(a, "point_tracking_json", None)
            if isinstance(point_track, dict) and point_track.get("frames"):
                point_track_by_asset[a.id] = dict(point_track)
            blob = getattr(a, "tracking_json", None)
            if isinstance(blob, dict) and (blob.get("frames") or blob.get("tracks")):
                tracking_by_asset[a.id] = dict(blob)
            secondary = getattr(a, "subtitle_secondary_segments_json", None)
            if isinstance(secondary, list) and secondary:
                secondary_segments_by_asset[a.id] = list(secondary)
        tx_rows = (
            (
                await session.execute(
                    select(AssetTranscript).where(AssetTranscript.asset_id.in_(asset_paths.keys()))
                )
            )
            .scalars()
            .all()
        )
        transcripts = {t.asset_id: t for t in tx_rows}
        return (
            project,
            asset_paths,
            transcripts,
            tracking_by_asset,
            tracking_target_by_asset,
            custom_roi_by_asset,
            point_track_by_asset,
            secondary_segments_by_asset,
            stabilized_asset_ids,
        )


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


async def _load_stored_plan(draft_id: int) -> CutPlan:
    """Reconstruct the CutPlan from ``Draft.cut_plan_json`` for the M7
    skip-plan path. Raises if the row has no stored plan — the only legit
    caller is a re-render after a manual reorder, and that always has a
    plan persisted."""
    async with async_session_maker() as session:
        draft = await session.get(Draft, draft_id)
        if draft is None:
            raise RuntimeError(f"draft {draft_id} not found while loading stored plan")
        if not isinstance(draft.cut_plan_json, dict) or not draft.cut_plan_json.get("segments"):
            raise RuntimeError(
                f"draft {draft_id} has no stored cut_plan_json; cannot skip plan stage"
            )
        return edit_planner.deserialise_plan(dict(draft.cut_plan_json))


async def _persist_subtitle_cues(draft_id: int, srt_text: str) -> None:
    """Truncate + reload ``subtitle_cues`` for ``draft_id`` from a freshly
    rendered SRT. Skipping the persist step keeps the M7.2 editor safe —
    if SRT is empty the table is cleared so the UI shows "no cues" rather
    than stale rows from a prior render."""
    cues = subtitles.parse_srt(srt_text) if srt_text else []
    async with async_session_maker() as session:
        draft = await session.get(Draft, draft_id)
        if draft is None or draft.status != DraftStatus.PROCESSING.value:
            return
        await session.execute(delete(SubtitleCueRow).where(SubtitleCueRow.draft_id == draft_id))
        for cue in cues:
            session.add(
                SubtitleCueRow(
                    draft_id=draft_id,
                    idx=cue.sequence,
                    start_ms=cue.timeline_start_ms,
                    end_ms=cue.timeline_end_ms,
                    text=cue.text,
                )
            )
        await session.commit()


async def _load_subtitle_srt_from_db(draft_id: int) -> str:
    """Render an SRT from the user-edited ``subtitle_cues`` rows. Returns
    empty string when the draft has no cues (treated as "no subtitles" by
    the renderer)."""
    async with async_session_maker() as session:
        rows = (
            (
                await session.execute(
                    select(SubtitleCueRow)
                    .where(SubtitleCueRow.draft_id == draft_id)
                    .order_by(SubtitleCueRow.idx)
                )
            )
            .scalars()
            .all()
        )
    if not rows:
        return ""
    cues = [
        subtitles.SubtitleCue(
            sequence=int(r.idx),
            timeline_start_ms=int(r.start_ms),
            timeline_end_ms=int(r.end_ms),
            text=str(r.text),
        )
        for r in rows
    ]
    return subtitles.render_srt(cues)


async def _plan_stage(
    project_id: int,
    target_duration_ms: int,
    *,
    style_preset: str = "custom",
) -> CutPlan:
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
                    style_preset=style_preset,
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


def _output_paths(project_id: int, version: int) -> tuple[Path, Path, Path]:
    """Return (mp4, primary-srt, secondary-srt) paths for a draft version.

    The secondary SRT is written next to the primary one with an ``_en``
    suffix; absent on disk = no second-language subtitles for this draft.
    """
    base = Path(settings.drafts_dir) / str(project_id)
    return (base / f"v{version}.mp4", base / f"v{version}.srt", base / f"v{version}_en.srt")


async def run_render(
    project_id: int,
    *,
    draft_id: int | None = None,
    force: bool = False,
    target_duration_ms: int | None = None,
    skip_plan: bool = False,
    subtitles_from_db: bool = False,
    stabilize: bool = True,
    subtitles_enabled: bool = True,
    transitions_enabled: bool = False,
    auto_reframe_enabled: bool = True,
    initial_voice_volume: float = 1.0,
    smart_camera_enabled: bool | None = None,
    style_preset: str = "custom",
) -> dict[str, Any]:
    """Run the full M5 pipeline for ``project_id`` and return a summary.

    When ``draft_id`` is given the API endpoint already created the row and
    the worker just adopts it (the common path now). When ``draft_id`` is
    ``None`` (legacy / direct invocation) the orchestrator reserves a fresh
    row so existing tooling keeps working.

    The return value is for RQ's job-result store; the UI polls
    ``GET /drafts/{id}`` and the orchestrator keeps that row in sync.
    ``target_duration_ms`` overrides the auto-computed length when set
    (clamped to [10 s, 300 s] regardless of caller); ``None`` lets the
    orchestrator pick from the source material. ``stabilize`` (default
    ``True``) toggles the v0.14.3 two-pass vidstab pipeline between cut
    and concat — disable for speed when the source is already stable
    (tripod / gimbal footage).
    """
    # Load the project up-front so we know the draft will have something
    # to attach to before we reserve a draft id.
    async with async_session_maker() as session:
        project = await session.get(Project, project_id)
        if project is None:
            raise RuntimeError(f"project {project_id} not found")
        # Pull asset count + total source duration so we can size the
        # target render length dynamically when the caller didn't supply
        # one. Cheap aggregate query — no asset rows materialised.
        source_total_ms, asset_count = (
            await session.execute(
                select(func.coalesce(func.sum(Asset.duration_ms), 0), func.count(Asset.id)).where(
                    Asset.project_id == project_id
                )
            )
        ).one()

    if draft_id is None:
        # Legacy / direct invocation: no API-side row exists, so claim
        # the latest pending row or create one defensively.
        handle = await _claim_pending_draft(project)
    else:
        handle = await _adopt_draft_row(project, draft_id)
    summary: dict[str, Any] = {
        "draft_id": handle.draft_id,
        "version": handle.version,
        "stages": _initial_progress(),
    }

    profile_spec = _try_load_profile(handle.profile_name)
    profile_target_ms = (
        profile_spec.editing_rules.target_duration_ms
        if profile_spec is not None
        else edit_planner.DEFAULT_TARGET_DURATION_MS
    )
    if target_duration_ms is not None:
        target_duration_ms = max(
            _USER_TARGET_MIN_MS, min(_USER_TARGET_MAX_MS, int(target_duration_ms))
        )
    else:
        target_duration_ms = _compute_auto_target_ms(
            profile_target_ms, int(source_total_ms or 0), int(asset_count or 0)
        )
    initial_voice_volume = max(0.0, min(1.5, float(initial_voice_volume)))

    # Stage 1 — plan. M7.1 skip-plan path: re-use the stored plan instead
    # of re-running Gemini (used after a manual timeline reorder).
    await _set_stage_state(handle.draft_id, EditStep.PLAN.value, "running")
    try:
        if skip_plan:
            plan = await _load_stored_plan(handle.draft_id)
            logger.info(
                "draft %d: skipping plan stage; reusing stored cut_plan_json (%d segments)",
                handle.draft_id,
                len(plan.segments),
            )
        else:
            plan = await _plan_stage(
                project_id,
                target_duration_ms,
                style_preset=style_preset,
            )
            await _persist_plan(handle, plan, initial_voice_volume=initial_voice_volume)
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
    # M7.2 path: when subtitles_from_db is set the user has manually
    # edited cues — render from the DB rows instead of regenerating from
    # transcripts (which would discard the edits). v0.14.4 added
    # ``subtitles_enabled``: when False the user explicitly opted out
    # of burned subtitles, so skip both SRT generation and the burn
    # stage entirely.
    (
        project,
        asset_paths,
        transcripts,
        tracking_by_asset,
        tracking_target_by_asset,
        custom_roi_by_asset,
        point_track_by_asset,
        secondary_segments_by_asset,
        stabilized_asset_ids,
    ) = await _gather_render_inputs(project_id)

    # v0.30.0 — opt-in AI Smart Camera stage. Resolves the project
    # toggle + render-flag override (priority: explicit kwarg from
    # caller > project.smart_camera_enabled > False), then runs
    # ``smart_camera_planner.plan_smart_camera`` against the cut
    # plan when enabled. Failures are partial-success (BGM stage
    # contract) — a Gemini error on cut N just means cut N renders
    # without a camera move.
    smart_camera_active = _resolve_smart_camera_flag(project, smart_camera_enabled)
    if _should_run_smart_camera_stage(
        smart_camera_active=smart_camera_active,
        skip_plan=skip_plan,
        plan=plan,
    ):
        try:
            async with async_session_maker() as session:
                api_keys = await get_llm_api_keys(session)
            if api_keys:
                directives = await smart_camera_planner.plan_smart_camera(
                    plan,
                    asset_paths,
                    api_keys=api_keys,
                    model=settings.llm_model,
                    base_url=_GEMINI_BASE_URL,
                    timeout_s=settings.llm_timeout_s,
                    scratch_dir=Path(settings.analysis_dir) / "smart_camera" / str(handle.draft_id),
                )
                if directives:
                    plan = smart_camera_planner.apply_smart_camera_to_plan(plan, directives)
                    await _restore_plan_blob(handle, plan)
                    logger.info(
                        "draft %d: smart-camera applied to %d/%d cuts",
                        handle.draft_id,
                        len(directives),
                        len(plan.segments),
                    )
                else:
                    logger.info(
                        "draft %d: smart-camera ran but produced no directives",
                        handle.draft_id,
                    )
            else:
                logger.warning(
                    "draft %d: smart-camera enabled but no LLM_API_KEYS configured",
                    handle.draft_id,
                )
                directives = smart_camera_planner.build_no_move_directives(
                    plan,
                    reason="no LLM_API_KEYS configured",
                )
                if directives:
                    plan = smart_camera_planner.apply_smart_camera_to_plan(plan, directives)
                    await _restore_plan_blob(handle, plan)
        except Exception:  # noqa: BLE001 — never let smart-camera fail the render.
            logger.exception(
                "draft %d: smart-camera stage failed; using no-move directives",
                handle.draft_id,
            )
            directives = smart_camera_planner.build_no_move_directives(
                plan,
                reason="smart-camera stage failed",
            )
            if directives:
                plan = smart_camera_planner.apply_smart_camera_to_plan(plan, directives)
                await _restore_plan_blob(handle, plan)
    elif smart_camera_active and skip_plan:
        logger.info(
            "draft %d: smart-camera skip-plan render reused existing directives",
            handle.draft_id,
        )

    if not subtitles_enabled:
        srt_text = ""
        secondary_srt_text = ""
        logger.info("draft %d: subtitles disabled by request", handle.draft_id)
    elif subtitles_from_db:
        srt_text = await _load_subtitle_srt_from_db(handle.draft_id)
        # subtitles_from_db re-uses the manually-edited primary cues. The
        # secondary track has no editor today, so just regenerate from
        # the source asset translations as on a fresh render.
        secondary_cues = subtitles.build_secondary_cues(plan, secondary_segments_by_asset)
        secondary_srt_text = subtitles.render_srt(secondary_cues)
        logger.info(
            "draft %d: subtitles loaded from DB (%d chars; secondary %d chars)",
            handle.draft_id,
            len(srt_text),
            len(secondary_srt_text),
        )
    else:
        srt_text = subtitles.build_srt(plan, transcripts)
        secondary_cues = subtitles.build_secondary_cues(plan, secondary_segments_by_asset)
        secondary_srt_text = subtitles.render_srt(secondary_cues)
    output_path, srt_path, secondary_srt_path = _output_paths(project_id, handle.version)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if srt_text:
        srt_path.write_text(srt_text, encoding="utf-8")
    else:
        # No subtitles for this draft; remove any stale file from a
        # prior render at the same version.
        if srt_path.is_file():
            srt_path.unlink()
    if secondary_srt_text:
        secondary_srt_path.write_text(secondary_srt_text, encoding="utf-8")
    elif secondary_srt_path.is_file():
        secondary_srt_path.unlink()

    scratch_dir = Path(settings.analysis_dir) / "edits"
    scratch_dir.mkdir(parents=True, exist_ok=True)

    # v0.30.20 — resolve the draft's BGM before video render so Smart Camera
    # can snap its move completion to the same track that the later BGM stage
    # will mix. This does not change cut lengths; no beat grid just falls back
    # to the existing visual-motion ease.
    bgm_source_path = await _snapshot_draft_bgm_path(handle.draft_id, project.bgm_path)
    smart_camera_beat_grid_s: list[float] | None = None
    if smart_camera_active and bgm_source_path:
        try:
            plan_duration_s = (
                sum(max(1, seg.asset_end_ms - seg.asset_start_ms) for seg in plan.segments) / 1000.0
            )
            analysis_duration_s = max(plan_duration_s, target_duration_ms / 1000.0) + 5.0
            beat_analysis = await asyncio.to_thread(
                beat_sync.analyze_bgm_beats,
                Path(bgm_source_path),
                duration_s=analysis_duration_s,
            )
            if beat_analysis.beats_s:
                smart_camera_beat_grid_s = beat_analysis.beats_s
                logger.info(
                    "draft %d: smart-camera beat sync enabled (bpm=%s, beats=%d)",
                    handle.draft_id,
                    beat_analysis.bpm,
                    len(beat_analysis.beats_s),
                )
            else:
                logger.info("draft %d: smart-camera beat sync unavailable", handle.draft_id)
        except Exception:  # noqa: BLE001 — beat sync should never block rendering.
            logger.exception("draft %d: smart-camera beat sync failed", handle.draft_id)

    # Stages 2-5 — cut / [stabilize] / concat / subtitles. The renderer
    # batches these so the on_progress callback is the only progress hook.
    progress_state = {
        "cut": "pending",
        "stabilize": "pending" if stabilize else "skipped",
        "concat": "pending",
        "subtitles": "pending" if subtitles_enabled else "skipped",
        "bgm": "pending",
    }
    if not stabilize:
        # Surface the skip in the persisted state right away so the UI
        # doesn't show "stabilize: pending" forever for tripod-stable
        # projects that opted out.
        await _set_stage_state(handle.draft_id, EditStep.STABILIZE.value, "skipped")
        summary["stages"][EditStep.STABILIZE.value] = "skipped"
    if not subtitles_enabled:
        # Same idea for the subtitles burn stage when the user opted out.
        await _set_stage_state(handle.draft_id, EditStep.SUBTITLES.value, "skipped")
        summary["stages"][EditStep.SUBTITLES.value] = "skipped"

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
        if stage in ("cut", "stabilize") and done < total:
            return  # only flip terminal state
        # Map the renderer's stages to our stage names + done state.
        if stage == "cut":
            asyncio.run_coroutine_threadsafe(update_state(EditStep.CUT.value, "done"), loop).result(
                timeout=10
            )
            next_stage = EditStep.STABILIZE.value if stabilize else EditStep.CONCAT.value
            asyncio.run_coroutine_threadsafe(update_state(next_stage, "running"), loop).result(
                timeout=10
            )
        elif stage == "stabilize":
            asyncio.run_coroutine_threadsafe(
                update_state(EditStep.STABILIZE.value, "done"), loop
            ).result(timeout=10)
            asyncio.run_coroutine_threadsafe(
                update_state(EditStep.CONCAT.value, "running"), loop
            ).result(timeout=10)
        elif stage == "concat":
            asyncio.run_coroutine_threadsafe(
                update_state(EditStep.CONCAT.value, "done"), loop
            ).result(timeout=10)
            # Only flip subtitles to running if the user actually asked
            # for them; otherwise it's already locked at "skipped" and
            # we leave it alone.
            if subtitles_enabled:
                asyncio.run_coroutine_threadsafe(
                    update_state(EditStep.SUBTITLES.value, "running"), loop
                ).result(timeout=10)
        elif stage == "subtitles":
            # Renderer always fires this at the end, even when srt_path
            # is None. Respect any prior "skipped" state set above.
            if progress_state.get(EditStep.SUBTITLES.value) != "skipped":
                asyncio.run_coroutine_threadsafe(
                    update_state(EditStep.SUBTITLES.value, "done"), loop
                ).result(timeout=10)

    # v0.18 — pull the user-customised subtitle style off the project row.
    # ``getattr(..., default)`` keeps the orchestrator runnable on a host
    # whose DB hasn't applied the 0015 migration yet — those columns
    # default to the historic look anyway.
    subtitle_style = video_renderer.SubtitleStyle(
        font=getattr(project, "subtitle_font", None) or "noto_sans_tc",
        color=getattr(project, "subtitle_color", None) or "#ffffff",
        outline_color=getattr(project, "subtitle_outline_color", None) or "#000000",
        position=getattr(project, "subtitle_position", None) or "bottom",
        size=getattr(project, "subtitle_size", None) or "medium",
        outline_width=getattr(project, "subtitle_outline_width", None) or "thin",
    )

    # v0.29.0 — static-crop anchor. Resolved off ``Project.crop_region_json``
    # (shape ``{x_norm, y_norm}`` both 0..1). ``None`` / malformed
    # entries fall back to centre, which the renderer's
    # ``aspect_filter`` short-circuits to the default ``crop=W:H``
    # (no x/y expression). The anchor is consulted by the static
    # aspect-crop path only — auto-reframe tracking paths ignore it
    # because they already centre on a tracked subject.
    crop_region_payload = getattr(project, "crop_region_json", None)
    crop_region_tuple: tuple[float, float] | None = None
    if isinstance(crop_region_payload, dict):
        try:
            x_raw = crop_region_payload.get("x_norm")
            y_raw = crop_region_payload.get("y_norm")
            if x_raw is not None and y_raw is not None:
                crop_region_tuple = (float(x_raw), float(y_raw))
        except (TypeError, ValueError):
            crop_region_tuple = None

    try:
        result = await asyncio.to_thread(
            video_renderer.render,
            plan,
            draft_id=handle.draft_id,
            target_aspect=handle.target_aspect,
            asset_paths=asset_paths,
            output_path=output_path,
            srt_path=srt_path if srt_text else None,
            secondary_srt_path=secondary_srt_path if secondary_srt_text else None,
            scratch_dir=scratch_dir,
            stabilize=stabilize,
            transitions_enabled=transitions_enabled,
            tracking_by_asset=tracking_by_asset if auto_reframe_enabled else None,
            tracking_target_by_asset=tracking_target_by_asset if auto_reframe_enabled else None,
            custom_roi_by_asset=custom_roi_by_asset if auto_reframe_enabled else None,
            point_track_by_asset=point_track_by_asset if auto_reframe_enabled else None,
            crop_region=crop_region_tuple,
            smart_camera_enabled=smart_camera_active,
            smart_camera_beat_grid_s=smart_camera_beat_grid_s,
            stabilized_asset_ids=stabilized_asset_ids,
            subtitle_style=subtitle_style if subtitles_enabled else None,
            on_progress=_sync_progress,
        )
    except Exception as exc:  # noqa: BLE001 — record + mark failed.
        reason = _failure_reason(exc)
        logger.exception("render stages failed for draft %d", handle.draft_id)
        # Find the first non-done stage and attribute the failure to it.
        for stage in (
            EditStep.CUT.value,
            EditStep.STABILIZE.value,
            EditStep.CONCAT.value,
            EditStep.SUBTITLES.value,
        ):
            if progress_state.get(stage) not in ("done", "skipped"):
                await _set_stage_state(handle.draft_id, stage, reason)
                summary["stages"][stage] = reason
                break
        await _mark_failed(handle.draft_id, f"render: {exc}")
        return summary

    # Stage 5 — BGM mix. No-op when neither the draft nor the project has
    # a BGM path. A BGM failure only fails the bgm stage, not the whole
    # draft — the subtitled mp4 at output_path is still a valid
    # deliverable on its own.
    #
    # v0.16.2 — snapshot semantics: the first time a draft renders we
    # copy ``project.bgm_path`` into ``draft.bgm_path`` and use that
    # snapshot from then on. Re-renders (timeline reorder, etc.) ignore
    # any newer ``project.bgm_path`` so each draft keeps whichever BGM
    # it actually shipped with — generating a fresh AI track no longer
    # silently swaps the soundtrack on older drafts.
    await update_state(EditStep.BGM.value, "running")
    # v0.17 — pull per-segment voice/bgm gain overrides off the draft's
    # DraftSegments so the mixer can apply them. ``segments`` is empty
    # when nothing's been overridden; mixer treats that as a no-op.
    segment_volumes = await _load_segment_volumes(handle.draft_id)
    has_voice_overrides = any(
        sv.voice_volume != 1.0 or sv.bgm_volume is not None for sv in segment_volumes
    )
    if bgm_source_path:
        try:
            tmp_mixed = scratch_dir / f"draft_{handle.draft_id}_bgm.mp4"
            # v0.24.0 — Project.bgm_fade_out_sec drives the tail fade
            # on the BGM track. Default 3.0 s; user can crank it to 0
            # for the historic hard-cut or up to 5 s in the FE.
            bgm_fade_out_sec = float(getattr(project, "bgm_fade_out_sec", 0.0) or 0.0)
            await asyncio.to_thread(
                bgm_mixer.mix_bgm,
                output_path,
                Path(bgm_source_path),
                srt_path if srt_text else None,
                tmp_mixed,
                segments=segment_volumes if has_voice_overrides else None,
                fade_out_sec=bgm_fade_out_sec,
            )
            os.replace(tmp_mixed, output_path)
            await update_state(EditStep.BGM.value, "done")
        except bgm_mixer.BgmMixError as exc:
            logger.warning(
                "bgm mix failed for draft %d, keeping subtitled mp4: %s",
                handle.draft_id,
                exc,
            )
            await _set_stage_state(
                handle.draft_id, EditStep.BGM.value, f"failed:{type(exc).__name__}"
            )
            summary["stages"][EditStep.BGM.value] = f"failed:{type(exc).__name__}"
    elif has_voice_overrides:
        # No BGM but the user set per-segment voice gain — apply gain
        # via a voice-only re-encode so the override actually lands.
        try:
            tmp_voice = scratch_dir / f"draft_{handle.draft_id}_voice.mp4"
            await asyncio.to_thread(
                bgm_mixer.apply_voice_volume,
                output_path,
                tmp_voice,
                segment_volumes,
            )
            os.replace(tmp_voice, output_path)
            await update_state(EditStep.BGM.value, "done")
        except bgm_mixer.BgmMixError as exc:
            logger.warning(
                "voice-volume re-encode failed for draft %d: %s",
                handle.draft_id,
                exc,
            )
            await _set_stage_state(
                handle.draft_id, EditStep.BGM.value, f"failed:{type(exc).__name__}"
            )
            summary["stages"][EditStep.BGM.value] = f"failed:{type(exc).__name__}"
    else:
        await update_state(EditStep.BGM.value, "done")

    # v0.18 — watermark / brand-logo overlay. Final stage. Skipped when
    # the project has no watermark configured. A failure here is
    # non-fatal — we keep the un-watermarked mp4 in place so the draft
    # still ships, matching the BGM-failure semantics above.
    if project.watermark_path and Path(project.watermark_path).is_file():
        try:
            tmp_wm = scratch_dir / f"draft_{handle.draft_id}_wm.mp4"
            await asyncio.to_thread(
                video_renderer.apply_watermark,
                output_path,
                tmp_wm,
                watermark_path=Path(project.watermark_path),
                target_aspect=handle.target_aspect,
                position=str(
                    project.watermark_position or video_renderer.WATERMARK_DEFAULT_POSITION
                ),
                scale=float(project.watermark_scale or 0.10),
                opacity=float(project.watermark_opacity or 1.0),
            )
            os.replace(tmp_wm, output_path)
        except video_renderer.VideoRenderError as exc:
            logger.warning(
                "watermark overlay failed for draft %d, keeping un-watermarked mp4: %s",
                handle.draft_id,
                exc,
            )

    # M7.2 — persist subtitle cues to ``subtitle_cues`` so the editor can
    # show / patch them. We only do this on the initial generation path
    # (subtitles_from_db=False) — when re-rendering with edited cues the
    # rows already reflect the user's edits and re-persisting would
    # round-trip through parse_srt unnecessarily.
    if not subtitles_from_db:
        try:
            await _persist_subtitle_cues(handle.draft_id, srt_text)
        except Exception:  # noqa: BLE001 — non-fatal; the mp4 still ships.
            logger.exception(
                "failed to persist subtitle cues for draft %d; mp4 unaffected",
                handle.draft_id,
            )

    await _mark_ready(
        handle.draft_id,
        output_path=result.output_path,
        srt_path=srt_path if srt_text else None,
    )
    video_renderer.cleanup_intermediates(result.intermediate_dir)
    return summary


__all__ = ["run_render"]
