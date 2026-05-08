"""Draft endpoints — read, Stage 4.5 LLM patch, M5 render trigger."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any, cast

from fastapi import APIRouter, Body, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from media_processor.api.config import settings
from media_processor.api.deps import get_llm_patcher, get_profile_loader, get_session
from media_processor.api.schemas import (
    CutPlanOut,
    CutPlanSegmentOut,
    DraftCommentCreate,
    DraftCommentOut,
    DraftDetail,
    DraftExportOut,
    DraftExportRequest,
    DraftExportResponse,
    DraftExportStatusLiteral,
    DraftPatchRequest,
    DraftPatchResponse,
    DraftRebuildSubtitlesRequest,
    DraftReorderRequest,
    DraftSegmentOut,
    DraftSegmentPatch,
    DraftSegmentSplitRequest,
    RenderFlagsOverride,
    SegmentVolumeOut,
    SegmentVolumePatch,
    SubtitleCueOut,
    SubtitleCuePatch,
)
from media_processor.models import (
    Asset,
    AssetSegment,
    AssetTag,
    Draft,
    DraftComment,
    DraftExport,
    DraftSegment,
    SubtitleCueRow,
)
from media_processor.models.enums import DraftStatus
from media_processor.profile.loader import ProfileSpec
from media_processor.services import exports
from media_processor.services.llm_patcher import (
    DraftSegmentSummary,
    LLMPatcher,
    LLMPatchError,
    apply_patch,
)
from media_processor.services.queue import (
    cancel_draft_render,
    enqueue_draft_export,
    enqueue_project_edit,
    has_draft_render_job,
)

router = APIRouter(prefix="/drafts", tags=["drafts"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
LLMPatcherDep = Annotated[LLMPatcher, Depends(get_llm_patcher)]
ProfileLoaderDep = Annotated[Callable[[str], ProfileSpec], Depends(get_profile_loader)]


# Public URL prefixes the browser uses to fetch generated mp4 / SRT files.
# StaticFiles is mounted at "/media/drafts" in api.main, and nginx proxies
# "/api/" → api:8000, so the full URL the browser sees is
# "/api/media/drafts/{project_id}/v{N}.mp4".
DRAFT_URL_PREFIX = "/api/media/drafts"


def _draft_filename(version: int, suffix: str) -> str:
    return f"v{version}.{suffix}"


def _draft_url(project_id: int, version: int, suffix: str) -> str:
    url = f"{DRAFT_URL_PREFIX}/{project_id}/{_draft_filename(version, suffix)}"
    path = _expected_draft_path(project_id, version, suffix)
    try:
        return f"{url}?v={path.stat().st_mtime_ns}"
    except OSError:
        return url


def _draft_export_url(project_id: int, output_filename: str) -> str:
    return f"{DRAFT_URL_PREFIX}/{project_id}/{output_filename}"


def _expected_draft_path(project_id: int, version: int, suffix: str) -> Path:
    return Path(settings.drafts_dir) / str(project_id) / _draft_filename(version, suffix)


def _draft_render_flags(
    draft: Draft,
    override: RenderFlagsOverride | None = None,
) -> dict[str, bool]:
    """v0.21.1 / v0.21.3 — resolve the four render flags for a skip-plan
    re-render.

    Priority, per-flag (independent — partial overrides are fine):
      1. ``override`` body field (FE-authoritative, sent fresh from the
         current ProjectEdit toggle state)
      2. ``Draft.render_flags_json`` snapshot (written by the trigger
         endpoint when the draft was created)
      3. The per-flag legacy default for rows that have no snapshot
         (pre-v0.21.1 drafts, or drafts created before the trigger
         endpoint started snapshotting). v0.24.0 flipped the default
         for ``transitions`` to ``False`` to match the new
         ``EditTriggerRequest`` default — a legacy draft re-rendered
         today picks up the same "transitions off by default"
         behaviour the FE shows for fresh projects.

    The boolean coercion guards against corrupt JSON blobs (e.g. a
    string ``"false"``) round-tripping as truthy.
    """
    snapshot = draft.render_flags_json if isinstance(draft.render_flags_json, dict) else {}
    over = override.model_dump(exclude_none=True) if override is not None else {}
    # v0.24.0 — per-flag legacy default (mirrors EditTriggerRequest).
    # Only used when neither override nor snapshot has a value.
    # v0.30.0 — added ``smart_camera`` (default False, opt-in).
    legacy_defaults = {
        "transitions": False,
        "stabilize": True,
        "subtitles": True,
        "auto_reframe": True,
        "smart_camera": False,
    }

    def _pick(key: str) -> bool:
        if key in over:
            return bool(over[key])
        if key in snapshot:
            return bool(snapshot[key])
        return legacy_defaults[key]

    return {
        "transitions": _pick("transitions"),
        "stabilize": _pick("stabilize"),
        "subtitles": _pick("subtitles"),
        "auto_reframe": _pick("auto_reframe"),
        "smart_camera": _pick("smart_camera"),
    }


async def _mark_draft_enqueue_failed(
    session: AsyncSession,
    draft: Draft,
    exc: Exception,
) -> None:
    draft.status = DraftStatus.FAILED.value
    draft.prompt_feedback = ((draft.prompt_feedback or "") + f"\n[enqueue-failed] {exc}").strip()
    await session.commit()


async def _prepare_draft_for_settings_rerender(
    session: AsyncSession,
    draft: Draft,
) -> None:
    """Validate render-time asset settings and clear stale draft snapshots.

    Re-render is the operator-facing "apply current settings" path. Keep
    the existing timeline, but do not silently reuse snapshots that are meant
    to be refreshed from Project / Asset rows at render time.
    """
    asset_ids = {s.asset_id for s in draft.segments if s.asset_id is not None}
    if asset_ids:
        assets = (
            (await session.execute(select(Asset).where(Asset.id.in_(asset_ids)))).scalars().all()
        )
        not_ready: list[str] = []
        for asset in assets:
            if getattr(asset, "tracked_object_index", None) != -4:
                continue
            point_blob = getattr(asset, "point_tracking_json", None)
            has_frames = isinstance(point_blob, dict) and bool(point_blob.get("frames"))
            point_status = getattr(asset, "point_tracking_status", None)
            if point_status == "pending":
                not_ready.append(f"asset {asset.id}: point tracking pending")
            elif point_status == "failed" or not has_frames:
                not_ready.append(f"asset {asset.id}: point tracking not available")
        if not_ready:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="; ".join(not_ready),
            )

    # Force the render worker to snapshot the current Project.bgm_path. This
    # lets "apply settings and re-render" actually pick up newly generated,
    # selected, uploaded, or removed BGM instead of keeping the old draft track.
    draft.bgm_path = None


def _resolve_draft_url(draft: Draft, *, suffix: str, stored_path: str | None) -> str | None:
    """Pick a public URL for the mp4 or srt sidecar.

    Honour the path stored on the row (the renderer always writes
    ``${DRAFTS_DIR}/{project_id}/v{N}.{suffix}``); fall back to the
    convention if the row has no path yet but the file is on disk.
    """
    if stored_path:
        return _draft_url(draft.project_id, draft.version, suffix)
    if _expected_draft_path(draft.project_id, draft.version, suffix).is_file():
        return _draft_url(draft.project_id, draft.version, suffix)
    return None


def _cut_plan_out(blob: Any | None) -> CutPlanOut | None:
    """Validate the JSON blob we stored in Draft.cut_plan_json and return a model.

    The blob comes from edit_planner.serialise_plan, but we tolerate older
    drafts that don't have one yet and pre-M5 rows where the column is null.
    """
    if not isinstance(blob, dict):
        return None
    raw_segments = blob.get("segments") or []
    segments: list[CutPlanSegmentOut] = []
    for seg in raw_segments:
        if not isinstance(seg, dict):
            continue
        try:
            segments.append(
                CutPlanSegmentOut(
                    order=int(seg["order"]),
                    asset_id=int(seg["asset_id"]),
                    asset_start_ms=int(seg["asset_start_ms"]),
                    asset_end_ms=int(seg["asset_end_ms"]),
                    source_kind=str(seg["source_kind"]),  # type: ignore[arg-type]
                    reason=str(seg.get("reason", "")),
                    transition_to_next=str(seg.get("transition_to_next", "dissolve")),
                )
            )
        except (KeyError, ValueError, TypeError):
            continue
    try:
        return CutPlanOut(
            schema_version=str(blob.get("schema_version", "")),
            target_duration_ms=int(blob.get("target_duration_ms", 0)),
            target_aspect_ratio=str(blob.get("target_aspect_ratio", "")),
            profile_name=str(blob.get("profile_name", "")),
            notes=str(blob.get("notes", "")),
            used_fallback=bool(blob.get("used_fallback", False)),
            fallback_reason=blob.get("fallback_reason"),
            segments=segments,
        )
    except (ValueError, TypeError):
        return None


def serialise_draft_detail(draft: Draft) -> DraftDetail:
    """Map a Draft row + its loaded segments into the response model.

    Centralised here so both ``GET /drafts/{id}`` and the M5 trigger
    endpoint emit the same shape.
    """
    return DraftDetail(
        id=draft.id,
        project_id=draft.project_id,
        profile_name=draft.profile_name,
        version=draft.version,
        status=draft.status,
        output_zip_path=draft.output_zip_path,
        mp4_preview_path=draft.mp4_preview_path,
        ai_score=draft.ai_score,
        created_at=draft.created_at,
        progress_steps=dict(draft.progress_steps_json or {}) or None,
        mp4_url=_resolve_draft_url(draft, suffix="mp4", stored_path=draft.mp4_preview_path),
        subtitle_url=_resolve_draft_url(draft, suffix="srt", stored_path=draft.subtitle_path),
        cut_plan=_cut_plan_out(draft.cut_plan_json),
        prompt_feedback=draft.prompt_feedback,
        style_preset=getattr(draft, "style_preset", "custom") or "custom",
        segments=[
            DraftSegmentOut(
                id=s.id,
                order=s.order,
                asset_segment_id=s.asset_segment_id,
                asset_id=s.asset_id,
                asset_start_ms=s.asset_start_ms,
                asset_end_ms=s.asset_end_ms,
                on_timeline_start_ms=s.on_timeline_start_ms,
                on_timeline_end_ms=s.on_timeline_end_ms,
                transition=s.transition,
                source_kind=s.source_kind,
                plan_reason=s.plan_reason,
                # v0.24.0 — explicit None-check (matches
                # ``edit_orchestrator._load_segment_volumes``). Pre-fix
                # ``or 1.0`` mapped voice_volume=0 → 1.0, so the GET
                # response told the FE the slider was at 100 % even
                # when the DB held 0 %, which caused the "I muted
                # this and it didn't take" report.
                voice_volume=(
                    float(s.voice_volume) if getattr(s, "voice_volume", None) is not None else 1.0
                ),
                bgm_volume=(
                    float(bgm_volume) if (bgm_volume := s.bgm_volume) is not None else None
                ),
            )
            for s in sorted(draft.segments, key=lambda x: x.order)
        ],
    )


def serialise_draft_export(export: DraftExport, *, project_id: int) -> DraftExportOut:
    """Map a durable derivative export row into the browser-facing shape."""
    download_url = (
        _draft_export_url(project_id, export.output_filename)
        if export.status == "done" and export.output_path
        else None
    )
    return DraftExportOut(
        export_id=export.id,
        draft_id=export.draft_id,
        aspect=export.aspect,
        height=export.height,
        status=cast(DraftExportStatusLiteral, export.status),
        job_id=export.job_id,
        output_filename=export.output_filename,
        download_url=download_url,
        error=export.error,
        created_at=export.created_at,
        started_at=export.started_at,
        completed_at=export.completed_at,
    )


@router.get("/{draft_id}", response_model=DraftDetail)
async def get_draft(
    draft_id: int,
    session: SessionDep,
) -> DraftDetail:
    stmt = select(Draft).where(Draft.id == draft_id).options(selectinload(Draft.segments))
    draft = (await session.execute(stmt)).scalar_one_or_none()
    if draft is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="draft not found")

    # v0.25.1 — orphan detection (read-time fast path). The watchdog
    # in ``api.watchdog`` is the canonical owner of orphan recovery
    # — it sweeps every 60 s, re-enqueues up to 3 times, and only
    # then gives up. Read-time we don't ATTEMPT recovery (don't want
    # to race the watchdog), but we DO surface the terminal failure
    # state immediately when the watchdog has exhausted its budget,
    # so the FE doesn't have to wait another 60 s for the next
    # watchdog tick. ``has_draft_render_job`` fails open on Redis
    # errors so a transient blip can't invent a phantom failure.
    if (
        draft.status in (DraftStatus.PENDING.value, DraftStatus.PROCESSING.value)
        and (draft.render_retry_count or 0) >= 3
        and not has_draft_render_job(draft.id)
    ):
        draft.status = DraftStatus.FAILED.value
        if not draft.prompt_feedback:
            draft.prompt_feedback = (
                "watchdog: retries exhausted — render job kept disappearing "
                "from the queue across multiple auto-resubmits"
            )
        await session.commit()
        await session.refresh(draft, attribute_names=["segments"])

    return serialise_draft_detail(draft)


@router.post("/{draft_id}/patch", response_model=DraftPatchResponse)
async def patch_draft(
    draft_id: int,
    payload: DraftPatchRequest,
    session: SessionDep,
    patcher: LLMPatcherDep,
    profile_loader: ProfileLoaderDep,
) -> DraftPatchResponse:
    """Stage 4.5 — turn user feedback into a profile patch and persist it.

    The endpoint is idempotent at the LLM level (the model is asked for
    deterministic JSON) but writes the latest feedback to ``Draft.prompt_feedback``.
    Re-running stages 2 + 4 against the patched profile happens in the worker
    pipeline (M3+); this endpoint returns the patched profile fields so the
    caller can preview the change.
    """
    draft = await session.get(Draft, draft_id)
    if draft is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="draft not found")

    profile = profile_loader(draft.profile_name)

    summaries = await _build_segment_summaries(session, draft_id)

    try:
        patch = await patcher.request_patch(
            profile=profile,
            segments=summaries,
            user_feedback=payload.user_feedback,
        )
    except LLMPatchError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"LLM patch failed: {exc}",
        ) from exc

    patched = apply_patch(profile, patch)

    draft.prompt_feedback = payload.user_feedback
    await session.commit()

    rs = patched.editing_rules.required_segments
    return DraftPatchResponse(
        draft_id=draft.id,
        profile_name=draft.profile_name,
        tag_weight_deltas=patch.tag_weight_deltas,
        required_segments_overrides=patch.required_segments_overrides,
        patched_tag_weights=patched.tag_weights,
        patched_required_segments={
            "opening_hero": rs.opening_hero,
            "closing_hero": rs.closing_hero,
            "hero_tag": rs.hero_tag,
        },
    )


async def _build_segment_summaries(
    session: AsyncSession, draft_id: int
) -> list[DraftSegmentSummary]:
    """Fetch DraftSegments for ``draft_id`` and resolve each to a primary tag.

    Primary tag = the highest-confidence ``AssetTag`` of the segment's parent
    asset. Segments whose asset has no tags get a ``"_untagged"`` primary tag —
    the LLM still sees them in the prompt and can choose to ignore them.
    """
    stmt = (
        select(DraftSegment, AssetSegment)
        .join(AssetSegment, DraftSegment.asset_segment_id == AssetSegment.id)
        .where(DraftSegment.draft_id == draft_id)
        .order_by(DraftSegment.order)
    )
    rows = (await session.execute(stmt)).all()

    asset_ids = {seg.asset_id for _, seg in rows}
    tag_stmt = (
        select(AssetTag)
        .where(AssetTag.asset_id.in_(asset_ids))
        .order_by(AssetTag.asset_id, AssetTag.confidence.desc())
    )
    primary_by_asset: dict[int, str] = {}
    for tag in (await session.execute(tag_stmt)).scalars():
        primary_by_asset.setdefault(tag.asset_id, tag.tag_name)

    summaries: list[DraftSegmentSummary] = []
    for draft_seg, asset_seg in rows:
        summaries.append(
            DraftSegmentSummary(
                order=draft_seg.order,
                primary_tag=primary_by_asset.get(asset_seg.asset_id, "_untagged"),
                score=asset_seg.score,
                on_timeline_start_ms=draft_seg.on_timeline_start_ms,
                on_timeline_end_ms=draft_seg.on_timeline_end_ms,
            )
        )
    return summaries


# ---------- M5.2 — cancel an in-flight render ----------


# Marker we stash in Draft.prompt_feedback when the user hits 停止剪輯 so
# the existing failed-card UI surfaces the reason without a new enum value
# (a real CANCELLED status would need an alembic migration of the CHECK
# constraint — left as a follow-up).
CANCELLED_FEEDBACK = "已被使用者取消"


@router.post("/{draft_id}/cancel", response_model=DraftDetail)
async def cancel_draft(
    draft_id: int,
    session: SessionDep,
) -> DraftDetail:
    """Stop the running render for ``draft_id`` and mark the draft failed.

    Locates the RQ job in the editing queue by matching ``kwargs[draft_id]``
    so the api never has to pre-store the job id on the draft. Pending jobs
    are dropped from the queue; running jobs get a stop signal so the
    work-horse kills its ffmpeg subprocess. Always flips the draft to
    ``failed`` with a marker in ``prompt_feedback`` regardless of whether
    a live job was found, so a stale "processing" row can also be cleaned up.
    """
    stmt = select(Draft).where(Draft.id == draft_id).options(selectinload(Draft.segments))
    draft = (await session.execute(stmt)).scalar_one_or_none()
    if draft is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="draft not found")
    if draft.status not in (DraftStatus.PENDING.value, DraftStatus.PROCESSING.value):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"draft is {draft.status}, nothing to cancel",
        )

    cancel_draft_render(draft_id)

    draft.status = DraftStatus.FAILED.value
    draft.prompt_feedback = CANCELLED_FEEDBACK
    await session.commit()
    await session.refresh(draft)
    return serialise_draft_detail(draft)


# ---------- M5.2 — comment thread ----------


@router.get("/{draft_id}/comments", response_model=list[DraftCommentOut])
async def list_draft_comments(
    draft_id: int,
    session: SessionDep,
) -> list[DraftCommentOut]:
    if (await session.get(Draft, draft_id)) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="draft not found")
    rows = (
        (
            await session.execute(
                select(DraftComment)
                .where(DraftComment.draft_id == draft_id)
                .order_by(DraftComment.created_at.asc(), DraftComment.id.asc())
            )
        )
        .scalars()
        .all()
    )
    return [DraftCommentOut.model_validate(r) for r in rows]


@router.post(
    "/{draft_id}/comments",
    response_model=DraftCommentOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_draft_comment(
    draft_id: int,
    payload: DraftCommentCreate,
    session: SessionDep,
) -> DraftCommentOut:
    if (await session.get(Draft, draft_id)) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="draft not found")
    comment = DraftComment(
        draft_id=draft_id,
        author=payload.author.strip(),
        body=payload.body.strip(),
    )
    session.add(comment)
    await session.commit()
    await session.refresh(comment)
    return DraftCommentOut.model_validate(comment)


# ---------- M7.1 / v0.20 — timeline reorder + segment-level mutations ----------


# v0.20 — renderer transition whitelist mirror. Imported lazily inside the
# patch endpoint so a pure-DB unit test that doesn't touch the renderer
# can still exercise the row-mutation path; this module-level constant is
# the cached frozenset reused across calls.
_RENDERER_VALID_TRANSITIONS: frozenset[str] | None = None


def _valid_transitions() -> frozenset[str]:
    global _RENDERER_VALID_TRANSITIONS
    if _RENDERER_VALID_TRANSITIONS is None:
        from media_processor.services.video_renderer import VALID_TRANSITIONS

        _RENDERER_VALID_TRANSITIONS = VALID_TRANSITIONS
    return _RENDERER_VALID_TRANSITIONS


async def _reflow_segments_and_cut_plan(draft: Draft) -> None:
    """Re-cursor ``on_timeline_*_ms`` left-to-right and regenerate
    ``cut_plan_json["segments"]`` so a downstream skip-plan render reads
    the current row state.

    Caller responsibilities:
      - Has already mutated ``draft.segments`` (added/removed/edited
        rows) AND assigned correct ``order`` values via the two-phase
        parking-offset trick to dodge the ``UNIQUE(draft_id, order)``
        constraint.
      - Owns the surrounding ``await session.commit()`` and any
        ``draft.status`` / ``progress_steps_json`` resets.

    The helper is intentionally side-effect-free on the session — it
    only mutates row attributes — so callers compose freely.
    """
    cursor_ms = 0
    new_plan_segments: list[dict[str, Any]] = []
    for seg in sorted(draft.segments, key=lambda s: s.order):
        duration = max(1, (seg.asset_end_ms or 0) - (seg.asset_start_ms or 0))
        seg.on_timeline_start_ms = cursor_ms
        seg.on_timeline_end_ms = cursor_ms + duration
        cursor_ms += duration
        if (
            seg.asset_id is not None
            and seg.asset_start_ms is not None
            and seg.asset_end_ms is not None
        ):
            new_plan_segments.append(
                {
                    "order": int(seg.order),
                    "asset_id": int(seg.asset_id),
                    "asset_start_ms": int(seg.asset_start_ms),
                    "asset_end_ms": int(seg.asset_end_ms),
                    "source_kind": str(seg.source_kind or "scripted"),
                    "reason": str(seg.plan_reason or ""),
                    "transition_to_next": str(seg.transition or "dissolve"),
                }
            )
    blob: dict[str, Any] = dict(draft.cut_plan_json or {})
    blob["segments"] = new_plan_segments
    draft.cut_plan_json = blob


@router.patch("/{draft_id}/order", response_model=DraftDetail)
async def reorder_draft_segments(
    draft_id: int,
    payload: DraftReorderRequest,
    session: SessionDep,
) -> DraftDetail:
    """Reorder a draft's cut segments and enqueue a re-render.

    The ``orders`` field is the new sequence of ``DraftSegment.id`` values
    — must be a strict permutation of the draft's current segments.
    On success the rows are renumbered (``order`` + ``on_timeline_*_ms``
    cursors), ``cut_plan_json`` is regenerated to match, and a
    skip-plan render job is enqueued so the new ordering is rendered
    without re-running Gemini.
    """
    stmt = select(Draft).where(Draft.id == draft_id).options(selectinload(Draft.segments))
    draft = (await session.execute(stmt)).scalar_one_or_none()
    if draft is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="draft not found")

    existing_ids = {seg.id for seg in draft.segments}
    requested_ids = list(payload.orders)
    if len(requested_ids) != len(existing_ids) or set(requested_ids) != existing_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="orders must be a permutation of the existing draft segment ids",
        )

    by_id = {seg.id: seg for seg in draft.segments}
    # Two-phase update — the table has a UNIQUE(draft_id, order) constraint
    # (``uq_draft_segments_order``) and SQLAlchemy autoflushes a single
    # row update at a time. Setting the new orders in one pass collides
    # mid-loop because each fresh assignment temporarily duplicates the
    # order of a sibling that hasn't been updated yet (e.g. setting
    # segment A's order to 0 while segment B still has order 0).
    #
    # Park every row at a guaranteed-unused negative offset first, flush
    # so the constraint sees no duplicates, THEN write the final 0..N-1
    # values. Negative ints aren't possible from any normal write path
    # so the temporary state can't leak into a stuck row even if a
    # subsequent SQL statement fails.
    parking_offset = -1 - len(requested_ids)  # e.g. for N=12 → -13..-2
    for tmp_idx, seg in enumerate(draft.segments):
        seg.order = parking_offset + tmp_idx
    await session.flush()

    for new_order, seg_id in enumerate(requested_ids):
        by_id[seg_id].order = new_order

    # Re-cursor the timeline + regenerate cut_plan_json from the current
    # rows. Shared helper — the v0.20 segment-level endpoints
    # (split / patch / delete) call this same routine.
    await _reflow_segments_and_cut_plan(draft)
    await _prepare_draft_for_settings_rerender(session, draft)

    draft.status = DraftStatus.PENDING.value
    draft.progress_steps_json = {}
    draft.prompt_feedback = None
    # v0.25.1 — explicit user re-trigger resets the watchdog retry
    # counter so an unrelated future failure gets the full
    # three-strike auto-resubmit budget.
    draft.render_retry_count = 0
    flags = _draft_render_flags(draft, payload.render_flags)
    # v0.21.3 — backfill the resolved flags onto the Draft so subsequent
    # re-renders (skip-plan or otherwise) stay consistent without the
    # FE having to keep re-sending the override. This is what makes
    # legacy NULL rows "settle" into a known state on first re-render.
    draft.render_flags_json = flags
    await session.commit()

    try:
        enqueue_project_edit(
            draft.project_id,
            draft_id=draft.id,
            force=True,
            skip_plan=True,
            transitions=flags["transitions"],
            stabilize=flags["stabilize"],
            subtitles=flags["subtitles"],
            auto_reframe=flags["auto_reframe"],
            smart_camera=flags["smart_camera"],
        )
    except Exception as exc:  # noqa: BLE001 — do not leave pending without RQ job.
        await _mark_draft_enqueue_failed(session, draft, exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"draft render enqueue failed: {exc}",
        ) from exc

    await session.refresh(draft, attribute_names=["segments"])
    return serialise_draft_detail(draft)


# ---------- v0.20 — timeline editor segment-level endpoints ----------
#
# These three endpoints mutate the DB only — no render is enqueued.
# The operator hits the existing PATCH /drafts/{id}/order with the
# current order list to fire a skip-plan re-render once they're done
# editing (the "Apply / Re-render" button on the timeline editor's
# header). Decoupling avoids a worker run after every trim/split when
# the operator is iterating fast.


async def _load_draft_with_segments(
    draft_id: int,
    session: AsyncSession,
) -> Draft:
    stmt = select(Draft).where(Draft.id == draft_id).options(selectinload(Draft.segments))
    draft = (await session.execute(stmt)).scalar_one_or_none()
    if draft is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="draft not found")
    return draft


def _segment_or_404(draft: Draft, seg_id: int) -> DraftSegment:
    for seg in draft.segments:
        if seg.id == seg_id:
            return seg
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="segment not found")


@router.post(
    "/{draft_id}/segments/{seg_id}/split",
    response_model=DraftDetail,
)
async def split_draft_segment(
    draft_id: int,
    seg_id: int,
    payload: DraftSegmentSplitRequest,
    session: SessionDep,
) -> DraftDetail:
    """Split one segment into two halves at ``at_ms`` (on-timeline ms).

    ``at_ms`` must be strictly inside the segment's
    ``[on_timeline_start_ms, on_timeline_end_ms)`` window — splits at
    the exact edges are rejected to avoid zero-length halves.

    The new (right-half) row inherits the original's ``transition``,
    ``voice_volume``, ``bgm_volume``, ``source_kind``, ``plan_reason``
    and ``reframe_keyframes``. The original row is shortened to the
    split point and its ``transition`` is preserved (a "hard cut" at
    the split boundary is deferred — see proposal).

    Does NOT enqueue a render. Returns the updated ``DraftDetail``.
    """
    draft = await _load_draft_with_segments(draft_id, session)
    seg = _segment_or_404(draft, seg_id)

    if seg.asset_id is None or seg.asset_start_ms is None or seg.asset_end_ms is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="segment is missing asset bindings; cannot split",
        )

    at_ms = int(payload.at_ms)
    if not (seg.on_timeline_start_ms < at_ms < seg.on_timeline_end_ms):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"at_ms ({at_ms}) must be strictly inside "
                f"({seg.on_timeline_start_ms}, {seg.on_timeline_end_ms})"
            ),
        )

    split_at_asset_ms = seg.asset_start_ms + (at_ms - seg.on_timeline_start_ms)
    if not (seg.asset_start_ms < split_at_asset_ms < seg.asset_end_ms):
        # Defensive — the on-timeline check above should make this
        # impossible, but a misaligned row could trip it.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="computed asset-time split point fell outside the asset window",
        )

    # Capture the right-half ranges BEFORE we shrink the original row.
    right_half_asset_end = seg.asset_end_ms
    right_half_on_timeline_end = seg.on_timeline_end_ms

    # Capture pre-split ordering so we know where to insert the new row.
    old_segments_in_order = sorted(draft.segments, key=lambda s: s.order)

    # Park existing rows at guaranteed-unused negative offsets so the
    # final renumber pass doesn't collide with the
    # UNIQUE(draft_id, order) constraint mid-flush.
    parking_offset = -1 - 2 * len(old_segments_in_order)
    for tmp_idx, s in enumerate(old_segments_in_order):
        s.order = parking_offset + tmp_idx
    await session.flush()

    # Shrink the original (left half).
    seg.asset_end_ms = split_at_asset_ms
    seg.on_timeline_end_ms = at_ms

    # Build the right half. Inherits everything; gets a parked order
    # that's lower than any in old_segments so it won't collide.
    new_seg = DraftSegment(
        draft_id=draft.id,
        order=parking_offset - 1,
        asset_segment_id=seg.asset_segment_id,
        asset_id=seg.asset_id,
        asset_start_ms=split_at_asset_ms,
        asset_end_ms=right_half_asset_end,
        on_timeline_start_ms=at_ms,
        on_timeline_end_ms=right_half_on_timeline_end,
        reframe_keyframes=seg.reframe_keyframes,
        transition=seg.transition,
        blurred_source_path=seg.blurred_source_path,
        source_kind=seg.source_kind,
        plan_reason=seg.plan_reason,
        voice_volume=seg.voice_volume,
        bgm_volume=seg.bgm_volume,
    )
    session.add(new_seg)
    await session.flush()  # assigns new_seg.id; orders all parked.

    # Renumber: walk pre-split rows in their old order; drop the new
    # row in immediately after the original.
    final_sequence: list[DraftSegment] = []
    for s in old_segments_in_order:
        final_sequence.append(s)
        if s.id == seg.id:
            final_sequence.append(new_seg)
    for new_order, s in enumerate(final_sequence):
        s.order = new_order
    await session.flush()

    await _reflow_segments_and_cut_plan(draft)
    await session.commit()
    await session.refresh(draft, attribute_names=["segments"])
    return serialise_draft_detail(draft)


@router.patch(
    "/{draft_id}/segments/{seg_id}",
    response_model=DraftDetail,
)
async def patch_draft_segment(
    draft_id: int,
    seg_id: int,
    payload: DraftSegmentPatch,
    session: SessionDep,
) -> DraftDetail:
    """Trim / re-flag one segment. Every field on ``DraftSegmentPatch``
    is optional; only present fields are written.

    ``asset_start_ms`` / ``asset_end_ms`` are validated against
    ``Asset.duration_ms`` and against each other (start < end). The
    on-timeline cursor is recomputed from the new asset-window length
    via the shared reflow helper, so subsequent segments shift to fill
    or open the gap.

    Does NOT enqueue a render. Returns the updated ``DraftDetail``.
    """
    draft = await _load_draft_with_segments(draft_id, session)
    seg = _segment_or_404(draft, seg_id)
    fields_set = payload.model_fields_set

    # Resolve the new asset-window first so we can validate against the
    # asset's true duration before mutating anything.
    new_start = seg.asset_start_ms
    new_end = seg.asset_end_ms
    if "asset_start_ms" in fields_set and payload.asset_start_ms is not None:
        new_start = int(payload.asset_start_ms)
    if "asset_end_ms" in fields_set and payload.asset_end_ms is not None:
        new_end = int(payload.asset_end_ms)

    if new_start is not None and new_end is not None and new_start >= new_end:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"asset_start_ms ({new_start}) must be < asset_end_ms ({new_end})",
        )

    # Validate against the asset's recorded duration so we can't trim
    # past the source. Only loaded when an asset-time field was actually
    # touched.
    if (
        "asset_start_ms" in fields_set or "asset_end_ms" in fields_set
    ) and seg.asset_id is not None:
        asset = await session.get(Asset, seg.asset_id)
        if asset is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="segment's asset is missing; cannot trim",
            )
        if new_start is not None and new_start < 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="asset_start_ms must be ≥ 0",
            )
        if new_end is not None and asset.duration_ms is not None and new_end > asset.duration_ms:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(f"asset_end_ms ({new_end}) must be ≤ asset duration ({asset.duration_ms})"),
            )

    if (
        "transition" in fields_set
        and payload.transition is not None
        and payload.transition not in _valid_transitions()
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(f"transition '{payload.transition}' is not in the renderer's whitelist"),
        )

    # All checks passed — apply.
    if "asset_start_ms" in fields_set and payload.asset_start_ms is not None:
        seg.asset_start_ms = int(payload.asset_start_ms)
    if "asset_end_ms" in fields_set and payload.asset_end_ms is not None:
        seg.asset_end_ms = int(payload.asset_end_ms)
    if "transition" in fields_set and payload.transition is not None:
        seg.transition = payload.transition
    if "voice_volume" in fields_set and payload.voice_volume is not None:
        seg.voice_volume = float(payload.voice_volume)
    if "bgm_volume" in fields_set:
        # bgm_volume can be set to None explicitly (= clear override).
        seg.bgm_volume = float(payload.bgm_volume) if payload.bgm_volume is not None else None

    await _reflow_segments_and_cut_plan(draft)
    await session.commit()
    await session.refresh(draft, attribute_names=["segments"])
    return serialise_draft_detail(draft)


@router.delete(
    "/{draft_id}/segments/{seg_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_draft_segment(
    draft_id: int,
    seg_id: int,
    session: SessionDep,
) -> None:
    """Remove one segment from a draft and reflow.

    Refuses with 409 if removing the segment would leave the draft with
    zero segments (a draft with no cut plan can't render).

    Does NOT enqueue a render.
    """
    draft = await _load_draft_with_segments(draft_id, session)
    seg = _segment_or_404(draft, seg_id)

    if len(draft.segments) <= 1:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="cannot delete the last remaining segment",
        )

    # Park survivors at negative offsets so the deletion + renumber
    # doesn't trip the UNIQUE(draft_id, order) constraint mid-flush.
    survivors = [s for s in draft.segments if s.id != seg.id]
    parking_offset = -1 - len(draft.segments)
    for tmp_idx, s in enumerate(sorted(survivors, key=lambda x: x.order)):
        s.order = parking_offset + tmp_idx
    await session.delete(seg)
    await session.flush()

    for new_order, s in enumerate(sorted(survivors, key=lambda x: x.order)):
        s.order = new_order
    await session.flush()

    # Reload draft.segments — the deleted row is gone and ``draft``'s
    # cached relationship still holds it until refreshed.
    await session.refresh(draft, attribute_names=["segments"])
    await _reflow_segments_and_cut_plan(draft)
    await session.commit()


# ---------- M7.2 — subtitle inline edit ----------


@router.get("/{draft_id}/subtitles", response_model=list[SubtitleCueOut])
async def list_subtitles(
    draft_id: int,
    session: SessionDep,
) -> list[SubtitleCueOut]:
    if (await session.get(Draft, draft_id)) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="draft not found")
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
    return [SubtitleCueOut.model_validate(r) for r in rows]


@router.patch("/{draft_id}/subtitles/{idx}", response_model=SubtitleCueOut)
async def patch_subtitle(
    draft_id: int,
    idx: int,
    payload: SubtitleCuePatch,
    session: SessionDep,
) -> SubtitleCueOut:
    """Update one cue's text. Timing stays locked to whatever the burn-in
    stage produced — fixing timing would mean re-cutting the source."""
    cue = (
        await session.execute(
            select(SubtitleCueRow)
            .where(SubtitleCueRow.draft_id == draft_id)
            .where(SubtitleCueRow.idx == idx)
        )
    ).scalar_one_or_none()
    if cue is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="subtitle cue not found")
    cue.text = payload.text
    await session.commit()
    await session.refresh(cue)
    return SubtitleCueOut.model_validate(cue)


# v0.22.1 — re-render with the current project settings, preserving
# the existing DraftSegment order + subtitle cues. Functionally a
# superset of /rebuild-subtitles (same skip-plan + subtitles-from-db
# flags) but the endpoint name + button copy make the operator's
# intent explicit: "I tweaked BGM / watermark / transitions / style
# and want a new render WITHOUT the AI re-shuffling my segments."
# The body shape is shared with /rebuild-subtitles so the FE doesn't
# need a separate type.
@router.post("/{draft_id}/re-render", response_model=DraftDetail)
async def re_render_draft(
    draft_id: int,
    session: SessionDep,
    payload: DraftRebuildSubtitlesRequest | None = Body(default=None),  # noqa: B008
) -> DraftDetail:
    """Re-run the render stages (cut → concat → subtitles → watermark
    → BGM) against the existing plan, picking up any project-level
    setting changes (BGM track / watermark / subtitle style /
    transitions toggle / etc.). Segments + subtitle cues stay
    verbatim. ``render_flags`` body field overrides the snapshotted
    toggles (same priority as /rebuild-subtitles + /order)."""
    stmt = select(Draft).where(Draft.id == draft_id).options(selectinload(Draft.segments))
    draft = (await session.execute(stmt)).scalar_one_or_none()
    if draft is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="draft not found")
    if not draft.cut_plan_json:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="draft has no plan to re-render against",
        )
    await _prepare_draft_for_settings_rerender(session, draft)
    draft.status = DraftStatus.PENDING.value
    draft.progress_steps_json = {}
    draft.prompt_feedback = None
    # v0.25.1 — explicit re-render resets the watchdog retry budget.
    draft.render_retry_count = 0
    override = payload.render_flags if payload is not None else None
    flags = _draft_render_flags(draft, override)
    draft.render_flags_json = flags
    await session.commit()

    try:
        enqueue_project_edit(
            draft.project_id,
            draft_id=draft.id,
            force=True,
            skip_plan=True,
            subtitles_from_db=True,
            transitions=flags["transitions"],
            stabilize=flags["stabilize"],
            subtitles=flags["subtitles"],
            auto_reframe=flags["auto_reframe"],
            smart_camera=flags["smart_camera"],
        )
    except Exception as exc:  # noqa: BLE001 — do not leave pending without RQ job.
        await _mark_draft_enqueue_failed(session, draft, exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"draft render enqueue failed: {exc}",
        ) from exc
    await session.refresh(draft, attribute_names=["segments"])
    return serialise_draft_detail(draft)


@router.post("/{draft_id}/rebuild-subtitles", response_model=DraftDetail)
async def rebuild_subtitles(
    draft_id: int,
    session: SessionDep,
    payload: DraftRebuildSubtitlesRequest | None = Body(default=None),  # noqa: B008
) -> DraftDetail:
    """Re-burn subtitles using the current ``subtitle_cues`` rows. Skips
    the plan + cut + concat stages — only the SRT-from-DB and burn-in
    stages run, plus the BGM stage at the end if a track is set.

    The body is optional so older clients (no body) keep working;
    when provided, ``render_flags`` overrides the per-flag values
    stored on the Draft (used by ProjectEdit to push the operator's
    current toggle state, especially for legacy drafts that pre-date
    ``Draft.render_flags_json``)."""
    stmt = select(Draft).where(Draft.id == draft_id).options(selectinload(Draft.segments))
    draft = (await session.execute(stmt)).scalar_one_or_none()
    if draft is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="draft not found")
    if not draft.cut_plan_json:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="draft has no plan to re-render against",
        )
    await _prepare_draft_for_settings_rerender(session, draft)
    draft.status = DraftStatus.PENDING.value
    draft.progress_steps_json = {}
    draft.prompt_feedback = None
    # v0.25.1 — explicit re-render resets the watchdog retry budget.
    draft.render_retry_count = 0
    override = payload.render_flags if payload is not None else None
    flags = _draft_render_flags(draft, override)
    draft.render_flags_json = flags
    await session.commit()

    try:
        enqueue_project_edit(
            draft.project_id,
            draft_id=draft.id,
            force=True,
            skip_plan=True,
            subtitles_from_db=True,
            transitions=flags["transitions"],
            stabilize=flags["stabilize"],
            subtitles=flags["subtitles"],
            auto_reframe=flags["auto_reframe"],
            smart_camera=flags["smart_camera"],
        )
    except Exception as exc:  # noqa: BLE001 — do not leave pending without RQ job.
        await _mark_draft_enqueue_failed(session, draft, exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"draft render enqueue failed: {exc}",
        ) from exc
    await session.refresh(draft, attribute_names=["segments"])
    return serialise_draft_detail(draft)


# ---------- v0.17 — per-segment voice / BGM volume ----------


@router.patch(
    "/{draft_id}/segments/{segment_id}/volume",
    response_model=SegmentVolumeOut,
)
async def patch_draft_segment_volume(
    draft_id: int,
    segment_id: int,
    payload: SegmentVolumePatch,
    session: SessionDep,
) -> SegmentVolumeOut:
    """Set per-segment voice / BGM volume.

    The patch is partial — only the supplied fields are written, so the
    UI can let the user adjust voice and BGM independently. The render
    pipeline picks these up on the next render (the user usually
    follows up with 重新剪輯). We don't auto-trigger a render here so
    the user can adjust multiple sliders before committing.
    """
    seg = await session.get(DraftSegment, segment_id)
    if seg is None or seg.draft_id != draft_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="draft segment not found",
        )
    if payload.voice_volume is not None:
        seg.voice_volume = float(payload.voice_volume)
    if payload.bgm_volume is not None:
        seg.bgm_volume = float(payload.bgm_volume)
    # When the client sends ``bgm_volume: null`` Pydantic decodes that
    # as None, indistinguishable from "field omitted". The HTTP body
    # ``null`` semantics mean "reset to auto-duck"; we honour that by
    # explicitly clearing the column when the field is in the JSON.
    body_keys = set(payload.model_fields_set)
    if "bgm_volume" in body_keys and payload.bgm_volume is None:
        seg.bgm_volume = None
    await session.commit()
    await session.refresh(seg)
    return SegmentVolumeOut(
        id=seg.id,
        voice_volume=float(seg.voice_volume),
        bgm_volume=float(seg.bgm_volume) if seg.bgm_volume is not None else None,
    )


# ---------- M7.3 — export at chosen aspect / resolution ----------


@router.get("/{draft_id}/exports", response_model=list[DraftExportOut])
async def list_draft_exports(
    draft_id: int,
    session: SessionDep,
) -> list[DraftExportOut]:
    """List durable derivative exports for one draft, newest first."""
    draft = await session.get(Draft, draft_id)
    if draft is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="draft not found")
    rows = (
        await session.execute(
            select(DraftExport)
            .where(DraftExport.draft_id == draft.id)
            .order_by(DraftExport.created_at.desc(), DraftExport.id.desc())
        )
    ).scalars()
    return [serialise_draft_export(row, project_id=draft.project_id) for row in rows]


@router.post("/{draft_id}/export", response_model=DraftExportResponse)
async def export_draft(
    draft_id: int,
    payload: DraftExportRequest,
    session: SessionDep,
) -> DraftExportResponse:
    """Enqueue a derivative export for the given aspect / height.

    The original ``v{N}.mp4`` is preserved; the export lands at
    ``v{N}-{aspect}-{height}p.mp4`` next to it. Multiple exports for the
    same draft co-exist."""
    draft = await session.get(Draft, draft_id)
    if draft is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="draft not found")
    if not draft.mp4_preview_path:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="draft has no rendered mp4 yet — wait for the initial render",
        )
    if payload.aspect not in exports.VALID_ASPECTS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"aspect must be one of {exports.VALID_ASPECTS}",
        )
    output_filename = exports.derive_filename(draft.version, payload.aspect, payload.height)
    artifact = DraftExport(
        draft_id=draft.id,
        aspect=payload.aspect,
        height=payload.height,
        status="queued",
        output_filename=output_filename,
    )
    session.add(artifact)
    await session.commit()
    await session.refresh(artifact)

    try:
        job_id = enqueue_draft_export(
            draft.id,
            export_id=artifact.id,
            aspect=payload.aspect,
            height=payload.height,
        )
    except Exception as exc:  # noqa: BLE001 — surface enqueue failures as artifact state.
        artifact.status = "failed"
        artifact.error = f"enqueue failed: {exc}"
        await session.commit()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"export enqueue failed: {exc}",
        ) from exc

    artifact.job_id = job_id
    await session.commit()
    await session.refresh(artifact)
    return DraftExportResponse(
        export_id=artifact.id,
        draft_id=draft.id,
        aspect=payload.aspect,
        height=payload.height,
        job_id=job_id,
        output_filename=output_filename,
        status="queued",
        download_url=None,
        error=None,
        created_at=artifact.created_at,
        started_at=artifact.started_at,
        completed_at=artifact.completed_at,
    )


__all__ = [
    "DRAFT_URL_PREFIX",
    "router",
    "serialise_draft_detail",
]
