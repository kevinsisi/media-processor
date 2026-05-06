"""v0.25.0 — RQ queue inspector + queued-job cancellation.

Operator pain point: when a render is "排隊中" the only feedback is the
status string itself. The new endpoints surface what the worker is
actually doing, how deep the line is, and let the operator drop a
queued job that's no longer wanted.

The current compose deployment runs multiple workers (1 analysis + 3 editing +
1 bgm), so the response may contain multiple ``running`` jobs.

* ``GET  /queue/status``  — current running + ordered queued list.
* ``DELETE /queue/jobs/{job_id}`` — cancel a queued job. 409s on a
  job that's already running (cancelling a live render needs the
  in-flight ffmpeg to be killed and the Draft row reset; that's the
  job of ``cancel_draft_render`` via ``POST /drafts/{id}/cancel``,
  not a generic queue cancel).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any, Literal

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
from media_processor.models import (
    Asset,
    AssetStatus,
    BgmGenerationJob,
    Draft,
    DraftExport,
    DraftStatus,
    Project,
)
from media_processor.services.queue import (
    has_asset_analysis_job,
    has_asset_analysis_step_job,
    has_bgm_generation_job,
    has_draft_export_job,
    has_draft_render_job,
    has_point_tracking_job,
)

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
    # v0.28.0 — pixel-precise LK point tracking, async on the analysis queue.
    "media_processor.workers.point_tracking_jobs.track_point_job": "point_track",
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

    if kind in ("analyze", "translate", "point_track"):
        if args:
            asset_id = int(args[0])
    elif kind == "render":
        if args:
            project_id = int(args[0])
        if "draft_id" in kwargs:
            draft_id = int(kwargs["draft_id"])
    elif kind == "export" and args:
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


async def _sync_cancelled_job(session: AsyncSession, job: Job) -> None:
    """Best-effort durable state sync for generic queued-job cancellation."""
    kind = _job_kind(job)
    args = list(job.args or ())
    kwargs = dict(job.kwargs or {})
    now = datetime.now(UTC)

    if kind == "render" and "draft_id" in kwargs:
        draft_id = int(kwargs["draft_id"])
        if has_draft_render_job(draft_id, exclude_job_id=job.id):
            return
        draft = await session.get(Draft, draft_id)
        if draft is not None and draft.status in (
            DraftStatus.PENDING.value,
            DraftStatus.PROCESSING.value,
        ):
            draft.status = DraftStatus.FAILED.value
            draft.prompt_feedback = "已被使用者取消"
    elif kind == "export" and "export_id" in kwargs:
        export_id = int(kwargs["export_id"])
        if has_draft_export_job(export_id, exclude_job_id=job.id):
            return
        artifact = await session.get(DraftExport, export_id)
        if artifact is not None and artifact.status in ("queued", "running"):
            artifact.status = "failed"
            artifact.error = "cancelled by user"
            artifact.completed_at = now
    elif kind == "bgm" and args:
        bgm_job_id = int(args[0])
        if has_bgm_generation_job(bgm_job_id, exclude_job_id=job.id):
            return
        bgm_job = await session.get(BgmGenerationJob, bgm_job_id)
        if bgm_job is not None and bgm_job.status in ("pending", "running"):
            bgm_job.status = "failed:cancelled"
            bgm_job.error = "cancelled by user"
            bgm_job.completed_at = now
    elif kind == "point_track" and args:
        asset_id = int(args[0])
        point_kwargs = dict(job.kwargs or {})
        if {"init_norm_x", "init_norm_y", "init_t_ms"}.issubset(
            point_kwargs
        ) and has_point_tracking_job(
            asset_id,
            init_norm_x=float(point_kwargs["init_norm_x"]),
            init_norm_y=float(point_kwargs["init_norm_y"]),
            init_t_ms=int(point_kwargs["init_t_ms"]),
            exclude_job_id=job.id,
        ):
            return
        asset = await session.get(Asset, asset_id)
        point_matches = True
        if {"init_norm_x", "init_norm_y", "init_t_ms"}.issubset(point_kwargs):
            origin = asset.point_tracking_origin if asset is not None else None
            point_matches = isinstance(origin, dict) and _point_job_matches_origin(
                point_kwargs,
                origin,
            )
        if (
            asset is not None
            and asset.tracked_object_index == -4
            and asset.point_tracking_status == "pending"
            and point_matches
        ):
            asset.point_tracking_status = "failed"
            asset.point_tracking_error = "cancelled by user"
    elif kind == "analyze" and args:
        asset_id = int(args[0])
        step_list = job.kwargs.get("steps")
        if (
            step_list is not None
            and has_asset_analysis_job(asset_id, exclude_job_id=job.id)
            and all(
                has_asset_analysis_step_job(asset_id, str(step), exclude_job_id=job.id)
                for step in step_list
            )
        ):
            return
        asset = await session.get(Asset, asset_id)
        if asset is not None and asset.status == AssetStatus.ANALYZING.value:
            steps = dict(asset.analysis_steps_json or {})
            cancelled_steps = set(step_list or steps.keys())
            for key, value in list(steps.items()):
                if has_asset_analysis_step_job(asset.id, key, exclude_job_id=job.id):
                    continue
                if key in cancelled_steps and value in ("pending", "running"):
                    steps[key] = "failed:cancelled"
            asset.analysis_steps_json = steps
            asset.status = (
                AssetStatus.ANALYZING.value
                if _has_active_analysis_step(steps)
                else AssetStatus.ANALYSIS_FAILED.value
            )

    await session.commit()


def _point_job_matches_origin(job_kwargs: dict[str, Any], origin: dict[str, Any]) -> bool:
    try:
        return (
            abs(float(job_kwargs["init_norm_x"]) - float(origin["norm_x"])) < 1e-9
            and abs(float(job_kwargs["init_norm_y"]) - float(origin["norm_y"])) < 1e-9
            and int(job_kwargs["init_t_ms"]) == int(origin["frame_ms"])
        )
    except (KeyError, TypeError, ValueError):
        return False


def _has_active_analysis_step(steps: dict[str, Any]) -> bool:
    return any(value in ("pending", "running") for value in steps.values())


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
async def cancel_queued_job(job_id: str, session: SessionDep) -> Response:
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

    await _sync_cancelled_job(session, job)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
