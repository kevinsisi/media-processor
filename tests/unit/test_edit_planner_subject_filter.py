"""v0.21 — unit tests for the subject-class filter helpers in edit_planner.

These exercise the pure-function path (no DB, no Gemini) so the trim
logic is covered without spinning up the full fixture used by
``test_edit_planner.py``.
"""

from __future__ import annotations

from media_processor.models import Asset
from media_processor.services.edit_planner import (
    _apply_subject_filter,
    _AssetScore,
    _subject_presence_range_ms,
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


# ---------- _subject_presence_range_ms ----------


def test_presence_range_pads_pm_500ms_and_clamps_to_asset_bounds() -> None:
    asset = _asset(
        duration_ms=10_000,
        tracking_json={
            "tracks": [
                {
                    "object_index": 0,
                    "cls_name": "person",
                    "frames": [
                        {"t_ms": 2_000},
                        {"t_ms": 5_000},
                        {"t_ms": 7_000},
                    ],
                }
            ],
        },
    )
    assert _subject_presence_range_ms(asset, "person") == (1_500, 7_500)


def test_presence_range_clamps_to_zero_lower_bound() -> None:
    asset = _asset(
        duration_ms=10_000,
        tracking_json={
            "tracks": [
                {"cls_name": "dog", "frames": [{"t_ms": 100}, {"t_ms": 9_900}]},
            ],
        },
    )
    # 100 - 500 = -400 → clamp to 0; 9900 + 500 = 10400 → clamp to duration.
    assert _subject_presence_range_ms(asset, "dog") == (0, 10_000)


def test_presence_range_returns_none_when_class_absent() -> None:
    asset = _asset(
        tracking_json={
            "tracks": [
                {"cls_name": "person", "frames": [{"t_ms": 1_000}]},
            ],
        },
    )
    assert _subject_presence_range_ms(asset, "dog") is None


def test_presence_range_returns_none_when_no_tracking() -> None:
    asset = _asset(tracking_json=None)
    assert _subject_presence_range_ms(asset, "person") is None


def test_presence_range_falls_back_to_legacy_top_level_frames() -> None:
    """Pre-v0.17 assets stored only the dominant track at the top level."""
    asset = _asset(
        duration_ms=10_000,
        tracking_json={
            "subject_class": "car",
            "frames": [{"t_ms": 1_000}, {"t_ms": 4_000}],
            # no "tracks" key
        },
    )
    assert _subject_presence_range_ms(asset, "car") == (500, 4_500)


def test_presence_range_legacy_path_does_not_match_other_classes() -> None:
    asset = _asset(
        tracking_json={
            "subject_class": "car",
            "frames": [{"t_ms": 1_000}],
        },
    )
    assert _subject_presence_range_ms(asset, "dog") is None


def test_presence_range_unions_multiple_matching_tracks() -> None:
    """Two tracks of the same class at different times — take the
    earliest start and latest end across both."""
    asset = _asset(
        duration_ms=20_000,
        tracking_json={
            "tracks": [
                {"cls_name": "person", "frames": [{"t_ms": 2_000}, {"t_ms": 3_000}]},
                {"cls_name": "person", "frames": [{"t_ms": 12_000}]},
                {"cls_name": "dog", "frames": [{"t_ms": 6_000}]},
            ],
        },
    )
    assert _subject_presence_range_ms(asset, "person") == (1_500, 12_500)


# ---------- _apply_subject_filter ----------


def test_apply_subject_filter_no_op_when_class_unset() -> None:
    asset = _asset()
    score = _score(span_ms=(2_000, 6_000))
    result = _apply_subject_filter([score], assets=(asset,), subject_class=None)
    assert result == [score]


def test_apply_subject_filter_clamps_span_to_intersection() -> None:
    asset = _asset(
        duration_ms=10_000,
        tracking_json={
            "tracks": [
                {"cls_name": "person", "frames": [{"t_ms": 3_000}, {"t_ms": 5_000}]},
            ],
        },
    )
    score = _score(span_ms=(1_000, 7_000))
    [out] = _apply_subject_filter([score], assets=(asset,), subject_class="person")
    # Presence (with ±500ms padding) = (2500, 5500).
    # Intersection of (1000, 7000) and (2500, 5500) = (2500, 5500).
    assert out.best_span_ms == (2_500, 5_500)


def test_apply_subject_filter_drops_asset_when_class_absent() -> None:
    asset = _asset(
        tracking_json={"tracks": [{"cls_name": "dog", "frames": [{"t_ms": 1_000}]}]},
    )
    score = _score(span_ms=(2_000, 6_000))
    result = _apply_subject_filter([score], assets=(asset,), subject_class="person")
    assert result == []


def test_apply_subject_filter_snaps_to_presence_when_no_overlap() -> None:
    """When the LLM picked a span that doesn't overlap the subject's
    appearance window, snap to the full presence window rather than
    dropping the asset (B=snap)."""
    asset = _asset(
        duration_ms=10_000,
        tracking_json={
            "tracks": [
                {"cls_name": "cat", "frames": [{"t_ms": 7_000}, {"t_ms": 8_000}]},
            ],
        },
    )
    score = _score(span_ms=(1_000, 4_000))  # entirely before presence range
    [out] = _apply_subject_filter([score], assets=(asset,), subject_class="cat")
    # Presence = (6500, 8500); span had zero overlap → snap.
    assert out.best_span_ms == (6_500, 8_500)
