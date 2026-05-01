## Why

Through M4 we know what every clip is *about* (transcript, scene tags, motion class, script-coverage map). The operator now stares at the analysis page and says "OK, so… edit it." Up to today the only export was the legacy CapCut .draft writer (`services/capcut_writer.py`) — useful, but it kicks the user out of the app and produces no preview.

M5 closes that loop end-to-end: one tap of **「開始剪輯」** on the analysis page should produce a watchable mp4 with burned-in zh-Hant subtitles, in the project's target aspect ratio, that the user can preview, re-roll, or download — without leaving the phone browser.

## What Changes

### Smart selection — Gemini cut planner

- New service `services/edit_planner.py` calls Gemini once per project with a structured prompt: project profile, target aspect, target duration, full script (if any), and per-asset blocks containing transcript segments, scene tags, motion segments, and coverage matches.
- Output: a `CutPlan` of ordered `CutPlanSegment` rows — `{asset_id, asset_start_ms, asset_end_ms, source_kind: "scripted" | "improv", reason: str}`. Scripted matches always go first in their script-line order; improv segments fill until target duration, weighted by scene/motion diversity and per-segment confidence.
- Reuses the existing `LLM_API_KEYS` rotation from `services/settings_store.get_llm_api_keys` (the same one M4 scene/coverage uses).
- Hard fallback: if the Gemini call fails after retries OR returns malformed JSON, fall back to the existing heuristic `services/cut_planner.plan_cuts` over the project's `AssetSegment` rows so a draft still produces (the user gets `prompt_feedback="cut-planner: gemini failed, used heuristic fallback"`).
- Prompt cap: ≤ 60 transcript segments per asset embedded; if an asset has more, summarise in 8-segment buckets so the prompt stays bounded for very long inputs.

### FFmpeg cut + concat + aspect normalisation

- New service `services/video_renderer.py` is the only place that shells out to ffmpeg for editing.
- For each `CutPlanSegment`:
  1. Cut + scale-and-crop to target aspect via filter_complex `scale=…,crop=…,setsar=1` (cover-fit — never letterbox).
  2. Re-encode (libx264 + aac, faststart, 30 fps, CRF 20) to a normalised intermediate so the final concat is frame-accurate.
- Intermediate `.mp4` files land in `${ANALYSIS_DIR}/edits/{draft_id}/seg_{order}.mp4` and are deleted after concat.
- Final concat uses ffmpeg's `concat` demuxer (mux-only, no re-encode) into `${DRAFTS_DIR}/{project_id}/v{version}.mp4`.
- Per-segment ffmpeg call timeout: 5 min. Whole-render timeout: 30 min. Failures bubble back to the worker which marks the draft `failed`.

### Subtitles — burned-in + sidecar SRT

- New service `services/subtitles.py` builds an SRT from the cut plan and per-asset transcripts. For each chosen segment, the asset transcript rows that overlap `[asset_start_ms, asset_end_ms]` are clipped and remapped onto the timeline.
- Subtitle text is already zh-Hant via the OpenCC `s2twp` pass M4 added.
- The renderer applies the SRT in two ways: (a) sidecar at `${DRAFTS_DIR}/{project_id}/v{version}.srt` and (b) burned in via the ffmpeg `subtitles=` filter on the final mux pass with `force_style='FontName=Noto Sans CJK TC,Fontsize=22,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,Outline=2,Alignment=2,MarginV=64'` (white text, 2 px black edge, bottom-centre).

### New worker job — `render_draft`

- `workers/edit_jobs.py::render_draft(project_id, *, force=False)` is the single RQ entry point on a new queue named `editing`.
- Stages tracked in `Draft.progress_steps_json`: `plan → cut → concat → subtitles → done` (each `pending | running | done | failed:{reason}`). Same shape as M4's `analysis_steps_json`, polled by the front-end.
- Concurrency: one render per project at a time (the API rejects a second `POST /edit` while `Draft.status='processing'`).

### Data model — Draft / DraftSegment additions

- New columns on `drafts`:
  - `progress_steps_json: JSON | null` — the stage tracker.
  - `subtitle_path: VARCHAR(1024) | null` — sidecar SRT path on disk.
  - `cut_plan_json: JSON | null` — the raw Gemini plan (for transparency on the preview page and for the re-roll loop).
- Make `draft_segments.asset_segment_id` **nullable** and add three new columns:
  - `asset_id: INTEGER NOT NULL FK assets(id) ON DELETE RESTRICT` (denormalised so the renderer has everything in one row).
  - `asset_start_ms: INTEGER NOT NULL`, `asset_end_ms: INTEGER NOT NULL`.
- A CHECK constraint guarantees the same `asset_start_ms < asset_end_ms` invariant that the existing `on_timeline_*` columns already enforce.
- Migration `0004_m5_auto_edit.py` is round-trip safe.

### API

- `POST /projects/{id}/edit` body `{force?: bool}` → 202 with the in-progress draft summary. Idempotent: returns the existing in-flight draft if one is already running, unless `force=true` (which cancels-by-superseding — actually the previous draft is left alone in the DB; M5 just bumps `version+1`).
- `GET /drafts/{id}` (existing) gains `progress_steps`, `cut_plan`, `mp4_url`, `subtitle_url` fields in its response so the front-end gets everything in one round-trip.
- Static mount `/media/drafts` → `${DRAFTS_DIR}` so the browser can `<video>` against `/api/media/drafts/{project_id}/v{N}.mp4` through the existing nginx proxy. Drafts get `Cache-Control: public, max-age=300` (much shorter than thumbnails — content can change as drafts re-render at the same path).
- `GET /projects/{id}/assets` (existing analysis polling endpoint) gains a `latest_draft: DraftSummary | null` field so the analysis page can show the 開始剪輯 / 預覽剪輯 CTA in the right state without a second request.

### Web — new `/projects/:id/edit` page + CTAs

- New `pages/ProjectEdit.tsx` + CSS — mobile-first, 繁體中文.
  - When `latest_draft.status === 'processing'`: progress card with the four-stage tracker and a polling indicator.
  - When `ready_for_review`: HTML5 `<video>` preview, scrollable timeline strip showing each cut (asset thumbnail + timeline range + source kind chip), 重新剪輯 / 下載成品 buttons. Tap a strip cell → seeks the video to that timeline offset.
  - When `failed`: error card with `prompt_feedback` and a retry button.
- New `hooks/useDraftPolling.ts` — same cadence as the asset polling (3 s while processing, 10 s for 1 min after, then stop).
- `pages/ProjectAnalysis.tsx` — when every asset is `analyzed | analysis_failed`, the page header shows a primary 開始剪輯 button that posts to `/projects/{id}/edit` and routes to `/projects/:id/edit`. Already-rendering or already-rendered shows 預覽剪輯.
- `App.tsx` — add route `/projects/:id/edit`.

### Out of scope (deferred)

- Manual segment-level edit UI (drag clips around, trim handles). M5 is "AI did it; you preview".
- Music-driven beat alignment beyond what `cut_planner` already does. The Gemini planner gets target duration but doesn't see the BGM track — wire it later.
- Multiple subtitle styles, per-line styling. One sane default only.
- Caption translation. Pass-through zh-Hant only.
- iOS background download / share-sheet integration. Plain anchor download.

## Capabilities

### New Capabilities

- `auto-edit`: AI-driven cut planning + ffmpeg render + sidecar/burned subtitles, exposed as a one-tap CTA from the analysis page through to a browser-playable mp4 preview and download.

### Modified Capabilities

- `core-api-routers`: new `/projects/{id}/edit` POST + `/media/drafts` static mount; existing `/drafts/{id}` augmented; existing `/projects/{id}/assets` augmented with `latest_draft`.
- `analysis-pipeline` (no orchestrator change, but) the analysis pipeline now feeds directly into M5 via the augmented project-assets response.
- `transcript-editor-ui`: the analysis page header gets a 開始剪輯 / 預覽剪輯 CTA bound to the new draft state.
