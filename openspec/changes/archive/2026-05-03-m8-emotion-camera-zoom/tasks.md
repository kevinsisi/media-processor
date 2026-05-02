# Tasks — m8-emotion-camera-zoom (0.14.0)

## 1. Bug fixes (must land first)

- [x] 1.1 Add `TRANSITION_OVERLAP_MS = 500` to `services/subtitles.py` and pull `timeline_cursor` back by it for cuts after the first inside `build_cues`.
- [x] 1.2 Update `test_build_cues_advances_timeline_across_cuts` to expect the overlapped offset and add `test_build_cues_xfade_overlap_three_cuts` for ≥3-cut anchoring.
- [x] 1.3 Add `circlecrop` to `VALID_TRANSITIONS` in `services/edit_planner.py` and `services/video_renderer.py`.
- [x] 1.4 Update the per-asset Gemini prompt with the wider transition set + per-bucket guidance + variety nudge.

## 2. Emotion analysis service

- [x] 2.1 Create `services/emotion.py` with blendshape→emotion heuristic + lazy MediaPipe model download + `EMOTION_FAKE=1` test seam.
- [x] 2.2 Add `mediapipe>=0.10.14,<0.11` to `pyproject.toml [analysis]` and to the worker `Dockerfile` pip install block.
- [x] 2.3 Add `EmotionUnavailableError` mapping to `failed:model-missing` in `services/analysis._KNOWN_REASONS`.
- [x] 2.4 Add `AnalysisStep.EMOTION` to `models/enums.py`; extend `VALID_STEPS` in `services/analysis.py` and `workers/analysis_jobs.py`.
- [x] 2.5 Implement `_run_emotion` in `services/analysis.py` — per-class range rows + `tag_name="dominant"` summary row.
- [x] 2.6 Wire env vars into `.env.example` (`EMOTION_SAMPLE_INTERVAL_MS`, `EMOTION_MODEL_DIR`, `EMOTION_MODEL_PATH`, `EMOTION_FAKE`).

## 3. Planner integration

- [x] 3.1 Add `_format_emotion` + `_dominant_emotion_for_asset` helpers; include `情緒：…` in `_format_asset_block`.
- [x] 3.2 Add `dominant_emotion: str = EMOTION_DEFAULT` to `_AssetScore` and `CutPlanSegment`; populate inside `_score_one_asset`.
- [x] 3.3 Make `_assemble_plan` escalate `transition_to_next` to `circlecrop` on emotion-bucket boundaries (`_is_emotion_shift`).
- [x] 3.4 Persist `dominant_emotion` through `serialise_plan` / `deserialise_plan` so the M7.1 skip-plan path keeps it.
- [x] 3.5 Set `dominant_emotion` on heuristic-fallback segments too.

## 4. Renderer integration

- [x] 4.1 Add `ZOOMPAN_EMOTIONS / ZOOMPAN_END_ZOOM / ZOOMPAN_FPS` constants and `_zoompan_filter` chain.
- [x] 4.2 In `_cut_segment`, append the zoompan chain after the aspect crop when `dominant_emotion ∈ ZOOMPAN_EMOTIONS`.
- [x] 4.3 Confirm `circlecrop` resolves through `_safe_transition` rather than coercing to default.

## 5. API + frontend

- [x] 5.1 Add `EmotionRangeOut` + `EmotionTagsOut` schemas; surface `emotion_tags` on `AssetAnalysisItem` (null when stage hasn't run).
- [x] 5.2 Build `_emotion_tags_for(asset)` in the projects router and wire it into the response builder.
- [x] 5.3 Mirror types in `web/src/api/types.ts`.
- [x] 5.4 Add `EMOTION_TAG_LABELS` / `EMOTION_TAG_ICONS` + `labelForEmotionTag` / `iconForEmotionTag` in `i18n/tags.ts`; add `emotion` to `ANALYSIS_STEP_LABELS` and `ANALYSIS_STEP_ORDER`.
- [x] 5.5 Render the emotion chip below the motion timeline in `ProjectAnalysis.tsx`; add `.emotion-chip` styles in `ProjectAnalysis.css`.

## 6. Tests

- [x] 6.1 `tests/unit/test_emotion.py` — blendshape classifier, range merge, dominant pick, FAKE stub.
- [x] 6.2 `tests/unit/test_video_renderer.py` — zoompan chain emits canvas size + FPS; `circlecrop` lives in the whitelist.
- [x] 6.3 `tests/unit/test_edit_planner.py` — emotion-shift escalates to circlecrop; serialise round-trip preserves `dominant_emotion`.
- [x] 6.4 Drive-by: fix stale `test_edit_trigger_persists_pending_draft` assertion (M6.4 added `bgm` step).

## 7. Version + docs + deploy

- [x] 7.1 Bump version to `0.14.0` in `pyproject.toml`, `src/media_processor/api/main.py`, `web/package.json`, `web/package-lock.json`.
- [x] 7.2 ROADMAP — flip Phase 8.1 to in-progress / done bucket.
- [x] 7.3 Update auto-memory: pipeline-shape entry for v0.14.0 (emotion stage + zoompan + circlecrop transitions).
- [x] 7.4 Run unit suite (112 passed / 7 skipped) and `cd web && npm run build`.
- [x] 7.5 Commit + push to main worktree branch + rebuild + deploy.
