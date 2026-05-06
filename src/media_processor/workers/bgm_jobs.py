"""v0.15 — RQ job for AI BGM generation.

Drives the lifecycle of a ``BgmGenerationJob`` row:
  pending → running → done | failed:{reason}

Output lands at ``${BGM_DIR}/{project_id}/generated_{timestamp}.wav``.
On success the row's ``output_path`` is set and ``Project.bgm_path``
is also updated so the next render picks the AI track up
automatically — operators don't have to also pick the track from the
library.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


async def _run(job_id: int) -> dict[str, Any]:
    # Local imports keep the api container free of torch / transformers.
    from sqlalchemy import select

    from media_processor.api.config import settings
    from media_processor.core.db import async_session_maker
    from media_processor.models import BgmGenerationJob, Project
    from media_processor.services import music_gen

    started_ms = time.monotonic()
    summary: dict[str, Any] = {"job_id": job_id, "status": "pending"}

    # Phase 1 — claim the row, flip to running.
    async with async_session_maker() as session:
        row = await session.get(BgmGenerationJob, job_id)
        if row is None:
            raise RuntimeError(f"bgm_generation_jobs row {job_id} not found")
        row.status = "running"
        await session.commit()
        await session.refresh(row)
        prompt = row.prompt
        project_id = row.project_id

    # Phase 2 — run MusicGen synchronously inside a thread so the rq
    # worker's asyncio loop stays responsive (other queued jobs can be
    # cancelled etc.).
    ts = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    out_path = Path(settings.bgm_dir) / str(project_id) / f"generated_{ts}.wav"
    try:
        result = await asyncio.to_thread(music_gen.generate, prompt, out_path)
    except music_gen.MusicGenUnavailableError as exc:
        logger.exception("MusicGen unavailable for job %d", job_id)
        async with async_session_maker() as session:
            row = await session.get(BgmGenerationJob, job_id)
            if row is not None:
                row.status = "failed:model-unavailable"
                row.error = str(exc)
                row.completed_at = datetime.now(UTC)
                await session.commit()
        summary["status"] = "failed:model-unavailable"
        summary["error"] = str(exc)
        return summary
    except Exception as exc:  # noqa: BLE001
        logger.exception("MusicGen inference failed for job %d", job_id)
        async with async_session_maker() as session:
            row = await session.get(BgmGenerationJob, job_id)
            if row is not None:
                row.status = f"failed:{type(exc).__name__}"
                row.error = str(exc)
                row.completed_at = datetime.now(UTC)
                await session.commit()
        summary["status"] = f"failed:{type(exc).__name__}"
        summary["error"] = str(exc)
        return summary

    # Phase 3 — mark done + flip Project.bgm_path so the next render
    # picks up the new track without an extra select-library step.
    async with async_session_maker() as session:
        row = await session.get(BgmGenerationJob, job_id)
        if row is not None:
            row.status = "done"
            row.output_path = str(result.output_path)
            row.completed_at = datetime.now(UTC)
        project = (
            await session.execute(select(Project).where(Project.id == project_id))
        ).scalar_one_or_none()
        if project is not None:
            project.bgm_path = str(result.output_path)
        await session.commit()

    elapsed = time.monotonic() - started_ms
    logger.info(
        "bgm gen job %d done in %.1fs → %s",
        job_id,
        elapsed,
        result.output_path,
    )
    summary["status"] = "done"
    summary["output_path"] = str(result.output_path)
    summary["elapsed_s"] = elapsed
    return summary


def generate_bgm(job_id: int) -> dict[str, Any]:
    """RQ job entry point — generate BGM for a queued
    ``BgmGenerationJob`` and update the row.
    """
    return asyncio.run(_run(job_id))


__all__ = ["generate_bgm"]
