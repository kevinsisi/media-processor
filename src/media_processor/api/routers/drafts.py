"""Draft read endpoint."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from media_processor.api.deps import get_session
from media_processor.api.schemas import DraftDetail, DraftSegmentOut
from media_processor.models import Draft

router = APIRouter(prefix="/drafts", tags=["drafts"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


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
