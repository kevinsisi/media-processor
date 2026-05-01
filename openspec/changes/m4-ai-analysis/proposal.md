## Why

M3 brought real footage and a project script into the system but stopped at upload. M4 turns that raw footage into machine-readable signal that the cut planner (M5) and the operator (晴晴) can act on:

- **What was said** —繁體中文逐字稿 with per-segment timestamps. The operator can read it on her phone, fix any mis-recognised characters in place, and feed the corrected text into downstream cut decisions.
- **How closely it followed the script** — semantic comparison (not literal text match) flagging each transcript segment as 照稿 or 即興, plus a coverage % per asset so the operator can spot improvised takes.
- **What the camera saw** — a small, generic taxonomy of scene tags (室內/室外/特寫/全景/動態/靜態/明亮/昏暗) attached as `AssetTag` rows.
- **How the camera moved** — optical-flow-based classification of each motion segment as pan / tilt / zoom / static / handheld.

Local Whisper on the dispatch host's RTX 2070 8 GB keeps audio off third-party servers; Gemini Vision (already wired in for the LLM patcher) handles the lower-bandwidth Vision and semantic-comparison calls via the same key-pool pattern.

This is the bridge from "real content in" (M3) to "AI cut planning that the operator trusts" (M5).

## What Changes

### Data model (alembic 0003_m4_analysis)

- New `asset_transcripts` (1:1 with `assets`):
  - `id`, `asset_id` (unique FK), `language`, `model` (e.g. `faster-whisper-medium`), `transcript_text` (full plain text, zh-Hant), `segments_json` (array of `{idx, start_ms, end_ms, text}`), `edited` (bool, true if user-edited since last STT run), `created_at`, `updated_at`.
- New `script_coverage` (1:1 with `assets` per project Script):
  - `id`, `asset_id` (unique FK), `script_id` (FK), `model` (e.g. `gemini-2.0-flash`), `scripted_segment_count`, `total_segment_count`, `coverage_ratio_by_count`, `coverage_ratio_by_duration_ms`, `match_details_json` (array of per-transcript-segment `{idx, classification: "scripted"|"improvised", confidence, matched_script_excerpt}`), `computed_at`.
- Reuse `asset_tags` for scene + motion outputs:
  - `tag_type='scene'` with `source_model='gemini-vision-2.0-flash'` and `tag_name` from a fixed enum (see `scene-tagging` spec).
  - `tag_type='motion'` with `source_model='opencv-optical-flow'`, `time_ranges_ms` populated for the motion segment, `tag_name` from a fixed enum (`pan`, `tilt`, `zoom`, `static`, `handheld`).
- Extend `Asset.status` accepted values to add `analyzing`, `analyzed`, `analysis_failed` (existing `pending` stays for the brief window between upload and pipeline pickup).
- New nullable `Asset.analysis_steps_json`: per-step status `{stt, scene, motion, coverage}` each `pending|running|done|failed:{reason}`. Lets the UI render progressive results.

### API (new + extended endpoints)

- `GET /assets/{id}/transcript` → `TranscriptOut | null` (404 if not yet computed).
- `PUT /assets/{id}/transcript` — body `{ segments: [{start_ms, end_ms, text}, ...] }`. Server replaces `segments_json`, recomputes `transcript_text` by joining segments, sets `edited=true`, returns `TranscriptOut`. Out-of-order or overlapping ranges are rejected 400.
- `GET /assets/{id}/coverage` → `ScriptCoverageOut | null`.
- `POST /assets/{id}/analyze` (re-trigger) — body `{steps?: ["stt"|"scene"|"motion"|"coverage"], force?: bool}`. If `steps` omitted, runs the missing/failed steps; if `force=true`, reruns even completed steps. STT respects `edited=true` unless `force=true`. Returns 202 with current `analysis_steps_json`.
- Extend `GET /assets/{id}` to include `transcript_summary` (line count, edited flag), motion segments, scene tag chips, and coverage summary.
- Extend `GET /projects/{id}` (or new `GET /projects/{id}/assets`) to return the asset list with analysis status for the project-detail page polling loop.

### Worker container (new docker-compose service)

- New `worker` service: GPU-enabled (`runtime: nvidia` + `deploy.resources.reservations.devices`), mounts the same `media` volume + `src` (read-only) so it sees uploaded files and shares ORM code with the API.
- Base image: `nvidia/cuda:12.1-cudnn8-runtime-ubuntu22.04` + Python 3.11 + ffmpeg + Python deps.
- Runs `rq worker analysis` against the existing Redis service.
- The API enqueues `analyze_asset(asset_id)` from `POST /uploads/{sid}/complete` for `kind=video` and from the manual re-trigger endpoint.
- Pipeline executes steps in sequence so the GPU job (Whisper) runs to completion before the next GPU consumer picks up. Within one job, OpenCV motion (CPU) can run while Vision API calls are in flight.

### STT (faster-whisper)

- `faster-whisper` with `medium` model by default, `compute_type=int8_float16` on CUDA — fits comfortably in 8 GB VRAM with headroom for ffmpeg / OpenCV concurrent on host.
- Force `language="zh"` and bias the decoder with `initial_prompt="以下是繁體中文影片逐字稿。"`. Run output through OpenCC `s2twp` post-converter so any simplified-character drift becomes Traditional Chinese.
- Output is normalised to SRT-style segments (`{idx, start_ms, end_ms, text}`); the full plain-text body is the joined segments separated by `\n`.
- A `WHISPER_FAKE=1` env flag returns a canned zh-Hant transcript so CI and any non-GPU dev box can exercise the rest of the pipeline.

### Scene tagging (Gemini Vision)

- Sample one frame every 5 s (configurable `SCENE_SAMPLE_INTERVAL_MS`, default `5000`) via ffmpeg.
- Send each frame to Gemini Vision (`gemini-2.0-flash` or successor) with a fixed-enum prompt: choose one or more from `[indoor, outdoor, studio, closeup, medium_shot, wide, dynamic, static, bright, dim, mixed_light]`. No industry-specific labels.
- Aggregate per-asset: a tag enters `asset_tags` only if it appears in ≥ 30 % of sampled frames OR has confidence ≥ 0.8 on at least one frame.
- Reuses the `GeminiKeyPoolConfig` rotation pattern from `llm_patcher.py`.

### Camera-motion detection (OpenCV)

- Pre-downsample the video to 320 px wide / 5 fps via ffmpeg into a scratch file under `${MEDIA_STORAGE_DIR}/analysis/{asset_id}/motion.mp4`.
- Compute Farnebäck dense optical flow between consecutive frames; aggregate flow vectors per 1-second window into a magnitude + direction summary.
- Classify each ≥ 0.8 s contiguous window into one of `{pan, tilt, zoom, static, handheld}`:
  - `pan` — sustained dominant horizontal motion
  - `tilt` — sustained dominant vertical motion
  - `zoom` — radial divergence/convergence pattern
  - `handheld` — high noise/no dominant direction
  - `static` — magnitude below threshold
- Emit one `AssetTag(tag_type='motion', tag_name=…, time_ranges_ms=[[start, end]])` row per detected window.

### Script-vs-transcript comparison (Gemini)

- Single Gemini text call per asset: prompt receives the full project Script body and the corrected transcript segments, asks the model to emit `{matches: [{transcript_idx, classification: "scripted"|"improvised", confidence, matched_script_excerpt}]}`.
- Server computes coverage = duration-weighted ratio of `scripted` to `total` ms; also count-based ratio for the UI.
- Stores the full per-segment matches in `match_details_json`. On script edit (`PUT /projects/{id}/script`) the existing coverage row is invalidated so it gets recomputed on the next analyze trigger.

### Web UI (mobile-first, 繁體中文)

- New page `/projects/:id/assets` ("素材分析") — list view per asset:
  - Header chip row: `轉錄 / 場景 / 運鏡 / 對稿` each showing pending/running/done/failed.
  - Expandable transcript: per-segment `[mm:ss → mm:ss] text` with inline edit (textarea), debounced 1.5 s autosave PUT. Saving an edit flips the segment chip to `已編輯`.
  - Tag chips: `室內 / 室外 / 特寫 / 全景 / 動態 / 靜態 / 明亮 / 昏暗` (only those that fired).
  - 運鏡時間軸: a thin horizontal bar coloured by motion type with timestamp tooltips.
  - 腳本覆蓋率卡片: `照稿 76 % · 即興 24 %` with a 2-segment progress bar.
  - "重新分析" CTA at the bottom — calls `POST /assets/{id}/analyze`.
- Polling: page polls `GET /projects/:id` every 3 s while any asset is `analyzing`; backs off to 10 s once everything is `analyzed|analysis_failed`.
- Update `Upload.tsx` summary card "進入審核" → "進入素材分析" linking to the new page.

### Bug fixes folded in (operator-reported, blocking the M4 entry flow)

- **`ProjectList` rows are not clickable.** Today only `drafted` / `approved` status cells render a "檢視" / "開啟" button; rows in `pending` / `analyzing` / unknown state have no way to enter the project. M4 needs the project-detail entry point anyway, so the whole `<li class="entry">` becomes a `<Link to="/projects/:id/assets">` (the analysis page). The status-cell buttons stay as a stronger CTA but the entire row is now a tap target.
- **Date format on the project list is truncated.** `formatCreatedAt` emits `2026·05·01 · 16:39` joined by middle dots; on the 48 px mobile / 64 px small-tablet column it visually clips to e.g. `2026-05:...16:39` per operator screenshot. Fix:
  - Change format to `2026/05/01` on line 1 and `16:39` on line 2 (`white-space: pre-line` on `.entry__num-when` honoring an embedded `\n`).
  - Two distinct lines mean both date and time render in full at every breakpoint.

These belong in M4 because the click fix routes into the new `/projects/:id/assets` page; doing them as a separate change would block the operator from testing M4 manually.

### Out of scope (deferred)

- Per-edit history / version tracking on transcripts (single current version + `edited` flag is enough for M4).
- Speaker diarisation (Whisper without `--diarize`; one column of text).
- Audio-event detection beyond speech (no music / SFX classifier).
- Custom scene-tag taxonomies — fixed generic enum only.
- Re-running motion analysis on transcript edits — motion is independent of audio.

## Capabilities

### New Capabilities

- `analysis-pipeline`: RQ worker that consumes `analyze_asset(asset_id)` jobs, runs STT → scene → motion → coverage in sequence, persists per-step status, and surfaces failures without dropping earlier results.
- `whisper-stt`: faster-whisper-on-GPU service that emits zh-Hant SRT-style segments per asset.
- `scene-tagging`: Gemini-Vision-on-frames classifier that writes generic scene `AssetTag` rows.
- `camera-motion`: OpenCV optical-flow classifier that writes per-window `motion` `AssetTag` rows with time ranges.
- `script-coverage`: Gemini semantic comparison between transcript and script with a per-asset coverage row.
- `transcript-editor-ui`: mobile-first inline-editable transcript page with autosave and analysis-status polling.

### Modified Capabilities

- `data-models`: add `asset_transcripts`, `script_coverage`; extend `Asset.status` enum; add `Asset.analysis_steps_json`.
- `core-api-routers`: add transcript / coverage / analyze endpoints to `/assets/*`; extend the asset detail response.
- `chunked-upload`: `POST /uploads/{sid}/complete` enqueues `analyze_asset` for `kind=video`.
- `mobile-upload-ui` (project list): rows become `<Link>`-wrapped tap targets to the new analysis page; date column renders date and time on two separate lines so neither truncates at narrow viewports.

## Impact

- **Code** — new modules `services/whisper_stt.py`, `services/scene_tagging.py`, `services/camera_motion.py`, `services/script_coverage.py`, `services/analysis.py` (orchestrator); new `workers/analysis_jobs.py` + `workers/__main__.py`; new `models/transcript.py`, `models/coverage.py`; extend `routers/assets.py`, `routers/uploads.py`, `routers/projects.py`. New web page `pages/ProjectAnalysis.tsx` (and CSS) plus `hooks/useAssetPolling.ts`.
- **DB** — alembic 0003.
- **Disk** — new scratch dir `${MEDIA_STORAGE_DIR}/analysis/{asset_id}/` for motion-downscaled video and frame thumbnails. Cleaned at end of pipeline.
- **Dependencies** — Python: `faster-whisper>=1.0.3`, `opencv-python-headless>=4.10`, `opencc>=1.1.7`, `numpy>=1.26`, `Pillow>=10.4`. The first three are heavy and live behind an optional `analysis` extras group so the API image stays slim — only the `worker` container installs them.
- **Docker** — new `docker/worker.Dockerfile` (CUDA 12.1 cuDNN base) and a new `worker` service in `docker-compose.yml`. Requires `nvidia-container-toolkit` on the dispatch host (already present per verification).
- **Env** — new `WHISPER_MODEL` (default `medium`), `WHISPER_COMPUTE_TYPE` (default `int8_float16`), `WHISPER_DEVICE` (default `cuda`), `WHISPER_FAKE` (default `0`), `SCENE_SAMPLE_INTERVAL_MS` (default `5000`), `GEMINI_VISION_MODEL` (default `gemini-2.0-flash`). `GEMINI_API_KEYS` already set for the LLM patcher is reused.
- **Risk** — VRAM headroom on RTX 2070 8 GB: `medium / int8_float16` measured ≈ 1.5 GB on a 30-min clip; large-v3 stays an opt-in upgrade. Optical flow on long clips is slow → mitigated by 320p / 5 fps pre-downscale. Mis-recognised Mandarin → operator-edit + re-coverage workflow covers it.
- **Version** — 0.7.1 → 0.8.0.
