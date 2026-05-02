## Why

Operators currently drive the planner via three independent, unbundled knobs (target duration, transition allowlist baked into the prompt, and the AI BGM suggestion's free-form prompt). Producing a coherent rhythm requires the user to reason about all three at once. A single pick — "this should feel fast / slow / commercial / artistic" — should cover all three.

## What Changes

### 1. New `ClipStylePreset` enum + per-draft column

- New `ClipStylePreset` StrEnum with five values: `fast`, `slow`, `commercial`, `artistic`, `custom`.
- New `Draft.style_preset` column (`String(32)`, default `"custom"`, CHECK-constrained) so each draft remembers which preset it was rendered with.
- Alembic migration `0014_draft_style_preset` (depends on `0013_draft_segment_volume`).

### 2. `services/edit_planner.StylePresetParams` bundle

Each preset packages four pieces:
- `min_span_ms` / `max_span_ms` — the clamp window applied in `_parse_asset_score` and the cap used by `_extended_span` during the duration-fill pass.
- `transition_allowlist` (frozenset of ffmpeg xfade filter names) — overrides `VALID_TRANSITIONS` per preset; out-of-set picks coerce to `default_transition`.
- `prompt_hint` — a one-paragraph banner injected at the top of the per-asset Gemini prompt (above the prior-feedback block).
- `bgm_hint` — a one-line music-style hint surfaced through the music-suggestion endpoint.

| preset      | span      | transitions                              | BGM hint                                     |
| ----------- | --------- | ---------------------------------------- | -------------------------------------------- |
| fast        | 3–5 s     | wipeleft / slideright / circlecrop       | high-energy electronic/rock 130–150 BPM      |
| slow        | 8–15 s    | dissolve / fade / fadeblack              | ambient/piano/strings 60–80 BPM              |
| commercial  | 5–8 s     | slideright / wipeleft / fadeblack        | corporate 90–110 BPM                         |
| artistic    | 3–12 s    | fade / fadewhite / fadeblack             | acoustic/indie 80–100 BPM                    |
| custom      | 1.5–6 s   | (legacy default trio)                    | (no preset hint)                             |

`fade` / `dissolve` / `fadeblack` / `fadewhite` are valid ffmpeg xfade filter names; v0.14.3 dropped them by policy. They're back in the renderer's `VALID_TRANSITIONS` allowlist so the slow / artistic / commercial presets can use them. The `custom` preset still picks from the v0.14.3 default trio so existing behaviour is unchanged.

### 3. Plumbing

- `services/edit_planner.plan(...)` accepts `style_preset: str = "custom"`; it resolves to a `StylePresetParams` and threads it through `_score_one_asset` → `_parse_asset_score` → `_assemble_plan`.
- `_assemble_plan` materialise step coerces transitions to the style's allowlist (so the emotion-shift escalation to `circlecrop` no longer sneaks past slow / artistic).
- `services/edit_orchestrator.run_render(...)` and `services/edit_orchestrator._plan_stage(...)` accept `style_preset` and forward to the planner.
- `workers/edit_jobs.render_draft(...)` + `services/queue.enqueue_project_edit(...)` thread `style_preset` through to the orchestrator.
- API: `EditTriggerRequest.style_preset` (Literal of the five values, default `"custom"`); the trigger endpoint persists the value on the new `Draft` row and forwards it to the queue.
- API: `GET /projects/{id}/music-suggestion?style_preset=fast` injects the preset's `bgm_hint` into the music-suggestion prompt so the AI BGM matches the rhythm.

### 4. Frontend — 4-card style picker (+ custom)

- New `StylePresetPicker` component on the project edit page, rendered above `RenderOptions` in both the initial (`showInitial`) and ready (`showReady`) cards.
- Five cards (fast / slow / commercial / artistic / custom), each with an icon, label, and three-line hint listing span / transition / BGM character.
- Selecting a card sets local state, which is passed to `triggerProjectEdit({ style_preset })` and to `BgmSourcePicker` so the music suggestion fetch carries the same preset.

## Acceptance

- `py -m ruff check src/ alembic/versions/0014_draft_style_preset.py` reports no new errors (8 pre-existing errors in unrelated files are unchanged).
- `npm run build` (web) compiles with no TS errors.
- A draft created with `style_preset=slow` is persisted with `drafts.style_preset='slow'`; the rendered cut plan's segments only reference transitions in `{dissolve, fade, fadeblack}`.
- The music-suggestion endpoint with `?style_preset=fast` produces a description matching the high-energy hint.
