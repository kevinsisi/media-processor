"""v0.15 — AI BGM generation + music library API.

Five endpoints surface the new BGM source workflow:

  GET  /projects/{id}/music-suggestion  — Gemini-derived prompt
  POST /projects/{id}/generate-bgm      — enqueue MusicGen job
  GET  /projects/{id}/bgm-status        — latest job status
  GET  /music-library                   — curated library tracks
  POST /projects/{id}/bgm/select-library — copy a library track onto
                                            ``Project.bgm_path``

The library is whatever wav/mp3 files live under
``${BGM_DIR}/_library/``. Operators seed it via
``scripts/seed_music_library.py`` (one-shot MusicGen pre-render) or
just by dropping files there manually.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from media_processor.api.config import settings
from media_processor.api.deps import get_session
from media_processor.api.schemas import (
    BgmGenerationStatusOut,
    GenerateBgmRequest,
    MusicLibraryItem,
    MusicLibraryOut,
    MusicSuggestionOut,
    SelectLibraryBgmRequest,
)
from media_processor.models import BgmGenerationJob, Project
from media_processor.services import music_suggest
from media_processor.services.edit_planner import resolve_style_preset
from media_processor.services.queue import enqueue_bgm_generation
from media_processor.services.settings_store import get_llm_api_keys

logger = logging.getLogger(__name__)

router = APIRouter(tags=["music"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]

# Filename extensions the library endpoint surfaces. wav for MusicGen
# defaults; the others let operators drop in human-recorded tracks.
_LIBRARY_EXTS: frozenset[str] = frozenset(
    {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg"}
)


def _library_dir() -> Path:
    return Path(settings.bgm_dir) / "_library"


def _public_url(rel_path: str) -> str:
    """Map an in-container BGM path to its public URL.

    The api mounts ``${BGM_DIR}`` at ``/api/media/bgm/...`` (the ``/api``
    prefix is added by the nginx fronting). ``rel_path`` is the path
    inside ``${BGM_DIR}``, e.g. ``_library/upbeat-lofi.wav`` →
    ``/api/media/bgm/_library/upbeat-lofi.wav``.
    """
    return f"/api/media/bgm/{rel_path.replace(chr(92), '/')}"


def _probe_duration_s(path: Path) -> float | None:
    """Return the audio duration in seconds via ffprobe; None on failure.

    Sync subprocess wrapped by callers in ``asyncio.to_thread`` so the
    request doesn't block the event loop on a slow disk.
    """
    try:
        out = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True,
            timeout=10,
            check=True,
        )
        return float(out.stdout.decode("utf-8", errors="replace").strip())
    except Exception as exc:  # noqa: BLE001 — duration is best-effort
        logger.debug("ffprobe failed for %s: %s", path, exc)
        return None


def _parse_library_name(stem: str) -> tuple[str, str | None]:
    """Split ``[style] name`` filenames into (display_name, style).

    Files seeded by ``scripts/seed_music_library.py`` use the convention
    ``[電影感] 開場-suspense.wav`` so the UI can group by style. Files
    without the prefix render with style=None.
    """
    if stem.startswith("[") and "]" in stem:
        end = stem.index("]")
        style = stem[1:end].strip()
        name = stem[end + 1 :].strip().lstrip("-_ ")
        return (name or stem, style or None)
    return stem, None


# ---------- /music-library ----------


@router.get("/music-library", response_model=MusicLibraryOut)
async def list_music_library() -> MusicLibraryOut:
    """List every audio file under ``${BGM_DIR}/_library/``."""
    lib = _library_dir()
    if not lib.is_dir():
        return MusicLibraryOut(items=[])

    items: list[MusicLibraryItem] = []
    for entry in sorted(lib.iterdir(), key=lambda p: p.name.lower()):
        if not entry.is_file():
            continue
        if entry.suffix.lower() not in _LIBRARY_EXTS:
            continue
        try:
            size = entry.stat().st_size
        except OSError:
            continue
        duration = await asyncio.to_thread(_probe_duration_s, entry)
        name, style = _parse_library_name(entry.stem)
        items.append(
            MusicLibraryItem(
                name=name,
                style=style,
                duration_s=duration,
                url=_public_url(f"_library/{entry.name}"),
                size_bytes=size,
            )
        )
    return MusicLibraryOut(items=items)


# ---------- /projects/{id}/music-suggestion ----------


@router.get(
    "/projects/{project_id}/music-suggestion",
    response_model=MusicSuggestionOut,
)
async def project_music_suggestion(
    project_id: int,
    session: SessionDep,
    style_preset: str = "custom",
) -> MusicSuggestionOut:
    """Compose a Gemini-driven music description for ``project_id``.

    Falls back to ``music_suggest.FALLBACK_DESCRIPTION`` when no LLM
    keys are configured, when every key returns 429, or when Gemini
    keeps emitting malformed JSON. The response always carries a
    non-empty ``description`` so the UI textarea is never blank.

    The optional ``style_preset`` query param feeds the preset's
    ``bgm_hint`` into the suggestion prompt so the suggested BGM
    matches the rhythm the user picked on the edit screen.
    """
    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="project not found"
        )

    style_hint = resolve_style_preset(style_preset).bgm_hint

    api_keys = await get_llm_api_keys(session)
    if not api_keys:
        return MusicSuggestionOut(
            description=music_suggest.FALLBACK_DESCRIPTION,
            used_fallback=True,
        )

    try:
        description = await music_suggest.suggest(
            project_id,
            session,
            api_keys=api_keys,
            model=settings.llm_model,
            timeout_s=settings.llm_timeout_s,
            style_hint=style_hint,
        )
        return MusicSuggestionOut(description=description, used_fallback=False)
    except music_suggest.MusicSuggestError as exc:
        logger.warning(
            "music-suggestion fell back for project %d: %s", project_id, exc
        )
        return MusicSuggestionOut(
            description=music_suggest.FALLBACK_DESCRIPTION,
            used_fallback=True,
        )


# ---------- /projects/{id}/generate-bgm ----------


@router.post(
    "/projects/{project_id}/generate-bgm",
    response_model=BgmGenerationStatusOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def trigger_generate_bgm(
    project_id: int,
    payload: GenerateBgmRequest,
    session: SessionDep,
) -> BgmGenerationStatusOut:
    """Enqueue a MusicGen job for ``project_id`` with ``payload.prompt``.

    Creates a ``BgmGenerationJob`` row in ``pending`` so the UI can
    poll right away — the worker flips it to ``running`` when it picks
    the rq message up. Multiple jobs per project are allowed; the
    status endpoint returns the most recent one.
    """
    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="project not found"
        )

    job = BgmGenerationJob(
        project_id=project_id,
        status="pending",
        prompt=payload.prompt.strip(),
    )
    session.add(job)
    await session.commit()
    await session.refresh(job)

    rq_job_id = enqueue_bgm_generation(job.id)
    job.rq_job_id = rq_job_id
    await session.commit()
    await session.refresh(job)

    return _serialise_job(job)


# ---------- /projects/{id}/bgm-status ----------


@router.get(
    "/projects/{project_id}/bgm-status",
    response_model=BgmGenerationStatusOut,
)
async def project_bgm_status(
    project_id: int,
    session: SessionDep,
) -> BgmGenerationStatusOut:
    """Return the latest ``BgmGenerationJob`` row for the project.

    Empty body (every field None) means the project has never
    generated; the UI can treat that as "尚未生成過". Otherwise we
    return status / output_url so the UI can preview when status==done.
    """
    latest = (
        await session.execute(
            select(BgmGenerationJob)
            .where(BgmGenerationJob.project_id == project_id)
            .order_by(BgmGenerationJob.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if latest is None:
        return BgmGenerationStatusOut()
    return _serialise_job(latest)


# ---------- /projects/{id}/bgm/select-library ----------


@router.post(
    "/projects/{project_id}/bgm/select-library",
    response_model=BgmGenerationStatusOut,
)
async def select_library_bgm(
    project_id: int,
    payload: SelectLibraryBgmRequest,
    session: SessionDep,
) -> BgmGenerationStatusOut:
    """Copy a library track into the project's BGM slot.

    We copy (not symlink) into ``${BGM_DIR}/{project_id}/<basename>``
    so the project owns its own immutable copy — replacing the library
    track later doesn't retroactively change rendered drafts that
    referenced it. Updates ``Project.bgm_path`` so the next render
    picks it up.
    """
    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="project not found"
        )

    # Look the requested name up in the library by either display name
    # or the raw stem so callers can pass whatever the /music-library
    # response gave them.
    target_name = payload.name.strip()
    lib = _library_dir()
    if not lib.is_dir():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="music library is empty",
        )

    chosen: Path | None = None
    for entry in lib.iterdir():
        if not entry.is_file() or entry.suffix.lower() not in _LIBRARY_EXTS:
            continue
        display, _style = _parse_library_name(entry.stem)
        if entry.stem == target_name or display == target_name:
            chosen = entry
            break
    if chosen is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"library track {target_name!r} not found",
        )

    proj_dir = Path(settings.bgm_dir) / str(project_id)
    proj_dir.mkdir(parents=True, exist_ok=True)
    dest = proj_dir / chosen.name
    await asyncio.to_thread(shutil.copyfile, str(chosen), str(dest))

    project.bgm_path = str(dest)
    await session.commit()

    # Reuse the BgmGenerationStatusOut shape for the UI's polling loop:
    # status="done" + output_url so the same component can show a
    # preview when a library track was chosen, just like an AI gen.
    return BgmGenerationStatusOut(
        job_id=None,
        status="done",
        prompt=None,
        output_url=_public_url(f"{project_id}/{chosen.name}"),
        error=None,
        created_at=None,
        completed_at=None,
    )


# ---------- helpers ----------


def _serialise_job(job: BgmGenerationJob) -> BgmGenerationStatusOut:
    output_url: str | None = None
    if job.output_path:
        # Strip the in-container ${BGM_DIR} prefix so the URL is relative
        # to the public mount.
        try:
            rel = Path(job.output_path).relative_to(Path(settings.bgm_dir))
            output_url = _public_url(str(rel))
        except ValueError:
            output_url = None
    return BgmGenerationStatusOut(
        job_id=job.id,
        status=job.status,
        prompt=job.prompt,
        output_url=output_url,
        error=job.error,
        created_at=job.created_at.isoformat() if job.created_at else None,
        completed_at=job.completed_at.isoformat() if job.completed_at else None,
    )


__all__ = ["router"]
