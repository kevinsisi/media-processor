"""Pexels stock footage search and import endpoints.

Allows operators to search Pexels for stock clips, preview results, and
download a chosen video directly into a project as a new Asset.

Requires PEXELS_API_KEY in environment or settings.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from media_processor.api.config import settings
from media_processor.api.deps import get_session
from media_processor.models import Asset, Project
from media_processor.services import pexels_service as pexels

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/materials", tags=["materials"])
SessionDep = Annotated[AsyncSession, Depends(get_session)]


class PexelsVideoFileOut(BaseModel):
    url: str
    width: int
    height: int
    fps: float
    quality: str


class PexelsVideoOut(BaseModel):
    id: int
    pexels_url: str
    duration_s: int
    width: int
    height: int
    aspect_ratio: str
    user_name: str
    best_file: PexelsVideoFileOut | None = None


class PexelsSearchResponse(BaseModel):
    query: str
    total: int
    videos: list[PexelsVideoOut]


class PexelsImportRequest(BaseModel):
    pexels_video_id: int
    project_id: int
    prefer_hd: bool = True


class PexelsImportResponse(BaseModel):
    asset_id: int
    project_id: int
    pexels_video_id: int
    file_path: str
    duration_ms: int
    resolution: str | None = None


def _video_to_out(v: pexels.PexelsVideo) -> PexelsVideoOut:
    best = v.best_file()
    return PexelsVideoOut(
        id=v.id,
        pexels_url=v.url,
        duration_s=v.duration_s,
        width=v.width,
        height=v.height,
        aspect_ratio=v.aspect_ratio,
        user_name=v.user_name,
        best_file=PexelsVideoFileOut(
            url=best.url, width=best.width, height=best.height, fps=best.fps, quality=best.quality
        ) if best else None,
    )


@router.get("/pexels/search", response_model=PexelsSearchResponse)
async def search_pexels(
    q: str,
    per_page: int = 10,
    page: int = 1,
    orientation: str | None = None,
    min_duration_s: int = 3,
    max_duration_s: int = 60,
) -> PexelsSearchResponse:
    """Search Pexels stock footage library.

    Returns videos matching *q*. Use *orientation*=portrait for 9:16,
    landscape for 16:9. Filters out clips outside [min_duration_s, max_duration_s].
    """
    if not settings.pexels_api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="PEXELS_API_KEY not configured on this server",
        )
    try:
        videos = await pexels.search_videos(
            q,
            per_page=per_page,
            page=page,
            orientation=orientation,
            min_duration_s=min_duration_s,
            max_duration_s=max_duration_s,
        )
    except pexels.PexelsError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Pexels search failed: {exc}",
        ) from exc

    return PexelsSearchResponse(
        query=q,
        total=len(videos),
        videos=[_video_to_out(v) for v in videos],
    )


@router.post(
    "/pexels/import",
    response_model=PexelsImportResponse,
    status_code=status.HTTP_201_CREATED,
)
async def import_pexels_video(
    payload: PexelsImportRequest,
    session: SessionDep,
) -> PexelsImportResponse:
    """Download a Pexels video and register it as a project Asset.

    Searches Pexels by ID, downloads the best quality file, creates an
    Asset record, and enqueues analysis. Returns the new asset.
    """
    if not settings.pexels_api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="PEXELS_API_KEY not configured",
        )

    project = await session.get(Project, payload.project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="project not found")

    # Fetch video metadata from Pexels
    try:
        results = await pexels.search_videos(
            f"id:{payload.pexels_video_id}",
            per_page=1,
        )
    except pexels.PexelsError:
        results = []

    # If search-by-id doesn't work, fetch directly via /videos/{id}
    if not results:
        import httpx as _httpx
        async with _httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"https://api.pexels.com/videos/videos/{payload.pexels_video_id}",
                headers={"Authorization": settings.pexels_api_key},
            )
        if resp.status_code != 200:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Pexels video {payload.pexels_video_id} not found",
            )
        video = pexels._parse_video(resp.json())
    else:
        video = results[0]

    # Download
    try:
        local_path = await pexels.download_video(
            video,
            target_dir=str(Path(settings.assets_dir) / str(payload.project_id)),
            prefer_hd=payload.prefer_hd,
        )
    except pexels.PexelsError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Pexels download failed: {exc}",
        ) from exc

    # Create Asset row
    meta = pexels.video_to_asset_info(video, local_path)
    asset = Asset(
        project_id=payload.project_id,
        file_path=str(local_path),
        duration_ms=meta["duration_ms"],
        resolution=meta.get("resolution"),
        fps=meta.get("fps"),
        sha256=meta["sha256"],
        status="pending",
    )
    session.add(asset)
    await session.commit()
    await session.refresh(asset)

    # Enqueue analysis
    try:
        from media_processor.services.queue import enqueue_asset_analysis
        enqueue_asset_analysis(asset.id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Pexels import: analysis enqueue failed for asset %d: %s", asset.id, exc)

    return PexelsImportResponse(
        asset_id=asset.id,
        project_id=payload.project_id,
        pexels_video_id=video.id,
        file_path=str(local_path),
        duration_ms=meta["duration_ms"],
        resolution=meta.get("resolution"),
    )
