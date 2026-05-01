# M4 — AI Analysis tasks

## 1. Data model + migration
- [ ] 1.1 New `AssetTranscript` ORM (`models/transcript.py`) with `segments_json`, `edited`, `language`, `model`.
- [ ] 1.2 New `ScriptCoverage` ORM (`models/coverage.py`).
- [ ] 1.3 Extend `Asset.status` accepted enum (`enums.py`) to include `analyzing | analyzed | analysis_failed`.
- [ ] 1.4 Add nullable `Asset.analysis_steps_json: JSON` column.
- [ ] 1.5 Update `models/__init__.py` exports.
- [ ] 1.6 Alembic migration `0003_m4_analysis.py` (2 new tables + Asset column add + widened CHECK constraint; downgrade reverses).
- [ ] 1.7 Migration smoke: `alembic upgrade head` then `alembic downgrade -1` round-trips clean.

## 2. Worker container + RQ wiring
- [ ] 2.1 `docker/worker.Dockerfile` — `nvidia/cuda:12.1-cudnn8-runtime-ubuntu22.04` base, Python 3.11, ffmpeg, `pip install media-processor[analysis]`.
- [ ] 2.2 `pyproject.toml` — new `[project.optional-dependencies] analysis = [...]` group with faster-whisper, opencv-python-headless, opencc, numpy, Pillow.
- [ ] 2.3 `src/media_processor/workers/__init__.py` + `workers/__main__.py` running `rq worker analysis` with the project's settings module loaded.
- [ ] 2.4 `workers/analysis_jobs.py` defining `analyze_asset(asset_id, *, steps=None, force=False)` that the RQ worker calls.
- [ ] 2.5 `services/queue.py` thin helper that exposes `enqueue_asset_analysis(asset_id)` for the API to call.
- [ ] 2.6 `docker-compose.yml` — add `worker` service with `runtime: nvidia` + device reservation, `depends_on: [redis, postgres]`, same `src` + `media` mounts as api.
- [ ] 2.7 `.env.example` — document new env vars (WHISPER_*, SCENE_SAMPLE_INTERVAL_MS, GEMINI_VISION_MODEL).

## 3. STT — faster-whisper service
- [ ] 3.1 `services/whisper_stt.py` — single `Transcriber` class with `transcribe(audio_path) -> Transcript`. Honors `WHISPER_FAKE`, `WHISPER_MODEL`, `WHISPER_COMPUTE_TYPE`, `WHISPER_DEVICE` env.
- [ ] 3.2 `initial_prompt="以下是繁體中文影片逐字稿。"` + OpenCC `s2twp` post-conversion of every segment text.
- [ ] 3.3 Lazy-load model on first call; reuse the loaded model across jobs in the same worker process.
- [ ] 3.4 Unit tests with `WHISPER_FAKE=1` round-tripping zh-Hant text + segment timestamps.

## 4. Scene tagging — Gemini Vision
- [ ] 4.1 `services/gemini_vision.py` — frame sampler (ffmpeg-based) + Vision REST POST + key-pool rotation modelled on `llm_patcher.GeminiKeyPoolConfig`.
- [ ] 4.2 Fixed allowed-tag enum constant (`SCENE_TAGS = (...)` — no industry labels).
- [ ] 4.3 Per-asset aggregation rule (≥ 30 % frames OR ≥ 0.8 confidence on at least one frame).
- [ ] 4.4 Persistence: insert `AssetTag(tag_type='scene', source_model='gemini-vision-2.0-flash', confidence=mean_conf)` rows.
- [ ] 4.5 Unit test with httpx-mock returning canned tag responses; verify aggregation + persistence.

## 5. Camera-motion detection — OpenCV
- [ ] 5.1 `services/camera_motion.py` — ffmpeg pre-downscale to scratch path, Farnebäck flow over consecutive frames, 1-second window aggregation.
- [ ] 5.2 Window classifier with thresholds in module constants (NOT magic numbers inline) producing one of `pan|tilt|zoom|static|handheld`.
- [ ] 5.3 Adjacent-window merge into `time_ranges_ms` segments, write `AssetTag(tag_type='motion', tag_name=…, time_ranges_ms=[[…]])` rows.
- [ ] 5.4 Cleanup of scratch downscale file at end of step.
- [ ] 5.5 Unit test with a synthetic video (short ffmpeg-generated test clip with known horizontal pan + static segments).

## 6. Script coverage — Gemini semantic compare
- [ ] 6.1 `services/script_coverage.py` — single Gemini text call, JSON response schema validation.
- [ ] 6.2 Coverage computation (count-based + duration-weighted) and `match_details_json` shape.
- [ ] 6.3 Persistence to `script_coverage` row (delete-then-insert if asset+script already had one).
- [ ] 6.4 Skip + record `failed:missing-script` when project has no Script.
- [ ] 6.5 `PUT /projects/{id}/script` invalidates existing coverage rows for that project's assets.
- [ ] 6.6 Unit test with httpx-mock returning canned matches.

## 7. Pipeline orchestration
- [ ] 7.1 `services/analysis.py` — orchestrator that runs steps in sequence, isolates each in its own try/except, updates `analysis_steps_json` after each step, writes `assets.status` final state.
- [ ] 7.2 Skip-rules: STT skipped when `transcript.edited=true` and not `force`; coverage skipped when project script missing.
- [ ] 7.3 Force-rules: `force=true` deletes existing scene/motion AssetTag rows for the asset before re-running, replaces transcript, recomputes coverage.
- [ ] 7.4 Per-step 30-min wall-clock timeout; emit `failed:timeout` and continue.
- [ ] 7.5 Integration test: WHISPER_FAKE=1 + httpx-mocked Gemini exercises the full pipeline against a Postgres test DB, asserts end state matches expectations.

## 8. API — transcript + coverage + analyze
- [ ] 8.1 `GET /assets/{id}/transcript` → `TranscriptOut | 404`.
- [ ] 8.2 `PUT /assets/{id}/transcript` — validate non-overlapping, ascending segments; replace `segments_json`; recompute `transcript_text`; set `edited=true`.
- [ ] 8.3 `GET /assets/{id}/coverage` → `ScriptCoverageOut | 404`.
- [ ] 8.4 `POST /assets/{id}/analyze` (body `{steps?, force?}`) — enqueues job, returns 202 with current `analysis_steps_json`.
- [ ] 8.5 Extend `GET /assets/{id}` response to include `transcript_summary`, motion segments, scene tag chips, coverage summary, `analysis_steps_json`.
- [ ] 8.6 Extend `GET /projects/{id}` response to embed each asset's analysis status (so the polling page only hits one endpoint).
- [ ] 8.7 Hook `POST /uploads/{sid}/complete` (kind=video) to call `enqueue_asset_analysis(asset_id)` after the Asset row is created.

## 9. Web — API client + types
- [ ] 9.1 Extend `web/src/api/types.ts`: `TranscriptSegment`, `TranscriptOut`, `ScriptCoverageOut`, `AnalysisStepStatus`, `AssetAnalysis`.
- [ ] 9.2 Extend `ApiClient`: `fetchTranscript`, `putTranscript`, `fetchCoverage`, `triggerAnalyze`.

## 10. Web — project analysis page
- [ ] 10.1 `pages/ProjectAnalysis.tsx` + CSS — assets list with status chips, expandable transcript with per-segment textarea inline edit (debounced 1.5 s autosave), tag chips, motion timeline bar, coverage card, 重新分析 CTA.
- [ ] 10.2 `hooks/useAssetPolling.ts` — 3 s while analyzing → 10 s for 1 min → stop. Returns `assets`, `pollIntervalMs`, `refresh()`.
- [ ] 10.3 `App.tsx` — add route `/projects/:id/assets`.
- [ ] 10.4 `pages/Upload.tsx` — change "進入審核" CTA to "進入素材分析" pointing at the new page.
- [ ] 10.5 `pages/ProjectList.tsx` — when status is `analyzing`, show 分析中 chip.

## 10b. Bug fixes folded in (operator-reported)
- [ ] 10b.1 `ProjectList` row becomes `<Link to="/projects/:id/assets">` — entire row clickable regardless of status. Status-cell buttons stay as inline overrides (use `e.stopPropagation` if React Router complains about nested links, or render the row's link as a `<div role="link">` with onClick + keyboard handler so the inner CTAs nest cleanly).
- [ ] 10b.2 `formatCreatedAt` returns `YYYY/MM/DD\nHH:MM`; `.entry__num-when` gets `white-space: pre-line` so the newline renders on every breakpoint. Verify at 375 × 812 (iPhone), 768 (tablet), and 1280 (desktop) that both lines display in full.

## 11. Mobile-first polish + zh-Hant copy
- [ ] 11.1 Touch targets ≥ 44 px on all new controls.
- [ ] 11.2 Per-segment textarea: 16 px font, line-height 1.6 for Chinese readability.
- [ ] 11.3 Single-column layout < 600 px; tag chips wrap; motion timeline horizontally scrollable on small screens.
- [ ] 11.4 zh-Hant labels for status chips: 待分析 / 分析中 / 已分析 / 分析失敗 / 已編輯; failure subtypes: 配額耗盡 / GPU 不可用 / 模型錯誤 / 缺少腳本 / 逾時.

## 12. Verification
- [ ] 12.1 `docker compose build worker` passes (image builds, faster-whisper imports, OpenCC initialises).
- [ ] 12.2 `docker compose up -d` brings all five services healthy (postgres, redis, api, web, worker); `docker compose exec worker nvidia-smi` sees the GPU from inside the container.
- [ ] 12.3 `alembic upgrade head` runs clean; `alembic downgrade -1` then re-`upgrade head` round-trips clean.
- [ ] 12.4 Backend unit + integration tests pass (`pytest`).
- [ ] 12.5 `ruff check` and `mypy` clean for new code.
- [ ] 12.6 Web `tsc -b && vite build` passes.
- [ ] 12.7 Smoke (real GPU): create project → upload short video → wait for analysis → transcript visible in zh-Hant → edit one segment → autosave persists → reload preserves edit → 重新分析 with `force=false` keeps the edit → with `force=true` overwrites.
- [ ] 12.8 Smoke (Gemini): same flow, scene tag chips populated, motion timeline shows ≥ 1 segment, coverage card shows non-null %.
- [ ] 12.9 Browser check at `/projects/:id/assets` — golden path on mobile viewport (375 × 812).

## 13. Memory + spec follow-through
- [ ] 13.1 Update memory with M4-relevant notes (e.g. faster-whisper VRAM footprint on RTX 2070, OpenCC zh-Hant post-conversion choice, worker container env).
- [ ] 13.2 Bump version 0.7.1 → 0.8.0 (`pyproject.toml`, `web/package.json`).
- [ ] 13.3 Commit + push.
