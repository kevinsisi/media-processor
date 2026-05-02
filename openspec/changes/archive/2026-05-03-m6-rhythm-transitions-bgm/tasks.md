# M6 — Rhythm, transitions, BGM

Captured post-hoc. All checked because the work landed in one emergency batch, smoke-tested in-container, and shipped behind version 0.12.0.

## 6.1 Per-asset Gemini fanout
- [x] Replace monolithic plan prompt with per-asset `_score_one_asset` calls
- [x] `_AssetScore` dataclass + `_parse_asset_score` validator
- [x] `_assemble_plan` walks `opening → middle → closing` by score
- [x] `asyncio.gather` fanout with key-pool round-robin start offset
- [x] Tests updated to mock the new per-asset response shape

## 6.2 Rhythm-aware ordering
- [x] `_dominant_motion_for_span(asset, span)` overlap helper
- [x] `dominant_motion` on `_AssetScore`, populated in `_score_one_asset`
- [x] `_rhythm_score` adds alternation (+10) + position (+15) bonuses
- [x] `_assemble_plan` picks argmax by rhythm-adjusted score per bucket
- [x] Smoke test confirms opening=dynamic, closing=static, 0 back-to-back same-motion in middle

## 6.3 Transitions
- [x] `transition_to_next` on `_AssetScore` + per-asset Gemini prompt + parser (whitelist coerce)
- [x] `transition_to_next` on `CutPlanSegment` (default `dissolve`) + `serialise_plan`
- [x] `video_renderer._build_xfade_filter(durations_ms, transitions)` returns `(video_chain, audio_chain)`
- [x] `concat_segments` accepts optional `durations_ms` / `transitions`; falls through to demuxer copy when single cut
- [x] `render()` plumbs durations + transitions from the plan into concat call
- [x] In-container smoke confirms cumulative offsets + acrossfade chain + whitelist coercion

## 6.4 BGM mix
- [x] `EditStep.BGM` enum value
- [x] `Project.bgm_path` column + alembic `0007_project_bgm`
- [x] `BGM_DIR` setting + .env entry (no new compose mount needed — under existing media bind)
- [x] `POST /projects/{id}/bgm` multipart upload (50 MB cap, ext whitelist, atomic-ish write)
- [x] `DELETE /projects/{id}/bgm` (idempotent)
- [x] `services/bgm_mixer.mix_bgm` with `_parse_cue_ranges` + `_build_duck_expression`
- [x] Orchestrator `bgm` stage between subtitles and `_mark_ready`; no-op when `bgm_path` is null; soft failure leaves the subtitled mp4 intact
- [x] `EDIT_STEP_LABELS.bgm = "配樂"`, `EDIT_STEP_ORDER` extended
- [x] `ProjectDetail.bgm_path` in API schema + TS types
- [x] In-container smoke: `_parse_cue_ranges`, `_build_duck_expression`

## Housekeeping
- [x] Version bump 0.11.0 → 0.12.0 (pyproject + web/package.json + api/main.py)
- [x] OpenSpec change `m6-rhythm-transitions-bgm` (this folder)
- [x] CLAUDE.md / auto-memory entries for M6 architecture
- [x] Commit + push + rebuild + deploy + smoke edit
