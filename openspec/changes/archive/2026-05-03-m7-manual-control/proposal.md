# M7 — Manual control（時間軸排序 / 字幕 / 匯出格式）

## Why

After M6 the auto-edit pipeline produces a *watchable* draft with rhythm, transitions and BGM. The remaining 30 % of the work that the model can't do itself is **manual touch-up**: the user wants to nudge cut order on the timeline, fix a typo in a subtitle, and export the same edit at 9:16 / 4:5 / 1:1 for different platforms. None of these touch-ups should re-trigger Gemini — they're pure ffmpeg / SRT operations on top of the existing plan.

Goal: ship a phone-first UX that lets the user finish a video in three taps after the auto-edit completes.

## What Changes

### 7.1 Timeline drag-drop reordering

- Frontend: `web/src/pages/ProjectEdit.tsx::TimelineStrip` becomes drag-droppable via `@dnd-kit/core` + `@dnd-kit/sortable`. After drop a `PATCH /api/drafts/{id}/order` is sent (debounced 500 ms).
- Backend: new `PATCH /drafts/{id}/order` accepts `{"orders": [draft_segment_id, ...]}`. The list must be a permutation of the draft's current segments; otherwise 400. The endpoint:
  - Updates `DraftSegment.order` to the new positions
  - Recomputes `on_timeline_start_ms` / `on_timeline_end_ms` cumulatively
  - Writes a fresh `cut_plan_json` reflecting the new order
  - Flips `status → pending` and enqueues `render_draft(draft_id, force=True, skip_plan=True)`
- Worker: `run_render` learns a `skip_plan: bool` flag — when set, it loads the plan from `cut_plan_json` instead of calling Gemini. Cut + concat + subtitle + bgm stages are unchanged.

### 7.2 Subtitle inline editor

- New table `subtitle_cue` (`id`, `draft_id` FK CASCADE, `idx` int, `start_ms` int, `end_ms` int, `text` text, `created_at`, `updated_at`). Unique on `(draft_id, idx)`.
- After the subtitles stage runs, the orchestrator parses the generated SRT and persists each cue to `subtitle_cue`. Re-rendering a draft truncates and re-inserts.
- New endpoints:
  - `GET /drafts/{id}/subtitles` — list cues
  - `PATCH /drafts/{id}/subtitles/{idx}` body `{"text": "..."}` — updates one cue's text (timing immutable)
  - `POST /drafts/{id}/rebuild-subtitles` — flips status to pending and enqueues a render with `skip_plan=True` AND `subtitles_from_db=True`. The orchestrator writes a fresh SRT from `subtitle_cue` rows before calling the renderer's burn-in stage.
- Frontend: a new `<SubtitleEditor>` block under the timeline. Each cue is a row: timecode (read-only) + textarea. Tap-to-edit, blur-to-save (debounced 500 ms). A "重新燒入字幕" CTA appears once any cue has been edited.

### 7.3 Export format / resolution

- New service `services/exports.py::export_render(input_path, output_path, aspect, height)` — runs ffmpeg with a scale + crop + pad chain to produce a derivative file. Aspects: `9:16 | 4:5 | 1:1`. Heights: clamped to `[480, source_height]`.
- New endpoint `POST /drafts/{id}/export` body `{"aspect": "9:16", "height": 1080}`. Enqueues an `editing` queue job `export_draft(draft_id, aspect, height)` and returns an export ID.
- File path: `${DRAFTS_DIR}/{project_id}/v{N}-{aspect}-{height}p.mp4` (alongside the original 16:9 deliverable). The original is never overwritten.
- Frontend: an "匯出" CTA opens a bottom-sheet with two segment controls (aspect + height). Submitting calls the API and shows a progress chip; on completion the new mp4 URL is added to the download list.

### Cross-cutting

- Alembic auto-upgrade: api Dockerfile entrypoint changes from `uvicorn ...` to `sh -c "alembic upgrade head && uvicorn ..."`. The api container becomes the migration runner. Worker keeps stateless.
- Version bump `0.12.0 → 0.13.0` (pyproject + web/package.json + api/main.py).
- New auto-memory entry: `m7_manual_control_pipeline.md` — captures skip-plan worker path, subtitle re-burn semantics, export filename convention.

## Impact

- **Affected services:** `edit_orchestrator` (skip_plan, subtitles_from_db), `subtitles` (parse SRT back into cues), `exports` (new), `routers/drafts` (3 new endpoints).
- **DB:** new `subtitle_cue` table (alembic `0008_subtitle_cue`).
- **Frontend:** new `@dnd-kit/core` + `@dnd-kit/sortable` deps; refactor `TimelineStrip` and add `<SubtitleEditor>` + `<ExportSheet>` components.
- **Docker:** api entrypoint runs alembic upgrade head before uvicorn — first deploy after this commit applies the new migration without manual `docker exec`.

## Non-goals (deferred)

- Per-cut alternative-take swap (use ranked _AssetScore candidates) — moved to M7.5 if requested.
- Multi-track audio / per-cut volume — deferred to M8.
- Inline trim of cut start/end on the timeline — deferred to M7.5; current scope is reorder only.
