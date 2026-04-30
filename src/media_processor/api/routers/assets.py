"""Asset read endpoint with attached tags."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from media_processor.api.deps import get_session
from media_processor.api.schemas import AssetDetail, AssetTagOut
from media_processor.models import Asset

router = APIRouter(prefix="/assets", tags=["assets"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.get("/{asset_id}", response_model=AssetDetail)
async def get_asset(
    asset_id: int,
    session: SessionDep,
) -> AssetDetail:
    stmt = select(Asset).where(Asset.id == asset_id).options(selectinload(Asset.tags))
    asset = (await session.execute(stmt)).scalar_one_or_none()
    if asset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="asset not found")
    sorted_tags = sorted(asset.tags, key=lambda t: t.confidence, reverse=True)
    return AssetDetail(
        id=asset.id,
        project_id=asset.project_id,
        file_path=asset.file_path,
        duration_ms=asset.duration_ms,
        resolution=asset.resolution,
        fps=asset.fps,
        codec=asset.codec,
        sha256=asset.sha256,
        thumbnail_path=asset.thumbnail_path,
        status=asset.status,
        tags=[AssetTagOut.model_validate(t) for t in sorted_tags],
    )
