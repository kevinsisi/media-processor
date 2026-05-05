"""v0.28.0 — RQ job entry point for pixel-precise point tracking.

Pre-0.28 the LK loop ran synchronously inside the API endpoint. On a
1728x3072 portrait clip @ 30 fps for 2 minutes that's ~3600 forward
+ several hundred backward LK iterations decoding 5.3 MP frames each;
v0.27.3 added a 30-second budget that bailed with a 504, but the
operator who clicked the "precise pixel tracking" button had
explicitly chosen this mode and was unhappy with a fallback.

v0.28.0 hands the loop off to the analysis worker. The endpoint
enqueues, sets ``Asset.point_tracking_status = 'pending'``, and
returns; the FE polls until ``status`` flips to ``"done"`` or
``"failed"``. The runner uses a large defensive wall-clock budget, while
RQ's ``default_timeout`` (set in ``services.queue``) remains the practical
deployment ceiling.

The worker writes back to ``Asset``:

  * ``point_tracking_json`` — the LK trace consumed by
    ``services.auto_reframe.compute_crop_path_from_point_track``.
  * ``point_tracking_origin`` — the operator's click resolved to
    cv2's POST-rotation pixel coords (mirroring v0.27's pattern; the
    FE crosshair lines up with the thumbnail because both use the
    same coord space).
  * ``point_tracking_status`` — ``"done"`` on success, ``"failed"``
    on exception.
  * ``point_tracking_error`` — populated when ``status == "failed"``.

Same lazy-import pattern as ``analysis_jobs`` so the api container
doesn't pull in OpenCV just to enqueue.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)


def track_point_job(
    asset_id: int,
    *,
    init_norm_x: float,
    init_norm_y: float,
    init_t_ms: int,
) -> dict[str, Any]:
    """RQ job — run pyramidal LK pixel tracking for one asset.

    The function returns a small summary dict for RQ's job-result
    store; the API doesn't read it (it polls Asset state). On
    OpenCV / IO failure the job catches and writes ``status="failed"``
    + ``error`` to the row rather than raising — raising would
    leave the row stuck in ``status="pending"`` until the orphan
    watchdog (which currently only watches Drafts) noticed.
    """
    logger.info(
        "track_point_job: asset_id=%d norm_xy=(%.4f, %.4f) init_t_ms=%d",
        asset_id,
        init_norm_x,
        init_norm_y,
        init_t_ms,
    )

    # Local import: pulling in OpenCV inside the function keeps the
    # api container's import graph clean (this module is loaded by
    # the worker only).
    from media_processor.services.point_tracking_runner import run_point_tracking

    return asyncio.run(
        run_point_tracking(
            asset_id,
            init_norm_x=init_norm_x,
            init_norm_y=init_norm_y,
            init_t_ms=init_t_ms,
        )
    )
