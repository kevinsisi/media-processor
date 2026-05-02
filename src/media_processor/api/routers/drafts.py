"""Draft endpoints — read, Stage 4.5 LLM patch, M5 render trigger."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
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
    DraftExportRequest,
    DraftExportResponse,
    DraftPatchRequest,
    DraftPatchResponse,
    DraftReorderRequest,
    DraftSegmentOut,
    SegmentVolumeOut,
    SegmentVolumePatch,
    SubtitleCueOut,
    SubtitleCuePatch,
)
from media_processor.models import (
    AssetSegment,
    AssetTag,
    Draft,
    DraftComment,
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
    return f"{DRAFT_URL_PREFIX}/{project_id}/{_draft_filename(version, suffix)}"


def _expected_draft_path(project_id: int, version: int, suffix: str) -> Path:
    return Path(settings.drafts_dir) / str(project_id) / _draft_filename(version, suffix)


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
                voice_volume=float(getattr(s, "voice_volume", 1.0) or 1.0),
                bgm_volume=(
                    float(s.bgm_volume) if getattr(s, "bgm_volume", None) is not None else None
                ),
            )
            for s in sorted(draft.segments, key=lambda x: x.order)
        ],
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


# ---------- M7.1 — timeline reorder ----------


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

    cursor_ms = 0
    new_plan_segments: list[dict[str, Any]] = []
    for new_order, seg_id in enumerate(requested_ids):
        seg = by_id[seg_id]
        duration = max(1, (seg.asset_end_ms or 0) - (seg.asset_start_ms or 0))
        seg.order = new_order
        seg.on_timeline_start_ms = cursor_ms
        seg.on_timeline_end_ms = cursor_ms + duration
        cursor_ms += duration
        if seg.asset_id is not None and seg.asset_start_ms is not None and seg.asset_end_ms is not None:
            new_plan_segments.append(
                {
                    "order": new_order,
                    "asset_id": int(seg.asset_id),
                    "asset_start_ms": int(seg.asset_start_ms),
                    "asset_end_ms": int(seg.asset_end_ms),
                    "source_kind": str(seg.source_kind or "scripted"),
                    "reason": str(seg.plan_reason or ""),
                    "transition_to_next": str(seg.transition or "dissolve"),
                }
            )

    # Update the stored cut_plan_json so a downstream skip-plan render
    # can reload the same shape without losing metadata. Fields outside
    # ``segments`` are preserved.
    blob: dict[str, Any] = dict(draft.cut_plan_json or {})
    blob["segments"] = new_plan_segments
    draft.cut_plan_json = blob

    draft.status = DraftStatus.PENDING.value
    draft.progress_steps_json = {}
    draft.prompt_feedback = None
    await session.commit()

    enqueue_project_edit(
        draft.project_id,
        draft_id=draft.id,
        force=True,
        skip_plan=True,
    )

    await session.refresh(draft, attribute_names=["segments"])
    return serialise_draft_detail(draft)


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


@router.post("/{draft_id}/rebuild-subtitles", response_model=DraftDetail)
async def rebuild_subtitles(
    draft_id: int,
    session: SessionDep,
) -> DraftDetail:
    """Re-burn subtitles using the current ``subtitle_cues`` rows. Skips
    the plan + cut + concat stages — only the SRT-from-DB and burn-in
    stages run, plus the BGM stage at the end if a track is set."""
    stmt = select(Draft).where(Draft.id == draft_id).options(selectinload(Draft.segments))
    draft = (await session.execute(stmt)).scalar_one_or_none()
    if draft is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="draft not found")
    if not draft.cut_plan_json:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="draft has no plan to re-render against",
        )
    draft.status = DraftStatus.PENDING.value
    draft.progress_steps_json = {}
    draft.prompt_feedback = None
    await session.commit()

    enqueue_project_edit(
        draft.project_id,
        draft_id=draft.id,
        force=True,
        skip_plan=True,
        subtitles_from_db=True,
    )
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
    job_id = enqueue_draft_export(
        draft.id,
        aspect=payload.aspect,
        height=payload.height,
    )
    output_filename = exports.derive_filename(draft.version, payload.aspect, payload.height)
    return DraftExportResponse(
        draft_id=draft.id,
        aspect=payload.aspect,
        height=payload.height,
        job_id=job_id,
        output_filename=output_filename,
    )


__all__ = [
    "DRAFT_URL_PREFIX",
    "router",
    "serialise_draft_detail",
]
