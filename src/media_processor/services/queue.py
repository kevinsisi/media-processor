"""Thin RQ-enqueue helper used by the API to schedule analysis jobs.

The API container does NOT import worker code (which would pull in
faster-whisper + OpenCV); it just enqueues a Redis message that names the
target function by string. The worker container resolves the function on
dequeue.
"""

from __future__ import annotations

import logging
from contextlib import suppress
from typing import Any

from redis import Redis
from rq import Queue
from rq.command import send_stop_job_command
from rq.exceptions import InvalidJobOperation, NoSuchJobError
from rq.job import Job
from rq.registry import StartedJobRegistry

from media_processor.api.config import settings
from media_processor.workers import ANALYSIS_QUEUE, BGM_QUEUE, EDITING_QUEUE

logger = logging.getLogger(__name__)

JOB_TIMEOUT_SECONDS = 60 * 60 * 2  # 2 h ceiling for the whole pipeline.
EDIT_JOB_TIMEOUT_SECONDS = 60 * 60  # 1 h ceiling for the M5 render pipeline.
ANALYZE_ASSET_FN = "media_processor.workers.analysis_jobs.analyze_asset"
STABILIZE_ASSET_FN = "media_processor.workers.asset_stabilization_jobs.stabilize_asset"
TRANSLATE_ASSET_FN = "media_processor.workers.analysis_jobs.translate_asset_subtitle"
TRANSLATE_JOB_TIMEOUT_SECONDS = 60 * 30  # 30 min — single Whisper pass
RENDER_DRAFT_FN = "media_processor.workers.edit_jobs.render_draft"
EXPORT_DRAFT_FN = "media_processor.workers.edit_jobs.export_draft"
EXPORT_JOB_TIMEOUT_SECONDS = 60 * 30  # 30 min — single ffmpeg pass
GENERATE_BGM_FN = "media_processor.workers.bgm_jobs.generate_bgm"
BGM_JOB_TIMEOUT_SECONDS = 60 * 15  # 15 min — small MusicGen + IO

# v0.28.0 — pixel-precise LK point tracking, async on the analysis
# queue. 30 min ceiling: even a 1728x3072 / 5-min portrait clip
# completes in single-digit minutes on the AMD 3700X, so 30 min is
# generous belt-and-suspenders against a corrupt cv2 read. The
# runner itself passes ``time_budget_s = 1 h`` to ``track_point``
# as a defence-in-depth ceiling; whichever fires first wins.
TRACK_POINT_FN = "media_processor.workers.point_tracking_jobs.track_point_job"
TRACK_POINT_JOB_TIMEOUT_SECONDS = 60 * 30  # 30 min
STABILIZE_ASSET_JOB_TIMEOUT_SECONDS = 60 * 60  # 1 h — full-source vidstab pass


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


def enqueue_asset_stabilization(asset_id: int, *, force: bool = False) -> str:
    """Schedule source-level stabilization for one asset on the analysis queue."""

    queue = Queue(
        ANALYSIS_QUEUE,
        connection=_redis(),
        default_timeout=STABILIZE_ASSET_JOB_TIMEOUT_SECONDS,
    )
    job = queue.enqueue(STABILIZE_ASSET_FN, args=(asset_id,), kwargs={"force": force})
    logger.info(
        "enqueued stabilize_asset(asset_id=%d, force=%s) as job %s",
        asset_id,
        force,
        job.id,
    )
    return job.id


def enqueue_asset_translate(asset_id: int, *, lang: str = "en") -> str:
    """Schedule ``translate_asset_subtitle(asset_id, lang=…)`` on the analysis queue.

    Runs on the same worker as STT (shares the loaded faster-whisper
    model) so we re-use the analysis queue rather than carve out a new
    one. Returns the RQ job id.
    """
    queue = Queue(
        ANALYSIS_QUEUE,
        connection=_redis(),
        default_timeout=TRANSLATE_JOB_TIMEOUT_SECONDS,
    )
    job = queue.enqueue(TRANSLATE_ASSET_FN, args=(asset_id,), kwargs={"lang": lang})
    logger.info(
        "enqueued translate_asset_subtitle(asset_id=%d, lang=%s) as job %s",
        asset_id,
        lang,
        job.id,
    )
    return job.id


def enqueue_project_edit(
    project_id: int,
    *,
    draft_id: int,
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
) -> str:
    """Schedule ``render_draft(project_id, draft_id=…, force=…, target_duration_ms=…)``.

    The API endpoint creates the Draft row up-front (so the response can carry
    a real draft id and the UI can start polling immediately) and hands the id
    to the worker — the worker no longer creates its own row.

    M7 added two flags:
      * ``skip_plan`` — re-use the stored ``cut_plan_json`` instead of running
        the Gemini planner. Used for the timeline-reorder re-render path.
      * ``subtitles_from_db`` — load subtitles from ``subtitle_cues`` rows
        instead of regenerating from transcripts. Used for the manual-edit
        re-burn path. ``skip_plan`` is generally also set when this is true.

    Returns the RQ job id. Like :func:`enqueue_asset_analysis`, the job target
    is referenced by string so the api container never imports the worker
    code path that pulls in ffmpeg-heavy modules. ``target_duration_ms`` is
    the user-supplied override from POST /projects/{id}/edit; ``None`` lets
    the orchestrator pick a length from the source material.
    """
    queue = Queue(EDITING_QUEUE, connection=_redis(), default_timeout=EDIT_JOB_TIMEOUT_SECONDS)
    job_kwargs: dict[str, Any] = {"draft_id": draft_id, "force": force}
    if target_duration_ms is not None:
        job_kwargs["target_duration_ms"] = target_duration_ms
    if skip_plan:
        job_kwargs["skip_plan"] = True
    if subtitles_from_db:
        job_kwargs["subtitles_from_db"] = True
    # stabilize / subtitles / transitions all default to True both here
    # and in run_render, so we only explicitly pass them when the caller
    # opted out — keeps the kwargs blob minimal for legacy job records.
    if not stabilize:
        job_kwargs["stabilize"] = False
    if not subtitles:
        job_kwargs["subtitles"] = False
    if not transitions:
        job_kwargs["transitions"] = False
    if not auto_reframe:
        job_kwargs["auto_reframe"] = False
    if initial_voice_volume != 1.0:
        job_kwargs["initial_voice_volume"] = float(initial_voice_volume)
    # v0.30.0 — opt-in smart camera. ``None`` = "use the project
    # toggle"; ``True``/``False`` = explicit override for this run.
    # Only emit on the wire when caller actually had an opinion so
    # legacy job records stay readable.
    if smart_camera is not None:
        job_kwargs["smart_camera"] = bool(smart_camera)
    # Only emit style_preset on the wire when it differs from the default
    # so legacy job-record dumps stay readable.
    if style_preset and style_preset != "custom":
        job_kwargs["style_preset"] = style_preset
    job = queue.enqueue(
        RENDER_DRAFT_FN,
        args=(project_id,),
        kwargs=job_kwargs,
    )
    logger.info(
        "enqueued render_draft(project_id=%d, draft_id=%d, force=%s, skip_plan=%s, "
        "subtitles_from_db=%s, stabilize=%s, subtitles=%s, transitions=%s, "
        "auto_reframe=%s, initial_voice_volume=%s, smart_camera=%s, "
        "style_preset=%s, target_duration_ms=%s) "
        "as job %s",
        project_id,
        draft_id,
        force,
        skip_plan,
        subtitles_from_db,
        stabilize,
        subtitles,
        transitions,
        auto_reframe,
        initial_voice_volume,
        smart_camera,
        style_preset,
        target_duration_ms,
        job.id,
    )
    return job.id


def enqueue_draft_export(
    draft_id: int,
    *,
    export_id: int | None = None,
    aspect: str,
    height: int,
) -> str:
    """Schedule ``export_draft(draft_id, aspect, height)`` on the editing queue.

    The export is a pure-ffmpeg derivative of the existing v{N}.mp4. When
    ``export_id`` is passed, the worker updates that durable artifact row
    so the UI can show queued/running/done/failed after refresh.
    """
    queue = Queue(EDITING_QUEUE, connection=_redis(), default_timeout=EXPORT_JOB_TIMEOUT_SECONDS)
    job_kwargs: dict[str, Any] = {"aspect": aspect, "height": height}
    if export_id is not None:
        job_kwargs["export_id"] = export_id
    job = queue.enqueue(
        EXPORT_DRAFT_FN,
        args=(draft_id,),
        kwargs=job_kwargs,
    )
    logger.info(
        "enqueued export_draft(draft_id=%d, export_id=%s, aspect=%s, height=%d) as job %s",
        draft_id,
        export_id,
        aspect,
        height,
        job.id,
    )
    return job.id


def enqueue_bgm_generation(job_id: int) -> str:
    """Schedule ``generate_bgm(job_id)`` on the bgm queue.

    Returns the RQ job id so the api can write it back to the
    ``BgmGenerationJob.rq_job_id`` column for cancel / inspection.
    """
    queue = Queue(BGM_QUEUE, connection=_redis(), default_timeout=BGM_JOB_TIMEOUT_SECONDS)
    job = queue.enqueue(GENERATE_BGM_FN, args=(job_id,))
    logger.info("enqueued generate_bgm(job_id=%d) as rq job %s", job_id, job.id)
    return job.id


def enqueue_point_tracking(
    asset_id: int,
    *,
    init_norm_x: float,
    init_norm_y: float,
    init_t_ms: int,
) -> str:
    """Schedule ``track_point_job(asset_id, ...)`` on the analysis queue.

    Returns the RQ job id. The endpoint writes
    ``Asset.point_tracking_status = 'pending'`` *before* calling this
    so the FE polling sees the in-flight state immediately on the
    next ``GET /assets/{id}/tracking``.

    v0.28.0 — pre-0.28 the LK loop ran inside the API's threadpool
    via ``asyncio.to_thread``; long / high-res clips blew past
    nginx's 60-second proxy timeout. Now the loop runs on
    ``worker-analysis``, which has no proxy in front of it.
    """
    queue = Queue(
        ANALYSIS_QUEUE,
        connection=_redis(),
        default_timeout=TRACK_POINT_JOB_TIMEOUT_SECONDS,
    )
    job = queue.enqueue(
        TRACK_POINT_FN,
        args=(asset_id,),
        kwargs={
            "init_norm_x": init_norm_x,
            "init_norm_y": init_norm_y,
            "init_t_ms": init_t_ms,
        },
    )
    logger.info(
        "enqueued track_point_job(asset_id=%d, norm=(%.4f, %.4f), init_t_ms=%d) as job %s",
        asset_id,
        init_norm_x,
        init_norm_y,
        init_t_ms,
        job.id,
    )
    return job.id


def _job_matches(
    job: Job,
    *,
    func_name: str,
    args_prefix: tuple[Any, ...] = (),
    kwargs: dict[str, Any] | None = None,
    job_id: str | None = None,
) -> bool:
    if job_id is not None and job.id == job_id:
        return True
    if (job.func_name or "") != func_name:
        return False
    job_args = tuple(job.args or ())
    if args_prefix and job_args[: len(args_prefix)] != args_prefix:
        return False
    job_kwargs = dict(job.kwargs or {})
    return all(job_kwargs.get(key) == value for key, value in (kwargs or {}).items())


def has_matching_job(
    queue_name: str,
    *,
    func_name: str,
    args_prefix: tuple[Any, ...] = (),
    kwargs: dict[str, Any] | None = None,
    job_id: str | None = None,
    exclude_job_id: str | None = None,
) -> bool:
    """Return True if a queued or started RQ job matches the durable intent.

    Redis errors fail open: callers use this from watchdog/reconciliation
    paths, and a transient Redis hiccup must not mark durable DB rows failed.
    """
    try:
        redis = _redis()
    except Exception:  # noqa: BLE001 — Redis unreachable; fail open.
        logger.warning("has_matching_job: Redis unreachable; assuming job exists")
        return True

    try:
        queue = Queue(queue_name, connection=redis)
        for queued_job_id in queue.get_job_ids():
            if queued_job_id == exclude_job_id:
                continue
            try:
                job = Job.fetch(queued_job_id, connection=redis)
            except NoSuchJobError:
                continue
            if _job_matches(
                job,
                func_name=func_name,
                args_prefix=args_prefix,
                kwargs=kwargs,
                job_id=job_id,
            ):
                return True

        registry = StartedJobRegistry(queue_name, connection=redis)
        for started_job_id in registry.get_job_ids():
            if started_job_id == exclude_job_id:
                continue
            try:
                job = Job.fetch(started_job_id, connection=redis)
            except NoSuchJobError:
                continue
            if _job_matches(
                job,
                func_name=func_name,
                args_prefix=args_prefix,
                kwargs=kwargs,
                job_id=job_id,
            ):
                return True
    except Exception:  # noqa: BLE001 — same fail-open as above.
        logger.warning(
            "has_matching_job(queue=%s, func=%s) Redis scan failed; assuming job exists",
            queue_name,
            func_name,
        )
        return True

    return False


def has_draft_render_job(draft_id: int, *, exclude_job_id: str | None = None) -> bool:
    """Return True iff some RQ job tied to ``draft_id`` is still queued
    or running on the editing queue.

    v0.25.1 — used by ``GET /drafts/{id}`` to detect orphan rows: if
    a Draft is still flagged ``pending`` / ``processing`` in the DB
    but no matching RQ job exists, the work-horse died (timeout,
    crash, manual ``rq purge``, etc.) and the FE will poll forever
    waiting on a ghost. The reader marks the row failed on the next
    GET so the operator gets a real "請重新提交" prompt.

    Mirrors the queue + registry scan from ``cancel_draft_render`` —
    same correctness guarantee (we look in BOTH places because a job
    might be queued OR running). Returns False on any Redis error so
    the caller fails open (won't mark a draft failed just because
    Redis hiccuped).
    """
    return has_matching_job(
        EDITING_QUEUE,
        func_name=RENDER_DRAFT_FN,
        kwargs={"draft_id": draft_id},
        exclude_job_id=exclude_job_id,
    )


def has_draft_export_job(
    export_id: int, *, job_id: str | None = None, exclude_job_id: str | None = None
) -> bool:
    return has_matching_job(
        EDITING_QUEUE,
        func_name=EXPORT_DRAFT_FN,
        kwargs={"export_id": export_id},
        job_id=job_id,
        exclude_job_id=exclude_job_id,
    )


def has_bgm_generation_job(
    job_id: int, *, rq_job_id: str | None = None, exclude_job_id: str | None = None
) -> bool:
    return has_matching_job(
        BGM_QUEUE,
        func_name=GENERATE_BGM_FN,
        args_prefix=(job_id,),
        job_id=rq_job_id,
        exclude_job_id=exclude_job_id,
    )


def has_point_tracking_job(
    asset_id: int,
    *,
    init_norm_x: float | None = None,
    init_norm_y: float | None = None,
    init_t_ms: int | None = None,
    exclude_job_id: str | None = None,
) -> bool:
    kwargs: dict[str, Any] = {}
    if init_norm_x is not None and init_norm_y is not None and init_t_ms is not None:
        kwargs = {
            "init_norm_x": init_norm_x,
            "init_norm_y": init_norm_y,
            "init_t_ms": init_t_ms,
        }
    return has_matching_job(
        ANALYSIS_QUEUE,
        func_name=TRACK_POINT_FN,
        args_prefix=(asset_id,),
        kwargs=kwargs,
        exclude_job_id=exclude_job_id,
    )


def has_asset_analysis_job(asset_id: int, *, exclude_job_id: str | None = None) -> bool:
    return has_matching_job(
        ANALYSIS_QUEUE,
        func_name=ANALYZE_ASSET_FN,
        args_prefix=(asset_id,),
        exclude_job_id=exclude_job_id,
    )


def has_asset_analysis_step_job(
    asset_id: int, step: str, *, exclude_job_id: str | None = None
) -> bool:
    """Return True when an analyze_asset job still owns ``step`` for asset.

    ``steps=None`` means a full analysis run, so it covers every step. A
    partial rerun only covers the explicit step names in its kwargs.
    """
    try:
        redis = _redis()
    except Exception:  # noqa: BLE001 — Redis unreachable; fail open.
        logger.warning("has_asset_analysis_step_job: Redis unreachable; assuming job exists")
        return True

    try:
        queue = Queue(ANALYSIS_QUEUE, connection=redis)
        registry = StartedJobRegistry(ANALYSIS_QUEUE, connection=redis)
        job_ids = list(queue.get_job_ids()) + list(registry.get_job_ids())
        for job_id in job_ids:
            if job_id == exclude_job_id:
                continue
            try:
                job = Job.fetch(job_id, connection=redis)
            except NoSuchJobError:
                continue
            if (job.func_name or "") != ANALYZE_ASSET_FN:
                continue
            if tuple(job.args or ())[:1] != (asset_id,):
                continue
            steps = job.kwargs.get("steps")
            if steps is None or step in set(steps):
                return True
    except Exception:  # noqa: BLE001 — same fail-open as above.
        logger.warning(
            "has_asset_analysis_step_job(asset_id=%d, step=%s) Redis scan failed; assuming job exists",
            asset_id,
            step,
        )
        return True

    return False


def cancel_draft_render(draft_id: int) -> bool:
    """Find the editing job rendering ``draft_id`` and cancel/stop it.

    Looks in both the queue (not yet picked up) and the StartedJobRegistry
    (worker is running it). For pending jobs we call ``Job.cancel`` so RQ
    drops it; for running jobs we additionally
    ``send_stop_job_command`` so the worker raises StopRequested in the
    work-horse, killing the in-flight ffmpeg subprocess.

    Returns True if any matching job was found, False otherwise. Does not
    touch the Draft row — the caller updates DB state.
    """
    redis = _redis()
    found = False

    # Pending: still in the queue, never picked up.
    queue = Queue(EDITING_QUEUE, connection=redis)
    for job_id in queue.get_job_ids():
        try:
            job = Job.fetch(job_id, connection=redis)
        except NoSuchJobError:
            continue
        if job.kwargs.get("draft_id") == draft_id:
            with suppress(InvalidJobOperation):
                job.cancel()
            logger.info("cancelled queued render_draft job %s for draft_id=%d", job_id, draft_id)
            found = True

    # Running: the work-horse is mid-render.
    registry = StartedJobRegistry(EDITING_QUEUE, connection=redis)
    for job_id in registry.get_job_ids():
        try:
            job = Job.fetch(job_id, connection=redis)
        except NoSuchJobError:
            continue
        if job.kwargs.get("draft_id") == draft_id:
            with suppress(InvalidJobOperation, NoSuchJobError):
                send_stop_job_command(redis, job_id)
            logger.info(
                "sent stop signal to running render_draft job %s for draft_id=%d", job_id, draft_id
            )
            found = True

    return found
