"""v0.21 / v0.22.2 — unit tests for ``aggregate_detected_classes``.

Pure-function tests against the JSON shape that the live YOLO path
serialises (per ``object_tracking.serialise``). No DB, no GPU.

v0.22.2 — every track must have ≥ ``MIN_TRACK_FRAMES`` (5) detections
to be counted; sub-threshold tracks are filtered out as YOLO noise.
"""

from __future__ import annotations

from media_processor.services.object_tracking import (
    MIN_TRACK_FRAMES,
    aggregate_detected_classes,
)


def _frames(n: int) -> list[dict]:
    """Build a list of ``n`` frame dicts. Tests use this so the
    intent (frame count, threshold relationship) is obvious at the
    call site."""
    return [{"t_ms": i * 200} for i in range(n)]


def test_min_track_frames_constant_is_five() -> None:
    """Pin the threshold so a future tweak surfaces in tests."""
    assert MIN_TRACK_FRAMES == 5


def test_aggregate_returns_empty_for_no_assets() -> None:
    assert aggregate_detected_classes([]) == []


def test_aggregate_skips_assets_with_no_tracking() -> None:
    assert aggregate_detected_classes([None, None]) == []


def test_aggregate_skips_unrecognisable_blobs() -> None:
    """Non-dict blobs (legacy text, accidental string), missing
    cls_name, and tracks with empty frame lists are all skipped
    rather than failing the request."""
    blobs: list[dict | None] = [
        {},  # empty
        {"tracks": "not a list"},  # type: ignore[dict-item]
        {"tracks": [{"cls_name": "person", "frames": []}]},  # 0 frames
        {"tracks": [{"cls_name": ""}]},  # empty class name
    ]
    assert aggregate_detected_classes(blobs) == []


def test_aggregate_sorts_by_total_frames_desc() -> None:
    blobs = [
        {
            "tracks": [
                {"cls_name": "person", "frames": _frames(8)},
                {"cls_name": "dog", "frames": _frames(7)},
            ],
        },
        {
            "tracks": [
                {"cls_name": "person", "frames": _frames(6)},
                {"cls_name": "car", "frames": _frames(5)},
            ],
        },
    ]
    rows = aggregate_detected_classes(blobs)
    assert [(r["cls_name"], r["total_frames"], r["asset_count"]) for r in rows] == [
        ("person", 14, 2),
        ("dog", 7, 1),
        ("car", 5, 1),
    ]


def test_aggregate_breaks_ties_alphabetically() -> None:
    """When two classes have the same total_frames, sort alphabetically
    so the dropdown order is deterministic between renders."""
    blobs = [
        {
            "tracks": [
                {"cls_name": "dog", "frames": _frames(6)},
                {"cls_name": "cat", "frames": _frames(6)},
            ],
        },
    ]
    rows = aggregate_detected_classes(blobs)
    assert [r["cls_name"] for r in rows] == ["cat", "dog"]


def test_aggregate_falls_back_to_legacy_top_level_frames() -> None:
    """Pre-v0.17 assets stored only the dominant track at the top
    level (``subject_class`` + ``frames``, no ``tracks``). Make sure
    those still show up so an operator doesn't have to re-run YOLO
    just to use the v0.21 picker."""
    blobs = [
        {
            "subject_class": "car",
            "frames": _frames(8),
            # no "tracks" key
        },
    ]
    rows = aggregate_detected_classes(blobs)
    assert rows == [{"cls_name": "car", "total_frames": 8, "asset_count": 1}]


def test_aggregate_does_not_double_count_when_tracks_present() -> None:
    """When ``tracks`` is non-empty, the legacy top-level ``frames``
    duplicates the dominant track and must NOT be counted again."""
    blobs = [
        {
            "subject_class": "person",
            "frames": _frames(10),
            "tracks": [
                {"cls_name": "person", "frames": _frames(10)},
            ],
        },
    ]
    rows = aggregate_detected_classes(blobs)
    assert rows == [{"cls_name": "person", "total_frames": 10, "asset_count": 1}]


def test_aggregate_counts_distinct_assets_per_class() -> None:
    """``asset_count`` is the number of assets where the class
    appears at least once — even multiple tracks of the same class
    in one asset count as a single asset."""
    blobs = [
        {
            "tracks": [
                {"cls_name": "person", "frames": _frames(6)},
                {"cls_name": "person", "frames": _frames(7)},  # second track, same asset
            ],
        },
        {
            "tracks": [{"cls_name": "person", "frames": _frames(8)}],
        },
    ]
    rows = aggregate_detected_classes(blobs)
    assert rows == [{"cls_name": "person", "total_frames": 21, "asset_count": 2}]


# ---------- v0.22.2 — MIN_TRACK_FRAMES threshold ----------


def test_aggregate_drops_sub_threshold_track() -> None:
    """A 4-frame track is YOLO noise (single mis-classification during
    fast motion) — it should not surface as a selectable class."""
    blobs = [
        {
            "tracks": [
                {"cls_name": "person", "frames": _frames(4)},
            ],
        },
    ]
    assert aggregate_detected_classes(blobs) == []


def test_aggregate_keeps_threshold_track_drops_sub_threshold_in_same_asset() -> None:
    """Mixed asset — the long track lands; the short one is dropped.
    The class itself only counts the surviving frames."""
    blobs = [
        {
            "tracks": [
                {"cls_name": "person", "frames": _frames(7)},
                # Sub-threshold "dog" — 1-frame YOLO flicker.
                {"cls_name": "dog", "frames": _frames(2)},
            ],
        },
    ]
    rows = aggregate_detected_classes(blobs)
    assert rows == [{"cls_name": "person", "total_frames": 7, "asset_count": 1}]


def test_aggregate_legacy_path_respects_threshold() -> None:
    """Pre-v0.17 blobs use the top-level ``frames`` field. The
    threshold applies there too so legacy assets don't get a free
    pass past the noise filter."""
    short = {
        "subject_class": "car",
        "frames": _frames(3),
    }
    assert aggregate_detected_classes([short]) == []


def test_aggregate_threshold_uses_constant_not_hardcoded() -> None:
    """Sanity: a track with exactly MIN_TRACK_FRAMES detections is
    kept; one with MIN_TRACK_FRAMES - 1 is dropped. This pins the
    inequality direction (>= not >)."""
    on_threshold = {
        "tracks": [
            {"cls_name": "person", "frames": _frames(MIN_TRACK_FRAMES)},
        ],
    }
    just_under = {
        "tracks": [
            {"cls_name": "dog", "frames": _frames(MIN_TRACK_FRAMES - 1)},
        ],
    }
    assert aggregate_detected_classes([on_threshold]) == [
        {"cls_name": "person", "total_frames": MIN_TRACK_FRAMES, "asset_count": 1}
    ]
    assert aggregate_detected_classes([just_under]) == []
