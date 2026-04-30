"""Draft endpoints — read + Stage 4.5 LLM patch."""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from media_processor.api.deps import get_llm_patcher, get_profile_loader, get_session
from media_processor.api.schemas import (
    DraftDetail,
    DraftPatchRequest,
    DraftPatchResponse,
    DraftSegmentOut,
)
from media_processor.models import AssetSegment, AssetTag, Draft, DraftSegment
from media_processor.profile.loader import ProfileSpec
from media_processor.services.llm_patcher import (
    DraftSegmentSummary,
    LLMPatcher,
    LLMPatchError,
    apply_patch,
)

router = APIRouter(prefix="/drafts", tags=["drafts"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
LLMPatcherDep = Annotated[LLMPatcher, Depends(get_llm_patcher)]
ProfileLoaderDep = Annotated[Callable[[str], ProfileSpec], Depends(get_profile_loader)]


@router.get("/{draft_id}", response_model=DraftDetail)
async def get_draft(
    draft_id: int,
    session: SessionDep,
) -> DraftDetail:
    stmt = select(Draft).where(Draft.id == draft_id).options(selectinload(Draft.segments))
    draft = (await session.execute(stmt)).scalar_one_or_none()
    if draft is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="draft not found")
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
        segments=[DraftSegmentOut.model_validate(s) for s in draft.segments],
    )


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
