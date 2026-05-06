"""v0.21 + v0.21.5 — unit tests for the subject-class filter helpers.

These exercise the pure-function path (no DB, no Gemini) so the trim
logic is covered without spinning up the full fixture used by
``test_edit_planner.py``.

v0.21.5 swapped the original "min..max of all detection timestamps"
range for contiguous-window detection: gaps larger than
``SUBJECT_GAP_TOLERANCE_MS`` split the run into separate windows so
that a clip where the subject appears at t=1s and t=9s no longer has
a "presence range" covering the floor-only stretch in between.
"""

from __future__ import annotations

from media_processor.models import Asset
from media_processor.services.edit_planner import (
    SUBJECT_GAP_TOLERANCE_MS,
    SUBJECT_MIN_WINDOW_MS,
    _apply_subject_filter,
    _AssetScore,
    _subject_presence_range_ms,
    _subject_presence_windows_ms,
)


def _asset(
    *,
    asset_id: int = 1,
    duration_ms: int = 10_000,
    tracking_json: dict | None = None,
) -> Asset:
    """Detached Asset row sufficient for the helpers (no DB session)."""
    return Asset(
        id=asset_id,
        project_id=1,
        file_path="/tmp/a.mp4",
        duration_ms=duration_ms,
        sha256="0" * 64,
        tracking_json=tracking_json,
    )


def _score(
    *,
    asset_id: int = 1,
    span_ms: tuple[int, int] = (2_000, 6_000),
    asset_duration_ms: int = 10_000,
) -> _AssetScore:
    return _AssetScore(
        asset_id=asset_id,
        score=80,
        position="middle",
        best_span_ms=span_ms,
        source_kind="planned",
        reason="test",
        asset_duration_ms=asset_duration_ms,
    )


def _dense_frames(start_ms: int, end_ms: int, *, step_ms: int = 200) -> list[dict]:
    """Generate per-frame dicts for a dense run of detections at the
    given step (default 200 ms = TRACKING_SAMPLE_FPS at 5 Hz)."""
    return [{"t_ms": ts} for ts in range(start_ms, end_ms + 1, step_ms)]


# ---------- _subject_presence_windows_ms ----------


def test_windows_dense_run_returns_one_padded_window() -> None:
    asset = _asset(
        duration_ms=10_000,
        tracking_json={
            "tracks": [
                {
                    "cls_name": "person",
                    "frames": _dense_frames(2_000, 7_000),
                }
            ]
        },
    )
    windows = _subject_presence_windows_ms(asset, "person")
    assert windows == [(1_500, 7_500)]


def test_windows_split_when_gap_exceeds_tolerance() -> None:
    """Two dense runs with a > SUBJECT_GAP_TOLERANCE_MS gap between
    them yield two separate windows — exactly the bug-report case
    (dog at the start, dog at the end, floor-only middle)."""
    asset = _asset(
        duration_ms=20_000,
        tracking_json={
            "tracks": [
                {
                    "cls_name": "dog",
                    # Run 1: 1.0 s – 2.4 s (last frame at the 200 ms
                    # step closest to 2_400 ms). Padded window
                    # = (500, 2_900). 6.6 s gap — well over tolerance.
                    # Run 2: 9.0 s – 11.0 s. Padded window = (8_500,
                    # 11_500).
                    "frames": [
                        *_dense_frames(1_000, 2_400),
                        *_dense_frames(9_000, 11_000),
                    ],
                }
            ]
        },
    )
    windows = _subject_presence_windows_ms(asset, "dog")
    assert windows == [(500, 2_900), (8_500, 11_500)]


def test_windows_drop_runs_shorter_than_min_after_padding() -> None:
    """A single-frame YOLO flicker becomes a 0-ms run + 500 ms × 2
    padding = 1000 ms total, which is below SUBJECT_MIN_WINDOW_MS
    and gets dropped so we don't ship a flicker cut."""
    asset = _asset(
        duration_ms=10_000,
        tracking_json={"tracks": [{"cls_name": "cat", "frames": [{"t_ms": 4_000}]}]},
    )
    assert SUBJECT_MIN_WINDOW_MS > 1_000  # premise of the test
    assert _subject_presence_windows_ms(asset, "cat") == []


def test_windows_clamp_to_asset_bounds() -> None:
    asset = _asset(
        duration_ms=10_000,
        tracking_json={
            "tracks": [
                {
                    "cls_name": "dog",
                    "frames": _dense_frames(100, 9_900),
                }
            ]
        },
    )
    windows = _subject_presence_windows_ms(asset, "dog")
    # Padding would push the window to (-400, 10_400); both ends
    # should clamp to the asset's [0, 10_000].
    assert windows == [(0, 10_000)]


def test_windows_returns_empty_when_class_absent() -> None:
    asset = _asset(
        tracking_json={"tracks": [{"cls_name": "person", "frames": _dense_frames(0, 5_000)}]}
    )
    assert _subject_presence_windows_ms(asset, "dog") == []


def test_windows_returns_empty_when_no_tracking() -> None:
    assert _subject_presence_windows_ms(_asset(tracking_json=None), "person") == []


def test_windows_legacy_top_level_frames_fallback() -> None:
    """Pre-v0.17 assets only stored the dominant track at the top
    level of tracking_json. The fallback should still produce a
    valid window."""
    asset = _asset(
        duration_ms=10_000,
        tracking_json={
            "subject_class": "car",
            "frames": _dense_frames(2_000, 5_000),
            # no "tracks" key
        },
    )
    assert _subject_presence_windows_ms(asset, "car") == [(1_500, 5_500)]


def test_windows_legacy_path_does_not_match_other_classes() -> None:
    asset = _asset(
        tracking_json={
            "subject_class": "car",
            "frames": _dense_frames(0, 5_000),
        },
    )
    assert _subject_presence_windows_ms(asset, "dog") == []


# ---------- _subject_presence_range_ms (legacy 1-tuple wrapper) ----------


def test_presence_range_returns_longest_window() -> None:
    """``_subject_presence_range_ms`` is the heuristic-fallback's
    1-tuple shim — it returns the LONGEST contiguous window or None.
    """
    asset = _asset(
        duration_ms=20_000,
        tracking_json={
            "tracks": [
                {
                    "cls_name": "dog",
                    "frames": [
                        # Short run at 1 – 2 s (1500 ms wide post-padding)
                        *_dense_frames(1_000, 2_000),
                        # Long run at 9 – 14 s (6000 ms wide post-padding)
                        *_dense_frames(9_000, 14_000),
                    ],
                }
            ]
        },
    )
    # Both runs survive the min-window filter. The longest one wins.
    assert _subject_presence_range_ms(asset, "dog") == (8_500, 14_500)


def test_presence_range_returns_none_when_class_absent() -> None:
    asset = _asset(
        tracking_json={"tracks": [{"cls_name": "person", "frames": _dense_frames(0, 5_000)}]}
    )
    assert _subject_presence_range_ms(asset, "dog") is None


# ---------- _apply_subject_filter ----------


def test_apply_subject_filter_no_op_when_class_unset() -> None:
    asset = _asset()
    score = _score(span_ms=(2_000, 6_000))
    assert _apply_subject_filter([score], assets=(asset,), subject_class=None) == [score]


def test_apply_subject_filter_clamps_span_to_overlapping_window() -> None:
    """LLM picked (1, 7) s, the dog has windows at (2.5, 5.5) s —
    the resulting span is the intersection (2.5, 5.5)."""
    asset = _asset(
        duration_ms=10_000,
        tracking_json={"tracks": [{"cls_name": "person", "frames": _dense_frames(3_000, 5_000)}]},
    )
    score = _score(span_ms=(1_000, 7_000))
    [out] = _apply_subject_filter([score], assets=(asset,), subject_class="person")
    assert out.best_span_ms == (2_500, 5_500)


def test_apply_subject_filter_drops_asset_when_class_absent() -> None:
    asset = _asset(
        tracking_json={"tracks": [{"cls_name": "dog", "frames": _dense_frames(1_000, 4_000)}]}
    )
    score = _score(span_ms=(2_000, 6_000))
    assert _apply_subject_filter([score], assets=(asset,), subject_class="person") == []


def test_apply_subject_filter_snaps_to_longest_window_when_no_overlap() -> None:
    """B=snap fallback. LLM picked a span that misses every window
    entirely → snap to the longest contiguous window so we don't
    lose the asset."""
    asset = _asset(
        duration_ms=15_000,
        tracking_json={
            "tracks": [
                {
                    "cls_name": "cat",
                    "frames": [
                        # Long window 7–10 s (3500 ms post-padding)
                        *_dense_frames(7_000, 10_000),
                        # Short window 13–13.5 s (1500 ms post-padding)
                        *_dense_frames(13_000, 13_500),
                    ],
                }
            ]
        },
    )
    score = _score(span_ms=(1_000, 4_000))
    [out] = _apply_subject_filter([score], assets=(asset,), subject_class="cat")
    # Longest window is (6_500, 10_500); span snaps there.
    assert out.best_span_ms == (6_500, 10_500)


def test_apply_subject_filter_picks_overlapping_window_over_longest() -> None:
    """When the LLM's pick overlaps a SHORTER window, we still
    clamp into that window rather than ignoring the LLM's choice
    in favour of the longer one elsewhere."""
    asset = _asset(
        duration_ms=20_000,
        tracking_json={
            "tracks": [
                {
                    "cls_name": "dog",
                    "frames": [
                        # Short window 2–3.4 s post-pad → (1_500,
                        # 3_900). Long window 12–17 s post-pad →
                        # (11_500, 17_500).
                        *_dense_frames(2_000, 3_400),
                        *_dense_frames(12_000, 17_000),
                    ],
                }
            ]
        },
    )
    score = _score(span_ms=(1_000, 4_000))
    [out] = _apply_subject_filter([score], assets=(asset,), subject_class="dog")
    # Span clamps to the overlap with the SHORT window:
    # (max(1_000, 1_500), min(4_000, 3_900)) = (1_500, 3_900).
    assert out.best_span_ms == (1_500, 3_900)


def test_apply_subject_filter_bug_report_floor_only_middle() -> None:
    """v0.21.5 regression test — the exact bug-report scenario.
    Dog appears at the start and end of a 12 s clip; the LLM picks
    the floor-only middle. The new contiguous-windows logic must
    NOT keep the LLM's floor-only span; it should snap to one of
    the two dog windows."""
    asset = _asset(
        duration_ms=12_000,
        tracking_json={
            "tracks": [
                {
                    "cls_name": "dog",
                    "frames": [
                        # Start window 0–1.4 s → padded (0, 1_900).
                        # End window 10–11.4 s → padded (9_500, 11_900).
                        *_dense_frames(0, 1_400),
                        *_dense_frames(10_000, 11_400),
                    ],
                }
            ]
        },
    )
    # LLM picked the floor-only middle 4–8 s.
    score = _score(span_ms=(4_000, 8_000))
    [out] = _apply_subject_filter([score], assets=(asset,), subject_class="dog")
    new_start, new_end = out.best_span_ms
    # Whichever window won, the cut MUST lie inside a contiguous dog
    # window — never the original (4_000, 8_000) floor-only middle.
    assert (new_start, new_end) != (4_000, 8_000)
    valid_windows = [(0, 1_900), (9_500, 11_900)]
    assert (new_start, new_end) in valid_windows


def test_subject_gap_tolerance_constant_is_realistic_for_5fps_yolo() -> None:
    """Sanity guardrail — at 5 Hz YOLO sampling the gap between
    consecutive frames is 200 ms. The tolerance should allow a few
    missed frames (occlusion / fast motion) but not so many that
    we paper over a real dog-leaves-the-frame moment."""
    assert 600 <= SUBJECT_GAP_TOLERANCE_MS <= 3_000
