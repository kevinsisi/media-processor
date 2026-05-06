"""v0.25.1 — periodic orphan-Draft watchdog.

A Draft row in ``status in ('pending', 'processing')`` should always
have a matching RQ job somewhere — either still queued or live in
the StartedJobRegistry. When the worker crashes, hits its job
timeout, or is manually purged (``rq purge`` / Redis flush), the row
becomes an orphan: the FE polls forever waiting on a ghost.

This watchdog runs in the API process (started from the lifespan
hook in ``api.main``) and sweeps every ``WATCHDOG_INTERVAL_S`` seconds:

  1. Find every Draft with ``status in ('pending', 'processing')``.
  2. For each, ``services.queue.has_draft_render_job(draft.id)``
     — scan the editing queue + StartedJobRegistry for any RQ job
     whose ``kwargs["draft_id"]`` matches.
  3. If the job is missing AND ``render_retry_count < 3``,
     re-enqueue the render and increment the counter.
  4. If the job is missing AND ``render_retry_count >= 3``, flip
     the row to ``failed`` with a "watchdog: retries exhausted"
     ``prompt_feedback`` so the FE surfaces a real "請重新提交"
     prompt instead of a frozen progress bar.
  5. The counter resets to 0 every time the user explicitly
     triggers a fresh render (initial trigger / re-render /
     reorder / rebuild-subtitles), so an unrelated future failure
     gets the full three-strike budget.

The watchdog runs at startup AND on the timer so a Draft that was
orphaned during a crash / restart cycle gets handled within seconds
of API boot, not 60 s later.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from media_processor.core.db import async_session_maker
from media_processor.models import (
    Asset,
    AssetStatus,
    BgmGenerationJob,
    Draft,
    DraftExport,
    DraftStatus,
)
from media_processor.services.queue import (
    enqueue_project_edit,
    has_asset_analysis_job,
    has_asset_analysis_step_job,
    has_bgm_generation_job,
    has_draft_export_job,
    has_draft_render_job,
    has_point_tracking_job,
)

logger = logging.getLogger(__name__)


def _has_active_analysis_step(steps: dict[str, Any]) -> bool:
    return any(value in ("pending", "running") for value in steps.values())


# Sweep cadence. 60 s is a comfortable trade-off between "watchdog
# notices the orphan within a minute of the worker dying" and "we
# don't hammer Redis + Postgres with a scan every few seconds for a
# system that's normally idle." If the API process is stopped, no
# sweeps run; the next API boot picks up any orphans that
# accumulated during the outage.
WATCHDOG_INTERVAL_S: float = 60.0

# Three strikes before we give up auto-retrying. Catches a single
# bad worker crash + a flaky restart, but not an infinite loop on a
# job that's deterministically broken (e.g. corrupt source asset
# that crashes ffmpeg every time).
WATCHDOG_MAX_RETRIES: int = 3

# Per-flag fallback when ``Draft.render_flags_json`` is NULL (legacy
# rows that pre-date the snapshot column). Mirrors the dict in
# ``api.routers.drafts._draft_render_flags`` — we don't import it
# to avoid the circular dep, the duplication is four lines and the
# values are stable enough to keep in sync by hand.
_LEGACY_FLAG_DEFAULTS: dict[str, bool] = {
    "transitions": False,
    "stabilize": True,
    "subtitles": True,
    "auto_reframe": True,
}


def _resolve_render_flags(draft: Draft) -> dict[str, bool]:
    """Pick the four render flags for an orphan re-enqueue.

    The watchdog has no FE override (no human is supplying fresh
    toggle state), so this is just the snapshot-or-legacy resolver.
    Mirrors ``api.routers.drafts._draft_render_flags`` minus the
    override branch.
    """
    snapshot = draft.render_flags_json if isinstance(draft.render_flags_json, dict) else {}
    return {
        key: bool(snapshot[key]) if key in snapshot else default
        for key, default in _LEGACY_FLAG_DEFAULTS.items()
    }


async def _sweep_once() -> None:
    """One pass over all in-flight drafts.

    Runs DB IO in a single session per sweep. The RQ scan
    (``has_draft_render_job``) is sync + Redis-bound, so we wrap
    each call in ``asyncio.to_thread`` to keep the API event loop
    responsive when the queue is deep.
    """
    async with async_session_maker() as session:
        stmt = select(Draft).where(
            Draft.status.in_(
                (
                    DraftStatus.PENDING.value,
                    DraftStatus.PROCESSING.value,
                )
            )
        )
        rows = (await session.execute(stmt)).scalars().all()

        for draft in rows:
            try:
                exists = await asyncio.to_thread(has_draft_render_job, draft.id)
            except Exception:  # noqa: BLE001 — fail open on Redis errors.
                logger.warning(
                    "watchdog: has_draft_render_job(draft_id=%d) raised; skipping this tick",
                    draft.id,
                )
                continue
            if exists:
                continue
            await _handle_orphan(session, draft)

        await _sweep_exports(session)
        await _sweep_bgm_jobs(session)
        await _sweep_point_tracking(session)
        await _sweep_asset_analysis(session)
        await session.commit()


async def _handle_orphan(session: Any, draft: Draft) -> None:
    """One orphan: re-enqueue if under the retry budget, else fail.

    Note ``session.commit()`` is the caller's responsibility — we
    just dirty the row. Re-enqueue itself happens via
    ``enqueue_project_edit`` which does a synchronous Redis write;
    we offload it to a thread for the same event-loop reason as the
    Redis scan.
    """
    if draft.render_retry_count >= WATCHDOG_MAX_RETRIES:
        # Three strikes — give up.
        if draft.status == DraftStatus.FAILED.value:
            return  # Already marked, nothing to do.
        logger.warning(
            "watchdog: draft %d exhausted %d auto-retries; marking failed",
            draft.id,
            WATCHDOG_MAX_RETRIES,
        )
        draft.status = DraftStatus.FAILED.value
        draft.prompt_feedback = (
            f"watchdog: retries exhausted after {WATCHDOG_MAX_RETRIES} "
            "auto-resubmits — the underlying render keeps disappearing "
            "from the queue (worker crashing, timing out, or being purged)"
        )
        return

    # Under the retry budget — bump the counter and re-enqueue.
    flags = _resolve_render_flags(draft)
    # When the cut plan is already on the row we can skip the
    # planning stage on retry; same for subtitle cues. Both paths
    # match what ``POST /drafts/{id}/re-render`` does for a manual
    # re-render trigger.
    skip_plan = bool(draft.cut_plan_json)
    subtitles_from_db = skip_plan and flags["subtitles"]

    previous_status = draft.status
    previous_progress = (
        dict(draft.progress_steps_json) if isinstance(draft.progress_steps_json, dict) else None
    )
    previous_feedback = draft.prompt_feedback
    new_retry = (draft.render_retry_count or 0) + 1
    draft.render_retry_count = new_retry
    draft.status = DraftStatus.PENDING.value
    draft.progress_steps_json = {}
    draft.prompt_feedback = (
        f"watchdog: auto-retry {new_retry}/{WATCHDOG_MAX_RETRIES} — "
        "previous RQ job vanished (worker crash / timeout / purge)"
    )
    # Commit the row update before enqueue so a fast worker never sees
    # the old non-pending state and rejects the retry as stale.
    await session.commit()

    try:
        await asyncio.to_thread(
            enqueue_project_edit,
            draft.project_id,
            draft_id=draft.id,
            force=True,
            skip_plan=skip_plan,
            subtitles_from_db=subtitles_from_db,
            transitions=flags["transitions"],
            stabilize=flags["stabilize"],
            subtitles=flags["subtitles"],
            auto_reframe=flags["auto_reframe"],
            style_preset=str(draft.style_preset or "custom"),
        )
    except Exception:  # noqa: BLE001 — Redis enqueue failed; revert.
        logger.exception(
            "watchdog: re-enqueue for draft %d failed; rolling counter back",
            draft.id,
        )
        # Roll the counter back so the next sweep retries; otherwise
        # a transient Redis blip would consume retries without an
        # actual attempt landing.
        draft.render_retry_count = new_retry - 1
        draft.status = previous_status
        draft.progress_steps_json = previous_progress
        draft.prompt_feedback = previous_feedback
        await session.commit()
        return

    # Warning level so the message surfaces under uvicorn's default
    # logger threshold — an orphan auto-resubmit IS an unusual event
    # an operator needs to see in `docker logs`, even if it's
    # technically a recovery rather than a failure.
    logger.warning(
        "watchdog: draft %d auto-resubmitted (retry %d/%d)",
        draft.id,
        new_retry,
        WATCHDOG_MAX_RETRIES,
    )


async def _sweep_exports(session: Any) -> None:
    rows = (
        await session.execute(
            select(DraftExport).where(DraftExport.status.in_(("queued", "running")))
        )
    ).scalars()
    now = datetime.now(UTC)
    for artifact in rows:
        exists = await asyncio.to_thread(
            has_draft_export_job,
            artifact.id,
            job_id=artifact.job_id,
        )
        if exists:
            continue
        artifact.status = "failed"
        artifact.completed_at = now
        artifact.error = "watchdog: export job vanished from queue; please retry export"
        logger.warning("watchdog: export artifact %d marked failed (missing RQ job)", artifact.id)


async def _sweep_bgm_jobs(session: Any) -> None:
    rows = (
        await session.execute(
            select(BgmGenerationJob).where(BgmGenerationJob.status.in_(("pending", "running")))
        )
    ).scalars()
    now = datetime.now(UTC)
    for job in rows:
        exists = await asyncio.to_thread(
            has_bgm_generation_job,
            job.id,
            rq_job_id=job.rq_job_id,
        )
        if exists:
            continue
        job.status = "failed:orphaned"
        job.error = "watchdog: BGM job vanished from queue; please retry generation"
        job.completed_at = now
        logger.warning("watchdog: bgm job %d marked failed (missing RQ job)", job.id)


async def _sweep_point_tracking(session: Any) -> None:
    rows = (
        await session.execute(select(Asset).where(Asset.point_tracking_status == "pending"))
    ).scalars()
    for asset in rows:
        origin = (
            asset.point_tracking_origin if isinstance(asset.point_tracking_origin, dict) else {}
        )
        try:
            exists = await asyncio.to_thread(
                has_point_tracking_job,
                asset.id,
                init_norm_x=float(origin["norm_x"]),
                init_norm_y=float(origin["norm_y"]),
                init_t_ms=int(origin["frame_ms"]),
            )
        except (KeyError, TypeError, ValueError):
            exists = await asyncio.to_thread(has_point_tracking_job, asset.id)
        if exists:
            continue
        asset.point_tracking_status = "failed"
        asset.point_tracking_error = (
            "watchdog: point tracking job vanished from queue; please retry tracking"
        )
        logger.warning("watchdog: point tracking asset %d marked failed", asset.id)


async def _sweep_asset_analysis(session: Any) -> None:
    rows = (
        await session.execute(select(Asset).where(Asset.status == AssetStatus.ANALYZING.value))
    ).scalars()
    for asset in rows:
        exists = await asyncio.to_thread(has_asset_analysis_job, asset.id)
        if exists:
            steps = dict(asset.analysis_steps_json or {})
            failed_any = False
            for key, value in list(steps.items()):
                if value not in ("pending", "running"):
                    continue
                step_exists = await asyncio.to_thread(has_asset_analysis_step_job, asset.id, key)
                if step_exists:
                    continue
                steps[key] = "failed:watchdog-orphaned"
                failed_any = True
            if not failed_any:
                continue
            asset.analysis_steps_json = steps
            asset.status = (
                AssetStatus.ANALYZING.value
                if _has_active_analysis_step(steps)
                else AssetStatus.ANALYSIS_FAILED.value
            )
            logger.warning("watchdog: analysis asset %d has orphaned steps", asset.id)
            continue
        steps = dict(asset.analysis_steps_json or {})
        for key, value in list(steps.items()):
            if value in ("pending", "running"):
                steps[key] = "failed:watchdog-orphaned"
        asset.analysis_steps_json = steps
        asset.status = (
            AssetStatus.ANALYZING.value
            if _has_active_analysis_step(steps)
            else AssetStatus.ANALYSIS_FAILED.value
        )
        logger.warning("watchdog: analysis asset %d marked failed (missing RQ job)", asset.id)


async def watchdog_loop() -> None:
    """Long-running task: sweep at startup, then every interval.

    Spawned from the FastAPI lifespan in ``api.main``. Cancellation
    bubbles out via ``CancelledError`` so the lifespan teardown can
    wait for the in-flight sweep to finish cleanly.
    """
    # Warning so it shows in the default uvicorn log output once at
    # startup; future "starting again" events under a restart loop
    # would also be visible.
    logger.warning(
        "watchdog: starting orphan sweep loop (interval=%.0fs, max_retries=%d)",
        WATCHDOG_INTERVAL_S,
        WATCHDOG_MAX_RETRIES,
    )
    while True:
        try:
            await _sweep_once()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — never crash the loop.
            logger.exception("watchdog: sweep raised; continuing")
        try:
            await asyncio.sleep(WATCHDOG_INTERVAL_S)
        except asyncio.CancelledError:
            raise
