"""v0.23 — unit tests for ``services.point_tracking``.

Production tests against a real video are integration territory and
need OpenCV + a fixture clip; here we exercise the FAKE seam (the
deterministic stub) and the surface-level invariants that the API
layer relies on.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from media_processor.services import point_tracking


@pytest.fixture(autouse=True)
def _force_fake(monkeypatch: pytest.MonkeyPatch) -> None:
    """All tests in this module exercise the deterministic stub so
    they pass on CI hosts without OpenCV installed."""
    monkeypatch.setenv("TRACKING_FAKE", "1")


def test_track_point_fake_returns_static_trace() -> None:
    """``TRACKING_FAKE=1`` emits a constant-position trace at the
    norm-resolved init coordinate. The output dict shape is the live
    shape — same keys, JSON-friendly types — so the API layer +
    auto_reframe helper can be exercised without real LK.

    v0.23.7 — track_point takes ``init_norm_x``/``init_norm_y`` and
    resolves to pixels internally; FAKE path uses 1920×1080 as the
    stub display resolution.
    """
    result = point_tracking.track_point(
        Path("/tmp/dummy.mp4"),
        init_norm_x=0.25,  # 0.25 × 1920 = 480
        init_norm_y=0.25,  # 0.25 × 1080 = 270
        init_t_ms=0,
        duration_ms=2_000,
    )
    assert set(result.keys()) >= {
        "src_w",
        "src_h",
        "fps",
        "init_t_ms",
        "init",
        "frames",
        "sampled_frames",
    }
    assert result["init"] == {"x": 480, "y": 270}
    assert result["sampled_frames"] >= 1
    # Every frame is JSON-friendly (no numpy types) and carries the
    # four required fields.
    for f in result["frames"]:
        assert {"t_ms", "x", "y", "lost"}.issubset(f.keys())
        assert isinstance(f["t_ms"], int)
        assert isinstance(f["lost"], bool)
        # Stub locks the position at the init coordinate.
        assert f["x"] == 480.0
        assert f["y"] == 270.0
        assert f["lost"] is False


def test_track_point_fake_respects_duration_ms() -> None:
    """Stub frame count scales with ``duration_ms`` — the API layer
    sizes its progress affordances off this."""
    short = point_tracking.track_point(
        Path("/tmp/dummy.mp4"),
        init_norm_x=0.005,
        init_norm_y=0.01,
        duration_ms=500,
    )
    long = point_tracking.track_point(
        Path("/tmp/dummy.mp4"),
        init_norm_x=0.005,
        init_norm_y=0.01,
        duration_ms=10_000,
    )
    assert long["sampled_frames"] > short["sampled_frames"]


def test_track_point_fake_frames_are_monotonic_in_time() -> None:
    result = point_tracking.track_point(
        Path("/tmp/dummy.mp4"),
        init_norm_x=0.05,
        init_norm_y=0.1,
        init_t_ms=0,
        duration_ms=3_000,
    )
    timestamps = [f["t_ms"] for f in result["frames"]]
    assert timestamps == sorted(timestamps)


def test_track_point_constants_within_reasonable_ranges() -> None:
    """Sanity guardrails on the LK params so a future tweak surfaces
    in tests rather than silently changing tracker behaviour."""
    win_w, win_h = point_tracking.LK_WIN_SIZE
    assert 9 <= win_w <= 51
    assert 9 <= win_h <= 51
    assert 1 <= point_tracking.LK_MAX_LEVEL <= 5
    assert 5 <= point_tracking.LK_MAX_ITER <= 100
    assert 5.0 <= point_tracking.LK_MAX_ERR <= 200.0


def test_track_point_unavailable_when_opencv_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the real path runs without OpenCV it raises a clean
    ``PointTrackUnavailableError`` rather than ImportError so the API
    layer can 500 with a meaningful message. This test forces the
    real path (no FAKE) AND blocks the cv2 import to simulate the
    "OpenCV-less host" case.
    """
    monkeypatch.setenv("TRACKING_FAKE", "0")
    # Hide cv2 from the import system for the duration of this call.
    import sys

    saved_cv2 = sys.modules.pop("cv2", None)
    monkeypatch.setitem(sys.modules, "cv2", None)
    try:
        with pytest.raises(point_tracking.PointTrackUnavailableError):
            point_tracking.track_point(
                Path("/tmp/dummy.mp4"),
                init_norm_x=0.0,
                init_norm_y=0.0,
                duration_ms=1_000,
            )
    finally:
        # Restore cv2 if it was already imported by another test in
        # the session — pytest-monkeypatch does this automatically
        # for setitem, but we belt-and-suspenders the cleanup.
        if saved_cv2 is not None:
            sys.modules["cv2"] = saved_cv2
        else:
            sys.modules.pop("cv2", None)
        os.environ["TRACKING_FAKE"] = "1"
