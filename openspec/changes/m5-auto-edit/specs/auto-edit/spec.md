# Auto-edit capability

## REQ-1 — Gemini cut planner

The system SHALL produce a `CutPlan` for a project by calling the configured Gemini model once with a structured prompt that includes the project's profile, target aspect ratio, target duration, full script body (if any), and per-asset blocks containing transcript segments, scene tags, motion segments, and coverage matches. The model's response SHALL conform to a JSON schema with `schema_version="m5.cut-plan.v1"` and `segments: [{asset_id, start_ms, end_ms, source_kind, reason}]`.

#### Scenario: Plan with both script and improv coverage
- **WHEN** `services.edit_planner.plan(project_id, session)` is called for a project whose assets cover the script and have additional improv content
- **THEN** the returned plan's scripted segments appear in script-line order
- **AND** improv segments fill until the target duration is approached
- **AND** every segment's `(asset_id, start_ms, end_ms)` lies inside the asset's bounds

#### Scenario: No script — improv only
- **WHEN** the project has no script
- **THEN** every returned segment has `source_kind="improv"`
- **AND** the planner does not raise

#### Scenario: Gemini quota exhausted
- **WHEN** all keys in `LLM_API_KEYS` return 429
- **THEN** the planner raises `EditPlanQuotaError`
- **AND** the worker logs the failure and falls back to the heuristic planner

#### Scenario: Invalid JSON response
- **WHEN** Gemini returns malformed JSON or a schema-version mismatch
- **THEN** the planner raises `EditPlanInvalidError`
- **AND** the worker falls back to `cut_planner.plan_cuts` over the project's `AssetSegment` rows
- **AND** the resulting draft's `prompt_feedback` notes the fallback path

## REQ-2 — Subtitle generation

The system SHALL produce a sidecar SRT file from the cut plan and per-asset transcripts. Each plan segment's transcript window (the asset's `AssetTranscript` segments overlapping `[asset_start_ms, asset_end_ms]`) SHALL be clipped, remapped onto the timeline using `on_timeline_start_ms`, and emitted as one or more SRT cues. SRT cues SHALL be ≤ 28 chars per line, ≤ 2 lines, displayed for ≥ 700 ms.

#### Scenario: Cut clips into the middle of a transcript line
- **WHEN** a plan segment runs `asset_start_ms=1500..3500` of an asset whose transcript has a single line covering `1000..4000` ms
- **THEN** the SRT cue for that segment uses the line text
- **AND** the cue's start/end time match the timeline position of the cut, not the original transcript range

#### Scenario: Adjacent transcript lines inside one cut
- **WHEN** a single plan segment covers two non-overlapping transcript lines back-to-back
- **THEN** two SRT cues are emitted with monotonic sequence numbers and non-overlapping timecodes

#### Scenario: Plan segment with no transcript overlap
- **WHEN** a plan segment falls entirely inside a region with no transcript text
- **THEN** no SRT cue is emitted for that segment
- **AND** the renderer still produces a valid SRT file (sequence numbers stay contiguous)

## REQ-3 — FFmpeg render

The system SHALL render the planned mp4 using ffmpeg in three stages: (a) per-segment cut + scale-and-crop to target aspect, re-encoded to a uniform intermediate (libx264 yuv420p, faststart, 30 fps, CRF 20, AAC 128 k); (b) concat-demuxer mux into the output path; (c) burn-in subtitle pass that applies the SRT through the `subtitles=` filter with white-text + black-edge + bottom-centre styling.

#### Scenario: 9:16 reels output from a 16:9 source
- **WHEN** the renderer processes a segment from a 1920×1080 asset with target aspect `9:16`
- **THEN** the intermediate output is 1080×1920
- **AND** the source is centre-cropped (cover-fit, no letterbox)

#### Scenario: Subtitle burn-in
- **WHEN** the renderer is given a sidecar SRT and a concat output path
- **THEN** the final mp4 has the SRT text rendered into the picture
- **AND** the sidecar SRT remains on disk at `${DRAFTS_DIR}/{project_id}/v{N}.srt`

#### Scenario: Per-segment timeout
- **WHEN** any single ffmpeg cut call takes longer than 300 s
- **THEN** the renderer raises `VideoRenderTimeoutError` naming the failed segment
- **AND** the worker marks the draft `failed` with `progress_steps_json.cut="failed:timeout"`

## REQ-4 — Draft progress + lifecycle

A draft SHALL track its render lifecycle in `progress_steps_json` with the keys `plan`, `cut`, `concat`, `subtitles`, each set to one of `pending | running | done | failed:{reason}`. `Draft.status` SHALL transition `pending → processing → ready_for_review` on success or `→ failed` on any stage failure.

#### Scenario: Successful render
- **WHEN** `render_draft` finishes without raising
- **THEN** the draft row has `status='ready_for_review'`
- **AND** `progress_steps_json` has every stage = `"done"`
- **AND** `mp4_preview_path`, `subtitle_path`, `cut_plan_json` are all populated

#### Scenario: Plan stage fails (no script, no segments anywhere)
- **WHEN** `plan` raises (e.g., the project has zero assets to choose from)
- **THEN** the draft row has `status='failed'`, `progress_steps_json.plan` starts with `"failed:"`
- **AND** subsequent stages remain at `"pending"`

#### Scenario: Concurrent edit requests
- **WHEN** the API receives a second `POST /projects/{id}/edit` while a draft for the same project is `status='processing'`
- **AND** the request body does NOT have `force=true`
- **THEN** the API returns `409 Conflict`

## REQ-5 — API + static serving

The API SHALL expose `POST /projects/{id}/edit` returning 202 with the in-progress `DraftSummary`, and SHALL augment `GET /drafts/{id}` with `progress_steps`, `cut_plan`, `mp4_url`, `subtitle_url`. The API SHALL static-serve files under `${DRAFTS_DIR}` at the URL prefix `/media/drafts/{project_id}/v{N}.mp4` (and the matching `.srt`), exposed to the browser as `/api/media/drafts/...` through the existing nginx `/api/` proxy. Cache-Control SHALL be `public, max-age=300`.

#### Scenario: Trigger render on an analyzed project
- **WHEN** the client POSTs to `/projects/42/edit` after every asset reached `analyzed | analysis_failed`
- **THEN** the response is 202 with a `DraftSummary` whose `status='processing'` and `progress_steps.plan='pending'`
- **AND** an RQ job has been enqueued on the `editing` queue

#### Scenario: Browser plays the rendered preview
- **WHEN** the browser GETs `/api/media/drafts/42/v1.mp4` after the worker completed
- **THEN** the response is 200 with `Content-Type: video/mp4`
- **AND** `Cache-Control` includes `max-age=300`

## REQ-6 — Web — ProjectEdit page

The `/projects/:id/edit` page SHALL render three states: `processing` (4-stage progress chips + polling), `ready_for_review` (HTML5 video preview + timeline strip + 重新剪輯 / 下載成品 CTAs), and `failed` (error card + 重新嘗試 CTA). The analysis page SHALL show a primary 開始剪輯 / 預覽剪輯 button when every asset has reached a terminal analysis state.

#### Scenario: Processing state
- **WHEN** `latest_draft.status === 'processing'`
- **THEN** the page shows four stage chips (規劃 / 切片 / 拼接 / 字幕) with the current stage spinning
- **AND** polls the draft endpoint every 3 s until status changes

#### Scenario: Ready-for-review state
- **WHEN** `latest_draft.status === 'ready_for_review'`
- **THEN** the page shows a `<video>` element loading `latest_draft.mp4_url`
- **AND** a horizontally-scrollable timeline strip below shows one card per cut with thumbnail + range + 照稿/即興 chip
- **AND** tapping a strip card seeks the video to the cut's timeline start

#### Scenario: 開始剪輯 / 預覽剪輯 CTA on the analysis page
- **WHEN** every asset has `status` in `{analyzed, analysis_failed}` and `latest_draft` is null
- **THEN** the analysis page header shows a primary `開始剪輯` button
- **AND** when `latest_draft.status === 'processing' | 'ready_for_review'`, the button reads `預覽剪輯` and routes to `/projects/:id/edit`
