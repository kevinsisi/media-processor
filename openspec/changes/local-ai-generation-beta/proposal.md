## Why

Operators today can only edit footage they shot themselves. They cannot turn a single phone photo into a Reel-style multi-clip cinematic short the way modern open-weight pipelines (Nano Banana Pro for stills + Kling / LTX-Video for image-to-video) make possible. Adding that upstream capability locally — on the existing single RTX 2070 8GB host, without depending on paid cloud APIs — unlocks a workflow class media-processor cannot currently reach. Shipping it as an opt-in beta lets us validate the local-GPU path (ComfyUI + FLUX-family image-edit model + LTX-Video 2B) and the wizard UX before committing to the long-run architecture.

## What Changes

- Add a ComfyUI service container that owns all new GPU forward-pass work and the FLUX image-edit + LTX-Video I2V workflow JSONs.
- Add `worker-imggen` and `worker-videogen` queues; the workers are pure HTTP clients that submit ComfyUI workflows and write results back as `Asset` rows.
- Add a redis-backed exclusive GPU lock (`gpu:exclusive`) and **BREAKING (internal)**: patch existing `worker-analysis` (Whisper / YOLO / MediaPipe / Gemini Vision) and `worker-bgm` (MusicGen) to acquire that lock before forward-pass calls. Previously these two relied on an informal "rare contention" assumption; serialising them is mandatory once the new GPU consumers join.
- Add `generation_job` table and extend `asset` with `source_type`, `parent_asset_id`, `generation_job_id` columns. `is_ai_generated` UI is derived, not stored.
- Add `/generations/image`, `/generations/video`, `/generations/{id}` (GET, DELETE) endpoints; `/ai/presets` and `/ai/health` for catalogue + readiness; surface `ai_gen` block in the existing `/health` payload.
- Add a 3-step FE wizard (`AiGenWizard`) that surfaces the chain only when `/health` reports `ai_gen.enabled && ai_gen.comfyui_up`.
- Add `AI_GEN_ENABLED` env flag. Default `false`. When `false`: all `/generations/*` and `/ai/presets` return 404, wizard entry hidden.
- Add 4 image presets and 4–6 video presets with Chinese labels + bundled English prompts; no free-form prompt entry in beta.
- Extend the v0.25.1 orphan-Draft watchdog to also sweep stuck `generation_job` rows; failures are terminal (no auto-retry).

## Capabilities

### New Capabilities

- `local-ai-generation`: Covers the local FLUX image-edit + LTX-Video image-to-video chain, the ComfyUI runtime, GPU serialisation lock, preset registry, generation job lifecycle, wizard UX, and beta gating. End-to-end concern from "user uploads phone photo" to "generated_video Asset is consumable by the existing edit pipeline".

### Modified Capabilities

None. The existing capabilities (analysis, editing, BGM, drafts) are not touched at the requirement level — they gain an internal `gpu_exclusive` wrapper but their external behaviour (inputs, outputs, status semantics) is unchanged.

## Impact

- **Compose / deploy**: new `comfyui` service (GPU-attached), new `worker-imggen` and `worker-videogen` services (CPU-only); `MEDIA_STORAGE_DIR/model_cache/comfyui_models/` and `MEDIA_STORAGE_DIR/comfyui_output/` mount points; ~40 GB of model weights operators must download once on first boot.
- **Schema**: alembic `0029_add_generation_job`, alembic `0030_asset_source_type`.
- **Backend**: new `services/ai_generation/` package (comfy_client, presets, gpu_lock, workflows), new `workers/imggen_jobs.py` + `workers/videogen_jobs.py`, new routers `api/routers/generations.py` + `api/routers/ai.py`, watchdog extension.
- **Internal patches to existing GPU paths**: `services/analysis/__init__.py::run_pipeline` and `services/musicgen.py::generate` each wrapped in `gpu_exclusive(...)`. Behaviour change is "wait for lock then proceed" instead of "proceed immediately"; tests for both must continue to pass when only one GPU consumer is active.
- **Frontend**: new `web/src/features/ai-gen/` wizard, project-page entry button gated on `/health` readiness.
- **Config**: `AI_GEN_ENABLED` env var (default `false`), pinned ComfyUI image tag committed in compose.
- **Tests**: unit (preset lookup, prompt-freezing, lock contract via fakeredis, workflow templating); integration (mock ComfyUI HTTP for full imggen + videogen job runs, watchdog extension); manual E2E checklist (`tests/manual/2026-05-15-local-ai-gen-checklist.md`) because real ComfyUI + real GPU + real model weights are not in CI.
- **Out of scope**: cloud fallback, free-form prompts, automatic reroll / quality scoring, multi-GPU, multi-user, streaming progress, audio generation, cross-project preset sharing.
