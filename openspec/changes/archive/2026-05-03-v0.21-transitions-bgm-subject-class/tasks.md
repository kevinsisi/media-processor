# Tasks — v0.21-transitions-bgm-subject-class (0.21.0 – 0.21.4)

## 1. Subject-class auto-trim filter (0.21.0)

- [x] 1.1 `Project.subject_class: str | None` column;
  `alembic 0018_project_subject_class` chains after
  `0017_secondary_subtitles`.
- [x] 1.2 `services/edit_planner._subject_presence_range_ms(asset, cls)` —
  reads `tracking_json["tracks"]` with legacy top-level `frames`
  fallback for pre-v0.17 assets; pads ±`SUBJECT_PADDING_MS = 500`,
  clamps to asset duration.
- [x] 1.3 `services/edit_planner._apply_subject_filter(scores,
  *, assets, subject_class)` — drop on missing presence,
  intersection-clamp on overlap, snap-to-presence on zero-overlap.
- [x] 1.4 Wire `_apply_subject_filter` into `plan()` between scoring
  and `_assemble_plan`.
- [x] 1.5 `heuristic_fallback()` skips whole assets with no presence
  and intersects per-segment ranges (skips when zero overlap).
- [x] 1.6 `services/object_tracking.aggregate_detected_classes(blobs)`
  rolls every asset's `tracking_json` into one row per detected
  class (`total_frames` sum, `asset_count` distinct), sorted desc by
  frames with alphabetical tiebreak; counts the v0.17 `tracks`
  preferentially without double-counting the legacy `frames`.
- [x] 1.7 `PATCH /projects/{id}/subject-class` endpoint +
  `SubjectClassPatch` schema (validates against `COCO80_CLASSES`).
- [x] 1.8 `GET /projects/{id}/detected-classes` endpoint +
  `DetectedClassOut` schema.
- [x] 1.9 `Project.subject_class` round-trips through
  `_project_detail` builder.
- [x] 1.10 `web/src/components/SubjectClassPicker.tsx` — fetch
  detected classes, render with frame count, auto-PATCH top-frequency
  class on first mount when `subject_class` is null + tracking has
  produced classes; "請先完成追蹤分析" hint when none; stale-saved-
  class fallback option keeps SELECT value in sync.
- [x] 1.11 Mount `SubjectClassPicker` inside the `視覺疊加` settings
  group on `ProjectEdit`.
- [x] 1.12 Tests: `tests/unit/test_edit_planner_subject_filter.py`
  (10 cases: padding / clamp / drop-when-absent / no-tracking-fallback
  / legacy-frames-fallback / multi-track-union / no-op-when-class-
  unset / clamp-to-intersection / drop-when-class-absent / snap-on-
  no-overlap). `tests/unit/test_object_tracking_aggregate.py`
  (8 cases including no-double-count + asset-count-distinct).

## 2. Subject_class merge collision

- [x] 2.1 An earlier branch shipped `1fdba2e feat(0.21.0):
  subject_class + auto-trim non-subject segments` with a different
  design (partition into [present, missing] + soft demotion).
- [x] 2.2 Resolve via `git revert 1fdba2e` on main (commit
  `c7d0399`), then merge the new design's branch (merge commit
  `019d4c5`). History stays linear forward — no force-push.
- [x] 2.3 Both old and new design use the same alembic revision id
  `0018_project_subject_class` and add the same `subject_class
  String(64) nullable` column, so any production DB that had already
  migrated under the old commit needs no schema change.

## 3. Skip-plan re-render flag preservation (0.21.1 + 0.21.3)

- [x] 3.1 `Draft.render_flags_json: dict | None` column;
  `alembic 0019_draft_render_flags` chains after
  `0018_project_subject_class`.
- [x] 3.2 Trigger endpoint snapshots the four flags on draft
  creation.
- [x] 3.3 `_draft_render_flags(draft, override=None)` resolves
  per-flag with priority `body > Draft snapshot > all-True default`.
- [x] 3.4 `RenderFlagsOverride` schema (4 optional bools);
  `DraftReorderRequest.render_flags` + new
  `DraftRebuildSubtitlesRequest.render_flags`.
- [x] 3.5 Both `/drafts/{id}/order` and `/drafts/{id}/rebuild-subtitles`
  consume the override + backfill `Draft.render_flags_json` so the
  legacy NULL row settles into a known state.
- [x] 3.6 `rebuild-subtitles` body is optional via
  `Body(default=None)` so older clients posting with no body keep
  working.
- [x] 3.7 Frontend: `RenderFlagsOverride` type, extend
  `DraftReorderRequest` + new `DraftRebuildSubtitlesRequest` types,
  `apiClient.rebuildDraftSubtitles` accepts an optional payload.
- [x] 3.8 `DraggableTimeline` + `SubtitleEditor` accept a
  `renderFlags` prop and forward it; `ProjectEdit` wires the four
  toggle states into both.
- [x] 3.9 Tests: 4 new cases — match-state preservation, NULL
  fallback, body-override-beats-snapshot + backfill verification,
  no-body rebuild-subtitles still works, override-beats-snapshot on
  rebuild.

## 4. BGM preset UX (0.21.2 → 0.21.4)

- [x] 4.1 `presetForPrompt(prompt)` reverse-lookup against
  `PRESET_BGM_HINT` to identify which preset produced the current
  BGM; `statusOutputMatchesFilename(output_url, filename)` confirms
  the AI job's output is the project's current bgm_path.
- [x] 4.2 Match banner "✓ 已根據「文青風」生成配樂" + quiet dotted-
  underline regen link.
- [x] 4.3 Mismatch banner: two-line block with bold title
  "配樂尚未更新！目前播放的仍是舊配樂" and a body explaining the
  preset shift; primary CTA with `--loud` modifier (larger font,
  pulse animation, respects `prefers-reduced-motion`).
- [x] 4.4 Status line replaces vague "狀態：已完成" with genre-tagged
  "配樂已生成（Acoustic/indie 風格）" via `PRESET_GENRE_SHORT` map;
  mismatch case reads "🕘 舊版本：… 風格" with greyed + desaturated
  audio player.
- [x] 4.5 Auto-trigger `useEffect` watching (source, presetActive,
  presetKey, presetHint, aiStatus, aiSubmitting, aiJobInFlight,
  presetMatches). Fires once per (source, presetKey) combo via
  `autoTriggeredFor` ref latch; switching presets re-arms; in-flight
  / submitting blocks; missing presetActive / presetKey skip.
- [x] 4.6 Lift derived `lastGenPreset` / `aiOutputIsCurrent` /
  `presetMatches` / `presetMismatch` / `bgmIsExternal` out of the
  JSX IIFE into component scope (memoised) so the auto-trigger
  effect can read them.
- [x] 4.7 Match-state regen button renamed "🔄 換一首" — auto-trigger
  covers initial generation, manual click is now purely "another
  MusicGen take".

## 5. Cross-cutting

- [x] 5.1 Versions aligned across `pyproject.toml`,
  `src/media_processor/api/main.py:51`, and `web/package.json` for
  every patch (0.21.0 → 0.21.4).
- [x] 5.2 Memory + ROADMAP entries land alongside each release;
  CLAUDE.md key-files pointers updated.
- [x] 5.3 Each release deployed via `docker compose build api worker
  web && docker compose up -d` from the main repo and verified via
  `/health` (always returns the new version).
