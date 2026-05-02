## Why

After M5 the auto-edit pipeline produces a watchable mp4, but the result still feels mechanical: cuts hard-jump on flat editor rhythm, two static shots can sit back-to-back, the audio is just the original voice with no underscoring, and the planning step takes 90–180 s because Gemini chews through one giant prompt with all assets at once. M6 fixes the user-visible rhythm of the cut and makes Gemini stop being the bottleneck.

This OpenSpec is post-hoc — the work was done in a single emergency batch. Captured here so future spec passes have something to anchor on.

## What Changes

### 6.1 Per-asset Gemini fanout (already in M5.x but recorded for context)

- `services/edit_planner.plan` no longer issues a single monolithic prompt. It fans out one small per-asset call (`{transcript, scene_tags, motion, coverage, script}` → `{score, position, best_span_ms, source_kind, transition_to_next, reason}`) over `httpx.AsyncClient` with key rotation; assembly into the ordered `CutPlan` happens locally in `_assemble_plan` with no extra Gemini round-trip.
- New `ASSET_SCORE_SCHEMA_VERSION = "m5.asset-score.v1"` for the per-asset response. CutPlan output schema (`m5.cut-plan.v1`) is unchanged.
- One slow / failing asset now only burns one slot, not the whole plan.

### 6.2 Rhythm-aware cut ordering

- Each `_AssetScore` carries a `dominant_motion` field, computed in `_score_one_asset` by intersecting the chosen `best_span_ms` with the asset's motion-tag `time_ranges_ms` (largest overlap wins; falls back to `static`).
- `_assemble_plan` picks the next cut by **rhythm-adjusted** score:
  - +10 if `dominant_motion` differs from the previous chosen cut (alternation bonus)
  - +15 if motion matches the bucket's preferred class (opening favours `pan/tilt/handheld`, closing favours `static`)
- Soft constraints — bonuses just shift ranking, no hard rejection. A tiny shoot with mostly-same-motion assets still produces a draft.

### 6.3 Transitions between cuts

- New `transition_to_next` field on every `_AssetScore` and on `CutPlanSegment` (default `dissolve`). Gemini suggests one of `fade / dissolve / wipeleft / slideright` per asset; unknown / missing values coerce to the default rather than rejecting the parse.
- `services/video_renderer.concat_segments` learned a second path: when called with `durations_ms` + `transitions`, it builds an ffmpeg `xfade` chain (one `[v_n]` label per transition, cumulative offsets accounting for 0.5 s overlap) plus a parallel `acrossfade` audio chain. The legacy concat-demuxer mux-only path stays for single-cut plans.
- `TRANSITION_DURATION_S = 0.5`; `VALID_TRANSITIONS` whitelist enforced inside the renderer so bad data from a stored plan doesn't crash ffmpeg.

### 6.4 Background-music mix with voice ducking

- New `services/bgm_mixer.py` with `mix_bgm(video_path, bgm_path, srt_path, output_path)`.
  - Voice presence comes from the SRT cue ranges the subtitle stage already produced — no separate VAD pass.
  - `volume` filter expression `if(between(t,a,b)+...,DUCKED,BASE)` evaluated per-frame, then `amix` with the voice track. `BGM_VOLUME_BASE = 0.55`, `BGM_VOLUME_DUCKED = 0.20`. `-shortest` clips a long BGM track to the video duration.
- New `EditStep.BGM` enum value + new `bgm` orchestrator stage between `subtitles` and `_mark_ready`. No-op when the project has no `bgm_path` set.
- New `Project.bgm_path` column (alembic `0007_project_bgm`), surfaced on `ProjectDetail` and the frontend `ProjectDetail` type.
- Storage: `BGM_DIR=/app/media/bgm` (env var, defaults under the existing media bind mount; on host: `G:\MediaStorage\bgm\{project_id}.{ext}`). No new compose volume.
- Upload: `POST /projects/{id}/bgm` multipart, streamed to disk in 1 MB chunks, capped at 50 MB, accepts `mp3 / wav / m4a / aac / flac / ogg`. `DELETE /projects/{id}/bgm` clears it. Single-file upload — the chunked-session machinery is overkill for typical BGM size.
- A BGM mix failure marks only the `bgm` stage as `failed:BgmMixError`; the subtitled mp4 already at `output_path` remains the deliverable.

### Frontend

- `EDIT_STEP_LABELS.bgm = "配樂"`, `EDIT_STEP_ORDER` extended with `bgm`. The chip auto-renders.
- `ProjectDetail.bgm_path` exposed in `web/src/api/types.ts` so a future UI can show a "BGM ✓" indicator.
- (No upload UI in this batch; users upload via API.)

### Version

- `0.11.0 → 0.12.0`. Pyproject + `web/package.json` + `api/main.py`.

## Impact

- **Affected services:** `edit_planner`, `video_renderer`, `edit_orchestrator`, `bgm_mixer` (new), `routers/projects` (new BGM endpoints + ProjectDetail field).
- **DB:** alembic `0007_project_bgm` adds `projects.bgm_path` (nullable string).
- **Frontend:** new step chip; `ProjectDetail` type widened.
- **Docs:** updated tests for `transition_to_next` field; smoke-tested xfade chain + BGM duck expression in-container before commit.
