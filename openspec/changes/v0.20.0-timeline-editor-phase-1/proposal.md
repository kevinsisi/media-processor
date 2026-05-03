# v0.20.0 — Timeline editor Phase 1 (visualise current single track + BGM)

## Why

The system is **AI-auto-edit-first**: the user uploads, Gemini plans, the worker renders. That covers ~90% of the brand-content workflow. The remaining 10% — fine-trimming a take, splitting an over-long cut, dropping a clip the planner liked but the operator doesn't — currently has to be done in CapCut after export. M7 (`PATCH /drafts/{id}/order`) added segment reordering to close the gap, but only via a flat list UI inside `<DraggableTimeline>`; there's no way to *see* the cuts laid out against time, no way to adjust an in/out point, and no way to split a segment.

Without a timeline view the operator has no spatial sense of pacing, and the M8.1 dedup/top-up logic that picks segment lengths is opaque. A visual single-track timeline plus three new editing primitives (trim, split, delete) lets the operator finish a draft in-app without round-tripping through external editors, while keeping the AI-auto-edit pipeline untouched as the primary flow.

This is the **Phase 1 of three**. Phase 1 is intentionally narrow: visualise what's already there, no schema changes. The audio/overlay multi-track work that turns this into a "real" NLE comes in Phase 2 / 3 (sketched at the bottom for direction, **not** implemented in v0.20.0).

## Positioning

- **AI-auto-edit remains the default** — every existing flow (`POST /projects/{id}/edit`, the standard `<DraggableTimeline>` view, BGM picker, watermark picker, subtitle editor) is unchanged.
- **Timeline editor is an opt-in "進階編輯" mode**, entered via a button on `ProjectEdit` after a draft has been produced.
- **Edits map onto existing rows.** Phase 1 mutates `DraftSegment` (`asset_start_ms`, `asset_end_ms`, `on_timeline_*_ms`, `transition`) — nothing new in the DB.
- **Preview is real-time via native `<video>`.** No render is needed to scrub, trim, or split — the browser plays the source asset MP4 directly. A re-render only happens when the operator commits (explicit "Apply / Re-render" button).

## What Changes

### 0.20.1 Static asset mount

- New `app.mount("/media/assets", StaticFiles(directory=settings.assets_dir, check_dir=False), name="assets")` in `api/main.py`. Mirrors the BGM / watermark / drafts mounts.
- `StaticFiles` already speaks `Range:` so the `<video>` element's seek-to-position behaviour works without a custom streaming endpoint.
- **No cache control middleware** — same trade-off as `/media/bgm`: a re-uploaded asset at the same path should appear immediately in preview.
- The asset-on-disk path lives at `${ASSETS_DIR}/{project_id}/{asset_id}.{ext}` (already enforced by `routers/uploads.py`); we expose `/media/assets/{project_id}/{filename}` through the static mount. Frontend resolves the URL from `Asset.file_path` returned by `GET /assets/{id}`.

### 0.20.2 Segment editing endpoints

Three new endpoints on `routers/drafts.py`. All three:
- Mutate exactly one `DraftSegment` row.
- Run a shared `_reflow_segments_and_cut_plan(draft)` helper that re-cursors `on_timeline_*_ms` left-to-right and regenerates `cut_plan_json` (extracted from the existing `reorder_draft_segments` body).
- Use the same negative-offset two-phase parking trick for `order` updates to dodge the `UNIQUE(draft_id, order)` constraint.
- **Do NOT auto-enqueue a render.** They only mutate DB. The operator hits the existing manual "Apply / Re-render" path (a fresh `PATCH /drafts/{id}/order` with the current order, OR a new convenience `POST /drafts/{id}/rerender` — see 0.20.4) when ready.
- Return `DraftDetail` so the client gets the full updated state in one round-trip.

#### `POST /drafts/{draft_id}/segments/{seg_id}/split`

```json
Request:  { "at_ms": <int> }   // on-timeline ms, must be inside this segment
Response: DraftDetail
```

- Computes `split_at_asset_ms = asset_start_ms + (at_ms - on_timeline_start_ms)`.
- Validates: `on_timeline_start_ms < at_ms < on_timeline_end_ms` (strict; refuses split exactly at edge to avoid zero-length segments).
- Mutates the original row: `asset_end_ms` and `on_timeline_end_ms` move to the split point. `transition` is left unchanged.
- Inserts a new row immediately after, with the right-half asset/timeline range and inherited `voice_volume`, `bgm_volume`, `source_kind`, `plan_reason`, `reframe_keyframes`, **and the same `transition` as the original** (so the new row's "transition to whatever was originally next" matches what the original row used to do). New row gets a fresh `id`; `order` is the original's `order + 1` (subsequent segments shift +1).
- **Known minor artifact**: the boundary between the two halves of a split clip ends up using whatever transition the original had set (e.g. a 0.5 s wipe between two halves of the same shot). A "hard cut" semantic would require per-pair variable xfade duration, which couples to `subtitles.TRANSITION_OVERLAP_MS` and is deferred. The Inspector lets the operator change either half's `transition` to `fade` / `dissolve` for a softer split look.
- Calls `_reflow_segments_and_cut_plan(draft)`.

#### `PATCH /drafts/{draft_id}/segments/{seg_id}`

```json
Request: {
  "asset_start_ms"?: <int>,        // 0 ≤ x < asset_end_ms (clamped to asset duration)
  "asset_end_ms"?:   <int>,        // asset_start_ms < x ≤ asset duration
  "transition"?:     <str>,        // one of the renderer's known transitions
  "voice_volume"?:   <float>,      // 0.0 – 1.5
  "bgm_volume"?:     <float|null>  // 0.0 – 1.5, null = use default ducking
}
Response: DraftDetail
```

- All fields optional; only present fields update.
- Validates the asset-time bounds against `Asset.duration_ms` (loads the related `Asset`).
- After mutation, calls `_reflow_segments_and_cut_plan(draft)` so `on_timeline_*_ms` re-flow seamlessly (this segment's new duration `= asset_end_ms - asset_start_ms`; subsequent segments shift to fill the gap or open a gap accordingly — Phase 1 always reflows tight, no gaps allowed).

#### `DELETE /drafts/{draft_id}/segments/{seg_id}`

- 204 on success.
- Refuses with 409 if the draft would have zero segments after the delete (a draft with no cut plan can't render).
- Calls `_reflow_segments_and_cut_plan(draft)`.

### 0.20.3 Reflow helper

- New module-level `async def _reflow_segments_and_cut_plan(draft: Draft) -> None` in `routers/drafts.py`. Body lifted from `reorder_draft_segments` lines 426-458 with the order array becoming `sorted(draft.segments, key=lambda s: s.order)` so callers don't have to pass it.
- `reorder_draft_segments` is refactored to call this helper after its two-phase parking step.
- Helper does NOT change `draft.status` or `progress_steps_json` (callers decide whether to enqueue) — the existing reorder endpoint's render-enqueue lines stay in the endpoint body, not the helper.

### 0.20.4 Frontend — Timeline editor route + components

#### Route + entry

- New route `path="/projects/:projectId/edit/timeline/:draftId"` in `App.tsx` mapped to `<TimelineEditor />`.
- New component `web/src/pages/TimelineEditor.tsx` — fullscreen layout container, fetches draft + project + assets, owns the editor state, exposes "← 返回基本編輯" link (back to `/projects/:id/edit`).
- `pages/ProjectEdit.tsx` — new "進階編輯 ✨" button rendered next to the existing draft-status panel. Disabled when no draft exists or the draft is mid-render. On click, navigates to the timeline route.

#### Layout (CSS Modules)

Three responsive states, gated by `matchMedia` queries on the root container:

**Desktop (`min-width: 1024px`)**
```
┌─────────────────────────────────────────────────────────┐
│ Header  [← back] Project · Draft v3 · [Apply] [Export]  │
├─────────────────────────┬───────────────────────────────┤
│  Preview <video>        │  Ruler (sec/min ticks)        │
│  16:9, 1/3 width        ├───────────────────────────────┤
│                         │  Video track (clip blocks)    │
│                         ├───────────────────────────────┤
│  [▶] [00:12 / 00:58]    │  BGM track (file name + bar)  │
│  [0.5×][1×][2×]         ├───────────────────────────────┤
│  ─slider─ 1.11×         │  (continues below)            │
│                         │  Inspector (slide-in)         │
└─────────────────────────┴───────────────────────────────┘
        ← 1/3 →                     ← 2/3 →
```

**Mobile landscape (`orientation: landscape AND max-width: 1023px`)**
- Identical 1/3 + 2/3 split, but preview pane uses `aspect-ratio: 9/16` (vertical-video framing).
- Inspector becomes a bottom-sheet that slides up over the lower half of the timeline panel.

**Mobile portrait (`orientation: portrait AND max-width: 1023px`)**
- The timeline UI is **never rendered** in portrait. `<RotateHint />` shows a full-screen card: rotate-icon + 「進階編輯需要橫向螢幕，請旋轉裝置」.
- Route guard: when the matchMedia switches back to landscape, the editor mounts. When it flips to portrait mid-edit, unsaved edits are kept in memory (React state) but the canvas dismounts behind the hint.

#### Components

- `components/timeline/TimelineCanvas.tsx` — the right 2/3. Renders ruler + video track + BGM track + playhead. Owns the `pxPerSec` zoom state and the scroll position. Forwards events to children.
- `components/timeline/SegmentClip.tsx` — a single clip block. Background = first thumbnail of the asset (`/api/media/thumbnails/{asset_id}/frame_0.jpg`). Rendered width = `(on_timeline_end_ms - on_timeline_start_ms) * pxPerSec / 1000`. Three drag handles: body (reorder, dispatches existing `PATCH /drafts/{id}/order`), left edge (trim `asset_start_ms`), right edge (trim `asset_end_ms`). Click selects.
- `components/timeline/PlayheadCursor.tsx` — red 1-px vertical line + grip handle on the ruler. Drag updates the `<video>.currentTime` of the preview pane via context. Click on ruler jumps the playhead.
- `components/timeline/PreviewPane.tsx` — the left 1/3. Native `<video>` element. Resolves the right asset by mapping the playhead's on-timeline ms to the segment that owns that ms, then sets `<video>.src` to `/media/assets/{project_id}/{asset_filename}` and `<video>.currentTime` to the corresponding asset-time. Switches `src` when the playhead crosses a segment boundary. Below the video: `<TransportControls />`.
- `components/timeline/TransportControls.tsx` — play/pause toggle, current time / total time readout, **fine-grained speed control**:
  - `<input type="range" min="0.25" max="3.0" step="0.01">` slider that drives `<video>.playbackRate` directly (HTML5 video supports arbitrary float `playbackRate`, so no quantisation needed).
  - Numeric readout next to the slider showing 2-decimal value (e.g. `1.11×`); clicking the number swaps it for an `<input type="number" step="0.01" min="0.25" max="3.0">` so the operator can type an exact value.
  - Three quick-jump buttons `0.5× / 1× / 2×` for the common cases — clicking snaps the slider to that value.
  - Layout: single horizontal row at desktop / mobile-landscape ≥ 480px — `[▶] [00:12 / 00:58]   [0.5×][1×][2×] [─slider─] 1.11×`. Below 480px the row wraps to two lines: `[▶] [00:12 / 00:58]` on top, `[0.5×][1×][2×] [─slider─] 1.11×` underneath.
- `components/timeline/SegmentInspector.tsx` — read-only metadata + editable controls for the currently-selected segment: asset name, in/out asset-times (numeric inputs), transition (select), voice volume + BGM volume (sliders), `[Split at playhead] [Delete]` buttons. Wires to `apiClient.patchDraftSegment`, `splitDraftSegment`, `deleteDraftSegment`.
- `components/timeline/RotateHint.tsx` — full-screen card for portrait mobile.
- `components/timeline/useTimelineGestures.ts` — hook that wires `wheel` (Ctrl + wheel zooms `pxPerSec`) and `touch` events (two-finger pinch zoom). Exports `pxPerSec`, `setPxPerSec`, scroll handlers.
- `components/timeline/useDirtyState.ts` — hook that tracks whether any edits have happened since the last render. Drives the "Apply / Re-render" button's disabled state and a "* unsaved" badge on the header.

#### API client (`web/src/api/`)

- `types.ts` — add:
  ```ts
  export interface DraftSegmentSplitRequest { at_ms: number }
  export interface DraftSegmentPatch {
    asset_start_ms?: number
    asset_end_ms?: number
    transition?: string
    voice_volume?: number
    bgm_volume?: number | null
  }
  ```
- `client.ts` — add `splitDraftSegment(draftId, segId, body)`, `patchDraftSegment(draftId, segId, body)`, `deleteDraftSegment(draftId, segId)`. Each `JSON.stringify`'s the body and returns the parsed `DraftDetail` (or void for delete). Helper `assetVideoUrl(asset)` that builds `/media/assets/{project_id}/{filename}` from the absolute `asset.file_path` (strips the container prefix `/app/media/assets/`).

### 0.20.5 Apply / Re-render trigger

- The header "Apply" button calls the **existing** `PATCH /drafts/{id}/order` with the current order list. That endpoint already does `enqueue_project_edit(..., skip_plan=True)`, which re-renders against the now-mutated `DraftSegment` rows including the new asset_start/end values. No new endpoint needed.
- The button is disabled until `useDirtyState` reports unsaved changes. After click, the button shows a spinner until the polled draft status flips to `succeeded`/`failed`.

### Cross-cutting

- Version: `0.19.0 → 0.20.0` in `pyproject.toml`, `web/package.json`, `api/main.py` (`FastAPI(version=…)`).
- New memory: `v020_timeline_editor_phase_1.md` + index entry in `MEMORY.md`.
- No alembic migration (no schema changes).
- No new docker compose mounts (`assets_dir` is already mounted at `/app/media/assets` in `docker-compose.yml`).

## Impact

- **DB:** none (no schema changes).
- **Services:** none (`video_renderer` / `edit_orchestrator` / `bgm_mixer` untouched — Phase 1 only mutates DB rows that the existing skip-plan render path already reads).
- **API:** `routers/drafts.py` gains 3 endpoints + 1 helper; `api/main.py` gains 1 static mount.
- **Frontend:** new `pages/TimelineEditor.tsx` + 8 components under `components/timeline/`; `App.tsx` + `pages/ProjectEdit.tsx` get small additions; `api/client.ts` + `api/types.ts` extended.
- **Docker:** none.

## Non-goals (deferred)

- **Multi-track audio.** Phase 1 BGM track is decorative — it shows the project's bound BGM as a single horizontal bar and doesn't allow per-segment BGM clips. Phase 2 owns the multi-track audio model.
- **Overlay clips.** Image / text / PiP overlays are Phase 3.
- **Real waveform rendering for BGM.** Phase 1 shows file-name + colour bar only. Waveform peaks would need a backend pre-compute step; bundled with Phase 2.
- **Per-edit auto-render.** Decision D1 — operator hits Apply explicitly. Reordering via the existing `PATCH /drafts/{id}/order` retains its auto-enqueue for backwards compatibility; only the new segment-level endpoints are render-deferred.
- **Gap-allowed timeline.** Phase 1 always reflows tight (cursor concatenates segments). Gaps become meaningful only with multi-track (Phase 2).
- **Undo/redo.** Edit history is out of scope. Operator must rely on browser refresh to discard unsaved edits before clicking Apply.
- **Keyboard shortcuts beyond `Space` (play/pause), `←/→` (frame nudge), `Delete` (delete selected).** Power-user shortcuts (J/K/L, I/O for in/out) deferred.

## Future phases (direction-only, not in v0.20.0)

### Phase 2 — Multi-track audio (target v0.21.x)

**Goal:** independent voice / BGM / SFX tracks with per-clip in/out, fade in/out, gain.

**Schema sketch:**
- `draft_audio_tracks` (`id, draft_id, kind ∈ {voice, bgm, sfx}, name, mute, gain, order`)
- `draft_audio_clips` (`id, track_id, source_kind ∈ {asset, library, upload, ai_bgm}, source_ref, in_ms, out_ms, on_timeline_start_ms, fade_in_ms, fade_out_ms, gain`)

**Renderer:** `bgm_mixer` becomes `audio_mixer`, ffmpeg `amix` of N tracks with per-track ducking expressions.

**UI:** timeline gains N stacked audio rows; right-click on BGM library / AI-BGM picker drops a clip onto a row.

### Phase 3 — Overlay track (target v0.22.x)

**Goal:** static images, text cards, second-video PiP burned onto the rendered output.

**Schema sketch:**
- `draft_overlay_clips` (`id, draft_id, kind ∈ {image, text, video}, source_ref, x, y, w, h, on_timeline_start_ms, on_timeline_end_ms, opacity, fade_in_ms, fade_out_ms, props_json`).

**Renderer:** `video_renderer` learns an overlay-chain stage (`filter_complex` `[v][o1]overlay[v1];[v1][o2]overlay…`), placed AFTER subtitles + watermark.

**UI:** new top track on the timeline; click overlay clip → properties panel for position / size / text styling.

Both phases reuse the Phase 1 timeline canvas, segment-clip drag/trim primitives, and preview-via-native-video pattern. The pieces being built in Phase 1 are deliberately the load-bearing ones: ruler, playhead, zoom, drag/trim, preview routing, dirty-state tracking, "Apply / Re-render" button — all reused 1:1 in P2/P3.
