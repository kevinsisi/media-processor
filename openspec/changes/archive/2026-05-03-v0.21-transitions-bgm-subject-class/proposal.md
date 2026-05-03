## Why

Three orthogonal pain points surfaced in operator feedback after v0.20
shipped. They share the same release window because each is small on
its own but they all touch the auto-edit / re-render path:

1. **Skip-plan re-renders silently re-enabled toggles the operator had
   turned off.** `PATCH /drafts/{id}/order` and
   `POST /drafts/{id}/rebuild-subtitles` enqueued the render without
   passing the four render flags (`transitions` / `stabilize` /
   `subtitles` / `auto_reframe`). They all defaulted to `True` in
   `enqueue_project_edit`, so an operator who turned transitions off
   on the trigger panel and then reordered the timeline got dissolves
   back. (Worker logs caught it: `render_draft: ... transitions=True`
   on a draft whose user expected `False`.)

2. **BGM picker UX was confusing in the "依風格預設自動生成" mode.**
   After picking the source, the panel still required a manual
   "重新生成配樂" click, the filename in the panel didn't tie back to
   the chosen style, and the player kept playing the OLD generated
   wav after the operator switched style preset. Operators were
   shipping reels with stale music thinking the new preset had taken
   effect.

3. **The planner had no way to focus on a specific subject in the
   footage.** A project might be primarily about a dog, but the
   operator's footage also contained crowd shots — those crowd shots
   competed for span time on equal footing with the dog clips. The
   only fix was to manually re-roll until the LLM happened to pick
   well, or to delete unwanted assets. Neither scaled.

The release ships as **0.21.0** for the subject_class feature and
**0.21.1 – 0.21.4** as iterative fixes on the BGM + render-flag
follow-ups.

## What Changes

### 1. Subject-class auto-trim filter (0.21.0)

A new per-project picker that biases the auto-edit planner to only
keep the time range where a chosen COCO-80 class is detected in
`Asset.tracking_json` (padded ±0.5 s).

- **Schema**: `Project.subject_class: str | None` — alembic 0018,
  nullable so legacy projects keep historical "every asset eligible
  at full duration" behaviour. NULL = no filter.
- **Planner integration**: `_subject_presence_range_ms(asset, cls)`
  reads `tracking_json["tracks"]` (with a legacy top-level `frames`
  fallback for pre-v0.17 assets). `_apply_subject_filter(scores,
  *, assets, subject_class)` runs between scoring and assembly:
  - Drop asset entirely when class never appears (decision A=drop —
    explicit choice rather than soft demotion).
  - Clamp `best_span_ms` to the presence window when there's overlap.
  - Snap to the full presence window when LLM-picked span and
    presence have zero overlap (decision B=snap — preserves the
    asset rather than dropping useful footage).
- **Heuristic fallback**: same drop / clamp / skip-segment logic
  applied per-segment so the no-Gemini path stays consistent.
- **Dynamic class picker**: `aggregate_detected_classes(blobs)` rolls
  every project asset's `tracking_json` into one row per detected
  class with `total_frames` (sum) + `asset_count` (distinct), sorted
  desc by frames with alphabetical tiebreak. Counts the v0.17
  `tracks` array preferentially, falling back to legacy top-level
  `frames` without double-counting when both are present.
- **API**: `PATCH /projects/{id}/subject-class` (validated against
  `COCO80_CLASSES` server-side) and `GET /projects/{id}/detected-
  classes` (powers the dropdown).
- **Frontend**: `SubjectClassPicker.tsx` fetches detected classes,
  renders each option with frame count (e.g. "人物（1,234 幀）"),
  auto-PATCHes the most-frequent class on first mount when
  `subject_class` is NULL and the project has tracking data.
  Surfaces "請先完成追蹤分析" when no class has been detected;
  preserves stale-saved-class entries with a "(已選但目前未偵測到)"
  suffix so the SELECT value never desyncs from the saved state.

### 2. Skip-plan re-render flag preservation (0.21.1 + 0.21.3)

**0.21.1 — snapshot on trigger.** `Draft.render_flags_json` (alembic
0019, nullable JSON) snapshots the four render flags when the trigger
endpoint creates a draft. Both skip-plan endpoints
(`PATCH /drafts/{id}/order`, `POST /drafts/{id}/rebuild-subtitles`)
read the snapshot via `_draft_render_flags(draft)` and pass the
values through to `enqueue_project_edit`. Legacy NULL rows fall back
to the all-True default — backwards compatible but doesn't fix
legacy drafts whose operator expects transitions off.

**0.21.3 — body override + backfill.** Real fix for legacy drafts.
New `RenderFlagsOverride` schema (4 optional bools) on
`DraftReorderRequest.render_flags` and a new
`DraftRebuildSubtitlesRequest`. `_draft_render_flags(draft, override)`
resolves per-flag with priority `body > snapshot > all-True`. Both
endpoints write the resolved flag set back to
`Draft.render_flags_json` on success, so a legacy NULL row "settles"
into a known state on first re-render.

`rebuild-subtitles` accepts an optional body via
`Body(default=None)` so older clients posting with no body still
work. Frontend wires `DraggableTimeline` + `SubtitleEditor` with a
`renderFlags` prop carrying ProjectEdit's current toggle state;
`ProjectEdit` plumbs the four toggles into both children.

### 3. BGM preset UX iterations (0.21.2 + 0.21.4)

**0.21.2 — match / mismatch banner.** The BGM picker's preset panel
gains a `presetForPrompt(prompt)` reverse lookup against
`PRESET_BGM_HINT` and a `statusOutputMatchesFilename` check (so an
upload after generation doesn't trigger a stale "已根據 X 生成"
banner).

- Match: green banner "✓ 已根據「文青風」生成配樂", small dotted-
  underline regen link.
- Mismatch: amber banner naming the previous preset, primary CTA.
- Genre-tagged status line ("配樂已生成（Acoustic/indie 風格）") via
  a new `PRESET_GENRE_SHORT` map.

**0.21.3 — stronger mismatch UX.** Bigger banner copy
("**配樂尚未更新！目前播放的仍是舊配樂**") with two-line layout;
`--loud` modifier on the CTA (pulse animation, larger font, respects
prefers-reduced-motion); audio preview greyed + desaturated when
mismatched, status line reads "🕘 舊版本：…".

**0.21.4 — auto-trigger MusicGen.** New `useEffect` watching
`(source, presetActive, presetKey, presetHint, aiStatus,
aiSubmitting, aiJobInFlight, presetMatches)`. When source is
`preset` and the current BGM doesn't match the active preset, fire
`handleGeneratePreset()` once per (source, presetKey) combo via an
`autoTriggeredFor` ref latch. Switching presets / leaving + returning
re-arms. Manual paths (loud CTA on mismatch / quiet "🔄 換一首"
link on match) bypass the latch. The match-state button is renamed
"🔄 換一首" since auto-trigger covers the initial generation; the
manual click is now purely "MusicGen is non-deterministic, give me
another take".

Derived state (`lastGenPreset` / `aiOutputIsCurrent` /
`presetMatches` / `presetMismatch` / `bgmIsExternal`) is lifted out
of the JSX IIFE into component scope (memoised) so the new effect
can read it.

## Impact

- **Schema.** Two new alembic migrations (0018 subject_class on
  projects, 0019 render_flags_json on drafts). Both nullable so
  legacy rows behave like pre-v0.21.
- **API contract.** Three new endpoints (`PATCH /projects/{id}/subject-
  class`, `GET /projects/{id}/detected-classes`,
  `DraftRebuildSubtitlesRequest` body schema). Two existing
  endpoints (`reorder` + `rebuild-subtitles`) gain optional
  `render_flags` override + backfill.
- **Planner.** `plan()` and `heuristic_fallback()` route through
  `_apply_subject_filter` when `subject_class` is set; otherwise
  zero behaviour change.
- **UX.** BGM picker auto-resolves the common path (pick preset →
  music plays); skip-plan re-renders honour the operator's current
  toggles for both fresh and legacy drafts.
- **Backwards compat.** All four sub-versions are fully backwards
  compatible — `subject_class IS NULL` and `render_flags_json IS
  NULL` rows behave identically to pre-v0.21, and the optional
  `render_flags` body keeps no-body callers working.
