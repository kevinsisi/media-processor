"""v0.23 — pixel-precise point tracking via pyramidal Lucas-Kanade.

Operators click a single pixel on a frame thumbnail (e.g. the centre
of an alloy wheel) and ``track_point()`` walks the Lucas-Kanade
optical flow forward + backward through the whole asset, recording
the point's position on every output frame. ``services.auto_reframe``
then keeps that pixel centred in the rendered crop — the After Effects
"point tracker" workflow without leaving the browser.

Differences vs. the v0.16 YOLO tracker and v0.17 CSRT custom ROI:

  * Resolution: full source fps (typically 24-60), not the 5 Hz
    sub-sampled cadence YOLO uses. Pixel-precise crop drift is more
    visible than bbox-precise drift, so we need the Kalman filter
    to see a measurement on every output frame rather than
    interpolating between sparse YOLO samples.
  * No bbox: only a centre point. Auto-reframe synthesises a 1×1
    "bbox" around the point so ``compute_crop_path``'s existing
    centre-of-bbox math works unchanged.
  * Robustness: LK can lose a point on fast motion, occlusion, or
    a sudden lighting change. We track forward, fall back to the
    last good position when LK reports ``status==0`` or
    ``err > MAX_ERR``, and emit ``lost=True`` on those frames so
    the operator can see in the UI which ranges were guessed.

``TRACKING_FAKE=1`` is the same test seam ``object_tracking.detect``
uses — emits a deterministic stub trace (constant point at the
init_xy) so CI / dev hosts without OpenCV can still exercise the
plumbing.
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any, cast

logger = logging.getLogger(__name__)


# v0.27.3 — cooperative wall-clock budget for the LK loop.
#
# track_point runs at FULL source fps, doing cv2.read + calcOpticalFlowPyrLK
# per frame. On a portrait 1728x3072 clip at 30 fps for 2 min that's
# ~3600 forward frames + however many backward, decoding 5.3 MP frames
# each — well past any sensible browser / proxy timeout. We had at
# least one operator report "stuck on 追蹤中..." with no visible error.
# Pre-0.27.3 the loop ran until done with no abort path, so the API
# kept grinding on the threadpool while the FE's fetch had long
# given up.
#
# 30 s is the budget. ~120 frames/s on a typical 1080p clip means
# ~3600 frames fit comfortably; longer / larger clips bail with a
# PointTrackTimeoutError that the endpoint translates into a 504
# the FE shows as a toast. The check fires every BUDGET_CHECK_EVERY
# frames so the per-iteration cost is a single time.monotonic()
# call amortised across 30 LK steps.
MAX_LK_DURATION_S: float = 30.0
BUDGET_CHECK_EVERY: int = 30


# Pyramidal Lucas-Kanade parameters. The OpenCV defaults are tuned
# for sparse feature points on noisy real-world video; these match
# what After Effects' point tracker uses by default. The window has
# to be big enough to encompass enough texture for the gradient to
# be unambiguous (a 5×5 window over a flat car door is hopeless),
# and the pyramid depth lets us track motion that's bigger than the
# window between frames.
LK_WIN_SIZE: tuple[int, int] = (21, 21)
LK_MAX_LEVEL: int = 3
# Termination criteria for the iterative LK solver. 30 iterations or
# ε=0.01 px — the cv2 sample value, OK for most footage.
LK_MAX_ITER: int = 30
LK_EPSILON: float = 0.01
# Per-frame error threshold above which we declare the point lost
# and freeze. ``calcOpticalFlowPyrLK`` returns the L1 patch error
# (sum of absolute differences) — values above ~50 in the cv2 sample
# space (uint8 grayscale) are typically a tracking failure.
LK_MAX_ERR: float = 50.0


def _is_fake() -> bool:
    return os.environ.get("TRACKING_FAKE", "0") == "1"


def _fake_point_track_result(
    *,
    src_w: int,
    src_h: int,
    fps: float,
    duration_ms: int,
    init_x: int,
    init_y: int,
    init_t_ms: int,
) -> dict[str, Any]:
    """Deterministic stub for the FAKE path — emits the init point as
    a static position at the source fps. Sufficient for CI / non-
    OpenCV dev boxes to exercise the persistence + render path."""
    interval_ms = max(1, int(1000 / max(fps, 1.0)))
    n = max(1, int(duration_ms / interval_ms))
    frames = [
        {
            "t_ms": init_t_ms + i * interval_ms,
            "x": float(init_x),
            "y": float(init_y),
            "lost": False,
        }
        for i in range(n)
    ]
    return {
        "src_w": src_w,
        "src_h": src_h,
        "fps": fps,
        "init_t_ms": int(init_t_ms),
        "init": {"x": int(init_x), "y": int(init_y)},
        "frames": frames,
        "sampled_frames": len(frames),
    }


class PointTrackError(RuntimeError):
    """LK / OpenCV failure during point tracking."""


class PointTrackUnavailableError(PointTrackError):
    """OpenCV is not importable on this host."""


class PointTrackTimeoutError(PointTrackError):
    """The LK loop exceeded its wall-clock budget.

    Raised cooperatively from inside the forward / backward passes
    so a stuck request returns a 504 to the FE instead of grinding
    in the API's threadpool until the operator gives up. The
    partial trace is discarded — auto-reframe wants a measurement
    on every frame, so half a trace is more dangerous than no
    trace.
    """


def track_point(
    media_path: Path,
    *,
    init_norm_x: float,
    init_norm_y: float,
    init_t_ms: int = 0,
    duration_ms: int | None = None,
    time_budget_s: float | None = None,
) -> dict[str, Any]:
    """Track a single pixel through the video using LK optical flow.

    ``init_norm_x`` / ``init_norm_y`` are 0..1 normalised click
    coordinates on the DISPLAYED (i.e. post-rotation, what the user
    sees on the thumbnail) frame at ``init_t_ms``. We resolve the
    pixel coords here from ``cv2``'s frame dimensions, NOT from
    ``Asset.resolution`` — because for assets with rotation metadata
    (e.g. iPhone / DJI portrait clips that store landscape frames +
    a ``rotate=90`` tag), ``Asset.resolution`` is the raw stream
    dimensions while the thumbnail is rotated to display orientation.
    Multiplying ``norm × Asset.resolution`` for those assets seeds LK
    at the wrong pixel (e.g. norm 0.48 on a portrait thumbnail picked
    up by ``norm * 3840 = 1848`` and clamped into a 2160-wide cv2
    frame ends up at 86 % across instead of 48 %). cv2 with
    ``CAP_PROP_ORIENTATION_AUTO=1`` (the OpenCV 4.13 default) reads
    the post-rotation frame, so its width/height match the
    thumbnail's coord space — single source of truth.

    Returns a JSON-friendly dict suitable for
    ``Asset.point_tracking_json``::

        {
          "src_w": int, "src_h": int, "fps": float,
          "init_t_ms": int, "init": {"x": int, "y": int},
          "frames": [{"t_ms": int, "x": float, "y": float, "lost": bool}],
          "sampled_frames": int,
        }

    ``init.x``/``init.y`` are the resolved pixel coordinates so the
    caller (the API endpoint) can mirror them back into
    ``Asset.point_tracking_origin`` for the post-commit crosshair
    without re-doing the resolution lookup.

    The frames cover the WHOLE asset starting from t=0 — when the
    user clicks at e.g. t=3.5 s, we still record positions for frames
    0..3.5 s by running LK BACKWARD from the init frame, then forward
    from the init frame to the end. This way auto_reframe has a
    measurement on every output frame regardless of where the operator
    happened to click.
    """
    if _is_fake():
        # FAKE path: resolve against a default display resolution so the
        # stub's init.x/init.y come out somewhere sensible.
        fake_src_w, fake_src_h = 1920, 1080
        fake_init_x = int(round(init_norm_x * fake_src_w))
        fake_init_y = int(round(init_norm_y * fake_src_h))
        return _fake_point_track_result(
            src_w=fake_src_w,
            src_h=fake_src_h,
            fps=30.0,
            duration_ms=duration_ms or 5_000,
            init_x=fake_init_x,
            init_y=fake_init_y,
            init_t_ms=init_t_ms,
        )

    try:
        import cv2
        import numpy as np
    except ImportError as exc:  # pragma: no cover
        raise PointTrackUnavailableError(f"opencv missing: {exc}") from exc

    cap = cv2.VideoCapture(str(media_path))
    if not cap.isOpened():
        raise PointTrackError(f"OpenCV could not open {media_path}")
    src_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    src_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 30.0)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    duration_ms = duration_ms or int(total_frames / max(1.0, fps) * 1000)
    interval_ms = 1000.0 / max(fps, 1.0)

    # v0.27.3 — cooperative wall-clock budget. Raised inside the
    # forward / backward loops every BUDGET_CHECK_EVERY frames so a
    # 1728x3072@60fps / 2 min asset (which would otherwise grind for
    # several minutes on the api threadpool) bails fast with a 504-
    # mappable error rather than a silently-stuck FE spinner.
    budget_s = MAX_LK_DURATION_S if time_budget_s is None else float(time_budget_s)
    started_at = time.monotonic()

    # Resolve pixel coords from cv2's POST-rotation dimensions. This
    # is the only place the seed→pixel mapping happens; no other layer
    # reads Asset.resolution for tracking purposes.
    init_x = int(round(init_norm_x * src_w))
    init_y = int(round(init_norm_y * src_h))
    init_x = max(0, min(src_w - 1, init_x))
    init_y = max(0, min(src_h - 1, init_y))

    lk_params = {
        "winSize": LK_WIN_SIZE,
        "maxLevel": LK_MAX_LEVEL,
        "criteria": (
            cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT,
            LK_MAX_ITER,
            LK_EPSILON,
        ),
    }
    calc_lk = cast(Any, cv2.calcOpticalFlowPyrLK)

    def _grayscale(frame: Any) -> Any:
        return cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

    # Two passes: BACKWARD from init to t=0, then FORWARD from init
    # to the end. We collect each pass into its own list and stitch
    # them so the returned frames array is sorted by t_ms ascending
    # without a re-sort at the end.
    backward: list[dict[str, Any]] = []
    forward: list[dict[str, Any]] = []

    def _check_budget() -> None:
        """Raise PointTrackTimeoutError if the wall-clock has run past
        ``budget_s``. Called every BUDGET_CHECK_EVERY frames so the
        time.monotonic() cost is amortised; the partial trace gathered
        so far is discarded."""
        elapsed = time.monotonic() - started_at
        if elapsed > budget_s:
            raise PointTrackTimeoutError(
                f"point tracking exceeded {budget_s:.0f} s budget after "
                f"{len(forward) + len(backward)} frames "
                f"(asset {src_w}x{src_h} @ {fps:.1f} fps, "
                f"{(duration_ms or 0) / 1000:.1f} s) — try a shorter "
                "clip or pick a point with stronger texture"
            )

    try:
        # ---- Seek to init frame ----
        cap.set(cv2.CAP_PROP_POS_MSEC, float(init_t_ms))
        ok, init_frame = cap.read()
        if not ok or init_frame is None:
            raise PointTrackError(f"could not seek to init_t_ms={init_t_ms} in {media_path}")
        init_gray = _grayscale(init_frame)
        init_pt = np.array([[[float(init_x), float(init_y)]]], dtype=np.float32)

        forward.append(
            {
                "t_ms": int(init_t_ms),
                "x": float(init_x),
                "y": float(init_y),
                "lost": False,
            }
        )

        # ---- Forward pass: init → end ----
        prev_gray = init_gray
        prev_pt = init_pt
        last_x, last_y = float(init_x), float(init_y)
        ts = init_t_ms + interval_ms
        while ts < duration_ms:
            ok, frame = cap.read()
            if not ok or frame is None:
                break
            curr_gray = _grayscale(frame)
            curr_pt, status, err = calc_lk(prev_gray, curr_gray, prev_pt, None, **lk_params)
            lost = (
                curr_pt is None
                or status is None
                or int(status[0][0]) == 0
                or (err is not None and float(err[0][0]) > LK_MAX_ERR)
            )
            if lost:
                # Freeze on the last good position; keep iterating so
                # auto_reframe still has a per-frame measurement.
                forward.append(
                    {
                        "t_ms": int(ts),
                        "x": last_x,
                        "y": last_y,
                        "lost": True,
                    }
                )
            else:
                last_x = float(curr_pt[0][0][0])
                last_y = float(curr_pt[0][0][1])
                last_x = max(0.0, min(float(src_w - 1), last_x))
                last_y = max(0.0, min(float(src_h - 1), last_y))
                forward.append(
                    {
                        "t_ms": int(ts),
                        "x": last_x,
                        "y": last_y,
                        "lost": False,
                    }
                )
                prev_pt = curr_pt
            prev_gray = curr_gray
            ts += interval_ms
            if len(forward) % BUDGET_CHECK_EVERY == 0:
                _check_budget()

        # ---- Backward pass: init → 0 ----
        # Re-seek to init then walk backward by reading consecutive
        # frames at decreasing timestamps. OpenCV's ``CAP_PROP_POS_MSEC``
        # seek is not random-access on H.264 files (it stops at the
        # nearest keyframe). For our purposes — the init click is
        # usually near t=0 — re-seeking per-frame is acceptable on
        # the typical 10-30 s asset.
        prev_gray = init_gray
        prev_pt = init_pt
        last_x, last_y = float(init_x), float(init_y)
        ts = init_t_ms - interval_ms
        while ts >= 0:
            cap.set(cv2.CAP_PROP_POS_MSEC, float(ts))
            ok, frame = cap.read()
            if not ok or frame is None:
                break
            curr_gray = _grayscale(frame)
            curr_pt, status, err = calc_lk(prev_gray, curr_gray, prev_pt, None, **lk_params)
            lost = (
                curr_pt is None
                or status is None
                or int(status[0][0]) == 0
                or (err is not None and float(err[0][0]) > LK_MAX_ERR)
            )
            if lost:
                backward.append(
                    {
                        "t_ms": int(ts),
                        "x": last_x,
                        "y": last_y,
                        "lost": True,
                    }
                )
            else:
                last_x = float(curr_pt[0][0][0])
                last_y = float(curr_pt[0][0][1])
                last_x = max(0.0, min(float(src_w - 1), last_x))
                last_y = max(0.0, min(float(src_h - 1), last_y))
                backward.append(
                    {
                        "t_ms": int(ts),
                        "x": last_x,
                        "y": last_y,
                        "lost": False,
                    }
                )
                prev_pt = curr_pt
            prev_gray = curr_gray
            ts -= interval_ms
            if len(backward) % BUDGET_CHECK_EVERY == 0:
                _check_budget()
    finally:
        cap.release()

    # Stitch: backward was collected from init → 0 (decreasing), so
    # reverse it before prepending to forward.
    backward.reverse()
    frames = backward + forward
    return {
        "src_w": src_w,
        "src_h": src_h,
        "fps": fps,
        "init_t_ms": int(init_t_ms),
        "init": {"x": int(init_x), "y": int(init_y)},
        "frames": frames,
        "sampled_frames": len(frames),
    }


__all__ = [
    "BUDGET_CHECK_EVERY",
    "LK_MAX_ERR",
    "LK_MAX_ITER",
    "LK_MAX_LEVEL",
    "LK_WIN_SIZE",
    "MAX_LK_DURATION_S",
    "PointTrackError",
    "PointTrackTimeoutError",
    "PointTrackUnavailableError",
    "track_point",
]
