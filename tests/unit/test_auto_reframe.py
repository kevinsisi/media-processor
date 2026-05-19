"""Unit tests for dynamic auto-reframe crop path generation."""

from __future__ import annotations

from media_processor.services import auto_reframe


def _path_x_range(path: auto_reframe.CropPath) -> int:
    xs = [x for _, x, _ in path.points]
    return max(xs) - min(xs)


def test_smooth_path_values_reduces_handheld_lateral_jitter() -> None:
    """Centred smoothing should absorb high-frequency left/right shake."""
    times = [i / auto_reframe.RENDER_FPS for i in range(90)]
    jittery = [500.0 + (60.0 if i % 2 else -60.0) for i in range(len(times))]

    smoothed = auto_reframe._smooth_path_values(jittery, times)

    assert max(smoothed) - min(smoothed) < (max(jittery) - min(jittery)) * 0.35


def test_compute_crop_path_smooths_lateral_shake_without_dropping_cut() -> None:
    """A shaky tracked subject still returns a crop path, but the path is stable."""
    frames = []
    for i in range(20):
        center_x = 960 + (90 if i % 2 else -90)
        frames.append(
            {
                "t_ms": i * 200,
                "x": center_x - 50,
                "y": 500,
                "w": 100,
                "h": 80,
            }
        )
    tracking = {"src_w": 1920, "src_h": 1080, "frames": frames}

    path = auto_reframe.compute_crop_path(
        tracking,
        target_aspect="9:16",
        asset_start_ms=0,
        asset_end_ms=4_000,
    )

    assert path is not None
    assert len(path.points) == 120
    assert _path_x_range(path) < 45


def test_compute_crop_path_can_keep_explicit_subject_lock_unsmoothed() -> None:
    """User-directed tracking can opt out so the crop keeps following the target."""
    frames = []
    for i in range(20):
        center_x = 960 + (90 if i % 2 else -90)
        frames.append(
            {
                "t_ms": i * 200,
                "x": center_x - 50,
                "y": 500,
                "w": 100,
                "h": 80,
            }
        )
    tracking = {"src_w": 1920, "src_h": 1080, "frames": frames}

    smoothed = auto_reframe.compute_crop_path(
        tracking,
        target_aspect="9:16",
        asset_start_ms=0,
        asset_end_ms=4_000,
    )
    locked = auto_reframe.compute_crop_path(
        tracking,
        target_aspect="9:16",
        asset_start_ms=0,
        asset_end_ms=4_000,
        smooth_camera_path=False,
    )

    assert smoothed is not None
    assert locked is not None
    assert _path_x_range(locked) > _path_x_range(smoothed) * 2


def test_compute_crop_path_keeps_slow_pan_after_smoothing() -> None:
    """The anti-jitter pass should preserve intentional slow lateral motion."""
    frames = []
    for i in range(20):
        center_x = 700 + i * 20
        frames.append(
            {
                "t_ms": i * 200,
                "x": center_x - 50,
                "y": 500,
                "w": 100,
                "h": 80,
            }
        )
    tracking = {"src_w": 1920, "src_h": 1080, "frames": frames}

    path = auto_reframe.compute_crop_path(
        tracking,
        target_aspect="9:16",
        asset_start_ms=0,
        asset_end_ms=4_000,
    )

    assert path is not None
    first_x = path.points[0][1]
    last_x = path.points[-1][1]
    assert last_x - first_x > 250


def test_compute_crop_path_centre_anchor_matches_no_anchor() -> None:
    """crop_region=(0.5, 0.5) must produce identical output to crop_region=None."""
    frames = [
        {"t_ms": i * 200, "x": 910, "y": 490, "w": 100, "h": 100}
        for i in range(20)
    ]
    tracking = {"src_w": 1920, "src_h": 1080, "frames": frames}

    path_none = auto_reframe.compute_crop_path(
        tracking, target_aspect="9:16", asset_start_ms=0, asset_end_ms=4_000
    )
    path_centre = auto_reframe.compute_crop_path(
        tracking,
        target_aspect="9:16",
        asset_start_ms=0,
        asset_end_ms=4_000,
        crop_region=(0.5, 0.5),
    )

    assert path_none is not None
    assert path_centre is not None
    xs_none = [x for _, x, _ in path_none.points]
    xs_centre = [x for _, x, _ in path_centre.points]
    assert xs_none == xs_centre


def test_compute_crop_path_left_anchor_shifts_idle_x() -> None:
    """Left anchor (x_norm=0.2) shifts the idle crop window toward the left edge."""
    frames = [
        {"t_ms": i * 200, "x": 960, "y": 490, "w": 100, "h": 100}
        for i in range(20)
    ]
    tracking = {"src_w": 1920, "src_h": 1080, "frames": frames}

    path_centre = auto_reframe.compute_crop_path(
        tracking, target_aspect="9:16", asset_start_ms=0, asset_end_ms=4_000
    )
    path_left = auto_reframe.compute_crop_path(
        tracking,
        target_aspect="9:16",
        asset_start_ms=0,
        asset_end_ms=4_000,
        crop_region=(0.2, 0.5),
    )

    assert path_centre is not None
    assert path_left is not None
    mean_x_centre = sum(x for _, x, _ in path_centre.points) / len(path_centre.points)
    mean_x_left = sum(x for _, x, _ in path_left.points) / len(path_left.points)
    assert mean_x_left < mean_x_centre


def test_compute_crop_path_anchor_bias_does_not_break_subject_lock() -> None:
    """With a moving subject, Kalman tracking still dominates over the anchor bias."""
    frames = []
    for i in range(20):
        cx = 320 + i * 64
        frames.append({"t_ms": i * 200, "x": cx - 50, "y": 490, "w": 100, "h": 100})
    tracking = {"src_w": 1920, "src_h": 1080, "frames": frames}

    path = auto_reframe.compute_crop_path(
        tracking,
        target_aspect="9:16",
        asset_start_ms=0,
        asset_end_ms=4_000,
        crop_region=(0.2, 0.5),
        smooth_camera_path=False,
    )

    assert path is not None
    xs = [x for _, x, _ in path.points]
    assert max(xs) - min(xs) > 200
