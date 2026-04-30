"""Stage 4 — greedy cut planner with diversity penalty and required-segment pins.

Algorithm spec: see project design document §6.3. Pure-Python; deterministic
under tied scores via stable sort by `(score, segment_id)`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from media_processor.profile.loader import EditingRules

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SegmentInput:
    """A scored segment candidate for the cut planner.

    `primary_tag` is the dominant tag from per-asset analysis (used by the
    diversity penalty and required-segments pinning). `score` is the
    profile-weighted aggregate from stage 2.
    """

    id: int
    asset_id: int
    start_ms: int
    end_ms: int
    primary_tag: str
    score: float


@dataclass(frozen=True)
class PlannedSegment:
    """A slot in the timeline assigned to a candidate segment."""

    order: int
    segment_id: int
    asset_id: int
    asset_start_ms: int
    asset_end_ms: int
    primary_tag: str
    score: float
    on_timeline_start_ms: int
    on_timeline_end_ms: int


@dataclass
class _Candidate:
    seg: SegmentInput
    used: bool = False


def plan_cuts(
    segments: list[SegmentInput],
    beats: list[float],
    rules: EditingRules,
    *,
    warnings: list[str] | None = None,
) -> list[PlannedSegment]:
    """Plan a timeline by greedy + diversity penalty + required-segment pins.

    `beats` is in seconds (librosa convention). The output's on-timeline ranges
    are in integer milliseconds and align to consecutive beat pairs; the last
    slot's end is `target_duration_ms` per the profile.
    """
    captured: list[str] = warnings if warnings is not None else []

    target_cuts = _clamp_target(len(beats), rules, captured)
    if target_cuts == 0:
        return []

    pool: list[_Candidate] = [_Candidate(seg=s) for s in segments]
    timeline: list[PlannedSegment] = []
    pinned_indices = _resolve_pinned_indices(target_cuts, rules)

    for slot_index in range(target_cuts):
        prev_tag = timeline[-1].primary_tag if timeline else None
        chosen = _pick_for_slot(
            pool=pool,
            slot_index=slot_index,
            target_cuts=target_cuts,
            rules=rules,
            prev_tag=prev_tag,
            pinned_indices=pinned_indices,
            warnings=captured,
        )
        if chosen is None:
            captured.append(f"slot {slot_index}: no candidate available; truncating timeline")
            break
        chosen.used = True
        start_ms, end_ms = _slot_range_ms(slot_index, beats, target_cuts, rules)
        timeline.append(
            PlannedSegment(
                order=slot_index,
                segment_id=chosen.seg.id,
                asset_id=chosen.seg.asset_id,
                asset_start_ms=chosen.seg.start_ms,
                asset_end_ms=chosen.seg.end_ms,
                primary_tag=chosen.seg.primary_tag,
                score=chosen.seg.score,
                on_timeline_start_ms=start_ms,
                on_timeline_end_ms=end_ms,
            )
        )

    return timeline


def _clamp_target(beat_count: int, rules: EditingRules, warnings: list[str]) -> int:
    if beat_count == 0:
        warnings.append("no beats provided; returning empty timeline")
        return 0
    if beat_count < rules.min_cuts:
        warnings.append(
            f"beat count {beat_count} below profile.min_cuts {rules.min_cuts}; "
            "emitting timeline at beat-count length"
        )
        return beat_count
    return min(beat_count, rules.max_cuts)


def _resolve_pinned_indices(target_cuts: int, rules: EditingRules) -> dict[int, str]:
    pinned: dict[int, str] = {}
    rs = rules.required_segments
    if rs.opening_hero and target_cuts > 0:
        pinned[0] = rs.hero_tag
    if rs.closing_hero and target_cuts > 0:
        pinned[target_cuts - 1] = rs.hero_tag
    return pinned


def _pick_for_slot(
    *,
    pool: list[_Candidate],
    slot_index: int,
    target_cuts: int,
    rules: EditingRules,
    prev_tag: str | None,
    pinned_indices: dict[int, str],
    warnings: list[str],
) -> _Candidate | None:
    if slot_index in pinned_indices:
        required_tag = pinned_indices[slot_index]
        hero = _best_with_tag(pool, required_tag)
        if hero is not None:
            return hero
        warnings.append(
            f"slot {slot_index}: required tag '{required_tag}' not found; "
            "falling back to greedy selection"
        )

    available = [c for c in pool if not c.used]
    if not available:
        return None

    penalty = rules.diversity_penalty_same_tag

    def adjusted_score(cand: _Candidate) -> float:
        s = cand.seg.score
        if prev_tag is not None and cand.seg.primary_tag == prev_tag:
            s *= penalty
        return s

    # Stable sort: highest adjusted score first; ties broken by segment id ASC.
    ranked = sorted(available, key=lambda c: (-adjusted_score(c), c.seg.id))
    return ranked[0]


def _best_with_tag(pool: list[_Candidate], tag: str) -> _Candidate | None:
    matches = [c for c in pool if not c.used and c.seg.primary_tag == tag]
    if not matches:
        return None
    matches.sort(key=lambda c: (-c.seg.score, c.seg.id))
    return matches[0]


def _slot_range_ms(
    slot_index: int,
    beats: list[float],
    target_cuts: int,
    rules: EditingRules,
) -> tuple[int, int]:
    start_ms = round(beats[slot_index] * 1000)
    if slot_index + 1 < len(beats) and slot_index + 1 < target_cuts:
        end_ms = round(beats[slot_index + 1] * 1000)
    else:
        end_ms = max(start_ms + 1, rules.target_duration_ms)
    if end_ms <= start_ms:
        end_ms = start_ms + 1
    return start_ms, end_ms


@dataclass(frozen=True)
class PlanningSummary:
    """Optional human-readable summary for callers; not used by the planner."""

    cuts: int
    distinct_tags: int
    warnings: list[str] = field(default_factory=list)
