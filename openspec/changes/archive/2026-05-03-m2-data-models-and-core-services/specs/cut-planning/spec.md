## ADDED Requirements

### Requirement: Cut planner is a pure deterministic function

The system SHALL provide `media_processor.services.cut_planner.plan_cuts(segments, beats, rules) -> list[PlannedSegment]` that takes pre-scored segments, a beat grid, and the editing rules from a profile, and returns an ordered timeline. The function SHALL NOT touch the database, network, or filesystem.

#### Scenario: Repeated calls with the same input produce the same output

- **WHEN** `plan_cuts(segments, beats, rules)` is called twice with identical inputs
- **THEN** both invocations return the same `PlannedSegment` list (same ids, same order, same on-timeline ranges)

### Requirement: Cut count clamped to profile range

The number of cuts in the output SHALL satisfy `min_cuts ≤ len(output) ≤ max_cuts`, with `len(output) ≤ len(beats)`.

#### Scenario: Beats exceed max_cuts

- **WHEN** `len(beats) = 50`, `min_cuts = 25`, `max_cuts = 40`
- **THEN** the output has length 40

#### Scenario: Beats below min_cuts

- **WHEN** `len(beats) = 10`, `min_cuts = 15`, `max_cuts = 40`
- **THEN** the output has length 10 (planner SHALL log a warning but still emit a timeline)

### Requirement: Diversity penalty discourages tag repetition

When `editing_rules.diversity_penalty.same_tag_consecutive` is set, candidate scores SHALL be multiplied by that factor when their primary tag matches the previously selected segment's primary tag.

#### Scenario: Repeated tag is demoted

- **WHEN** the highest-raw-score candidate carries the same primary tag as the previous slot, and the next candidate carries a different tag with raw score within `1/penalty` of the first
- **THEN** the planner picks the second candidate

### Requirement: Required-segments rule pins opening / closing slots

When `editing_rules.required_segments.opening_hero` is true, slot 0 SHALL be filled with the highest-scoring segment whose primary tag matches the configured hero tag (default `integral_hero_shot`). When `closing_hero` is true the last slot follows the same rule. When no candidate matches, the planner SHALL emit a warning and fall back to greedy selection.

#### Scenario: Opening hero is pinned

- **WHEN** the candidate pool contains 2 hero segments and `opening_hero=true`
- **THEN** slot 0 is the higher-scoring hero segment

#### Scenario: No hero available, fallback warns

- **WHEN** the candidate pool has zero hero segments and `opening_hero=true`
- **THEN** the planner returns a non-empty timeline and logs a warning naming the missing tag

### Requirement: On-timeline ranges align to the beat grid

For each `PlannedSegment` at index i, `on_timeline_start_ms == round(beats[i] * 1000)` and `on_timeline_end_ms == round(beats[i+1] * 1000)` (last slot uses `target_duration_ms`).

#### Scenario: Even beats produce even timeline windows

- **WHEN** beats are `[0.0, 1.0, 2.0]` (seconds)
- **THEN** slot 0 has `on_timeline_start_ms=0, on_timeline_end_ms=1000`; slot 1 has `1000, 2000`

### Requirement: Each segment SHALL be used at most once

A given `AssetSegment.id` SHALL appear at most once in a single planner output, even when its score dominates multiple slots.

#### Scenario: Top-scoring segment is not double-used

- **WHEN** a single segment has the highest score across all slots
- **THEN** it appears in exactly one slot; subsequent slots fall through to the next-best candidate
