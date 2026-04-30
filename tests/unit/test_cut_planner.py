"""Unit tests for the greedy cut planner."""

from __future__ import annotations

from media_processor.profile.loader import EditingRules, RequiredSegments
from media_processor.services.cut_planner import SegmentInput, plan_cuts


def _rules(
    *,
    min_cuts: int = 2,
    max_cuts: int = 10,
    target_ms: int = 30000,
    diversity: float = 1.0,
    opening_hero: bool = False,
    closing_hero: bool = False,
    hero_tag: str = "integral_hero_shot",
) -> EditingRules:
    return EditingRules(
        target_duration_ms=target_ms,
        min_cuts=min_cuts,
        max_cuts=max_cuts,
        diversity_penalty_same_tag=diversity,
        required_segments=RequiredSegments(
            opening_hero=opening_hero,
            closing_hero=closing_hero,
            hero_tag=hero_tag,
        ),
    )


def test_planner_is_deterministic() -> None:
    segments = [
        SegmentInput(id=1, asset_id=1, start_ms=0, end_ms=1000, primary_tag="logo", score=0.9),
        SegmentInput(id=2, asset_id=2, start_ms=0, end_ms=1000, primary_tag="wheel", score=0.8),
        SegmentInput(id=3, asset_id=3, start_ms=0, end_ms=1000, primary_tag="hero", score=0.95),
        SegmentInput(id=4, asset_id=4, start_ms=0, end_ms=1000, primary_tag="logo", score=0.7),
    ]
    beats = [0.0, 1.0, 2.0]
    rules = _rules()

    a = plan_cuts(segments, beats, rules)
    b = plan_cuts(segments, beats, rules)
    assert [s.segment_id for s in a] == [s.segment_id for s in b]
    assert [s.on_timeline_start_ms for s in a] == [s.on_timeline_start_ms for s in b]


def test_cut_count_capped_to_max_cuts() -> None:
    segments = [
        SegmentInput(id=i, asset_id=i, start_ms=0, end_ms=1000, primary_tag="t", score=1.0)
        for i in range(1, 100)
    ]
    beats = [float(i) for i in range(50)]
    rules = _rules(min_cuts=25, max_cuts=40)
    out = plan_cuts(segments, beats, rules)
    assert len(out) == 40


def test_beats_below_min_cuts_emits_warning_and_full_length() -> None:
    segments = [
        SegmentInput(id=i, asset_id=i, start_ms=0, end_ms=1000, primary_tag="t", score=1.0)
        for i in range(1, 20)
    ]
    beats = [float(i) for i in range(10)]
    rules = _rules(min_cuts=15, max_cuts=40)
    warnings: list[str] = []
    out = plan_cuts(segments, beats, rules, warnings=warnings)
    assert len(out) == 10
    assert any("min_cuts" in w for w in warnings)


def test_diversity_penalty_demotes_repeat_tag() -> None:
    # Without penalty the planner picks the highest-scoring candidate every time;
    # with a heavy penalty it picks a different-tagged candidate when scores are
    # close enough.
    segments = [
        SegmentInput(id=1, asset_id=1, start_ms=0, end_ms=1000, primary_tag="logo", score=1.0),
        SegmentInput(id=2, asset_id=2, start_ms=0, end_ms=1000, primary_tag="logo", score=0.95),
        SegmentInput(id=3, asset_id=3, start_ms=0, end_ms=1000, primary_tag="wheel", score=0.6),
    ]
    beats = [0.0, 1.0, 2.0]
    rules = _rules(diversity=0.3)
    out = plan_cuts(segments, beats, rules)
    # Slot 0: id=1 (logo, top score). Slot 1: id=2 has 0.95*0.3=0.285 < 0.6 wheel, so id=3 wins.
    assert out[0].segment_id == 1
    assert out[1].segment_id == 3


def test_opening_hero_pinned_to_highest_hero() -> None:
    segments = [
        SegmentInput(id=1, asset_id=1, start_ms=0, end_ms=1000, primary_tag="logo", score=1.5),
        SegmentInput(
            id=2, asset_id=2, start_ms=0, end_ms=1000, primary_tag="integral_hero_shot", score=0.9
        ),
        SegmentInput(
            id=3, asset_id=3, start_ms=0, end_ms=1000, primary_tag="integral_hero_shot", score=1.2
        ),
    ]
    beats = [0.0, 1.0, 2.0]
    rules = _rules(opening_hero=True)
    out = plan_cuts(segments, beats, rules)
    assert out[0].segment_id == 3
    assert out[0].primary_tag == "integral_hero_shot"


def test_missing_hero_falls_back_with_warning() -> None:
    segments = [
        SegmentInput(id=1, asset_id=1, start_ms=0, end_ms=1000, primary_tag="logo", score=1.0),
        SegmentInput(id=2, asset_id=2, start_ms=0, end_ms=1000, primary_tag="wheel", score=0.5),
    ]
    beats = [0.0, 1.0]
    rules = _rules(opening_hero=True)
    warnings: list[str] = []
    out = plan_cuts(segments, beats, rules, warnings=warnings)
    # When the hero tag is unavailable the planner falls back to greedy and
    # still emits a full timeline (one slot per beat); a warning naming the
    # missing tag is recorded so callers can surface it in the UI.
    assert len(out) >= 1
    assert any("integral_hero_shot" in w for w in warnings)


def test_segment_used_at_most_once() -> None:
    segments = [
        SegmentInput(id=1, asset_id=1, start_ms=0, end_ms=1000, primary_tag="t", score=10.0),
        SegmentInput(id=2, asset_id=2, start_ms=0, end_ms=1000, primary_tag="u", score=2.0),
        SegmentInput(id=3, asset_id=3, start_ms=0, end_ms=1000, primary_tag="v", score=1.0),
    ]
    beats = [0.0, 1.0, 2.0, 3.0]
    rules = _rules(min_cuts=1)
    out = plan_cuts(segments, beats, rules)
    chosen = [s.segment_id for s in out]
    assert len(set(chosen)) == len(chosen)


def test_on_timeline_aligned_to_beats() -> None:
    segments = [
        SegmentInput(id=i, asset_id=i, start_ms=0, end_ms=1000, primary_tag="t", score=1.0)
        for i in range(1, 5)
    ]
    beats = [0.0, 1.0, 2.0]
    rules = _rules(min_cuts=1, target_ms=3000)
    out = plan_cuts(segments, beats, rules)
    assert out[0].on_timeline_start_ms == 0
    assert out[0].on_timeline_end_ms == 1000
    assert out[1].on_timeline_start_ms == 1000
    assert out[1].on_timeline_end_ms == 2000


def test_empty_inputs_returns_empty_timeline() -> None:
    rules = _rules()
    assert plan_cuts([], [], rules) == []


def test_closing_hero_pinned_to_last_slot() -> None:
    segments = [
        SegmentInput(id=1, asset_id=1, start_ms=0, end_ms=1000, primary_tag="logo", score=2.0),
        SegmentInput(id=2, asset_id=2, start_ms=0, end_ms=1000, primary_tag="logo", score=1.5),
        SegmentInput(
            id=3, asset_id=3, start_ms=0, end_ms=1000, primary_tag="integral_hero_shot", score=0.5
        ),
    ]
    beats = [0.0, 1.0, 2.0]
    rules = _rules(closing_hero=True)
    out = plan_cuts(segments, beats, rules)
    assert out[-1].primary_tag == "integral_hero_shot"
    assert out[-1].segment_id == 3
