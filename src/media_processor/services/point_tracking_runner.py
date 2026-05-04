"""v0.28.0 — async orchestrator for the worker-side point-tracking job.

Pre-0.28 the LK loop ran inside the API endpoint via
``asyncio.to_thread(track_point, ...)``. Long / high-resolution
assets blew past nginx's 60-second proxy timeout and the FE got
stuck on "追蹤中…". v0.27.3's 30-second cooperative budget gave a
clean error but didn't help operators who needed the tracking to
actually complete on a 1728x3072 / 2-min portrait clip.

This runner is the worker-side orchestrator that:

  1. Reads the operator's intent off the job args (norm coords +
     init frame timestamp).
  2. Calls ``services.point_tracking.track_point`` with **no time
     budget** — RQ's ``default_timeout`` (set in
     ``services.queue.POINT_TRACKING_JOB_TIMEOUT_SECONDS``) is the
     only ceiling.
  3. On success, writes ``Asset.point_tracking_json`` +
     ``Asset.point_tracking_origin`` (with cv2-resolved x/y pixels)
     and flips ``point_tracking_status`` to ``"done"``.
  4. On any exception (OpenCV missing, bad file, malformed video),
     catches and writes ``status="failed"`` + ``error=<reason>`` so
     the FE polling sees a terminal state instead of looping
     forever.

Mirrors ``services.analysis.run_pipeline``'s shape so RQ's job
target is a thin sync function (``workers/point_tracking_jobs.py``)
that just runs ``asyncio.run(run_point_tracking(...))``.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from sqlalchemy import select

from media_processor.core.db import async_session_maker
from media_processor.models import Asset
from media_processor.services import point_tracking as point_tracking_svc

logger = logging.getLogger(__name__)


async def run_point_tracking(
    asset_id: int,
    *,
    init_norm_x: float,
    init_norm_y: float,
    init_t_ms: int,
) -> dict[str, Any]:
    """Run LK pixel tracking for one asset and persist the result.

    Returns a small summary for RQ's job-result store. The API
    polls ``Asset`` state to learn whether the trace landed; this
    return value is only useful for ``rq info`` / debugging.
    """
    logger.info(
        "run_point_tracking: asset_id=%d norm=(%.4f, %.4f) init_t_ms=%d",
        asset_id,
        init_norm_x,
        init_norm_y,
        init_t_ms,
    )

    async with async_session_maker() as session:
        asset = (
            await session.execute(select(Asset).where(Asset.id == asset_id))
        ).scalar_one_or_none()
        if asset is None:
            logger.warning("run_point_tracking: asset %d not found", asset_id)
            return {"asset_id": asset_id, "status": "missing"}
        media_path = Path(asset.file_path)
        duration_ms = asset.duration_ms

    # The cv2 work happens outside the DB session — we don't want to
    # hold a transaction open for the duration of a multi-minute LK
    # walk. ``track_point`` is sync and does its own cv2 lifecycle.
    try:
        # No ``time_budget_s`` — the worker has no nginx in front of
        # it and RQ's default_timeout (set on enqueue) is the only
        # ceiling. The 30-second budget v0.27.3 added is still the
        # default value of ``MAX_LK_DURATION_S`` for backwards-compat
        # callers; we explicitly opt out by passing ``None`` ... no,
        # passing ``None`` falls back to the default. Pass a very
        # large finite number instead so a corrupt cv2 read still
        # gets an eventual bail-out (defence in depth) but a normal
        # 5-minute clip completes.
        point_json = point_tracking_svc.track_point(
            media_path,
            init_norm_x=init_norm_x,
            init_norm_y=init_norm_y,
            init_t_ms=init_t_ms,
            duration_ms=duration_ms,
            time_budget_s=60 * 60,  # 1 h hard ceiling, far above RQ's 30 min job timeout.
        )
    except (
        point_tracking_svc.PointTrackError,
        point_tracking_svc.PointTrackUnavailableError,
    ) as exc:
        logger.exception("run_point_tracking: asset %d failed", asset_id)
        async with async_session_maker() as session:
            row = await session.get(Asset, asset_id)
            if row is not None:
                row.point_tracking_status = "failed"
                row.point_tracking_error = f"{type(exc).__name__}: {exc}"
                await session.commit()
        return {
            "asset_id": asset_id,
            "status": "failed",
            "error": f"{type(exc).__name__}: {exc}",
        }
    except Exception as exc:  # noqa: BLE001 — last-resort guard.
        # Defensive catch: anything we didn't anticipate (cv2
        # segfault wrapped as RuntimeError, OOM, file-IO race, etc.)
        # still flips the row to failed so the FE doesn't poll
        # forever.
        logger.exception("run_point_tracking: asset %d unexpected failure", asset_id)
        async with async_session_maker() as session:
            row = await session.get(Asset, asset_id)
            if row is not None:
                row.point_tracking_status = "failed"
                row.point_tracking_error = (
                    f"unexpected {type(exc).__name__}: {exc}"
                )
                await session.commit()
        return {
            "asset_id": asset_id,
            "status": "failed",
            "error": f"unexpected {type(exc).__name__}: {exc}",
        }

    # Success — persist the trace + origin click. ``init.x`` / ``y``
    # are the pixel coords cv2 resolved from the operator's norm
    # click using POST-rotation frame dims; mirror them into
    # ``point_tracking_origin`` so the FE crosshair lines up with the
    # thumbnail (same logic the v0.27 sync endpoint had).
    async with async_session_maker() as session:
        row = await session.get(Asset, asset_id)
        if row is None:
            logger.warning(
                "run_point_tracking: asset %d disappeared mid-job", asset_id
            )
            return {"asset_id": asset_id, "status": "missing_after"}
        row.point_tracking_json = point_json
        row.point_tracking_origin = {
            "x": int(point_json["init"]["x"]),
            "y": int(point_json["init"]["y"]),
            "frame_ms": int(init_t_ms),
            "norm_x": float(init_norm_x),
            "norm_y": float(init_norm_y),
        }
        row.point_tracking_status = "done"
        row.point_tracking_error = None
        # ``tracked_object_index`` is set to -4 by the API endpoint
        # at enqueue time, so we don't touch it here.
        await session.commit()

    return {
        "asset_id": asset_id,
        "status": "done",
        "sampled_frames": int(point_json.get("sampled_frames", 0)),
    }


__all__ = ["run_point_tracking"]
