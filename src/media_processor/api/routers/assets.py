"""Asset endpoints — read with attached tags, transcript, coverage, analyze."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from media_processor.api.config import settings
from media_processor.api.deps import get_session
from media_processor.api.schemas import (
    AnalyzeRequest,
    AnalyzeResponse,
    AssetDetail,
    AssetTagOut,
    AssetThumbnailsOut,
    CoverageMatchOut,
    ScriptCoverageOut,
    ThumbnailUrl,
    TranscriptOut,
    TranscriptSegmentOut,
    TranscriptUpsert,
)
from media_processor.models import Asset, AssetTranscript, ScriptCoverage
from media_processor.services import thumbnails as thumbnails_svc
from media_processor.services.queue import enqueue_asset_analysis

# Public URL prefix the browser uses to fetch thumbnail JPEGs.
# StaticFiles is mounted at "/media/thumbnails" in api.main, and the web
# nginx proxies "/api/" → api:8000, so the full URL the browser sees is
# "/api/media/thumbnails/{asset_id}/frame_{n}.jpg".
THUMBNAIL_URL_PREFIX = "/api/media/thumbnails"


def thumbnail_url_for(asset_id: int, index: int) -> str:
    return f"{THUMBNAIL_URL_PREFIX}/{asset_id}/frame_{index}.jpg"


def thumbnail_urls_for_asset(asset_id: int) -> list[str]:
    """Return public URLs for whichever frames currently exist on disk."""
    files = thumbnails_svc.list_existing_frames(settings.thumbnails_dir, asset_id)
    out: list[str] = []
    for f in files:
        stem = f.name[len("frame_") : -len(".jpg")]
        try:
            idx = int(stem)
        except ValueError:
            continue
        out.append(thumbnail_url_for(asset_id, idx))
    return out

router = APIRouter(prefix="/assets", tags=["assets"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


def _serialise_asset(asset: Asset) -> AssetDetail:
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
        analysis_steps=dict(asset.analysis_steps_json or {}) or None,
    )


@router.get("/{asset_id}", response_model=AssetDetail)
async def get_asset(
    asset_id: int,
    session: SessionDep,
) -> AssetDetail:
    stmt = select(Asset).where(Asset.id == asset_id).options(selectinload(Asset.tags))
    asset = (await session.execute(stmt)).scalar_one_or_none()
    if asset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="asset not found")
    return _serialise_asset(asset)


@router.get("/{asset_id}/thumbnails", response_model=AssetThumbnailsOut)
async def get_asset_thumbnails(
    asset_id: int,
    session: SessionDep,
) -> AssetThumbnailsOut:
    asset = await session.get(Asset, asset_id)
    if asset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="asset not found")
    files = thumbnails_svc.list_existing_frames(settings.thumbnails_dir, asset_id)
    items: list[ThumbnailUrl] = []
    for f in files:
        stem = f.name[len("frame_") : -len(".jpg")]
        try:
            idx = int(stem)
        except ValueError:
            continue
        items.append(ThumbnailUrl(index=idx, url=thumbnail_url_for(asset_id, idx)))
    return AssetThumbnailsOut(asset_id=asset_id, count=len(items), thumbnails=items)


# ----- transcript -----


def _transcript_to_out(row: AssetTranscript) -> TranscriptOut:
    raw_segments = list(row.segments_json or [])
    out_segments = [
        TranscriptSegmentOut(
            idx=int(s["idx"]),
            start_ms=int(s["start_ms"]),
            end_ms=int(s["end_ms"]),
            text=str(s["text"]),
        )
        for s in raw_segments
    ]
    return TranscriptOut(
        asset_id=row.asset_id,
        language=row.language,
        model=row.model,
        transcript_text=row.transcript_text,
        segments=out_segments,
        edited=row.edited,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.get("/{asset_id}/transcript", response_model=TranscriptOut)
async def get_asset_transcript(
    asset_id: int,
    session: SessionDep,
) -> TranscriptOut:
    asset = await session.get(Asset, asset_id)
    if asset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="asset not found")
    row = (
        await session.execute(
            select(AssetTranscript).where(AssetTranscript.asset_id == asset_id)
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="transcript not yet computed",
        )
    return _transcript_to_out(row)


@router.put("/{asset_id}/transcript", response_model=TranscriptOut)
async def put_asset_transcript(
    asset_id: int,
    payload: TranscriptUpsert,
    session: SessionDep,
) -> TranscriptOut:
    asset = await session.get(Asset, asset_id)
    if asset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="asset not found")
    if not payload.segments:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="segments must not be empty",
        )

    # Validate ascending non-overlapping ranges; reassign idx server-side so a
    # caller can re-order segments without computing indices.
    last_end = -1
    normalised: list[dict[str, Any]] = []
    texts: list[str] = []
    for i, seg in enumerate(payload.segments):
        if seg.end_ms <= seg.start_ms:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"segment {i} has end_ms <= start_ms",
            )
        if seg.start_ms < last_end:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"segment {i} starts before previous segment ends",
            )
        last_end = seg.end_ms
        normalised.append(
            {
                "idx": i,
                "start_ms": seg.start_ms,
                "end_ms": seg.end_ms,
                "text": seg.text,
            }
        )
        texts.append(seg.text)

    transcript_text = "\n".join(texts)
    now = datetime.now(UTC)

    row = (
        await session.execute(
            select(AssetTranscript).where(AssetTranscript.asset_id == asset_id)
        )
    ).scalar_one_or_none()
    if row is None:
        row = AssetTranscript(
            asset_id=asset_id,
            language="zh-Hant",
            model="user-edit",
            transcript_text=transcript_text,
            segments_json=normalised,
            edited=True,
        )
        session.add(row)
    else:
        row.transcript_text = transcript_text
        row.segments_json = normalised
        row.edited = True
        row.updated_at = now
    await session.commit()
    await session.refresh(row)
    return _transcript_to_out(row)


# ----- coverage -----


def _coverage_to_out(row: ScriptCoverage) -> ScriptCoverageOut:
    raw_matches = list(row.match_details_json or [])
    matches = [
        CoverageMatchOut(
            transcript_idx=int(m["transcript_idx"]),
            classification=m["classification"],
            confidence=float(m["confidence"]),
            matched_script_excerpt=str(m.get("matched_script_excerpt", "")),
        )
        for m in raw_matches
    ]
    return ScriptCoverageOut(
        asset_id=row.asset_id,
        script_id=row.script_id,
        model=row.model,
        scripted_segment_count=row.scripted_segment_count,
        total_segment_count=row.total_segment_count,
        coverage_ratio_by_count=row.coverage_ratio_by_count,
        coverage_ratio_by_duration_ms=row.coverage_ratio_by_duration_ms,
        matches=matches,
        computed_at=row.computed_at,
    )


@router.get("/{asset_id}/coverage", response_model=ScriptCoverageOut)
async def get_asset_coverage(
    asset_id: int,
    session: SessionDep,
) -> ScriptCoverageOut:
    asset = await session.get(Asset, asset_id)
    if asset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="asset not found")
    row = (
        await session.execute(
            select(ScriptCoverage).where(ScriptCoverage.asset_id == asset_id)
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="coverage not yet computed",
        )
    return _coverage_to_out(row)


# ----- analyze trigger -----


@router.post(
    "/{asset_id}/analyze",
    response_model=AnalyzeResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def trigger_asset_analysis(
    asset_id: int,
    payload: AnalyzeRequest,
    session: SessionDep,
) -> AnalyzeResponse:
    asset = await session.get(Asset, asset_id)
    if asset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="asset not found")
    job_id = enqueue_asset_analysis(
        asset_id,
        steps=list(payload.steps) if payload.steps is not None else None,
        force=payload.force,
    )
    return AnalyzeResponse(
        asset_id=asset_id,
        job_id=job_id,
        status=asset.status,
        analysis_steps=dict(asset.analysis_steps_json or {}),
    )
