"""RQ job entry point for M5 auto-edit (`render_draft`).

The job orchestrates: Gemini cut planning → Draft + DraftSegment writes →
ffmpeg cut/concat → SRT build + subtitle burn-in. Each stage updates
``Draft.progress_steps_json`` so the polling UI can show progress in
real time.

Mirrors the M4 ``analysis_jobs`` pattern: RQ runs sync, the orchestrator
is async, so this module owns the asyncio.run() boundary and keeps RQ
unaware of the SQLAlchemy session.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def render_draft(
    project_id: int,
    *,
    draft_id: int | None = None,
    force: bool = False,
    target_duration_ms: int | None = None,
) -> dict[str, Any]:
    """RQ job — produce the next-version draft mp4 for ``project_id``.

    The API pre-creates the Draft row and passes ``draft_id`` so the UI can
    poll progress without racing the worker. ``draft_id=None`` is kept as a
    fallback for older enqueues / tooling that still drives the orchestrator
    directly; in that case the orchestrator creates the row itself.

    The return value is a small summary dict so RQ persists something
    debuggable. All status persistence lives in Postgres on the Draft
    row; this dict is for ops, not the UI. ``target_duration_ms`` is the
    user-configurable target render length; ``None`` means the
    orchestrator picks one from the source material.
    """
    logger.info(
        "render_draft: project_id=%d draft_id=%s force=%s target_duration_ms=%s",
        project_id,
        draft_id,
        force,
        target_duration_ms,
    )
    # Local import keeps the api container free of ffmpeg / heavy deps.
    from media_processor.services.edit_orchestrator import run_render

    return asyncio.run(
        run_render(
            project_id,
            draft_id=draft_id,
            force=force,
            target_duration_ms=target_duration_ms,
        )
    )


def _scratch_dir() -> Path:
    """Test seam — overridden in unit tests to point at a temp dir."""
    from media_processor.api.config import settings

    return Path(settings.analysis_dir) / "edits"


__all__ = ["render_draft"]
