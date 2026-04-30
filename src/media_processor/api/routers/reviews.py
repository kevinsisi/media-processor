"""Review write endpoint."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from media_processor.api.deps import get_session
from media_processor.api.schemas import ReviewCreate, ReviewOut
from media_processor.models import Draft, Review

router = APIRouter(prefix="/reviews", tags=["reviews"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.post("", response_model=ReviewOut, status_code=status.HTTP_201_CREATED)
async def create_review(
    payload: ReviewCreate,
    session: SessionDep,
) -> ReviewOut:
    draft = await session.get(Draft, payload.draft_id)
    if draft is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="draft not found")

    review = Review(
        draft_id=payload.draft_id,
        action=payload.action,
        prompt_feedback=payload.prompt_feedback,
        reviewer=payload.reviewer,
    )
    session.add(review)
    await session.commit()
    await session.refresh(review)
    return ReviewOut.model_validate(review)
