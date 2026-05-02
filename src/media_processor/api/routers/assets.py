"""Asset endpoints — read with attached tags, transcript, coverage, analyze."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
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
    TrackingDetailOut,
    TrackingTargetRequest,
    TrackingTargetResponse,
    TrackingTrackOut,
    TranscriptOut,
    TranscriptSegmentOut,
    TranscriptUpsert,
    TranslateSubtitleRequest,
    TranslateSubtitleResponse,
)
from media_processor.models import Asset, AssetTranscript, ScriptCoverage
from media_processor.services import object_tracking
from media_processor.services import thumbnails as thumbnails_svc
from media_processor.services.queue import enqueue_asset_analysis, enqueue_asset_translate

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
        await session.execute(select(AssetTranscript).where(AssetTranscript.asset_id == asset_id))
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
        await session.execute(select(AssetTranscript).where(AssetTranscript.asset_id == asset_id))
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
        await session.execute(select(ScriptCoverage).where(ScriptCoverage.asset_id == asset_id))
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="coverage not yet computed",
        )
    return _coverage_to_out(row)


# ----- v0.17 — tracking detail + tracking-target picker -----


# Sample-frame downsample target for the analysis page picker. The full
# per-frame bbox track can run into thousands of rows on a long clip;
# we only need a handful (one every ~500 ms) to draw the bbox + show a
# motion preview if we want one later.
_SAMPLE_FRAME_LIMIT = 24


def _downsample_frames(frames: list[dict[str, Any]]) -> list[list[int]]:
    if not frames:
        return []
    n = len(frames)
    step = max(1, n // _SAMPLE_FRAME_LIMIT)
    out: list[list[int]] = []
    for i in range(0, n, step):
        f = frames[i]
        out.append(
            [
                int(f.get("t_ms", 0)),
                int(f.get("x", 0)),
                int(f.get("y", 0)),
                int(f.get("w", 0)),
                int(f.get("h", 0)),
            ]
        )
    return out


@router.get("/{asset_id}/tracking", response_model=TrackingDetailOut)
async def get_asset_tracking(
    asset_id: int,
    session: SessionDep,
) -> TrackingDetailOut:
    """Return the full per-track YOLO data for the picker UI."""
    asset = await session.get(Asset, asset_id)
    if asset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="asset not found")
    blob = getattr(asset, "tracking_json", None)
    if not isinstance(blob, dict):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="tracking has not run for this asset",
        )
    raw_tracks = blob.get("tracks") or []
    tracks: list[TrackingTrackOut] = []
    if raw_tracks:
        for t in raw_tracks:
            if not isinstance(t, dict):
                continue
            frames = list(t.get("frames") or [])
            tracks.append(
                TrackingTrackOut(
                    object_index=int(t.get("object_index", 0)),
                    cls_name=str(t.get("cls_name", "")),
                    confidence=float(t.get("confidence", 0.0)),
                    area_score=float(t.get("area_score", 0.0)),
                    frame_count=len(frames),
                    sample_frames=_downsample_frames(frames),
                )
            )
    else:
        # Legacy tracking_json (pre-v0.17, single-track). Synthesise a
        # one-track view from ``frames`` so the picker has something
        # to render before the user re-runs the tracking step.
        legacy_frames = list(blob.get("frames") or [])
        if legacy_frames:
            tracks.append(
                TrackingTrackOut(
                    object_index=0,
                    cls_name=str(blob.get("subject_class") or ""),
                    confidence=float(blob.get("confidence") or 0.0),
                    area_score=0.0,
                    frame_count=len(legacy_frames),
                    sample_frames=_downsample_frames(legacy_frames),
                )
            )
    return TrackingDetailOut(
        src_w=int(blob.get("src_w") or 0),
        src_h=int(blob.get("src_h") or 0),
        fps=float(blob.get("fps") or 0.0),
        sampled_frames=int(blob.get("sampled_frames") or 0),
        subject_class=str(blob.get("subject_class") or ""),
        confidence=float(blob.get("confidence") or 0.0),
        tracks=tracks,
        tracked_object_index=getattr(asset, "tracked_object_index", None),
        has_custom_roi=isinstance(getattr(asset, "custom_roi_json", None), dict),
    )


@router.patch("/{asset_id}/tracking-target", response_model=TrackingTargetResponse)
async def patch_asset_tracking_target(
    asset_id: int,
    payload: TrackingTargetRequest,
    session: SessionDep,
) -> TrackingTargetResponse:
    """Set which tracked object (or custom ROI) the renderer follows.

    Modes:
      * ``auto``    → ``tracked_object_index = NULL`` (use dominant track)
      * ``object``  → ``tracked_object_index = N`` (must exist in tracking_json["tracks"])
      * ``custom``  → ``tracked_object_index = -1`` + run CSRT to fill ``custom_roi_json``
      * ``fixed``   → ``tracked_object_index = -2`` (static centered crop)
      * ``none``    → ``tracked_object_index = -3`` (no auto-reframe at all)
    """
    asset = await session.get(Asset, asset_id)
    if asset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="asset not found")

    if payload.mode == "auto":
        asset.tracked_object_index = None
    elif payload.mode == "object":
        if payload.object_index is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="mode=object requires object_index",
            )
        # Validate the index exists in tracking_json["tracks"]. Pre-v0.17
        # blobs only carry the legacy single-track ``frames`` field; the
        # GET endpoint synthesises one bbox at object_index=0 for those,
        # so accept that same index here. The renderer's
        # ``_frames_for_object`` already falls back to ``frames`` when the
        # requested track id isn't in ``tracks``.
        blob = getattr(asset, "tracking_json", None)
        blob_dict = blob if isinstance(blob, dict) else {}
        tracks = blob_dict.get("tracks") or []
        legacy_frames = blob_dict.get("frames") or []
        index_ok = any(
            isinstance(t, dict) and int(t.get("object_index", -1)) == payload.object_index
            for t in tracks
        ) or (not tracks and bool(legacy_frames) and payload.object_index == 0)
        if not index_ok:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"object_index={payload.object_index} not in tracking_json[\"tracks\"]",
            )
        asset.tracked_object_index = int(payload.object_index)
    elif payload.mode == "custom":
        if not payload.custom_roi:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="mode=custom requires custom_roi {x,y,w,h}",
            )
        try:
            x = int(payload.custom_roi["x"])
            y = int(payload.custom_roi["y"])
            w = int(payload.custom_roi["w"])
            h = int(payload.custom_roi["h"])
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"custom_roi must contain integer x,y,w,h: {exc}",
            ) from exc
        init_t_ms = int(payload.custom_roi.get("source_t_ms") or 0)
        media_path = Path(asset.file_path)
        # CSRT can be slow on long clips (real-time-ish), but the user
        # is waiting on this single asset — run inline. asyncio.to_thread
        # keeps the event loop responsive.
        try:
            roi_json = await asyncio.to_thread(
                object_tracking.track_custom_roi,
                media_path,
                init_x=x,
                init_y=y,
                init_w=w,
                init_h=h,
                init_t_ms=init_t_ms,
                duration_ms=asset.duration_ms,
            )
        except (object_tracking.TrackingError, object_tracking.TrackingUnavailableError) as exc:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"CSRT failed: {exc}",
            ) from exc
        asset.custom_roi_json = roi_json
        asset.tracked_object_index = -1
    elif payload.mode == "fixed":
        asset.tracked_object_index = -2
    else:  # "none"
        asset.tracked_object_index = -3

    await session.commit()
    await session.refresh(asset)
    return TrackingTargetResponse(
        asset_id=asset_id,
        tracked_object_index=getattr(asset, "tracked_object_index", None),
        has_custom_roi=isinstance(getattr(asset, "custom_roi_json", None), dict),
    )


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


# ----- v0.18 — secondary-language subtitle (Whisper translate) -----


@router.post(
    "/{asset_id}/translate-subtitle",
    response_model=TranslateSubtitleResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def trigger_asset_subtitle_translation(
    asset_id: int,
    payload: TranslateSubtitleRequest,
    session: SessionDep,
) -> TranslateSubtitleResponse:
    """Enqueue Whisper task='translate' for ``asset_id`` to produce a
    secondary-language subtitle track.

    Whisper's translate task always emits English, so ``payload.lang``
    is constrained to ``"en"``. The job persists segments to
    ``Asset.subtitle_secondary_segments_json`` and the language tag to
    ``Asset.subtitle_secondary_lang`` once it completes; the UI polls
    asset state to learn when the translation is ready.
    """
    asset = await session.get(Asset, asset_id)
    if asset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="asset not found")
    job_id = enqueue_asset_translate(asset_id, lang=payload.lang)
    return TranslateSubtitleResponse(
        asset_id=asset_id,
        job_id=job_id,
        lang=payload.lang,
    )
