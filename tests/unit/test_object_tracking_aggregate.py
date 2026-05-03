"""v0.21 — unit tests for ``aggregate_detected_classes``.

Pure-function tests against the JSON shape that the live YOLO path
serialises (per ``object_tracking.serialise``). No DB, no GPU.
"""

from __future__ import annotations

from media_processor.services.object_tracking import aggregate_detected_classes


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
                {"cls_name": "person", "frames": [{"t_ms": i} for i in range(5)]},
                {"cls_name": "dog", "frames": [{"t_ms": i} for i in range(2)]},
            ],
        },
        {
            "tracks": [
                {"cls_name": "person", "frames": [{"t_ms": i} for i in range(3)]},
                {"cls_name": "car", "frames": [{"t_ms": i} for i in range(1)]},
            ],
        },
    ]
    rows = aggregate_detected_classes(blobs)
    assert [(r["cls_name"], r["total_frames"], r["asset_count"]) for r in rows] == [
        ("person", 8, 2),
        ("dog", 2, 1),
        ("car", 1, 1),
    ]


def test_aggregate_breaks_ties_alphabetically() -> None:
    """When two classes have the same total_frames, sort alphabetically
    so the dropdown order is deterministic between renders."""
    blobs = [
        {
            "tracks": [
                {"cls_name": "dog", "frames": [{"t_ms": 0}, {"t_ms": 1}]},
                {"cls_name": "cat", "frames": [{"t_ms": 0}, {"t_ms": 1}]},
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
            "frames": [{"t_ms": 0}, {"t_ms": 1}, {"t_ms": 2}],
            # no "tracks" key
        },
    ]
    rows = aggregate_detected_classes(blobs)
    assert rows == [{"cls_name": "car", "total_frames": 3, "asset_count": 1}]


def test_aggregate_does_not_double_count_when_tracks_present() -> None:
    """When ``tracks`` is non-empty, the legacy top-level ``frames``
    duplicates the dominant track and must NOT be counted again."""
    blobs = [
        {
            "subject_class": "person",
            "frames": [{"t_ms": i} for i in range(10)],
            "tracks": [
                {"cls_name": "person", "frames": [{"t_ms": i} for i in range(10)]},
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
                {"cls_name": "person", "frames": [{"t_ms": 0}]},
                {"cls_name": "person", "frames": [{"t_ms": 1}]},  # second track, same asset
            ],
        },
        {
            "tracks": [{"cls_name": "person", "frames": [{"t_ms": 0}]}],
        },
    ]
    rows = aggregate_detected_classes(blobs)
    assert rows == [{"cls_name": "person", "total_frames": 3, "asset_count": 2}]
