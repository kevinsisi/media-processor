"""Asset source-variant helpers and source-level stabilization.

v0.40.0 keeps the raw upload immutable and adds an optional stabilized
derivative. Analysis, tracking, and render resolve the active source through
this module so coordinates are not mixed across raw/stabilized variants.
"""

from __future__ import annotations

import os
import subprocess
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

from media_processor.api.config import settings
from media_processor.services import auto_reframe

AssetVariant = Literal["raw", "stabilized"]

RAW_VARIANT: AssetVariant = "raw"
STABILIZED_VARIANT: AssetVariant = "stabilized"
VARIANT_VALUES = frozenset({RAW_VARIANT, STABILIZED_VARIANT})

STABILIZATION_NOT_STARTED = "not_started"
STABILIZATION_PENDING = "pending"
STABILIZATION_RUNNING = "running"
STABILIZATION_DONE = "done"
STABILIZATION_SKIPPED = "skipped"
STABILIZATION_FAILED = "failed"

STABILIZATION_STATUS_VALUES = frozenset(
    {
        STABILIZATION_NOT_STARTED,
        STABILIZATION_PENDING,
        STABILIZATION_RUNNING,
        STABILIZATION_DONE,
        STABILIZATION_SKIPPED,
        STABILIZATION_FAILED,
    }
)

# Source-level stabilization should be conservative: remove high-frequency
# handheld shake without inventing a new framing path.
STABILIZE_SHAKINESS = 6
STABILIZE_ACCURACY = 9
STABILIZE_STEPSIZE = 6
STABILIZE_SMOOTHING = 30
STABILIZE_ZOOM = 0
STABILIZE_TIMEOUT_S = 60 * 60

# Preflight is deliberately measured on downscaled frames. These thresholds are
# in analysis pixels, not source pixels. Production DJI samples where raw high-
# frequency jitter was below this floor got worse after vidstab because the
# correction was mostly estimating feature-tracking noise.
PREFLIGHT_ANALYSIS_WIDTH = 640
PREFLIGHT_TARGET_SAMPLE_FPS = 60.0
PREFLIGHT_MAX_SECONDS = 12.0
PREFLIGHT_MIN_USABLE_STEPS = 60
PREFLIGHT_LOW_JITTER_RMS_PX = 0.18
PREFLIGHT_LOW_JITTER_P95_PX = 0.40
TRACKING_STABILIZE_MARGIN = 1.08
TRACKING_STABILIZE_CROP_ZOOM_FACTOR = 1.0 / TRACKING_STABILIZE_MARGIN
TRACKING_STABILIZE_MIN_POINTS = 30
TRACKING_STABILIZE_MAX_JITTER_REGRESSION = 1.10


@dataclass(frozen=True)
class StabilizationNeedEstimate:
    should_stabilize: bool
    sampled_frames: int
    usable_steps: int
    jitter_rms_px: float | None
    jitter_p95_px: float | None
    reason: str


@dataclass(frozen=True)
class TrackingStabilizationResult:
    mode: str
    point_count: int
    crop_w: int
    crop_h: int


class AssetStabilizationError(RuntimeError):
    """Raised when ffmpeg/vidstab cannot produce the stabilized derivative."""


def active_variant(asset: Any) -> AssetVariant:
    value = str(getattr(asset, "active_asset_variant", RAW_VARIANT) or RAW_VARIANT)
    return value if value in VARIANT_VALUES else RAW_VARIANT


def stabilization_status(asset: Any) -> str:
    value = str(getattr(asset, "stabilization_status", STABILIZATION_NOT_STARTED) or "")
    return value if value in STABILIZATION_STATUS_VALUES else STABILIZATION_NOT_STARTED


def stabilized_path_for_asset(asset: Any) -> Path:
    raw = Path(str(asset.file_path))
    safe_stem = raw.stem or f"asset_{asset.id}"
    suffix = raw.suffix or ".mp4"
    return (
        Path(settings.assets_dir)
        / str(asset.project_id)
        / "_stabilized"
        / f"{asset.id}_{safe_stem}.stab{suffix}"
    )


def selected_media_path(asset: Any) -> Path:
    if active_variant(asset) == STABILIZED_VARIANT:
        path = getattr(asset, "stabilized_path", None)
        if path and stabilization_status(asset) == STABILIZATION_DONE:
            return Path(str(path))
    return Path(str(asset.file_path))


def public_asset_url(path: str | Path | None) -> str | None:
    if path is None:
        return None
    try:
        rel = Path(path).resolve().relative_to(Path(settings.assets_dir).resolve())
    except (OSError, ValueError):
        return None
    return "/api/media/assets/" + rel.as_posix()


def variant_urls(asset: Any) -> dict[str, str | None]:
    return {
        RAW_VARIANT: public_asset_url(getattr(asset, "file_path", None)),
        STABILIZED_VARIANT: public_asset_url(getattr(asset, "stabilized_path", None)),
    }


def _is_fake() -> bool:
    return os.getenv("FFMPEG_FAKE", "0") == "1"


def _run(cmd: list[str], *, timeout_s: float, stage: str) -> None:
    if _is_fake():
        out = Path(cmd[-1])
        if out.name != "-":
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(b"")
        return
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=timeout_s)
    except subprocess.TimeoutExpired as exc:
        raise AssetStabilizationError(f"{stage} timed out after {timeout_s:.0f}s") from exc
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        raise AssetStabilizationError(f"{stage} failed: {stderr or exc}") from exc


def _rolling_median(values: list[tuple[float, float]], *, radius: int) -> list[tuple[float, float]]:
    medians: list[tuple[float, float]] = []
    for index in range(len(values)):
        window = values[max(0, index - radius) : min(len(values), index + radius + 1)]
        xs = sorted(point[0] for point in window)
        ys = sorted(point[1] for point in window)
        mid = len(window) // 2
        medians.append((xs[mid], ys[mid]))
    return medians


def estimate_stabilization_need(src: Path) -> StabilizationNeedEstimate:
    """Return whether source-level vidstab is worth running for ``src``.

    The estimate separates intentional slow camera motion from shake: optical
    flow + RANSAC estimates frame-to-frame global translation, a rolling median
    approximates the slow motion path, and the residual is high-frequency jitter.
    If the residual is already below the measured noise floor, vidstab is more
    likely to invent compensation jitter than improve the asset.
    """

    if _is_fake():
        return StabilizationNeedEstimate(True, 0, 0, None, None, "fake ffmpeg")
    try:
        import cv2
        import numpy as np
    except ImportError:
        return StabilizationNeedEstimate(True, 0, 0, None, None, "opencv unavailable")
    cv2_any = cast(Any, cv2)

    cap = cv2_any.VideoCapture(str(src))
    if not cap.isOpened():
        return StabilizationNeedEstimate(True, 0, 0, None, None, "video open failed")
    try:
        fps = float(cap.get(cv2_any.CAP_PROP_FPS) or 30.0)
        step = max(1, int(round(fps / PREFLIGHT_TARGET_SAMPLE_FPS)))
        frame_count = int(cap.get(cv2_any.CAP_PROP_FRAME_COUNT) or int(PREFLIGHT_MAX_SECONDS * fps))
        max_source_frames = min(frame_count, int(PREFLIGHT_MAX_SECONDS * fps))
        prev_gray: Any | None = None
        transforms: list[tuple[float, float]] = []
        sampled_frames = 0
        source_index = 0

        while source_index < max_source_frames:
            ok, frame = cap.read()
            if not ok:
                break
            if source_index % step != 0:
                source_index += 1
                continue
            height, width = frame.shape[:2]
            if width > PREFLIGHT_ANALYSIS_WIDTH:
                scale = PREFLIGHT_ANALYSIS_WIDTH / float(width)
                frame = cv2_any.resize(
                    frame,
                    (PREFLIGHT_ANALYSIS_WIDTH, max(1, int(height * scale))),
                    interpolation=cv2_any.INTER_AREA,
                )
            gray = cv2_any.cvtColor(frame, cv2_any.COLOR_BGR2GRAY)
            if prev_gray is not None:
                points0 = cv2_any.goodFeaturesToTrack(
                    prev_gray,
                    maxCorners=500,
                    qualityLevel=0.01,
                    minDistance=8,
                    blockSize=7,
                )
                if points0 is not None and len(points0) >= 20:
                    points1, status, _ = cv2_any.calcOpticalFlowPyrLK(
                        prev_gray, gray, points0, None
                    )
                    if points1 is not None and status is not None:
                        mask = status.reshape(-1) == 1
                        good0 = points0[mask]
                        good1 = points1[mask]
                        if len(good0) >= 20:
                            mat, _inliers = cv2_any.estimateAffinePartial2D(
                                good0,
                                good1,
                                method=cv2_any.RANSAC,
                                ransacReprojThreshold=3.0,
                            )
                            if mat is not None:
                                transforms.append((float(mat[0, 2]), float(mat[1, 2])))
            prev_gray = gray
            sampled_frames += 1
            source_index += 1
    finally:
        cap.release()

    usable_steps = len(transforms)
    if usable_steps < PREFLIGHT_MIN_USABLE_STEPS:
        return StabilizationNeedEstimate(
            True,
            sampled_frames,
            usable_steps,
            None,
            None,
            "insufficient motion samples",
        )

    slow_path = _rolling_median(transforms, radius=30)
    residuals = [
        ((dx - slow_dx) ** 2 + (dy - slow_dy) ** 2) ** 0.5
        for (dx, dy), (slow_dx, slow_dy) in zip(transforms, slow_path, strict=True)
    ]
    residual_arr = np.array(residuals, dtype=np.float64)
    jitter_rms = float(np.sqrt(np.mean(residual_arr**2)))
    jitter_p95 = float(np.percentile(residual_arr, 95))
    low_jitter = (
        jitter_rms < PREFLIGHT_LOW_JITTER_RMS_PX and jitter_p95 < PREFLIGHT_LOW_JITTER_P95_PX
    )
    reason = (
        f"jitter_rms={jitter_rms:.3f}px jitter_p95={jitter_p95:.3f}px usable_steps={usable_steps}"
    )
    return StabilizationNeedEstimate(
        not low_jitter,
        sampled_frames,
        usable_steps,
        jitter_rms,
        jitter_p95,
        reason,
    )


def stabilize_source(src: Path, dst: Path, scratch_dir: Path) -> None:
    """Run two-pass vidstab over the full source asset into ``dst``."""
    src = Path(src)
    dst = Path(dst)
    scratch_dir = Path(scratch_dir)
    if not src.is_file() and not _is_fake():
        raise AssetStabilizationError(f"source missing: {src}")
    dst.parent.mkdir(parents=True, exist_ok=True)
    scratch_dir.mkdir(parents=True, exist_ok=True)
    transforms_path = scratch_dir / f"{dst.stem}.trf"
    detect_filter = (
        f"vidstabdetect=stepsize={STABILIZE_STEPSIZE}"
        f":shakiness={STABILIZE_SHAKINESS}"
        f":accuracy={STABILIZE_ACCURACY}"
        f":result={transforms_path.as_posix()}"
    )
    _run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(src),
            "-vf",
            detect_filter,
            "-f",
            "null",
            "-",
        ],
        timeout_s=STABILIZE_TIMEOUT_S,
        stage="asset-stabilize-detect",
    )
    transform_filter = (
        f"vidstabtransform=input={transforms_path.as_posix()}"
        f":zoom={STABILIZE_ZOOM}"
        f":smoothing={STABILIZE_SMOOTHING}"
        ",unsharp=5:5:0.6:3:3:0.3"
    )
    temp_dst = dst.with_suffix(f".tmp{dst.suffix}")
    _run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(src),
            "-vf",
            transform_filter,
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-preset",
            "veryfast",
            "-crf",
            "18",
            "-c:a",
            "copy",
            "-movflags",
            "+faststart",
            str(temp_dst),
        ],
        timeout_s=STABILIZE_TIMEOUT_S,
        stage="asset-stabilize-apply",
    )
    if _is_fake() and not temp_dst.exists():
        temp_dst.write_bytes(b"")
    temp_dst.replace(dst)


def _parse_resolution(value: Any) -> tuple[int, int] | None:
    if not value:
        return None
    text = str(value).lower().replace("×", "x")
    if "x" not in text:
        return None
    left, right = text.split("x", 1)
    try:
        width = int(float(left.strip()))
        height = int(float(right.strip()))
    except ValueError:
        return None
    if width <= 0 or height <= 0:
        return None
    return width, height


def _source_aspect_for_dimensions(width: int, height: int) -> str:
    return "16:9" if width >= height else "9:16"


def _tracking_crop_path_for_asset(asset: Any) -> tuple[str, auto_reframe.CropPath] | None:
    duration_ms = int(getattr(asset, "duration_ms", 0) or 0)
    if duration_ms <= 0:
        return None
    # Tracking coordinates are produced from cv2/post-rotation frames. Prefer
    # tracking blob dimensions over Asset.resolution, which can reflect raw
    # stream dimensions for rotated phone/drone clips.
    src_w: int | None = None
    src_h: int | None = None
    for blob_name in ("point_tracking_json", "custom_roi_json", "tracking_json"):
        blob = getattr(asset, blob_name, None)
        if isinstance(blob, dict):
            src_w = int(blob.get("src_w") or 0) or None
            src_h = int(blob.get("src_h") or 0) or None
            if src_w and src_h:
                break
    if src_w is None or src_h is None:
        resolution = _parse_resolution(getattr(asset, "resolution", None))
        src_w = resolution[0] if resolution else None
        src_h = resolution[1] if resolution else None
    if not src_w or not src_h:
        return None
    target_aspect = _source_aspect_for_dimensions(src_w, src_h)
    tracked_object_index = getattr(asset, "tracked_object_index", None)
    path: auto_reframe.CropPath | None = None
    mode = ""
    if tracked_object_index == -4:
        path = auto_reframe.compute_crop_path_from_point_track(
            getattr(asset, "point_tracking_json", None),
            target_aspect=target_aspect,
            asset_start_ms=0,
            asset_end_ms=duration_ms,
            src_w=src_w,
            src_h=src_h,
            smooth_camera_path=True,
            crop_zoom_factor=TRACKING_STABILIZE_CROP_ZOOM_FACTOR,
        )
        mode = "tracking_point"
    elif tracked_object_index == -1:
        path = auto_reframe.compute_crop_path_from_custom_roi(
            getattr(asset, "custom_roi_json", None),
            target_aspect=target_aspect,
            asset_start_ms=0,
            asset_end_ms=duration_ms,
            src_w=src_w,
            src_h=src_h,
            smooth_camera_path=True,
            crop_zoom_factor=TRACKING_STABILIZE_CROP_ZOOM_FACTOR,
        )
        mode = "tracking_custom_roi"
    elif isinstance(tracked_object_index, int) and tracked_object_index >= 0:
        path = auto_reframe.compute_crop_path(
            getattr(asset, "tracking_json", None),
            target_aspect=target_aspect,
            asset_start_ms=0,
            asset_end_ms=duration_ms,
            src_w=src_w,
            src_h=src_h,
            object_index=tracked_object_index,
            smooth_camera_path=True,
            crop_zoom_factor=TRACKING_STABILIZE_CROP_ZOOM_FACTOR,
        )
        mode = "tracking_object"
    elif tracked_object_index not in (-2, -3):
        path = auto_reframe.compute_crop_path(
            getattr(asset, "tracking_json", None),
            target_aspect=target_aspect,
            asset_start_ms=0,
            asset_end_ms=duration_ms,
            src_w=src_w,
            src_h=src_h,
            object_index=None,
            smooth_camera_path=True,
            crop_zoom_factor=TRACKING_STABILIZE_CROP_ZOOM_FACTOR,
        )
        mode = "auto_tracking"
    if path is None or len(path.points) < TRACKING_STABILIZE_MIN_POINTS:
        return None
    return mode, path


def _tracking_quality_gate(src: Path, rendered: Path) -> tuple[bool, str]:
    raw = estimate_stabilization_need(src)
    stabilized = estimate_stabilization_need(rendered)
    if raw.jitter_rms_px is None or stabilized.jitter_rms_px is None:
        return True, "quality gate skipped: insufficient measurable jitter"
    allowed = max(
        raw.jitter_rms_px * TRACKING_STABILIZE_MAX_JITTER_REGRESSION,
        raw.jitter_rms_px + 0.05,
    )
    if stabilized.jitter_rms_px > allowed:
        return (
            False,
            "tracking output regressed jitter: "
            f"raw_rms={raw.jitter_rms_px:.3f}px output_rms={stabilized.jitter_rms_px:.3f}px "
            f"allowed={allowed:.3f}px",
        )
    return (
        True,
        "tracking quality ok: "
        f"raw_rms={raw.jitter_rms_px:.3f}px output_rms={stabilized.jitter_rms_px:.3f}px",
    )


def stabilize_source_from_tracking(
    asset: Any,
    src: Path,
    dst: Path,
    scratch_dir: Path,
) -> TrackingStabilizationResult | None:
    """Render a stabilized derivative from existing tracking data.

    This is the video-editor-style path: explicit tracking controls framing,
    the crop path is smoothed, and the output is scaled back to the source
    dimensions. Whole-frame vidstab is left as a fallback when no usable
    tracking target exists.
    """

    crop = _tracking_crop_path_for_asset(asset)
    if crop is None:
        return None
    mode, crop_path = crop
    dst = Path(dst)
    scratch_dir = Path(scratch_dir)
    dst.parent.mkdir(parents=True, exist_ok=True)
    scratch_dir.mkdir(parents=True, exist_ok=True)
    sendcmd_path = auto_reframe.write_sendcmd_file(
        crop_path, scratch_dir / f"{dst.stem}.tracking.txt"
    )
    filter_chain = auto_reframe.build_filter_chain(
        crop_path,
        sendcmd_path,
        crop_path.src_w,
        crop_path.src_h,
    )
    temp_dst = dst.with_suffix(f".tmp{dst.suffix}")
    _run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(src),
            "-vf",
            filter_chain,
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-preset",
            "veryfast",
            "-crf",
            "18",
            "-c:a",
            "copy",
            "-movflags",
            "+faststart",
            str(temp_dst),
        ],
        timeout_s=STABILIZE_TIMEOUT_S,
        stage="asset-stabilize-tracking",
    )
    quality_ok, quality_reason = _tracking_quality_gate(src, temp_dst)
    if not quality_ok:
        with suppress(OSError):
            temp_dst.unlink(missing_ok=True)
        raise AssetStabilizationError(quality_reason)
    temp_dst.replace(dst)
    return TrackingStabilizationResult(
        mode=mode,
        point_count=len(crop_path.points),
        crop_w=crop_path.crop_w,
        crop_h=crop_path.crop_h,
    )


__all__ = [
    "RAW_VARIANT",
    "STABILIZED_VARIANT",
    "STABILIZATION_DONE",
    "STABILIZATION_FAILED",
    "STABILIZATION_NOT_STARTED",
    "STABILIZATION_PENDING",
    "STABILIZATION_RUNNING",
    "STABILIZATION_SKIPPED",
    "AssetStabilizationError",
    "StabilizationNeedEstimate",
    "TrackingStabilizationResult",
    "active_variant",
    "estimate_stabilization_need",
    "public_asset_url",
    "selected_media_path",
    "stabilization_status",
    "stabilized_path_for_asset",
    "stabilize_source",
    "stabilize_source_from_tracking",
    "variant_urls",
]
