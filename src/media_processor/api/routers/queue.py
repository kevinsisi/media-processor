"""v0.25.0 — RQ queue inspector + queued-job cancellation.

Operator pain point: when a render is "排隊中" the only feedback is the
status string itself. The new endpoints surface what the worker is
actually doing, how deep the line is, and let the operator drop a
queued job that's no longer wanted.

The worker container is single-process (one job at a time across all
three queues), so the response always has at most one ``running`` job.

* ``GET  /queue/status``  — current running + ordered queued list.
* ``DELETE /queue/jobs/{job_id}`` — cancel a queued job. 409s on a
  job that's already running (cancelling a live render needs the
  in-flight ffmpeg to be killed and the Draft row reset; that's the
  job of ``cancel_draft_render`` via ``POST /drafts/{id}/cancel``,
  not a generic queue cancel).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict
from redis import Redis
from rq import Queue
from rq.exceptions import InvalidJobOperation, NoSuchJobError
from rq.job import Job
from rq.registry import StartedJobRegistry
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from media_processor.api.config import settings
from media_processor.api.deps import get_session
from media_processor.models import Asset, Draft, Project
from media_processor.workers import ANALYSIS_QUEUE, BGM_QUEUE, EDITING_QUEUE

router = APIRouter(prefix="/queue", tags=["queue"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]

# Maps the canonical RQ ``func_name`` strings to the operator-facing
# job kind. The ``func_name`` itself is fully qualified
# (``media_processor.workers.edit_jobs.render_draft``) and not what we
# want to show in zh-Hant text labels.
_JOB_KIND_BY_FUNC: dict[str, str] = {
    "media_processor.workers.analysis_jobs.analyze_asset": "analyze",
    "media_processor.workers.analysis_jobs.translate_asset_subtitle": "translate",
    "media_processor.workers.edit_jobs.render_draft": "render",
    "media_processor.workers.edit_jobs.export_draft": "export",
    "media_processor.workers.bgm_jobs.generate_bgm": "bgm",
}

QueueName = Literal["analysis", "editing", "bgm"]
JobState = Literal["running", "queued"]


class QueueJobItem(BaseModel):
    """One row in the queue inspector's response.

    Surfaces the minimum a UI needs to render "X 的 Y（已等 N 分鐘）"
    plus the job_id so the cancel button knows what to target.
    """

    model_config = ConfigDict(from_attributes=True)

    job_id: str
    queue: QueueName
    kind: str  # analyze / translate / render / export / bgm / unknown
    state: JobState
    # Position in the queue (0 = head). ``None`` for running jobs.
    position: int | None = None
    enqueued_at: datetime | None = None
    started_at: datetime | None = None
    elapsed_s: float | None = None
    # Best-effort entity context. The FE renders these into a label
    # like "{project_name} 的 {kind}"; missing fields fall back to
    # the job_id alone.
    project_id: int | None = None
    project_name: str | None = None
    asset_id: int | None = None
    draft_id: int | None = None


class QueueStatusOut(BaseModel):
    """Response for ``GET /queue/status``.

    ``running`` is the list of jobs currently held by some worker's
    StartedJobRegistry. v0.27.0 widened this from a single optional
    item to a list because the multi-worker compose runs five
    concurrent processes (1 analysis + 3 editing + 1 bgm) so up to
    five jobs can be live at once. Pre-0.27 single-worker deploys
    return a list with at most one entry.

    ``queued`` is in dispatch order across all three queues, walking
    ``analysis → editing → bgm`` in the worker pickup order. The
    multi-worker setup means a queued job may actually start on any
    free worker for that queue — ``position`` still reflects "you're
    Nth in line on your queue", which is what the operator cares
    about.
    """

    running: list[QueueJobItem]
    queued: list[QueueJobItem]


# ---- helpers ----


def _redis() -> Redis:
    return Redis.from_url(settings.redis_url)


def _job_kind(job: Job) -> str:
    return _JOB_KIND_BY_FUNC.get(job.func_name or "", "unknown")


def _job_to_item(
    job: Job,
    queue_name: QueueName,
    state: JobState,
    *,
    position: int | None = None,
) -> QueueJobItem:
    """Pull the human-readable + entity-link fields off an RQ Job.

    Always returns a valid item; missing data shows up as ``None``.
    The entity ids are read off the job's args / kwargs, mirroring
    the call signatures in ``services.queue``:

      * ``analyze_asset(asset_id, ...)``
      * ``translate_asset_subtitle(asset_id, ...)``
      * ``render_draft(project_id, draft_id=…, ...)``
      * ``export_draft(draft_id, aspect=…, height=…)``
      * ``generate_bgm(bgm_job_id)``
    """
    kind = _job_kind(job)
    args = list(job.args or ())
    kwargs = dict(job.kwargs or {})

    asset_id: int | None = None
    draft_id: int | None = None
    project_id: int | None = None

    if kind in ("analyze", "translate"):
        if args:
            asset_id = int(args[0])
    elif kind == "render":
        if args:
            project_id = int(args[0])
        if "draft_id" in kwargs:
            draft_id = int(kwargs["draft_id"])
    elif kind == "export":
        if args:
            draft_id = int(args[0])

    elapsed_s: float | None = None
    if state == "running" and job.started_at is not None:
        # ``started_at`` is naive UTC in newer rq; normalise to aware.
        started = job.started_at
        if started.tzinfo is None:
            started = started.replace(tzinfo=UTC)
        elapsed_s = max(0.0, (datetime.now(UTC) - started).total_seconds())

    return QueueJobItem(
        job_id=job.id,
        queue=queue_name,
        kind=kind,
        state=state,
        position=position,
        enqueued_at=job.enqueued_at,
        started_at=job.started_at,
        elapsed_s=elapsed_s,
        project_id=project_id,
        asset_id=asset_id,
        draft_id=draft_id,
    )


async def _resolve_project_links(
    session: AsyncSession,
    items: list[QueueJobItem],
) -> None:
    """Mutate ``items`` to fill in ``project_id`` / ``project_name``.

    Asset-bound jobs (analyze / translate) carry only ``asset_id``;
    draft-bound jobs (export) carry only ``draft_id``. We look up
    the missing ``project_id`` in one query each, then pull the
    distinct project names in one final batch. All three lookups
    short-circuit when the relevant id list is empty so the typical
    "no jobs" response stays single-query free.
    """
    asset_ids = {it.asset_id for it in items if it.asset_id is not None}
    draft_ids = {it.draft_id for it in items if it.draft_id is not None}

    asset_to_project: dict[int, int] = {}
    if asset_ids:
        rows = await session.execute(
            select(Asset.id, Asset.project_id).where(Asset.id.in_(asset_ids))
        )
        asset_to_project = {row[0]: row[1] for row in rows.all()}

    draft_to_project: dict[int, int] = {}
    if draft_ids:
        rows = await session.execute(
            select(Draft.id, Draft.project_id).where(Draft.id.in_(draft_ids))
        )
        draft_to_project = {row[0]: row[1] for row in rows.all()}

    # Backfill project_id from asset / draft links.
    for it in items:
        if it.project_id is None:
            if it.asset_id is not None:
                it.project_id = asset_to_project.get(it.asset_id)
            elif it.draft_id is not None:
                it.project_id = draft_to_project.get(it.draft_id)

    project_ids = {it.project_id for it in items if it.project_id is not None}
    if project_ids:
        rows = await session.execute(
            select(Project.id, Project.name).where(Project.id.in_(project_ids))
        )
        names = {row[0]: row[1] for row in rows.all()}
        for it in items:
            if it.project_id is not None:
                it.project_name = names.get(it.project_id)


# ---- endpoints ----


# Worker dispatch order matches the listen list in
# ``python -m media_processor.workers``: analysis first, then
# editing, then bgm. Iterate in the same order so the queued
# response's positions match the worker's pickup order.
_QUEUE_ORDER: tuple[QueueName, ...] = ("analysis", "editing", "bgm")


@router.get("/status", response_model=QueueStatusOut)
async def queue_status(session: SessionDep) -> QueueStatusOut:
    redis = _redis()

    running: list[QueueJobItem] = []
    queued: list[QueueJobItem] = []
    queued_pos = 0

    for qname in _QUEUE_ORDER:
        queue = Queue(qname, connection=redis)

        # StartedJobRegistry holds every job currently held by a
        # worker for this queue. Pre-0.27 we had ONE worker
        # listening on all three queues so only one registry was
        # ever non-empty; v0.27.0's multi-worker setup runs 1
        # analysis + 3 editing + 1 bgm processes so up to five
        # jobs may be live concurrently. Collect them all.
        registry = StartedJobRegistry(qname, connection=redis)
        for job_id in registry.get_job_ids():
            try:
                job = Job.fetch(job_id, connection=redis)
            except NoSuchJobError:
                continue
            running.append(_job_to_item(job, qname, "running"))

        for job_id in queue.get_job_ids():
            try:
                job = Job.fetch(job_id, connection=redis)
            except NoSuchJobError:
                continue
            queued.append(_job_to_item(job, qname, "queued", position=queued_pos))
            queued_pos += 1

    await _resolve_project_links(session, running + queued)
    return QueueStatusOut(running=running, queued=queued)


@router.delete("/jobs/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_queued_job(job_id: str) -> Response:
    """Drop a job from its queue.

    Only valid for jobs that haven't been picked up yet. A running
    job has a live work-horse with potentially in-flight ffmpeg /
    Whisper subprocesses; the right way to interrupt those is the
    domain-specific cancel (``POST /drafts/{id}/cancel`` for renders),
    not this generic queue-cancel.
    """
    redis = _redis()
    try:
        job = Job.fetch(job_id, connection=redis)
    except NoSuchJobError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="job not found") from exc

    if job.is_started:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "job is currently running; use the domain-specific "
                "cancel endpoint instead (e.g. POST /drafts/{id}/cancel "
                "for render jobs)"
            ),
        )

    try:
        job.cancel()
    except InvalidJobOperation as exc:
        # InvalidJobOperation surfaces when the job is in a state RQ
        # refuses to cancel (e.g. already finished). Treat as 409 so
        # the FE can refresh the list rather than 500.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"cannot cancel job in state {job.get_status()!r}",
        ) from exc

    return Response(status_code=status.HTTP_204_NO_CONTENT)
