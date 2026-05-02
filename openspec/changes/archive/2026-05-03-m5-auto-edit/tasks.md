# M5 — Auto-edit tasks

## 1. Data model + migration
- [ ] 1.1 Add `Draft.progress_steps_json: JSON | null`, `Draft.subtitle_path: VARCHAR(1024) | null`, `Draft.cut_plan_json: JSON | null`.
- [ ] 1.2 Add `DraftSegment.asset_id: INTEGER NOT NULL FK assets(id) ON DELETE RESTRICT`, `DraftSegment.asset_start_ms: INTEGER NOT NULL`, `DraftSegment.asset_end_ms: INTEGER NOT NULL`. Make `DraftSegment.asset_segment_id` nullable.
- [ ] 1.3 Keep / extend the `on_timeline_*` CHECK constraint and add `ck_draft_segments_asset_range` (`asset_start_ms < asset_end_ms`).
- [ ] 1.4 Alembic migration `0004_m5_auto_edit.py`. Round-trip clean.
- [ ] 1.5 Update `models/__init__.py` exports if needed.

## 2. Config + storage layout
- [ ] 2.1 Add `drafts_dir` is already in `Settings`. Confirm the on-disk layout `${DRAFTS_DIR}/{project_id}/v{N}.mp4` + `v{N}.srt` is created by the renderer (mkdir parents).
- [ ] 2.2 New `editing_queue` constant + queue helper `services/queue.enqueue_project_edit(project_id, *, force=False)`.
- [ ] 2.3 Define stage tokens (`PLAN`, `CUT`, `CONCAT`, `SUBTITLES`) as a `StrEnum` in `models/enums.py` plus a `EDIT_STEP_VALUES` tuple. Reuse `AnalysisStepState` values for stage state.

## 3. Edit planner — Gemini smart selection
- [ ] 3.1 `services/edit_planner.py` — `plan(project_id, session) -> CutPlan` async function. Loads project, script body, and per-asset transcripts/tags/coverage in one round-trip.
- [ ] 3.2 Prompt builder — emits a strict JSON schema (`schema_version="m5.cut-plan.v1"`) with `segments: [{asset_id, start_ms, end_ms, source_kind, reason}]`. System prompt is in zh-Hant; the prompt explicitly requires `source_kind` ∈ `{"scripted","improv"}` and demands scripted matches in script-line order.
- [ ] 3.3 Per-asset prompt block budget — ≤ 60 transcript segments verbatim; longer assets emit 8-segment buckets with start/end + concatenated text.
- [ ] 3.4 JSON validation: schema_version match, per-segment range sanity (`start_ms < end_ms`, in-bounds), `asset_id` belongs to the project. On any validation failure, raise `EditPlanInvalidError`.
- [ ] 3.5 Key-pool retry over Gemini quota errors (reuse `llm_patcher` rotation pattern). 3 attempts max; on exhaustion raise `EditPlanQuotaError`.
- [ ] 3.6 Fallback path — `services.cut_planner.plan_cuts` over the existing `AssetSegment` rows when Gemini fails. Return a `CutPlan` whose `prompt_feedback` notes the fallback.
- [ ] 3.7 Unit test with httpx-mock returning a canned plan; covers success + invalid-JSON-fallback + missing-script-still-works.

## 4. Subtitles
- [ ] 4.1 `services/subtitles.py` — `build_srt(plan: CutPlan, transcripts: dict[int, AssetTranscript]) -> str` returns SRT text. Maps each plan segment's transcript window onto the timeline; merges adjacent transcript segments inside one cut.
- [ ] 4.2 Constants for max chars per line (28) + max lines (2) + min display ms (700) + sentence-break heuristic.
- [ ] 4.3 SRT writer: timecode in `HH:MM:SS,mmm`, sequence numbers 1-indexed, blank line between blocks.
- [ ] 4.4 Unit test with fixture transcripts asserts per-cut clipping + monotonic timecodes.

## 5. Video renderer (FFmpeg)
- [ ] 5.1 `services/video_renderer.py` — `render(plan, draft_id, target_aspect, output_path, srt_path)` async. Uses `asyncio.to_thread` for blocking ffmpeg subprocess calls.
- [ ] 5.2 Aspect filter helper — for target `9:16 | 4:5 | 1:1`, build a `scale=…:force_original_aspect_ratio=increase,crop=…,setsar=1` filter chain. Constants for output widths (`1080×1920`, `1080×1350`, `1080×1080`).
- [ ] 5.3 Per-segment cut + re-encode to a uniform intermediate (libx264 yuv420p, faststart, 30 fps, CRF 20, AAC 128k). Intermediate dir under `${ANALYSIS_DIR}/edits/{draft_id}/`.
- [ ] 5.4 Concat demuxer pass — write `concat.txt`, run `ffmpeg -f concat -safe 0 -i concat.txt -c copy -movflags +faststart`.
- [ ] 5.5 Subtitle burn-in — separate ffmpeg call applies `subtitles={srt_path}:force_style='FontName=Noto Sans CJK TC,Fontsize=22,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,Outline=2,Alignment=2,MarginV=64'` to the concat output. (Two-pass keeps each step debuggable.)
- [ ] 5.6 Per-segment ffmpeg timeout 300 s; whole-render hard cap 1800 s. On timeout raise `VideoRenderTimeoutError` with the segment offset.
- [ ] 5.7 Cleanup intermediates on success; leave them on failure for inspection.
- [ ] 5.8 Unit test with `FFMPEG_FAKE=1` env that swaps the subprocess for a stub (writes empty placeholder mp4 files) so the planner→renderer→DB happy path runs in CI.

## 6. Worker job + queue
- [ ] 6.1 `workers/edit_jobs.py` — `render_draft(project_id, *, force=False)`. Owns DB session lifecycle.
- [ ] 6.2 Stage progression: load → plan (write `cut_plan_json`) → write Draft + DraftSegments → cut → concat → subtitles → done. Each stage updates `progress_steps_json`.
- [ ] 6.3 `Draft.status` lifecycle: `pending` → `processing` → `ready_for_review` (success) | `failed` (any stage fails after retries).
- [ ] 6.4 Per-stage exception → `progress_steps_json[stage] = "failed:{reason}"` + `Draft.status = 'failed'` + `Draft.prompt_feedback = error message`. Pipeline returns instead of cascading to the next stage.
- [ ] 6.5 Wire `services/queue.py::enqueue_project_edit(project_id, *, force=False)` and a new RQ queue `editing` consumed by the worker container.
- [ ] 6.6 `workers/__main__.py` listens to both `analysis` and `editing` queues so the same container handles both M4 and M5.
- [ ] 6.7 Unit test using `FFMPEG_FAKE=1` + httpx-mocked Gemini to drive the full pipeline end-to-end against a Postgres test DB.

## 7. API
- [ ] 7.1 `POST /projects/{id}/edit` body `{force?: bool}` → 202 with `DraftSummary`. Rejects with 409 if a draft is already `processing` for the project (unless `force=true`).
- [ ] 7.2 Augment `GET /drafts/{id}` response with `progress_steps`, `cut_plan` (whole stored blob), `mp4_url`, `subtitle_url`.
- [ ] 7.3 Augment `GET /projects/{id}/assets` to include `latest_draft: DraftSummary | null` + `mp4_url` so the analysis page can show 開始剪輯 / 預覽剪輯.
- [ ] 7.4 Mount `StaticFiles` at `/media/drafts` → `${DRAFTS_DIR}` with `Cache-Control: public, max-age=300` (drafts can change at the same path during re-rolls).
- [ ] 7.5 Pydantic schemas — `EditTriggerRequest`, `DraftDetail` extension, `CutPlanSegmentOut`, `DraftProgressOut`.
- [ ] 7.6 OpenAPI smoke test that the new route is registered + returns 202 on a happy-path stub.

## 8. Web — types + client
- [ ] 8.1 `web/src/api/types.ts` — `CutPlanSegment`, `DraftProgressStep`, `DraftSummary` (with `mp4_url`, `progress_steps`, `version`, `status`), extend `AssetAnalysisItem` parent shape with `latest_draft`.
- [ ] 8.2 `ApiClient` — `triggerProjectEdit(projectId, force)`, `fetchDraft(draftId)`.

## 9. Web — ProjectEdit page
- [ ] 9.1 `pages/ProjectEdit.tsx` + `ProjectEdit.css` — mobile-first 繁體中文 UI.
- [ ] 9.2 States — `processing` (progress card with 4-stage chips + spinner), `ready_for_review` (`<video controls preload="metadata" playsinline>` + timeline strip + 重新剪輯 / 下載成品 buttons), `failed` (error card + 重新嘗試).
- [ ] 9.3 Timeline strip — for each `DraftSegment`, render an asset thumbnail (use `latest_draft.cut_plan` mapped through asset thumbnails embedded on the project), the timeline range chip, and a source-kind chip (照稿 / 即興). Tap a cell → `videoRef.currentTime = on_timeline_start_ms / 1000`.
- [ ] 9.4 Download button — anchor `download` attribute pointing at the `mp4_url`.
- [ ] 9.5 Polling hook `hooks/useDraftPolling.ts` — 3 s while processing → 10 s for 1 min → stop.
- [ ] 9.6 `App.tsx` — add route `/projects/:id/edit`.
- [ ] 9.7 `pages/ProjectAnalysis.tsx` — when every asset is `analyzed | analysis_failed`, render the primary 開始剪輯 / 預覽剪輯 button bound to the `latest_draft` state.

## 10. Backfill / migration ergonomics
- [ ] 10.1 No backfill needed — old drafts predate M5 and don't have mp4 outputs. The new endpoint just creates fresh `version+1` drafts.

## 11. Verification
- [ ] 11.1 `ruff check src tests` clean.
- [ ] 11.2 `pytest -q` passes (new unit + integration tests + existing).
- [ ] 11.3 `cd web && npm run build` (`tsc -b && vite build`) passes.
- [ ] 11.4 `docker compose build api worker` succeeds.

## 12. Memory + commit + deploy
- [ ] 12.1 Update memory with drafts mp4/srt path convention + `editing` queue + ffmpeg-fake env.
- [ ] 12.2 Bump `pyproject.toml` + `web/package.json` patch versions; bump `api/main.FastAPI(version=…)` to match.
- [ ] 12.3 Commit + push on `claude/recursing-dirac-e1b72b`.
- [ ] 12.4 Pull + rebuild + redeploy on the production main worktree.
- [ ] 12.5 Converge worktree.
