"""Chunked-upload endpoints — init session, PUT chunks, fetch state, complete."""

from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from media_processor.api.config import settings
from media_processor.api.deps import get_session
from media_processor.api.schemas import (
    AssetDetail,
    AssetTagOut,
    ScriptOut,
    UploadCompleteOut,
    UploadSessionCreate,
    UploadSessionOut,
)
from media_processor.models import (
    Asset,
    Project,
    Script,
    UploadKind,
    UploadSession,
    UploadStatus,
)
from media_processor.services import asset_variants
from media_processor.services import thumbnails as thumbnails_svc
from media_processor.services import uploads as upload_svc
from media_processor.services.queue import enqueue_asset_stabilization

SCRIPT_MAX_BYTES = 1_048_576

router = APIRouter(tags=["uploads"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


def _to_session_out(row: UploadSession) -> UploadSessionOut:
    received = list(row.received_chunks or [])
    return UploadSessionOut(
        id=row.id,
        project_id=row.project_id,
        kind=row.kind,
        filename=row.filename,
        total_size=row.total_size,
        chunk_size=row.chunk_size,
        received_chunks=sorted(int(i) for i in received),
        status=row.status,
    )


def _expected_chunks(row: UploadSession) -> int:
    return upload_svc.expected_chunk_count(row.total_size, row.chunk_size)


@router.post(
    "/projects/{project_id}/uploads",
    response_model=UploadSessionOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_upload_session(
    project_id: int,
    payload: UploadSessionCreate,
    session: SessionDep,
) -> UploadSessionOut:
    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="project not found")
    row = UploadSession(
        project_id=project_id,
        kind=payload.kind,
        filename=payload.filename,
        total_size=payload.total_size,
        chunk_size=payload.chunk_size,
        sha256=payload.sha256,
        received_chunks=[],
        status=UploadStatus.PENDING.value,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return _to_session_out(row)


@router.get("/uploads/{session_id}", response_model=UploadSessionOut)
async def get_upload_session(
    session_id: str,
    session: SessionDep,
) -> UploadSessionOut:
    row = await _load_session(session_id, session)
    # Self-heal: drop indexes whose chunk file is missing on disk.
    on_disk = set(upload_svc.list_present_chunks(settings.uploads_dir, session_id))
    recorded = {int(i) for i in (row.received_chunks or [])}
    healed = sorted(recorded & on_disk)
    if healed != sorted(recorded):
        row.received_chunks = healed
        await session.commit()
        await session.refresh(row)
    return _to_session_out(row)


@router.put("/uploads/{session_id}/chunks/{chunk_index}", response_model=UploadSessionOut)
async def put_upload_chunk(
    session_id: str,
    chunk_index: int,
    request: Request,
    session: SessionDep,
) -> UploadSessionOut:
    row = await _load_session(session_id, session)
    if row.status != UploadStatus.PENDING.value:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"session is {row.status}, chunks no longer accepted",
        )
    expected = _expected_chunks(row)
    if chunk_index < 0 or chunk_index >= expected:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"chunk_index {chunk_index} outside [0, {expected})",
        )

    body = await request.body()
    if not body:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="empty chunk body",
        )

    # write_chunk is sync file I/O (4 MiB write per call). With many
    # concurrent video uploads each PUTting chunks in parallel, calling
    # it directly from the asyncio event loop serialises every disk
    # write and lets one slow disk stall the entire uvicorn worker —
    # which manifests as nginx 502s when the proxy timeout fires before
    # the queued write finishes. asyncio.to_thread offloads to the
    # default executor so writes overlap.
    await asyncio.to_thread(
        upload_svc.write_chunk, settings.uploads_dir, session_id, chunk_index, body
    )

    received = sorted({int(i) for i in (row.received_chunks or [])} | {chunk_index})
    row.received_chunks = received
    await session.commit()
    await session.refresh(row)
    return _to_session_out(row)


@router.post("/uploads/{session_id}/complete", response_model=UploadCompleteOut)
async def complete_upload_session(
    session_id: str,
    session: SessionDep,
) -> UploadCompleteOut:
    row = await _load_session(session_id, session)
    if row.status == UploadStatus.COMPLETE.value:
        return await _build_complete_response(row, session)
    if row.status != UploadStatus.PENDING.value:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"session is {row.status}; cannot complete",
        )

    expected = _expected_chunks(row)
    received = sorted({int(i) for i in (row.received_chunks or [])})
    if received != list(range(expected)):
        missing = sorted(set(range(expected)) - set(received))
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"missing chunks: {missing[:10]}{'…' if len(missing) > 10 else ''}",
        )

    if row.kind == UploadKind.VIDEO.value:
        finalised_asset = await _finalize_video(row, session, expected)
        finalised_script: ScriptOut | None = None
    elif row.kind == UploadKind.SCRIPT.value:
        finalised_asset = None
        finalised_script = await _finalize_script(row, session, expected)
    else:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"unsupported kind: {row.kind}",
        )

    row.status = UploadStatus.COMPLETE.value
    row.completed_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(row)
    upload_svc.cleanup_session_dir(settings.uploads_dir, session_id)
    return UploadCompleteOut(
        session=_to_session_out(row),
        asset=finalised_asset,
        script=finalised_script,
    )


async def _load_session(session_id: str, session: AsyncSession) -> UploadSession:
    row = await session.get(UploadSession, session_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="upload session not found",
        )
    return row


async def _build_complete_response(row: UploadSession, session: AsyncSession) -> UploadCompleteOut:
    # Idempotent re-complete: rebuild the response from current DB state.
    if row.kind == UploadKind.VIDEO.value:
        # Best-effort: find the latest asset for this project with this filename.
        candidate = (
            await session.execute(
                select(Asset)
                .where(Asset.project_id == row.project_id)
                .where(Asset.file_path.like(f"%{row.filename}"))
                .options(selectinload(Asset.tags))
                .order_by(Asset.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if candidate is None:
            return UploadCompleteOut(session=_to_session_out(row))
        return UploadCompleteOut(
            session=_to_session_out(row),
            asset=_asset_to_detail(candidate),
        )
    if row.kind == UploadKind.SCRIPT.value:
        script = (
            await session.execute(select(Script).where(Script.project_id == row.project_id))
        ).scalar_one_or_none()
        return UploadCompleteOut(
            session=_to_session_out(row),
            script=ScriptOut.model_validate(script) if script else None,
        )
    return UploadCompleteOut(session=_to_session_out(row))


async def _finalize_video(row: UploadSession, session: AsyncSession, expected: int) -> AssetDetail:
    target_dir = Path(settings.assets_dir) / str(row.project_id)
    target_path = target_dir / row.filename
    upload_svc.assemble_file(settings.uploads_dir, row.id, target_path, expected)

    sha256 = _sha256_of_file(target_path)
    probe = upload_svc.probe_media(target_path)

    asset = Asset(
        project_id=row.project_id,
        file_path=str(target_path),
        duration_ms=probe.duration_ms,
        resolution=probe.resolution,
        fps=probe.fps,
        codec=probe.codec,
        sha256=sha256,
        thumbnail_path=None,
        status="pending",
    )
    session.add(asset)
    await session.flush()
    asset_id = asset.id
    await session.commit()

    asset_loaded = (
        await session.execute(
            select(Asset).where(Asset.id == asset_id).options(selectinload(Asset.tags))
        )
    ).scalar_one()

    # M4.6 — generate the keyframe thumbnail gallery before returning so the
    # asset card renders with a preview on first paint. ~3-8s of synchronous
    # ffmpeg work; runs in a thread so the event loop stays responsive.
    # Failure is best-effort: backfill script can re-generate later.
    try:
        await asyncio.to_thread(
            thumbnails_svc.generate,
            asset_id,
            target_path,
            asset_loaded.duration_ms,
            settings.thumbnails_dir,
        )
    except Exception as exc:  # noqa: BLE001 — log and continue.
        import logging

        logging.getLogger(__name__).warning(
            "thumbnail generation failed for asset %d: %s — operator can backfill",
            asset_id,
            exc,
        )

    # v0.40.0 — stabilize first; the runner auto-switches variant and enqueues
    # analysis at each terminal state (success → stabilized, skip/fail → raw).
    # Analysis is no longer enqueued here; it runs after stabilization completes.
    try:
        asset_loaded.stabilization_status = asset_variants.STABILIZATION_PENDING
        asset_loaded.stabilized_path = str(asset_variants.stabilized_path_for_asset(asset_loaded))
        asset_loaded.stabilization_error = None
        await session.commit()
        enqueue_asset_stabilization(asset_id)
    except Exception as exc:  # noqa: BLE001 — raw workflow remains available.
        import logging

        asset_loaded.stabilization_status = asset_variants.STABILIZATION_FAILED
        asset_loaded.stabilization_error = f"enqueue failed: {exc}"
        await session.commit()
        logging.getLogger(__name__).warning(
            "failed to enqueue stabilization for asset %d: %s — operator can retry",
            asset_id,
            exc,
        )

    return _asset_to_detail(asset_loaded)


async def _finalize_script(row: UploadSession, session: AsyncSession, expected: int) -> ScriptOut:
    target_dir = Path(settings.uploads_dir) / row.id
    assembled = target_dir / "_assembled.txt"
    upload_svc.assemble_file(settings.uploads_dir, row.id, assembled, expected)
    if assembled.stat().st_size > SCRIPT_MAX_BYTES:
        assembled.unlink(missing_ok=True)
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="script exceeds 1 MB limit",
        )
    body = assembled.read_text(encoding="utf-8", errors="replace")

    existing = (
        await session.execute(select(Script).where(Script.project_id == row.project_id))
    ).scalar_one_or_none()
    now = datetime.now(UTC)
    if existing is None:
        script = Script(
            project_id=row.project_id,
            body=body,
            source_filename=row.filename,
            updated_at=now,
        )
        session.add(script)
    else:
        existing.body = body
        existing.source_filename = row.filename
        existing.updated_at = now
        script = existing
    await session.commit()
    await session.refresh(script)
    return ScriptOut.model_validate(script)


def _asset_to_detail(asset: Asset) -> AssetDetail:
    sorted_tags = sorted(asset.tags, key=lambda t: t.confidence, reverse=True)
    return AssetDetail(
        id=asset.id,
        project_id=asset.project_id,
        file_path=asset.file_path,
        active_asset_variant=asset_variants.active_variant(asset),
        stabilized_path=getattr(asset, "stabilized_path", None),
        stabilization_status=asset_variants.stabilization_status(asset),
        stabilization_error=getattr(asset, "stabilization_error", None),
        variant_urls=asset_variants.variant_urls(asset),
        duration_ms=asset.duration_ms,
        resolution=asset.resolution,
        fps=asset.fps,
        codec=asset.codec,
        sha256=asset.sha256,
        thumbnail_path=asset.thumbnail_path,
        status=asset.status,
        tags=[AssetTagOut.model_validate(t) for t in sorted_tags],
    )


def _sha256_of_file(path: Path, buf_size: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(buf_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()
