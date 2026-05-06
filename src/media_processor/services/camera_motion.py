"""Camera-motion detection via OpenCV Farnebäck dense optical flow.

The original asset is pre-downscaled to 320 px wide / 5 fps before flow is
computed; that turns a 10-min HD clip into a few thousand small frames so
the whole pass runs in a couple of minutes on CPU.

Each 1-second window is classified into one of pan / tilt / zoom / static /
handheld using thresholds declared as module constants — no magic numbers
inline.
"""

from __future__ import annotations

import contextlib
import logging
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

logger = logging.getLogger(__name__)


MOTION_TAGS: tuple[str, ...] = ("static", "pan", "tilt", "zoom", "handheld")

# Pre-downscale parameters — keep small + slow so handheld noise dominates
# rather than being averaged out, and the flow pass is cheap.
DOWNSCALE_WIDTH = 320
DOWNSCALE_FPS = 5

# Farnebäck parameters — modest defaults known to work for handheld phone
# footage. Tunable in this file if a future asset class proves problematic.
FARNEBACK_PYR_SCALE = 0.5
FARNEBACK_LEVELS = 3
FARNEBACK_WINSIZE = 15
FARNEBACK_ITERATIONS = 3
FARNEBACK_POLY_N = 5
FARNEBACK_POLY_SIGMA = 1.2

# Window aggregation — each window is WINDOW_FRAMES frames wide.
WINDOW_FRAMES = DOWNSCALE_FPS  # 1 second
WINDOW_DURATION_MS = 1000

# Classification thresholds (median magnitude in pixels per frame).
STATIC_MAG = 0.5
DIRECTIONAL_MIN_MAG = 1.5
PAN_TILT_RATIO = 2.5
ZOOM_DIVERGENCE = 0.4
ZOOM_MIN_MAG = 1.0
HANDHELD_ANGLE_VAR = 1.5
HANDHELD_MAG_VAR = 2.0


class CameraMotionError(RuntimeError):
    """Caught by the orchestrator and mapped to failed:{reason}."""


@dataclass(frozen=True)
class MotionSegment:
    """One contiguous run of frames classified as the same motion type."""

    motion_type: str
    start_ms: int
    end_ms: int


def _downscale(media_path: Path, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        "-i",
        str(media_path),
        "-vf",
        f"scale={DOWNSCALE_WIDTH}:-2,fps={DOWNSCALE_FPS}",
        "-an",
        "-vcodec",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        str(out_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, timeout=900, check=False)
    if proc.returncode != 0:
        raise CameraMotionError(
            f"ffmpeg downscale failed (code={proc.returncode}): {proc.stderr.decode(errors='replace')[:300]}"
        )


def _flow_per_frame(
    cap: object,  # cv2.VideoCapture but typed loosely so api side can import this module
) -> list[tuple[float, float, float, float]]:
    """Compute per-frame (median_dx, median_dy, magnitude_median, divergence).

    Returns one tuple per frame transition (so N-1 entries for N frames).
    """
    import cv2
    import numpy as np

    cap_typed = cap

    ok, prev = cap_typed.read()  # type: ignore[attr-defined]
    if not ok:
        return []
    prev_gray = cv2.cvtColor(prev, cv2.COLOR_BGR2GRAY)

    out: list[tuple[float, float, float, float]] = []
    while True:
        ok, frame = cap_typed.read()  # type: ignore[attr-defined]
        if not ok:
            break
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        calc_farneback = cast(Any, cv2.calcOpticalFlowFarneback)
        flow = calc_farneback(
            prev_gray,
            gray,
            None,
            FARNEBACK_PYR_SCALE,
            FARNEBACK_LEVELS,
            FARNEBACK_WINSIZE,
            FARNEBACK_ITERATIONS,
            FARNEBACK_POLY_N,
            FARNEBACK_POLY_SIGMA,
            0,
        )
        fx = flow[..., 0]
        fy = flow[..., 1]
        median_dx = float(np.median(fx))
        median_dy = float(np.median(fy))
        magnitudes = np.sqrt(fx * fx + fy * fy)
        magnitude_median = float(np.median(magnitudes))
        # Divergence = ∂fx/∂x + ∂fy/∂y; positive = expansion (zoom in),
        # negative = contraction (zoom out). Take absolute median.
        dfx_dx = np.diff(fx, axis=1)
        dfy_dy = np.diff(fy, axis=0)
        # Trim to common shape so we can sum elementwise.
        h = min(dfx_dx.shape[0], dfy_dy.shape[0])
        w = min(dfx_dx.shape[1], dfy_dy.shape[1])
        divergence_field = dfx_dx[:h, :w] + dfy_dy[:h, :w]
        divergence_median = float(abs(np.median(divergence_field)))
        out.append((median_dx, median_dy, magnitude_median, divergence_median))
        prev_gray = gray
    return out


def _classify_window(samples: list[tuple[float, float, float, float]]) -> str:
    """Reduce a per-frame sample window to one motion class."""
    import numpy as np

    if not samples:
        return "static"
    arr = np.asarray(samples, dtype=float)
    median_dx = float(np.median(arr[:, 0]))
    median_dy = float(np.median(arr[:, 1]))
    median_mag = float(np.median(arr[:, 2]))
    median_divergence = float(np.median(arr[:, 3]))

    if median_mag < STATIC_MAG:
        return "static"

    abs_dx = abs(median_dx)
    abs_dy = abs(median_dy)
    if median_mag >= DIRECTIONAL_MIN_MAG and abs_dx >= PAN_TILT_RATIO * max(abs_dy, 1e-6):
        return "pan"
    if median_mag >= DIRECTIONAL_MIN_MAG and abs_dy >= PAN_TILT_RATIO * max(abs_dx, 1e-6):
        return "tilt"
    if median_mag >= ZOOM_MIN_MAG and median_divergence >= ZOOM_DIVERGENCE:
        return "zoom"

    angles = np.arctan2(arr[:, 1], arr[:, 0])
    angle_var = float(np.var(angles))
    mag_var = float(np.var(arr[:, 2]))
    if angle_var >= HANDHELD_ANGLE_VAR and mag_var >= HANDHELD_MAG_VAR:
        return "handheld"

    # Catch-all for noisy motion that doesn't fit a clean direction.
    return "handheld"


def _windowed(
    flow: list[tuple[float, float, float, float]],
) -> list[tuple[int, int, str]]:
    """Yield (start_frame_idx, end_frame_idx_exclusive, motion_type) tuples."""
    out: list[tuple[int, int, str]] = []
    n = len(flow)
    i = 0
    while i < n:
        end = min(i + WINDOW_FRAMES, n)
        if end - i < WINDOW_FRAMES * 0.8 and out:
            # Tail window shorter than 0.8 s — merge into previous window.
            prev_start, _prev_end, prev_class = out[-1]
            out[-1] = (prev_start, end, prev_class)
            break
        cls = _classify_window(flow[i:end])
        out.append((i, end, cls))
        i = end
    return out


def _merge_adjacent(windows: list[tuple[int, int, str]]) -> list[tuple[int, int, str]]:
    if not windows:
        return []
    merged = [windows[0]]
    for start, end, cls in windows[1:]:
        prev_start, _prev_end, prev_cls = merged[-1]
        if prev_cls == cls:
            merged[-1] = (prev_start, end, cls)
        else:
            merged.append((start, end, cls))
    return merged


def detect_motion(media_path: Path, scratch_dir: Path) -> list[MotionSegment]:
    """Pre-downscale via ffmpeg, run optical flow, classify, merge."""
    import cv2

    scratch_dir.mkdir(parents=True, exist_ok=True)
    downscaled = scratch_dir / "motion.mp4"
    try:
        _downscale(media_path, downscaled)
        cap = cv2.VideoCapture(str(downscaled))
        if not cap.isOpened():
            raise CameraMotionError("OpenCV could not open the downscaled clip")
        try:
            flow = _flow_per_frame(cap)
        finally:
            cap.release()
    finally:
        # Always clean up the downscaled scratch file.
        downscaled.unlink(missing_ok=True)
        # Also clean the (likely) empty scratch_dir if we created it just for
        # this step. If the dir is shared with other steps, leave it alone.
        with contextlib.suppress(OSError):
            shutil.rmtree(scratch_dir)

    if not flow:
        return []

    windows = _windowed(flow)
    merged = _merge_adjacent(windows)
    # Convert frame-window indexes (at DOWNSCALE_FPS) to ms.
    frame_to_ms = 1000 / DOWNSCALE_FPS
    return [
        MotionSegment(
            motion_type=cls,
            start_ms=int(round(start_idx * frame_to_ms)),
            end_ms=int(round(end_idx * frame_to_ms)),
        )
        for start_idx, end_idx, cls in merged
    ]


__all__ = [
    "MOTION_TAGS",
    "CameraMotionError",
    "MotionSegment",
    "detect_motion",
]
