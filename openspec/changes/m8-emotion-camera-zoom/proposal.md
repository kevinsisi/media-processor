## Why

After M6 the auto-edit pipeline produces a watchable mp4 with rhythm + transitions + BGM, but two unforced regressions and a missing creative dimension still bleed through:

1. **Subtitles drift later than the video.** With M6.3 xfade overlap (0.5 s per transition), every cut after the first overlaps the previous on the rendered timeline, but `subtitles.build_cues` still advances `timeline_cursor` by the cut's full asset duration. By cut N the burn-in is `(N-1) × 500 ms` late.
2. **Every transition lands on `dissolve`.** Gemini almost always picks `dissolve` because the prompt phrasing nudges that way and the alternative whitelist is small. The renderer ends up with a flat dissolve chain regardless of what the cuts actually want.
3. **No emotion signal anywhere in the pipeline.** Excited / surprised content reads as flat as serious content because the planner doesn't see emotion and the renderer can't act on it. Phase 8.1 ("情緒分析 + 運鏡縮放") was the next ROADMAP item — this change ships it.

## What Changes

### 1. Bug — subtitle xfade-overlap drift

- `services/subtitles.py` learned the same `TRANSITION_OVERLAP_MS = 500` constant the renderer already uses, mirrored locally to avoid an import cycle.
- `build_cues` now pulls `timeline_cursor` back by `TRANSITION_OVERLAP_MS` before placing each cut after the first, mirroring the cumulative-offset math inside `video_renderer._build_xfade_filter`. The total subtitle timeline length now equals `sum(d_i) − (N − 1) × TRANSITION_OVERLAP_MS`, which is exactly what the renderer produces.
- New unit test (`test_build_cues_xfade_overlap_three_cuts`) pins the offset behaviour for ≥3 cuts so the next regression is caught at PR time.

### 2. Bug — transitions stuck on dissolve

- `VALID_TRANSITIONS` (in both `edit_planner` and `video_renderer`) extended to include `circlecrop` for the punchy emotion-shift variant.
- Per-asset Gemini prompt updated: explicit per-bucket guidance (情緒延續 / 場景大跳 / 情緒大跳), explicit "避免整支片只用一種" instruction, and circlecrop in the schema literal so the model can pick it.

### 3. Phase 8.1 — face emotion analysis (new)

#### Worker stage

- New `services/emotion.py` — MediaPipe Face Landmarker (Tasks API) sampled at 2 fps, blendshapes mapped to one of `{happy, surprised, serious, neutral}` per frame, adjacent same-class frames merged into ranges, dominant verdict picked by total duration.
- The `.task` model file downloads lazily on first analyze run to `${EMOTION_MODEL_DIR}/face_landmarker.task` (default `/app/media/emotion_models/`), so the worker image build doesn't need network access to a Google CDN. Override via `EMOTION_MODEL_PATH` for air-gapped deploys.
- `EMOTION_FAKE=1` test seam returns a deterministic stub for CI / orchestration tests without bundling mediapipe in the dev environment.
- New `AnalysisStep.EMOTION` enum value; new `_run_emotion` in `services/analysis.py`; `EmotionUnavailableError` maps to `failed:model-missing`.
- Storage: per-class spans land in existing `asset_tags` rows with `tag_type="emotion"` (no schema migration needed); the dominant verdict lands in a parallel `tag_name="dominant"` row whose `time_ranges_ms` stashes the class string in element 0.

#### Planner integration

- `_format_emotion(asset)` adds an `情緒：…` line to the per-asset prompt block.
- `_AssetScore` and `CutPlanSegment` carry a new `dominant_emotion` field (default `"neutral"`); `serialise_plan` / `deserialise_plan` round-trip it so reorders (M7.1 skip-plan path) keep the verdict.
- `_assemble_plan` escalates `transition_to_next` to `circlecrop` when adjacent cuts sit in different emotion buckets (`{happy, surprised}` vs `{serious, neutral}`), so the visual jolt mirrors the emotional jolt without the model having to reason about it.

#### Renderer integration

- New `_zoompan_filter(target_aspect, duration_s)` chain. When a cut's `dominant_emotion ∈ {happy, surprised}`, the per-segment ffmpeg call appends `zoompan` after the aspect crop with `z` ramping `1.0 → ZOOMPAN_END_ZOOM (1.15)` across the cut's duration, output canvas pinned to `ASPECT_DIMENSIONS[target_aspect]`, fps pinned to `VIDEO_FPS`. Static / neutral cuts stay locked off (no zoompan).
- The xfade renderer already routes through `transition_to_next`; with circlecrop in the whitelist + the planner picking it on emotion shifts, the user sees stronger transitions exactly when the emotion bucket changes.

#### API + frontend

- `EmotionTagsOut` (dominant + per-class ranges) added to `api/schemas`. `AssetAnalysisItem.emotion_tags` is `null` when the stage hasn't run, populated otherwise.
- `web/src/api/types.ts` mirrors the schema. `i18n/tags.ts` adds `EMOTION_TAG_LABELS / EMOTION_TAG_ICONS` (繁體中文 + emoji glyphs).
- `ProjectAnalysis.tsx` shows an emotion chip per asset card (color-coded per class) right below the motion timeline, with a tooltip listing all detected ranges.

### Version

- `0.13.0 → 0.14.0`. Updated in `pyproject.toml`, `src/media_processor/api/main.py`, `web/package.json`, `web/package-lock.json`.

## Impact

- **Affected specs**: `auto-edit` (renderer + planner), `analysis-pipeline` (new emotion step).
- **Migration**: none. Emotion tags piggyback on `asset_tags`; cut plan schema is additive (`dominant_emotion` defaults to `neutral` so older drafts deserialise unchanged).
- **Worker image**: gains `mediapipe>=0.10.14,<0.11`. First analyze run after deploy downloads the ~4 MB `.task` model to the bind-mounted media volume, so subsequent runs skip the fetch.
- **Backwards compatibility**: assets that haven't been re-analysed since the deploy show up with `emotion_tags = null` (UI hides the chip). Their `dominant_emotion` defaults to `neutral`, so the renderer just doesn't apply zoompan and the planner uses the default-bucket transition logic.
