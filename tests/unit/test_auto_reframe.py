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


def test_point_tracking_defaults_to_smoothed_user_camera_path() -> None:
    """Point tracking is user intent, but it must not expose tracker micro-jitter."""
    frames = []
    for i in range(60):
        frames.append(
            {
                "t_ms": i * 33,
                "x": 960 + (50 if i % 2 else -50),
                "y": 540,
                "lost": False,
            }
        )
    point_track = {"src_w": 1920, "src_h": 1080, "frames": frames}

    smoothed = auto_reframe.compute_crop_path_from_point_track(
        point_track,
        target_aspect="16:9",
        asset_start_ms=0,
        asset_end_ms=2_000,
    )
    raw = auto_reframe.compute_crop_path_from_point_track(
        point_track,
        target_aspect="16:9",
        asset_start_ms=0,
        asset_end_ms=2_000,
        smooth_camera_path=False,
    )

    assert smoothed is not None
    assert raw is not None
    assert _path_x_range(smoothed) < _path_x_range(raw) * 0.5


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
