"""D5 — Smoothing quality tests for compute_crop_path."""

from __future__ import annotations

from media_processor.services import auto_reframe


def _jitter_tracking(n_frames: int = 15, *, src_w: int = 1920, src_h: int = 1080) -> dict:
    """High-frequency oscillating tracking: x center alternates ±100px every 200ms."""
    frames = []
    for i in range(n_frames):
        center_x = 860 if i % 2 == 0 else 1060
        frames.append({"t_ms": i * 200, "x": center_x - 100, "y": 440, "w": 200, "h": 200})
    return {"src_w": src_w, "src_h": src_h, "frames": frames}


def _pan_tracking(n_frames: int = 25, *, src_w: int = 1920, src_h: int = 1080) -> dict:
    """Slow linear pan: x center moves from 500 to 1400 over n_frames at 5Hz."""
    frames = []
    for i in range(n_frames):
        center_x = int(500 + (1400 - 500) * i / max(1, n_frames - 1))
        frames.append({"t_ms": i * 200, "x": center_x - 100, "y": 440, "w": 200, "h": 200})
    return {"src_w": src_w, "src_h": src_h, "frames": frames}


def test_compute_crop_path_smooths_high_frequency_jitter() -> None:
    tracking = _jitter_tracking(15)
    result_smooth = auto_reframe.compute_crop_path(
        tracking,
        target_aspect="9:16",
        asset_start_ms=0,
        asset_end_ms=3000,
        smooth_camera_path=True,
    )
    result_raw = auto_reframe.compute_crop_path(
        tracking,
        target_aspect="9:16",
        asset_start_ms=0,
        asset_end_ms=3000,
        smooth_camera_path=False,
    )
    assert result_smooth is not None
    assert result_raw is not None

    xs_smooth = [x for _, x, _ in result_smooth.points]
    xs_raw = [x for _, x, _ in result_raw.points]
    range_smooth = max(xs_smooth) - min(xs_smooth)
    range_raw = max(xs_raw) - min(xs_raw)

    assert range_raw > 0, "raw path should have non-zero jitter range"
    assert range_smooth < range_raw * 0.4, (
        f"smooth={range_smooth}px should be < 40% of raw={range_raw}px"
    )


def test_compute_crop_path_preserves_slow_intentional_pan() -> None:
    tracking = _pan_tracking(25)
    result = auto_reframe.compute_crop_path(
        tracking,
        target_aspect="9:16",
        asset_start_ms=0,
        asset_end_ms=5000,
        smooth_camera_path=True,
    )
    assert result is not None

    xs = [x for _, x, _ in result.points]
    range_pan = max(xs) - min(xs)
    assert range_pan > 200, (
        f"slow pan range {range_pan}px should be preserved (> 200px) by smoothing"
    )


def test_compute_crop_path_no_smoothing_follows_subject_tightly() -> None:
    tracking = _jitter_tracking(15)
    result_smooth = auto_reframe.compute_crop_path(
        tracking,
        target_aspect="9:16",
        asset_start_ms=0,
        asset_end_ms=3000,
        smooth_camera_path=True,
    )
    result_no_smooth = auto_reframe.compute_crop_path(
        tracking,
        target_aspect="9:16",
        asset_start_ms=0,
        asset_end_ms=3000,
        smooth_camera_path=False,
    )
    assert result_smooth is not None
    assert result_no_smooth is not None

    xs_smooth = [x for _, x, _ in result_smooth.points]
    xs_no_smooth = [x for _, x, _ in result_no_smooth.points]
    range_smooth = max(xs_smooth) - min(xs_smooth)
    range_no_smooth = max(xs_no_smooth) - min(xs_no_smooth)

    assert range_no_smooth > range_smooth * 3, (
        f"no-smooth range {range_no_smooth}px should be > 3x smooth range {range_smooth}px"
    )
