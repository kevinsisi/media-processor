"""v0.16 — Auto-reframe via Kalman-smoothed dynamic crop.

Reads ``Asset.tracking_json`` (YOLOv8 per-frame bboxes from
``services.object_tracking``) and produces a per-output-frame crop
window so the renderer keeps the dominant subject centered in the
target aspect (9:16 / 4:5 / 1:1). Two pieces:

  1. ``compute_crop_path(...)``: Kalman-smooth the bbox centers across
     time, then for every output frame interpolate a ``(crop_x,
     crop_y)`` value. The crop window dimensions are determined by the
     target aspect + the source dimensions (whichever axis is the
     limiting one stays full-resolution; the other gets cropped).

  2. ``write_sendcmd_file(...)``: emit an ffmpeg ``sendcmd`` commands
     file that retargets a tagged ``crop@reframe`` filter at the
     output frame rate. The renderer chains
     ``sendcmd=f=cmds.txt,crop@reframe=W:H:0:0,scale=…,setsar=1`` so
     the runtime crop position tracks the smoothed subject path.

Pure-Python — only ``numpy`` is required at runtime.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# Target aspect → (w, h) integer ratio. Mirrors
# ``video_renderer.ASPECT_DIMENSIONS`` but at integer-ratio level so
# the crop math stays exact regardless of source resolution.
ASPECT_RATIOS: dict[str, tuple[int, int]] = {
    "9:16": (9, 16),
    "4:5": (4, 5),
    "1:1": (1, 1),
}

# Render fps the sendcmd file emits at. Matches
# ``video_renderer.VIDEO_FPS`` so the crop position updates exactly
# once per output frame.
RENDER_FPS: int = 30

# Kalman process / measurement noise. Hand-tuned for v0.16.1 — the
# initial tuning lagged the subject so the car never re-centered after
# a quick pan; raising Q + dropping R lets the filter follow real
# motion without re-adding YOLO's frame-to-frame jitter.
# - Q (process noise): how much the subject "wants" to wander.
#   Lower → smoother but lazier; higher → tracks abrupt moves better.
# - R (measurement noise): how noisy the YOLO bbox center is. Higher
#   means we trust predictions over measurements (smoother but laggier).
KALMAN_Q: float = 120.0
KALMAN_R: float = 80.0

# Hard ceiling on how far the crop window can move per output frame.
# Prevents the camera from snapping when a 5 Hz YOLO detection
# disagrees with the smoothed prediction. v0.16.1 raised from 8 → 24
# px/frame so a passing car at highway speed isn't visibly trailing
# the crop window. 24 px/frame at 30fps ≈ 720 px/sec — well above any
# realistic phone-shoot pan rate.
MAX_DELTA_PX_PER_FRAME: float = 24.0

# v0.16.1 — fraction of the maximum target-aspect window we actually
# use for the dynamic crop. Shrinking below 1.0 zooms the subject in
# (it fills more of the output frame) AND gives the crop window slack
# to translate inside the source — the previous always-maximal window
# left no room to track the subject left/right when source already
# matched target aspect. 0.75 keeps subjects 33 % larger in the output
# while still leaving 25 % source margin to chase a moving subject.
CROP_ZOOM_FACTOR: float = 0.75


@dataclass(frozen=True)
class CropPath:
    """Computed dynamic-crop trajectory for one cut.

    ``crop_w`` × ``crop_h`` is the fixed crop window size; ``points``
    holds the per-frame ``(t_seconds, x, y)`` triples — the renderer
    serialises these into an ffmpeg sendcmd file.
    """

    crop_w: int
    crop_h: int
    src_w: int
    src_h: int
    points: list[tuple[float, int, int]]


def _crop_dimensions(
    src_w: int, src_h: int, target_aspect: str
) -> tuple[int, int]:
    """Compute the dynamic crop window for ``target_aspect`` inside
    ``(src_w, src_h)``.

    Two factors stack:
      1. The largest target-aspect window that fits inside the source
         (the geometric maximum — for a 1920×1080 → 9:16 we'd get
         606×1080).
      2. ``CROP_ZOOM_FACTOR`` (default 0.75) — shrinks below the
         maximum so the subject takes up more of the output frame AND
         the crop window has slack to slide left/right as the subject
         moves. Critical for sources that already match the target
         aspect — without the zoom there is literally no margin in
         which to translate the crop, and ``compute_crop_path`` would
         have to bail.
    """
    aw, ah = ASPECT_RATIOS[target_aspect]
    # Two candidates: keep src_w (height limited by aspect) or keep src_h.
    # Pick the one that fits — i.e. the limiting axis.
    by_width = (src_w, src_w * ah // aw)
    by_height = (src_h * aw // ah, src_h)
    if by_width[1] <= src_h:
        cw, ch = by_width
    else:
        cw, ch = by_height
    # Apply the zoom factor and round to even values; libx264 prefers
    # even widths/heights.
    cw = int(round(cw * CROP_ZOOM_FACTOR))
    ch = int(round(ch * CROP_ZOOM_FACTOR))
    cw = cw - (cw % 2)
    ch = ch - (ch % 2)
    return max(2, cw), max(2, ch)


def _kalman_smooth_1d(measurements: list[tuple[float, float]]) -> list[float]:
    """Constant-velocity 1-D Kalman over ``[(t, x), …]`` measurements.

    Returns smoothed ``x`` at each input timestamp. Process model:
    ``x_{k+1} = x_k + v_k * dt``; measurement model: ``z_k = x_k +
    noise``. Hand-tuned Q / R from the module constants.
    """
    if not measurements:
        return []
    # State: [x, v]; covariance P.
    x = measurements[0][1]
    v = 0.0
    p_xx = 100.0
    p_xv = 0.0
    p_vv = 100.0

    smoothed: list[float] = []
    last_t = measurements[0][0]
    for i, (t, z) in enumerate(measurements):
        dt = max(0.0, t - last_t) if i > 0 else 0.0
        # Predict.
        x = x + v * dt
        # P = F P F^T + Q ;  F = [[1, dt], [0, 1]]; Q diag (KALMAN_Q on velocity).
        p_xx = p_xx + 2 * dt * p_xv + dt * dt * p_vv
        p_xv = p_xv + dt * p_vv
        p_vv = p_vv + KALMAN_Q
        # Update.
        s = p_xx + KALMAN_R
        kx = p_xx / s
        kv = p_xv / s
        residual = z - x
        x = x + kx * residual
        v = v + kv * residual
        p_xx = (1 - kx) * p_xx
        p_xv = (1 - kx) * p_xv
        p_vv = p_vv - kv * p_xv
        smoothed.append(x)
        last_t = t
    return smoothed


def _interpolate(
    measurements: list[tuple[float, float]],
    target_times_s: list[float],
) -> list[float]:
    """Piecewise-linear interpolation of ``[(t, v), …]`` at
    ``target_times_s``. Clamps at endpoints. Returns the value-at-each-
    target list."""
    if not measurements:
        return [0.0] * len(target_times_s)
    out: list[float] = []
    j = 0
    for tt in target_times_s:
        while j + 1 < len(measurements) and measurements[j + 1][0] <= tt:
            j += 1
        if tt <= measurements[0][0]:
            out.append(measurements[0][1])
            continue
        if tt >= measurements[-1][0]:
            out.append(measurements[-1][1])
            continue
        t0, v0 = measurements[j]
        t1, v1 = measurements[j + 1]
        denom = (t1 - t0) or 1.0
        alpha = (tt - t0) / denom
        out.append(v0 + alpha * (v1 - v0))
    return out


def _frames_for_object(
    tracking: dict[str, Any], object_index: int | None
) -> list[dict[str, Any]]:
    """Pick the right per-frame bbox list out of ``tracking_json``.

    ``object_index = None`` (auto) returns the legacy ``frames`` field
    which is also the dominant track. ``>= 0`` returns the matching
    entry from ``tracking["tracks"]`` so the user can follow a non-
    dominant subject. Falls back to ``frames`` when the requested
    track id is missing (e.g. tracking was re-run with different
    detections after the user's pick).
    """
    if object_index is None or object_index < 0:
        return list(tracking.get("frames") or [])
    for tk in tracking.get("tracks") or []:
        if isinstance(tk, dict) and int(tk.get("object_index", -1)) == object_index:
            return list(tk.get("frames") or [])
    return list(tracking.get("frames") or [])


def compute_crop_path(
    tracking: dict[str, Any] | None,
    *,
    target_aspect: str,
    asset_start_ms: int,
    asset_end_ms: int,
    src_w: int | None = None,
    src_h: int | None = None,
    object_index: int | None = None,
) -> CropPath | None:
    """Build a Kalman-smoothed dynamic crop for the cut span.

    ``tracking`` is ``Asset.tracking_json``; when None / empty / no
    overlapping frames the function returns ``None`` and the caller
    falls back to a static centered crop. Otherwise the returned
    ``CropPath`` carries one ``(t, x, y)`` point per output frame in
    the cut's local timeline (t starts at 0 at the cut's start).

    ``object_index`` (v0.17) selects which track to follow inside a
    multi-track ``tracking`` dict. ``None`` keeps the historic
    behaviour (follow the dominant track via ``frames``).
    """
    if not tracking:
        return None
    # Source dimensions can come from the tracking blob OR the caller
    # (caller knows the actual asset resolution from ffprobe).
    sw = int(src_w if src_w is not None else tracking.get("src_w", 0))
    sh = int(src_h if src_h is not None else tracking.get("src_h", 0))
    if sw <= 0 or sh <= 0:
        return None
    if target_aspect not in ASPECT_RATIOS:
        return None

    crop_w, crop_h = _crop_dimensions(sw, sh, target_aspect)
    # ``CROP_ZOOM_FACTOR`` shrinks the crop window below the maximum
    # target-aspect rectangle, so even when source matches target
    # aspect the window is smaller than the source and has room to
    # translate around the subject. The earlier "bail when full-frame"
    # guard is no longer needed.

    frames = _frames_for_object(tracking, object_index)
    # Only frames inside the cut's [asset_start_ms, asset_end_ms) window
    # are useful. Tracking is sampled at ~5 Hz so a short cut might see
    # only 2-3 detections — Kalman handles that fine.
    span_frames = [
        f for f in frames
        if isinstance(f, dict)
        and asset_start_ms <= int(f.get("t_ms", -1)) < asset_end_ms
    ]
    if not span_frames:
        return None

    # Convert bbox top-left → center, in source pixel coords. Subtract
    # the cut's start so timestamps are local (0 at cut start).
    measurements_x: list[tuple[float, float]] = []
    measurements_y: list[tuple[float, float]] = []
    for f in span_frames:
        t_s = max(0.0, (int(f["t_ms"]) - asset_start_ms) / 1000.0)
        cx = int(f["x"]) + int(f["w"]) // 2
        cy = int(f["y"]) + int(f["h"]) // 2
        measurements_x.append((t_s, float(cx)))
        measurements_y.append((t_s, float(cy)))

    # Kalman-smooth the centers, then interpolate to RENDER_FPS so each
    # output frame gets a value.
    smoothed_x = _kalman_smooth_1d(measurements_x)
    smoothed_y = _kalman_smooth_1d(measurements_y)
    smoothed_pts_x = list(zip([t for t, _ in measurements_x], smoothed_x, strict=True))
    smoothed_pts_y = list(zip([t for t, _ in measurements_y], smoothed_y, strict=True))

    duration_s = (asset_end_ms - asset_start_ms) / 1000.0
    n_out = max(1, int(math.ceil(duration_s * RENDER_FPS)))
    target_times = [i / RENDER_FPS for i in range(n_out)]
    cx_list = _interpolate(smoothed_pts_x, target_times)
    cy_list = _interpolate(smoothed_pts_y, target_times)

    # Convert center → top-left of the crop window. Clamp inside
    # source. Apply a per-frame movement cap so any residual jitter
    # in the smoothed signal can't pop the camera around.
    half_w = crop_w / 2.0
    half_h = crop_h / 2.0
    max_x = max(0, sw - crop_w)
    max_y = max(0, sh - crop_h)
    points: list[tuple[float, int, int]] = []
    last_x: float | None = None
    last_y: float | None = None
    for t, cx, cy in zip(target_times, cx_list, cy_list, strict=True):
        target_x = max(0.0, min(float(max_x), cx - half_w))
        target_y = max(0.0, min(float(max_y), cy - half_h))
        if last_x is None or last_y is None:
            x_now = target_x
            y_now = target_y
        else:
            dx = target_x - last_x
            dy = target_y - last_y
            x_now = last_x + max(-MAX_DELTA_PX_PER_FRAME, min(MAX_DELTA_PX_PER_FRAME, dx))
            y_now = last_y + max(-MAX_DELTA_PX_PER_FRAME, min(MAX_DELTA_PX_PER_FRAME, dy))
        last_x, last_y = x_now, y_now
        points.append((t, int(round(x_now)), int(round(y_now))))

    return CropPath(
        crop_w=crop_w,
        crop_h=crop_h,
        src_w=sw,
        src_h=sh,
        points=points,
    )


def compute_crop_path_from_custom_roi(
    custom_roi: dict[str, Any] | None,
    *,
    target_aspect: str,
    asset_start_ms: int,
    asset_end_ms: int,
    src_w: int | None = None,
    src_h: int | None = None,
) -> CropPath | None:
    """Same as :func:`compute_crop_path` but using a CSRT-tracked ROI.

    ``custom_roi`` is ``Asset.custom_roi_json`` — same per-frame bbox
    shape as a single track entry, plus ``init`` / ``init_t_ms``
    metadata. We adapt to the existing crop-path code by wrapping it
    as a single-track tracking dict.
    """
    if not custom_roi:
        return None
    wrapped = {
        "src_w": custom_roi.get("src_w"),
        "src_h": custom_roi.get("src_h"),
        "fps": custom_roi.get("fps"),
        "frames": custom_roi.get("frames") or [],
    }
    return compute_crop_path(
        wrapped,
        target_aspect=target_aspect,
        asset_start_ms=asset_start_ms,
        asset_end_ms=asset_end_ms,
        src_w=src_w,
        src_h=src_h,
    )


def write_sendcmd_file(path: CropPath, out_path: Path) -> Path:
    """Serialise a :class:`CropPath` to an ffmpeg sendcmd commands file.

    Format documented at https://ffmpeg.org/ffmpeg-filters.html#sendcmd
    Each line is ``<t> <filter_tag> <command> <value>;``. We tag the
    crop filter ``reframe`` so the renderer can target
    ``crop@reframe`` without colliding with other ad-hoc crop usages
    (subtitle layout, etc.).

    Returns ``out_path`` for chaining.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    for t, x, y in path.points:
        # ``reinit`` is overkill (changes filter params); for x/y
        # changes the crop filter accepts plain ``x`` / ``y`` runtime
        # commands.
        lines.append(f"{t:.4f} crop@reframe x {x};")
        lines.append(f"{t:.4f} crop@reframe y {y};")
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out_path


def build_filter_chain(
    path: CropPath,
    sendcmd_path: Path,
    target_w: int,
    target_h: int,
) -> str:
    """Compose the ``sendcmd=…,crop@reframe=…,scale=…,setsar=1`` chain
    that the renderer chains into ``-vf``.

    The crop filter MUST be tagged ``@reframe`` for sendcmd to find
    it. ``-1`` for crop ``x`` / ``y`` lets the filter pick a centered
    default before the first command lands.
    """
    posix = sendcmd_path.as_posix()
    return (
        f"sendcmd=f={posix},"
        f"crop@reframe={path.crop_w}:{path.crop_h}:0:0,"
        f"scale={target_w}:{target_h},setsar=1"
    )


__all__ = [
    "ASPECT_RATIOS",
    "CROP_ZOOM_FACTOR",
    "KALMAN_Q",
    "KALMAN_R",
    "MAX_DELTA_PX_PER_FRAME",
    "RENDER_FPS",
    "CropPath",
    "build_filter_chain",
    "compute_crop_path",
    "compute_crop_path_from_custom_roi",
    "write_sendcmd_file",
]
