# M7 — Manual control tasks

## 7.1 Reorder
- [ ] `PATCH /drafts/{id}/order` API endpoint validating permutation, writing `DraftSegment.order` + `on_timeline_*_ms`, refreshing `cut_plan_json`
- [ ] `run_render(skip_plan=True)` orchestrator path that loads `cut_plan_json` instead of calling Gemini
- [ ] `render_draft` worker job accepts `skip_plan` kwarg
- [ ] Frontend: `@dnd-kit/core` + `@dnd-kit/sortable` deps
- [ ] Frontend: refactor `TimelineStrip` to `<DraggableTimeline>`; debounced PATCH on drop

## 7.2 Subtitle editor
- [ ] Alembic `0008_subtitle_cue` — new table with unique `(draft_id, idx)`
- [ ] `SubtitleCue` model + relationship from Draft
- [ ] `subtitles.parse_srt(text)` round-trip helper (already half-implemented in tests)
- [ ] Orchestrator persists cues after subtitle stage
- [ ] `GET /drafts/{id}/subtitles` + `PATCH /drafts/{id}/subtitles/{idx}` endpoints
- [ ] `POST /drafts/{id}/rebuild-subtitles` enqueues skip-plan render with `subtitles_from_db=True`
- [ ] Orchestrator writes fresh SRT from `subtitle_cue` when the flag is set
- [ ] Frontend: `<SubtitleEditor>` component with tap-to-edit + debounced PATCH + 重新燒入字幕 CTA

## 7.3 Export format
- [ ] `services/exports.py::export_render` ffmpeg scale+crop+pad helper
- [ ] `POST /drafts/{id}/export` endpoint enqueueing the export job
- [ ] `export_draft(draft_id, aspect, height)` worker job in `editing` queue
- [ ] Filename convention `v{N}-{aspect}-{height}p.mp4`; do not overwrite original
- [ ] Frontend: `<ExportSheet>` bottom-sheet UI with aspect + height segment controls
- [ ] Frontend: API client + types include `triggerExport` / `pollExport`

## Cross-cutting
- [ ] api Dockerfile entrypoint → `alembic upgrade head && uvicorn`
- [ ] Version bump 0.12.0 → 0.13.0 (pyproject + web/package.json + api/main.py)
- [ ] Memory: `m7_manual_control_pipeline.md`
- [ ] CLAUDE.md: ensure references stay up to date with ROADMAP entry
- [ ] In-container smoke: reorder → 60s re-render; subtitle edit → 60s re-burn; export 9:16 → no black bars on 9:16 source
