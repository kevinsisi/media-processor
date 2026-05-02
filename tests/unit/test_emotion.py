"""Unit tests for services.emotion — blendshape heuristics + helpers.

The MediaPipe runtime is heavy and platform-dependent so these tests
exercise the pure-Python helpers (``_classify_blendshapes``,
``_merge_adjacent``, ``_pick_dominant``) plus the ``EMOTION_FAKE`` short
circuit. The actual ``classify_asset`` ML path is covered by the worker
integration tests rather than here.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from media_processor.services import emotion
from media_processor.services.emotion import (
    EMOTION_DEFAULT,
    EMOTION_TAGS,
    EmotionRange,
    _classify_blendshapes,
    _merge_adjacent,
    _pick_dominant,
)


def test_emotion_tags_canonical() -> None:
    """All known classes line up with the planner / renderer + frontend lookup."""
    assert EMOTION_TAGS == ("happy", "surprised", "serious", "neutral")


def test_classify_smile_dominates() -> None:
    bs = {
        "mouthSmileLeft": 0.6,
        "mouthSmileRight": 0.6,
        "browDownLeft": 0.4,
        "browDownRight": 0.4,
    }
    # Smile beats brow-down.
    assert _classify_blendshapes(bs) == "happy"


def test_classify_surprised_requires_jaw_plus_brow() -> None:
    # browInnerUp + browOuterUpLeft averaged → both must clear so the
    # threshold catches an actual brow-raise rather than a single
    # twitch on one landmark.
    bs = {"jawOpen": 0.6, "browInnerUp": 0.7, "browOuterUpLeft": 0.7}
    assert _classify_blendshapes(bs) == "surprised"


def test_classify_serious_brows_down() -> None:
    # browDownLeft + browDownRight summed against the threshold (so both
    # must contribute, mirroring the implementation in services/emotion.py).
    bs = {"browDownLeft": 0.4, "browDownRight": 0.4}
    assert _classify_blendshapes(bs) == "serious"


def test_classify_neutral_default() -> None:
    assert _classify_blendshapes({}) == "neutral"


def test_merge_adjacent_collapses_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(emotion, "EMOTION_SAMPLE_INTERVAL_MS", 500)
    samples = [
        (0, "neutral"),
        (500, "neutral"),
        (1000, "happy"),
        (1500, "happy"),
        (2000, "neutral"),
    ]
    ranges = _merge_adjacent(samples)
    assert ranges == [
        EmotionRange("neutral", 0, 1000),
        EmotionRange("happy", 1000, 2000),
        EmotionRange("neutral", 2000, 2500),
    ]


def test_merge_empty() -> None:
    assert _merge_adjacent([]) == []


def test_pick_dominant_picks_longest_total() -> None:
    ranges = [
        EmotionRange("happy", 0, 800),
        EmotionRange("neutral", 800, 1600),
        EmotionRange("happy", 1600, 2400),
    ]
    # happy total = 1600, neutral total = 800.
    assert _pick_dominant(ranges) == "happy"


def test_pick_dominant_falls_back_to_default() -> None:
    assert _pick_dominant([]) == EMOTION_DEFAULT


def test_classify_asset_fake(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """EMOTION_FAKE returns a deterministic 'happy' result for orchestration tests."""
    monkeypatch.setenv("EMOTION_FAKE", "1")
    media = tmp_path / "asset.mp4"
    media.write_bytes(b"fake")
    result = emotion.classify_asset(media, duration_ms=4_000)
    assert result.dominant in EMOTION_TAGS
    assert result.sampled_frames > 0
