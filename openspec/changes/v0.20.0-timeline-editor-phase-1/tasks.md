# v0.20.0 — Tasks

## Backend — static + endpoints
- [ ] `app.mount("/media/assets", StaticFiles(directory=settings.assets_dir, check_dir=False), name="assets")` in `api/main.py` (no cache middleware — match BGM mount).
- [ ] Extract `_reflow_segments_and_cut_plan(draft)` helper in `routers/drafts.py` from the body of `reorder_draft_segments` (lines 426-458). Refactor the existing reorder endpoint to call the helper.
- [ ] `POST /drafts/{draft_id}/segments/{seg_id}/split` in `routers/drafts.py`:
  - Body schema `DraftSegmentSplitRequest { at_ms: int }`.
  - 404 if draft / segment not found; 400 if `at_ms` not strictly inside `(on_timeline_start_ms, on_timeline_end_ms)`.
  - Computes `split_at_asset_ms`, mutates the original row, inserts a new row (`order = original.order + 1`), shifts `order` of subsequent rows.
  - Moves `transition` to the new (right-half) row; original's `transition` becomes `"cut"`.
  - Two-phase parking-offset for `order` reassignment (same trick as reorder).
  - Calls `_reflow_segments_and_cut_plan(draft)`.
  - Does NOT enqueue render. Returns `DraftDetail`.
- [ ] `PATCH /drafts/{draft_id}/segments/{seg_id}` in `routers/drafts.py`:
  - Body schema `DraftSegmentPatch` — all 5 fields optional (`asset_start_ms`, `asset_end_ms`, `transition`, `voice_volume`, `bgm_volume`).
  - Loads the related `Asset` to validate `asset_*_ms` against `Asset.duration_ms`.
  - Validation: `0 ≤ asset_start_ms < asset_end_ms ≤ asset.duration_ms`; `voice_volume ∈ [0.0, 1.5]`; `bgm_volume ∈ [0.0, 1.5] | None`; `transition` in renderer's known set.
  - Calls `_reflow_segments_and_cut_plan(draft)`.
  - Does NOT enqueue render. Returns `DraftDetail`.
- [ ] `DELETE /drafts/{draft_id}/segments/{seg_id}` in `routers/drafts.py`:
  - 404 if not found; 409 if removing would leave 0 segments.
  - Deletes row, shifts subsequent `order` values, calls `_reflow_segments_and_cut_plan(draft)`.
  - Does NOT enqueue render. Returns 204.

## Backend — schemas
- [ ] Add `DraftSegmentSplitRequest` and `DraftSegmentPatch` Pydantic models in `routers/drafts.py` (or `api/schemas/draft.py` if that exists).

## Frontend — API surface
- [ ] `web/src/api/types.ts` — add `DraftSegmentSplitRequest`, `DraftSegmentPatch`.
- [ ] `web/src/api/client.ts` — add `splitDraftSegment(draftId, segId, body)`, `patchDraftSegment(draftId, segId, body)`, `deleteDraftSegment(draftId, segId)`, `assetVideoUrl(asset)`.

## Frontend — route + entry
- [ ] `web/src/App.tsx` — add `<Route path="/projects/:projectId/edit/timeline/:draftId" element={<TimelineEditor />} />`.
- [ ] `web/src/pages/ProjectEdit.tsx` — add "進階編輯 ✨" button next to draft-status panel; navigates to the timeline route. Disabled when no draft or draft is mid-render.

## Frontend — Timeline editor page + components
- [ ] `web/src/pages/TimelineEditor.tsx` — fullscreen container; fetches draft / project / assets; owns selected-segment + dirty-state + zoom; renders `<RotateHint>` in portrait, `<TimelineCanvas>` + `<PreviewPane>` otherwise.
- [ ] `web/src/pages/TimelineEditor.css` — desktop / mobile-landscape / mobile-portrait media queries.
- [ ] `web/src/components/timeline/TimelineCanvas.tsx` (+ `.css`) — ruler + video track + BGM track + playhead; receives `pxPerSec` from gestures hook.
- [ ] `web/src/components/timeline/SegmentClip.tsx` (+ `.css`) — clip block with thumbnail BG, body drag (reorder via existing `dnd-kit`), left/right edge trim handles, click-to-select.
- [ ] `web/src/components/timeline/PlayheadCursor.tsx` (+ `.css`) — red vertical line + drag handle; updates preview's `<video>.currentTime`.
- [ ] `web/src/components/timeline/PreviewPane.tsx` (+ `.css`) — `<video>` element; switches `src` when playhead crosses a segment boundary; resolves to `/media/assets/{project_id}/{filename}`; holds `<TransportControls>` underneath.
- [ ] `web/src/components/timeline/TransportControls.tsx` (+ `.css`) — play/pause + time readout + fine-grained speed control:
  - `<input type="range" min="0.25" max="3.0" step="0.01">` slider bound to `<video>.playbackRate`.
  - 2-decimal readout (`1.11×`); click-to-edit swaps to `<input type="number">` for exact entry.
  - Quick-jump buttons `0.5× / 1× / 2×` that snap the slider.
  - Layout: single row at ≥ 480px; two-line wrap below 480px (transport on top, speed cluster below).
- [ ] `web/src/components/timeline/SegmentInspector.tsx` (+ `.css`) — read-only metadata + editable in/out / transition / voice_volume / bgm_volume; `[Split at playhead] [Delete]` buttons.
- [ ] `web/src/components/timeline/RotateHint.tsx` (+ `.css`) — full-screen "rotate to landscape" card.
- [ ] `web/src/components/timeline/useTimelineGestures.ts` — wheel + pinch zoom hook returning `pxPerSec` + scroll handlers.
- [ ] `web/src/components/timeline/useDirtyState.ts` — tracks edits since last Apply; drives header badge + Apply button.

## Tests
- [ ] `tests/unit/test_drafts_router.py` — split endpoint:
  - Splits a 4 s segment at 1.5 s → two segments with correct asset_start/end and on_timeline_start/end.
  - Original `transition` migrates to right half; original becomes `"cut"`.
  - 400 if `at_ms` at exact edge.
  - 404 if seg / draft not found.
  - Does NOT enqueue render (assert no `enqueue_project_edit` call via mock).
- [ ] `tests/unit/test_drafts_router.py` — patch segment endpoint:
  - Trim `asset_end_ms` shorter → subsequent segments reflow tight.
  - Out-of-bound `asset_end_ms > asset.duration_ms` → 422 (or 400, match existing pattern).
  - `voice_volume` clamp to [0.0, 1.5].
- [ ] `tests/unit/test_drafts_router.py` — delete segment endpoint:
  - Removes segment, subsequent `order` and `on_timeline_*_ms` reflow.
  - 409 if last segment.
- [ ] `tests/unit/test_drafts_router.py` — reorder endpoint (regression): assert refactor to use shared helper still produces same `cut_plan_json` shape.

## Verification + ship
- [ ] `pytest tests/unit/test_drafts_router.py` passes (full file).
- [ ] `pytest tests/unit -q` overall pass count unchanged or up.
- [ ] `ruff check src tests` clean.
- [ ] `cd web && npm run build` succeeds (tsc + vite).
- [ ] Bump `0.19.0 → 0.20.0` in `pyproject.toml`, `web/package.json`, `src/media_processor/api/main.py` (`FastAPI(version=…)`).
- [ ] Update `MEMORY.md` index + new memory file `v020_timeline_editor_phase_1.md` (covers: 3-phase plan, asset static mount, edits don't auto-enqueue render, Apply via existing PATCH /order).
- [ ] Commit + push.
- [ ] `docker compose up -d --build api worker web` from `D:\GitClone\_HomeProject\media-processor` (deploy host on `main`; this worktree only commits/pushes — see `deploy_topology` memory).
