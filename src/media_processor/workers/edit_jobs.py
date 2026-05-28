"""RQ job entry point for M5 auto-edit (`render_draft`) and M7 export.

The render job orchestrates: Gemini cut planning → Draft + DraftSegment
writes → ffmpeg cut/concat → SRT build + subtitle burn-in → BGM mix.
Each stage updates ``Draft.progress_steps_json`` so the polling UI can
show progress in real time.

The export job is a pure-ffmpeg derivative: scale + crop the existing
v{N}.mp4 to a different aspect / height. No DB state is mutated.

Mirrors the M4 ``analysis_jobs`` pattern: RQ runs sync, the orchestrator
is async, so this module owns the asyncio.run() boundary and keeps RQ
unaware of the SQLAlchemy session.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def render_draft(
    project_id: int,
    *,
    draft_id: int | None = None,
    force: bool = False,
    target_duration_ms: int | None = None,
    skip_plan: bool = False,
    subtitles_from_db: bool = False,
    stabilize: bool = True,
    subtitles: bool = True,
    transitions: bool = False,
    auto_reframe: bool = True,
    initial_voice_volume: float = 1.0,
    smart_camera: bool | None = None,
    style_preset: str = "custom",
    edit_mode: str = "standard",
) -> dict[str, Any]:
    """RQ job — produce the next-version draft mp4 for ``project_id``.

    The API pre-creates the Draft row and passes ``draft_id`` so the UI can
    poll progress without racing the worker. ``draft_id=None`` is kept as a
    fallback for older enqueues / tooling that still drives the orchestrator
    directly; in that case the orchestrator creates the row itself.

    M7 added two re-render fast paths:
      * ``skip_plan`` — load the plan from ``Draft.cut_plan_json`` instead
        of re-running Gemini. Used by the timeline reorder endpoint.
      * ``subtitles_from_db`` — render the SRT from ``subtitle_cues``
        rows (user-edited text) instead of regenerating from transcripts.
        Used by the subtitle re-burn endpoint. ``skip_plan`` should also
        be true here (the plan didn't change).

    v0.14.3 added ``stabilize`` (default ``True``) for the two-pass
    vidstab digital stabilization stage between cut and concat.

    The return value is a small summary dict so RQ persists something
    debuggable. All status persistence lives in Postgres on the Draft
    row; this dict is for ops, not the UI.
    """
    logger.info(
        "render_draft: project_id=%d draft_id=%s force=%s target_duration_ms=%s "
        "skip_plan=%s subtitles_from_db=%s stabilize=%s subtitles=%s transitions=%s "
        "auto_reframe=%s initial_voice_volume=%s smart_camera=%s style_preset=%s edit_mode=%s",
        project_id,
        draft_id,
        force,
        target_duration_ms,
        skip_plan,
        subtitles_from_db,
        stabilize,
        subtitles,
        transitions,
        auto_reframe,
        initial_voice_volume,
        smart_camera,
        style_preset,
        edit_mode,
    )
    # Local import keeps the api container free of ffmpeg / heavy deps.
    from media_processor.services.edit_orchestrator import run_render

    return asyncio.run(
        run_render(
            project_id,
            draft_id=draft_id,
            force=force,
            target_duration_ms=target_duration_ms,
            skip_plan=skip_plan,
            subtitles_from_db=subtitles_from_db,
            stabilize=stabilize,
            # Renamed at the orchestrator boundary to avoid colliding with
            # the ``subtitles`` module the orchestrator already imports.
            subtitles_enabled=subtitles,
            transitions_enabled=transitions,
            auto_reframe_enabled=auto_reframe,
            initial_voice_volume=initial_voice_volume,
            smart_camera_enabled=smart_camera,
            style_preset=style_preset,
            edit_mode=edit_mode,
        )
    )


def export_draft(
    draft_id: int,
    *,
    aspect: str,
    height: int,
    export_id: int | None = None,
) -> dict[str, Any]:
    """RQ job — produce a derivative mp4 in the given aspect / height.

    Reads the existing ``v{N}.mp4`` deliverable (rendered by ``render_draft``)
    and runs ``services.exports.export_render`` to produce
    ``v{N}-{aspect}-{height}p.mp4`` next to it. The original is never
    overwritten so multiple aspect / height combos can co-exist.

    Returns ``{"draft_id", "output_path", "width", "height", "aspect"}`` on
    success; raises ``ExportError`` on bad input or ffmpeg failure.
    """
    logger.info(
        "export_draft: draft_id=%d export_id=%s aspect=%s height=%d",
        draft_id,
        export_id,
        aspect,
        height,
    )
    # Local imports keep the api container free of heavy deps.
    from sqlalchemy import select

    from media_processor.api.config import settings
    from media_processor.core.db import async_session_maker
    from media_processor.models import Draft, DraftExport
    from media_processor.services import exports

    async def _mark_export(
        status: str,
        *,
        output_path: Path | None = None,
        error: str | None = None,
    ) -> bool:
        if export_id is None:
            return True
        async with async_session_maker() as session:
            artifact = await session.get(DraftExport, export_id)
            if artifact is None:
                logger.warning("export_draft: export_id=%d not found", export_id)
                return False
            now = datetime.now(UTC)
            if (
                artifact.draft_id != draft_id
                or artifact.aspect != aspect
                or artifact.height != height
            ):
                artifact.status = "failed"
                artifact.completed_at = now
                artifact.error = "export intent mismatch; job ignored"
                await session.commit()
                logger.warning(
                    "export_draft: export_id=%d intent mismatch; job draft/aspect/height=%s/%s/%s",
                    export_id,
                    draft_id,
                    aspect,
                    height,
                )
                return False
            if artifact.status not in ("queued", "running"):
                logger.info(
                    "export_draft: export_id=%d already terminal (%s); skipping",
                    export_id,
                    artifact.status,
                )
                return False
            artifact.status = status
            if status == "running":
                artifact.started_at = now
                artifact.error = None
            elif status == "done":
                artifact.output_path = str(output_path) if output_path is not None else None
                artifact.completed_at = now
                artifact.error = None
            elif status == "failed":
                artifact.completed_at = now
                artifact.error = (error or "export failed")[:2000]
            await session.commit()
            return True

    async def _resolve_paths() -> tuple[Path, Path]:
        async with async_session_maker() as session:
            draft = (
                await session.execute(select(Draft).where(Draft.id == draft_id))
            ).scalar_one_or_none()
            if draft is None:
                raise exports.ExportError(f"draft {draft_id} not found")
            base = Path(settings.drafts_dir) / str(draft.project_id)
            input_path = base / f"v{draft.version}.mp4"
            output_path = base / exports.derive_filename(draft.version, aspect, height)
            return input_path, output_path

    try:
        if not asyncio.run(_mark_export("running")):
            return {"draft_id": draft_id, "export_id": export_id, "status": "skipped"}
        input_path, output_path = asyncio.run(_resolve_paths())
        result = exports.export_render(
            input_path,
            output_path,
            aspect=aspect,
            height=height,
        )
        asyncio.run(_mark_export("done", output_path=result.output_path))
    except Exception as exc:
        try:
            asyncio.run(_mark_export("failed", error=str(exc)))
        except Exception:  # noqa: BLE001 — don't hide the real export failure.
            logger.exception("export_draft: failed to mark export_id=%s failed", export_id)
        raise
    return {
        "draft_id": draft_id,
        "export_id": export_id,
        "output_path": str(result.output_path),
        "width": result.width,
        "height": result.height,
        "aspect": result.aspect,
    }


def _scratch_dir() -> Path:
    """Test seam — overridden in unit tests to point at a temp dir."""
    from media_processor.api.config import settings

    return Path(settings.analysis_dir) / "edits"


__all__ = ["export_draft", "render_draft"]
