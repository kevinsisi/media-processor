"""Thin RQ-enqueue helper used by the API to schedule analysis jobs.

The API container does NOT import worker code (which would pull in
faster-whisper + OpenCV); it just enqueues a Redis message that names the
target function by string. The worker container resolves the function on
dequeue.
"""

from __future__ import annotations

import logging
from typing import Any

from redis import Redis
from rq import Queue

from media_processor.api.config import settings
from media_processor.workers import ANALYSIS_QUEUE, EDITING_QUEUE

logger = logging.getLogger(__name__)

JOB_TIMEOUT_SECONDS = 60 * 60 * 2  # 2 h ceiling for the whole pipeline.
EDIT_JOB_TIMEOUT_SECONDS = 60 * 60  # 1 h ceiling for the M5 render pipeline.
ANALYZE_ASSET_FN = "media_processor.workers.analysis_jobs.analyze_asset"
RENDER_DRAFT_FN = "media_processor.workers.edit_jobs.render_draft"


def _redis() -> Redis:
    return Redis.from_url(settings.redis_url)


def enqueue_asset_analysis(
    asset_id: int,
    *,
    steps: list[str] | None = None,
    force: bool = False,
) -> str:
    """Schedule ``analyze_asset(asset_id, steps=…, force=…)`` on the analysis queue.

    Returns the RQ job id. The job target is referenced by string so the
    api container never imports the worker module (which transitively pulls
    in faster-whisper / OpenCV on the worker side only).
    """

    queue = Queue(ANALYSIS_QUEUE, connection=_redis(), default_timeout=JOB_TIMEOUT_SECONDS)
    job_kwargs: dict[str, Any] = {"steps": steps, "force": force}
    job = queue.enqueue(ANALYZE_ASSET_FN, args=(asset_id,), kwargs=job_kwargs)
    logger.info(
        "enqueued analyze_asset(asset_id=%d, steps=%s, force=%s) as job %s",
        asset_id,
        steps if steps is not None else "all",
        force,
        job.id,
    )
    return job.id


def enqueue_project_edit(
    project_id: int,
    *,
    force: bool = False,
    target_duration_ms: int | None = None,
) -> str:
    """Schedule ``render_draft(project_id, force=..., target_duration_ms=...)``.

    Returns the RQ job id. Like :func:`enqueue_asset_analysis`, the job target
    is referenced by string so the api container never imports the worker
    code path that pulls in ffmpeg-heavy modules. ``target_duration_ms`` is
    the user-supplied override from POST /projects/{id}/edit; ``None`` lets
    the orchestrator pick a length from the source material.
    """
    queue = Queue(EDITING_QUEUE, connection=_redis(), default_timeout=EDIT_JOB_TIMEOUT_SECONDS)
    job_kwargs: dict[str, Any] = {"force": force}
    if target_duration_ms is not None:
        job_kwargs["target_duration_ms"] = target_duration_ms
    job = queue.enqueue(
        RENDER_DRAFT_FN,
        args=(project_id,),
        kwargs=job_kwargs,
    )
    logger.info(
        "enqueued render_draft(project_id=%d, force=%s, target_duration_ms=%s) as job %s",
        project_id,
        force,
        target_duration_ms,
        job.id,
    )
    return job.id
