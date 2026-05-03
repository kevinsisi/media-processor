"""v0.21.6 — watermark preset endpoints.

A "preset" is a saved snapshot of one project's current watermark
configuration (PNG + position / scale / opacity) that can be applied
to any other project without re-uploading the PNG. Each preset owns
its own file under ``${WATERMARK_DIR}/_presets/{preset_id}.png`` so
the lifecycle is independent of any project.

Endpoints:
  * ``GET    /watermark-presets`` — list all saved presets, newest
    first; each carries a public ``preview_url`` for the gallery.
  * ``POST   /watermark-presets`` — body
    ``{project_id, name}``; copies the project's current watermark
    file + four ``watermark_*`` columns into a new preset row.
  * ``DELETE /watermark-presets/{id}`` — remove preset row + file.
    Idempotent (204 even when the row vanished out from under us).
  * ``POST   /projects/{id}/watermark/apply-preset`` body
    ``{preset_id}`` — reverse direction: copies the preset PNG into
    ``${WATERMARK_DIR}/{project_id}.png`` and overwrites the
    project's four columns to match. Lives on the projects router
    side (see ``routers/projects.py``).
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from media_processor.api.config import settings
from media_processor.api.deps import get_session
from media_processor.api.schemas import (
    WatermarkPresetOut,
    WatermarkPresetSaveRequest,
)
from media_processor.models import Project, WatermarkPreset

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/watermark-presets", tags=["watermark-presets"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


def _preset_dir() -> Path:
    """Where preset PNGs live on disk. Subdir under WATERMARK_DIR so
    we share the existing static-file mount without a new compose
    volume."""
    return Path(settings.watermark_dir) / "_presets"


def _preset_filename(preset_id: int) -> str:
    return f"{preset_id}.png"


def _preset_url(preset: WatermarkPreset) -> str | None:
    """Public URL for the preset PNG, with mtime cache-bust query.
    Returns None when the file vanished out from under us."""
    p = Path(preset.file_path)
    if not p.is_file():
        return None
    try:
        mtime = int(p.stat().st_mtime)
    except OSError:
        mtime = 0
    return f"/api/media/watermarks/_presets/{p.name}?v={mtime}"


def _to_out(preset: WatermarkPreset) -> WatermarkPresetOut:
    return WatermarkPresetOut(
        id=preset.id,
        name=preset.name,
        position=preset.position,  # type: ignore[arg-type]
        scale=float(preset.scale),
        opacity=float(preset.opacity),
        created_at=preset.created_at,
        preview_url=_preset_url(preset),
    )


@router.get("", response_model=list[WatermarkPresetOut])
async def list_watermark_presets(
    session: SessionDep,
) -> list[WatermarkPresetOut]:
    rows = (
        (
            await session.execute(
                select(WatermarkPreset).order_by(WatermarkPreset.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return [_to_out(p) for p in rows]


@router.post(
    "",
    response_model=WatermarkPresetOut,
    status_code=status.HTTP_201_CREATED,
)
async def save_watermark_preset(
    payload: WatermarkPresetSaveRequest,
    session: SessionDep,
) -> WatermarkPresetOut:
    project = await session.get(Project, payload.project_id)
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="project not found"
        )
    if not project.watermark_path:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="project has no watermark to save as a preset",
        )
    src_path = Path(project.watermark_path)
    if not src_path.is_file():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "project's watermark file is missing on disk; re-upload "
                "before saving as a preset"
            ),
        )

    # Insert the row first to get an id, then copy the file into the
    # preset dir and persist the path. The two-phase approach keeps
    # ``WatermarkPreset.file_path`` always pointing at an id-named
    # file so the static mount can serve presets by their row id.
    preset = WatermarkPreset(
        name=payload.name.strip(),
        file_path="",  # placeholder; rewritten below
        position=project.watermark_position,
        scale=float(project.watermark_scale),
        opacity=float(project.watermark_opacity),
    )
    session.add(preset)
    await session.flush()

    preset_dir = _preset_dir()
    preset_dir.mkdir(parents=True, exist_ok=True)
    target = preset_dir / _preset_filename(preset.id)
    try:
        shutil.copyfile(src_path, target)
    except OSError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"failed to copy watermark to preset dir: {exc}",
        ) from exc

    preset.file_path = str(target)
    await session.commit()
    await session.refresh(preset)
    logger.info(
        "watermark-preset: saved id=%d name=%r from project=%d",
        preset.id,
        preset.name,
        payload.project_id,
    )
    return _to_out(preset)


@router.delete(
    "/{preset_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_watermark_preset(
    preset_id: int,
    session: SessionDep,
) -> None:
    preset = await session.get(WatermarkPreset, preset_id)
    if preset is None:
        # Idempotent — the row vanished but the caller's intent
        # ("make sure this preset is gone") is already satisfied.
        return
    file_path = Path(preset.file_path) if preset.file_path else None
    await session.delete(preset)
    await session.commit()
    if file_path is not None:
        try:
            file_path.unlink(missing_ok=True)
        except OSError as exc:
            # File-system problems shouldn't block the delete since the
            # row is already gone; log and move on. Orphan PNGs can be
            # swept later.
            logger.warning(
                "watermark-preset: row %d deleted but file %s wouldn't unlink: %s",
                preset_id,
                file_path,
                exc,
            )
    return


# v0.21.6 — bookkeeping seam used by ``routers/projects.py``'s
# apply-preset endpoint to avoid a circular import. Returns the
# preset row + on-disk file path or raises a 404 / 400 with the
# right message.
async def _load_preset_for_apply(
    session: AsyncSession, preset_id: int
) -> tuple[WatermarkPreset, Path]:
    preset = await session.get(WatermarkPreset, preset_id)
    if preset is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="watermark preset not found",
        )
    src_path = Path(preset.file_path) if preset.file_path else None
    if src_path is None or not src_path.is_file():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "preset file is missing on disk; delete the preset and "
                "re-create it from a project that has the watermark"
            ),
        )
    return preset, src_path


# Re-exported so callers don't reach into a private name.
__all__ = [
    "_load_preset_for_apply",
    "_preset_dir",
    "_preset_url",
    "router",
]
