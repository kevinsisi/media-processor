## Why

After v0.19 the auto-edit pipeline could produce stylistically varied
drafts but the operator's main complaints had moved up the funnel:

1. **No way to fine-tune timing without re-rolling.** A draft might be
   95 % right with one segment that needs to be trimmed by 1 second or
   split mid-sentence. The only operator action available was "trigger
   another full edit" which costs Gemini calls + a few minutes of
   render time. Operators wanted a precise, sub-second-resolution
   timeline editor that could trim / split / delete segments without
   a re-plan.

2. **The trigger panel was hard to read.** Multiple settings groups
   (basic / BGM / visual overlay) showed all their fields at once with
   no summary of "what will happen if I click 觸發剪輯". Operators had
   to scroll up to confirm e.g. that subtitles were on or that the BGM
   was the AI-generated one. On mobile landscape the panel was nearly
   unusable.

3. **BGM source picker had three layers of indirection.** Suggested
   prompt + final effect recap + override banner — too many moving
   parts. Two operators in a row had clicked through it without
   realising which BGM their draft would actually ship with.

4. **Several silent UI bugs** — the watermark shadow lived in two
   `_project_detail` builders and one shadowed the other; the
   re-render after a status flip didn't clear the previous version's
   chip; the upload page let users navigate away mid-upload (carry-
   over from v0.18.x).

This batch is **0.20.x** because the timeline editor is large enough
to be its own milestone but the UX-clarity passes piggy-backed on the
same release window — releases 0.20.0 → 0.20.3 ship over a few days.

## What Changes

### 1. Timeline editor (Phase 1) — 0.20.0

A new opt-in `進階編輯` view inside `ProjectEdit` that replaces the
read-only timeline strip with an editable single-track view:

- **Trim**: drag either edge of a segment to extend / shrink against
  the underlying asset's duration; cursor snaps to 100 ms.
- **Split**: click the playhead handle to split a segment at the
  current scrub position; the two halves inherit the parent's
  transition / source_kind / reason.
- **Delete**: right-click → delete (with confirm); a single-cut plan
  refuses delete with a 409 so a draft never goes empty.
- **Apply**: a toolbar `Apply / Re-render` button enqueues a skip-
  plan render with the new segment table; `Discard` reverts to the
  server snapshot.

Three new endpoints (none auto-enqueue a render — only the existing
`PATCH /drafts/{id}/order` does that, on Apply):

- `POST /drafts/{id}/segments/{seg_id}/split` — body
  `{at_ms: int}` on-timeline offset. Renumbers the surrounding
  segments via the parking-offset trick to dodge the
  `UNIQUE(draft_id, order)` constraint.
- `PATCH /drafts/{id}/segments/{seg_id}` — partial update of
  `asset_start_ms` / `asset_end_ms`; bounds-checked against the
  underlying asset.
- `DELETE /drafts/{id}/segments/{seg_id}` — same renumber dance;
  refuses when only one segment remains.

A shared `_reflow_segments_and_cut_plan(draft)` helper recursors the
on-timeline coordinates + regenerates `cut_plan_json["segments"]` so
the skip-plan render path reads consistent state.

Slider speed bump: the auto-edit duration picker uses a quick-pick
list (`[30, 60, 90, 120]` s) instead of free-form text for the most
common targets.

### 2. Mobile landscape patch — 0.20.1

The timeline editor's drag handles were 4 px wide on landscape mobile
(unusable). Pass widens hit-targets to 14 px while the visual rail
stays at 2 px so the timeline looks the same; keyboard left/right
arrows now nudge the focused edge by 100 ms for accessibility.

### 3. UX clarity pass — 0.20.2

- **Three status-summary sub-cards on `ProjectEdit`.** Each settings
  group (基本剪輯設定 / 配樂 / 視覺疊加) gets a one-line summary at
  the top showing the current resolved state — e.g.
  `60 秒 · 文青風 · 字幕中下方` so the operator can confirm at a
  glance without expanding the group.
- **`StylePreset → BGM` interaction banner.** When the user picks a
  non-`custom` style preset and the current BGM source is `none` /
  upload / library (i.e. won't auto-update), an inline banner
  surfaces "風格預設「文青風」搭配建議：依風格預設自動生成" with a
  one-click switch.
- **`WatermarkPicker` optimistic-feedback patch.** The post-upload
  banner displays the just-uploaded filename for ~2.5 s before the
  parent's project state finishes round-tripping, so the user sees
  the green ✓ even on flaky parent state.
- **`ProjectAnalysis` per-step status grid + retry icons.** Each
  pipeline step (transcript / scene-tag / coverage / motion / emotion
  / tracking) gets its own row with a small retry icon when failed.
- **BGM 5-radio simplification (a 0.20.3 follow-up).** Collapses the
  two-layer "suggestion + final effect" UX into a single 5-radio
  picker (none / preset / library / ai / upload) where each radio IS
  the final outcome. No more "suggestion banner".

### 4. Bug fixes — 0.20.3

- **`_project_detail` duplicate shadow bug.** Two `_project_detail`
  builders existed in `routers/projects.py` (last-definition-wins),
  so `GET /projects/{id}` always returned `watermark_path = NULL`
  even when the upload had set it. Folded into a single canonical
  builder.
- **`WatermarkPicker` `createObjectURL` live preview.** Use a local
  object-URL while the POST round-trips so the canvas pops to the
  new logo instantly; revoked on unmount + when the server's
  `watermark_url` (with cache-bust query) lands.

## Impact

- **API surface.** Three new draft segment endpoints; existing
  `PATCH /drafts/{id}/order` unchanged in shape.
- **No schema changes.** All work is segment-level mutations on
  existing rows + new shared reflow helper.
- **Renderer / planner.** Untouched. Timeline editor produces a new
  segment table that the existing skip-plan render path consumes.
- **UX.** Significantly clearer trigger panel; mobile landscape now
  usable; one-click corrections that previously needed a full re-roll.
- **Backwards compat.** All legacy drafts open in the timeline editor
  cleanly (the read-only fallback still works when `cut_plan_json`
  is missing).
