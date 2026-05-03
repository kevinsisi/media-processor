# Tasks — v0.20-timeline-editor-ux-pass (0.20.0 – 0.20.3)

## 1. Timeline editor Phase 1 (0.20.0)

- [x] 1.1 New page `web/src/pages/TimelineEditor.tsx` (route
  `/projects/:id/drafts/:draftId/timeline`).
- [x] 1.2 `web/src/components/DraggableTimeline.tsx` — single-track
  view with edge-drag, split handle, right-click delete.
- [x] 1.3 `POST /drafts/{id}/segments/{seg_id}/split` body
  `{at_ms: int}`; backend reflows surrounding segments via the
  parking-offset trick + regenerates `cut_plan_json`.
- [x] 1.4 `PATCH /drafts/{id}/segments/{seg_id}` body
  `{asset_start_ms, asset_end_ms}`; bounds-checked against asset
  duration.
- [x] 1.5 `DELETE /drafts/{id}/segments/{seg_id}` — refuses when only
  one segment remains (409); same reflow + cut_plan regeneration.
- [x] 1.6 Shared `_reflow_segments_and_cut_plan(draft)` helper used
  by all three endpoints.
- [x] 1.7 None of the three endpoints auto-enqueue a render. The
  existing `PATCH /drafts/{id}/order` (Apply button) keeps doing the
  skip-plan re-render.
- [x] 1.8 Quick-pick duration buttons in the trigger picker
  (`[30, 60, 90, 120]` s).

## 2. Mobile landscape patch (0.20.1)

- [x] 2.1 Widen edge-drag hit-targets to 14 px (visual rail still
  2 px).
- [x] 2.2 Keyboard left/right arrow nudges the focused edge by 100 ms.
- [x] 2.3 Mobile-landscape media query collapses the playhead label
  so the timeline doesn't overflow.

## 3. UX clarity pass (0.20.2)

- [x] 3.1 Three settings-group summary lines in `ProjectEdit` —
  `formatBasicSummary` / `formatBgmSummary` / `formatVisualSummary`
  produce one-liner state strings.
- [x] 3.2 `SettingsGroup` component with collapsible header showing
  the summary line.
- [x] 3.3 `StylePreset → BGM` interaction banner inline in the BGM
  section when a non-custom preset is set and source is none /
  upload / library.
- [x] 3.4 `WatermarkPicker` optimistic-feedback state — keeps the
  just-uploaded filename for 2.5 s after POST regardless of
  parent state.
- [x] 3.5 `ProjectAnalysis` per-step status grid + retry icon when
  the step is `failed:*`.

## 4. BGM 5-radio simplification (0.20.3)

- [x] 4.1 Collapse the previous two-layer (suggestion + final
  effect) BGM picker into a single 5-radio group: `none` /
  `preset` / `library` / `ai` / `upload`.
- [x] 4.2 Each radio's panel renders the FINAL outcome — no
  suggestion-banner override layer.
- [x] 4.3 Sticky `userChoseSourceRef` so the auto-switch from `none`
  → `preset` (when a non-custom style is in play) doesn't override
  an explicit user click.

## 5. 0.20.3 bug fixes

- [x] 5.1 Fold the duplicate `_project_detail` builder in
  `routers/projects.py` into one canonical version so
  `watermark_path` round-trips correctly on `GET /projects/{id}`.
- [x] 5.2 `WatermarkPicker` uses `createObjectURL` for the local
  preview while the POST round-trips; revokes on unmount + when
  server `watermark_url` lands.

## 6. Tests

- [x] 6.1 `tests/unit/test_drafts_segment_endpoints.py` covers
  split / patch / delete + reflow correctness +
  `cut_plan_json["segments"]` regeneration.
- [x] 6.2 Reorder regression — `PATCH /drafts/{id}/order` still
  enqueues a render after the segment-level endpoints refactored
  the shared reflow helper.
- [x] 6.3 Frontend type-check + build green for the new components.
