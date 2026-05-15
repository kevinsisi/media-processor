# Local AI Generation Beta — Design

**Status:** draft (brainstorming output, awaiting writing-plans)
**Date:** 2026-05-15
**Target version:** v0.50.0-beta
**Hardware target:** kevinhome — AMD 3700X + 48GB RAM + RTX 2070 8GB

## Problem

Operators of media-processor can today only edit footage they shot themselves. They cannot turn a single phone photo into a Reel-style multi-clip cinematic short the way recent open-weight AI pipelines (Nano Banana Pro for stills + Kling / LTX-Video for I2V) make possible. Adding that upstream capability locally — without depending on paid cloud APIs — unlocks a workflow class the product currently cannot reach.

## Goal

Ship an opt-in beta that, on the existing single RTX 2070 8GB host, lets a user:

1. Upload one phone photo to a Project
2. Pick a Chinese-labelled image preset → get an AI-cleaned cinematic still
3. Pick a Chinese-labelled camera-motion preset + a last-frame still → get a 5-10s AI-generated video clip
4. Generated clips land as ordinary `Asset` rows in the Project so the existing cut-plan / Smart Camera / subtitle / BGM render path consumes them unchanged

Success criterion for the beta: a user reaches step 4 end-to-end on real hardware, the resulting Asset successfully drives a Draft render, and the operator can ship that Draft via the existing flow. Output **quality** parity with commercial Kling / Higgsfield is explicitly NOT a success criterion — LTX-Video 2B on 2070 has a known ceiling below those.

## Non-goals

- Cloud AI fallback (this is "local only" — failure goes back to the user, not to an API)
- Editable / free-form prompts (only Chinese presets in beta)
- Automatic quality scoring or auto-reroll (manual reroll only)
- Multi-user gating (no User model in this product; gating is process-wide via env var)
- Image generation from scratch without a source photo (only "edit/clean an existing photo")
- Audio generation as part of this beta (BGM stays on the existing MusicGen path)
- Cross-host GPU support (single RTX 2070 only)

## Confirmed beta decisions

| # | Decision | Value |
|---|---|---|
| 1 | Scope | Image + video full chain |
| 2 | Project integration | Generated assets join the same `Asset` table with `source_type` enum |
| 3 | UX shape | Step-by-step wizard with per-step preview + reroll |
| 4 | Beta gating | `.env` variable `AI_GEN_ENABLED` |
| 5 | Prompt control | 4-6 Chinese-labelled presets per kind; bundled English prompt stored alongside |
| 6 | Reroll | Manual only, one result per invocation, seed re-randomised |
| 7 | Runtime | ComfyUI as a separate service container |
| 8 | GPU contention | Redis-backed exclusive lock; existing `worker-analysis` and `worker-bgm` patched to acquire it |

## Architecture

```
Browser (FE Wizard)
  ↓ HTTP via existing :8523/api
FastAPI (api container)
  • new router /generations/*, /ai/*
  • AI_GEN_ENABLED gate (404 when off)
  • preset_key → bundled English prompt
  • enqueue → RQ queues `imggen` / `videogen`
  ↓
Redis (RQ + gpu:exclusive lock)        Postgres (new generation_job table)
  ↓
worker-imggen ×1, worker-videogen ×1
  • no GPU device attached
  • each job: acquire gpu:exclusive lock → POST workflow to ComfyUI →
    poll /history → download output → register Asset → release lock
  ↓ HTTP
comfyui service container
  • port 8188
  • GPU access (nvidia)
  • mounts MEDIA_STORAGE_DIR/model_cache/comfyui_models/
  • mounts MEDIA_STORAGE_DIR/comfyui_output/
  • runs FLUX image-edit OR LTX I2V workflow depending on incoming JSON
```

The two new workers are deliberately **CPU-only HTTP clients**. The GPU lives in ComfyUI and only ComfyUI loads model weights. Existing `worker-analysis` (Whisper / YOLO / MediaPipe / Gemini Vision) and `worker-bgm` (MusicGen) keep loading their own models directly, but each gain a `with gpu_exclusive(...)` wrapper around their forward-pass call sites so the four GPU consumers serialise through one lock.

### Why ComfyUI as service, not raw diffusers in worker

The beta period will see frequent model / quantisation changes (the open-weight video-gen field is moving monthly). ComfyUI workflows are JSON files and change at zero code cost. LTX-Video does not ship via the `diffusers` main library — it has its own `ltx-video` package whose API is unstable, and integrating it directly into a worker forces a docker rebuild for every model bump. ComfyUI also provides built-in VRAM management (offload to RAM / disk between model swaps), which is critical on 8GB. The cost is one extra container and a cold-start latency of ~30s on first request after restart.

### Why redis lock, not GPU memory partitioning

The RTX 2070 has only one GPU. `CUDA_VISIBLE_DEVICES` cannot split a single device meaningfully. The pragmatic answer is a single mutex: only one of {analysis, bgm, imggen, videogen} runs on the GPU at a time. The lock implementation is a redis list with one token (`BLPOP` to acquire, `RPUSH` to release, 10-minute acquire timeout). This is upstream from the existing assumption in `docker-compose.yml` comments that "analysis is upload-time, bgm is render-time, rare contention" — making serialisation explicit removes that race.

## Components

| Module | Path | Purpose |
|---|---|---|
| ComfyUI HTTP client | `services/ai_generation/comfy_client.py` | POST `/prompt`, poll `/history`, download `/view` |
| Workflow templates | `services/ai_generation/workflows/{flux_image_edit,ltx_i2v}.json` | ComfyUI workflow JSON with `{{prompt}}`, `{{image_path}}`, `{{seed}}` placeholders. `flux_image_edit` uses a FLUX-family image-editing model (e.g. FLUX Kontext NF4 or FLUX img2img); exact model picked in writing-plans |
| Preset registry | `services/ai_generation/presets.py` | Chinese label, description, bundled English prompt, target workflow |
| GPU exclusive lock | `services/ai_generation/gpu_lock.py` | `@contextmanager gpu_exclusive(reason, timeout=600)` |
| Image gen RQ jobs | `workers/imggen_jobs.py` | Acquire lock → call comfy_client(flux_image_edit) → register Asset |
| Video gen RQ jobs | `workers/videogen_jobs.py` | Acquire lock → call comfy_client(ltx_i2v) → register Asset |
| Generation API router | `api/routers/generations.py` | `POST /generations/image`, `POST /generations/video`, `GET /generations/{id}`, `DELETE /generations/{id}` |
| AI metadata router | `api/routers/ai.py` | `GET /ai/presets`, `GET /ai/health` |
| GenerationJob model | `models/generation_job.py` | ORM for the new table |
| Existing-worker patches | `services/analysis/__init__.py`, `services/musicgen.py` | Wrap forward passes in `gpu_exclusive` |
| Compose patch | `docker-compose.yml` | Add `comfyui`, `worker-imggen`, `worker-videogen` |
| FE wizard | `web/src/features/ai-gen/` | 3-step modal flow |

## Data flow

### Step 1 — Image clean-up

```
FE → POST /generations/image
       { project_id, source_asset_id, preset_key: "cinematic-teal-orange" }
API → preset.lookup("cinematic-teal-orange") → english_prompt
API → INSERT generation_job (kind="image", status="queued", preset_key, prompt_en, seed=random())
API → rq.enqueue("imggen", job_id) → return { job_id }

FE polls GET /generations/{job_id} every 2s

worker-imggen picks job:
  with gpu_exclusive(reason="imggen"):
      payload = render_workflow("flux_image_edit", prompt=prompt_en, image=source_path, seed=seed)
      prompt_id = comfy_client.submit(payload)
      output_paths = comfy_client.wait_for(prompt_id, timeout=300)
  asset = Asset(source_type="generated_image", parent_asset_id=source_asset_id,
                generation_job_id=job_id, file_path=copied_path)
  session.add(asset); session.commit()
  generation_job.status = "done"; generation_job.output_asset_id = asset.id

FE sees status=done → shows thumbnail of output_asset_id
User: 「接受」(advance to step 3) | 「Reroll」(re-POST with new random seed)
```

### Step 2 — Video generation

```
FE → POST /generations/video
       { project_id, first_frame_asset_id, last_frame_asset_id, preset_key: "linear-slider" }
API → preset.lookup("linear-slider") → english_prompt (the slider motion prompt)
API → INSERT generation_job (kind="video", second_source_asset_id=last_frame_asset_id, ...)
API → rq.enqueue("videogen", job_id)

worker-videogen, same lock-and-go pattern but with ltx_i2v workflow.
Output is a ~5s mp4 file. Asset is registered with source_type="generated_video",
duration_s and resolution probed via ffprobe.
```

### Step 3 — Loop / accept

The wizard repeats step 2 as many times as the user wants, accumulating N `generated_video` Assets in the Project. When the user closes the wizard, those Assets are already in the pool and the existing Draft / cut-plan / Smart Camera flow consumes them like any other Asset.

## Data model

### New table — `generation_job`

Alembic revision `0029_add_generation_job`.

```python
class GenerationJob(Base):
    __tablename__ = "generation_job"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("project.id", ondelete="CASCADE"))
    kind: Mapped[str]                     # "image" | "video"  (CheckConstraint)
    preset_key: Mapped[str]               # e.g. "cinematic-teal-orange"
    prompt_en: Mapped[str]                # frozen at enqueue time
    source_asset_id: Mapped[int] = mapped_column(ForeignKey("asset.id"))
    second_source_asset_id: Mapped[int | None] = mapped_column(ForeignKey("asset.id"))
    status: Mapped[str]                   # "queued" | "running" | "done" | "failed"
    output_asset_id: Mapped[int | None] = mapped_column(ForeignKey("asset.id"))
    error: Mapped[str | None]
    seed: Mapped[int]
    workflow_version: Mapped[str]         # e.g. "flux-nf4-v1", "ltx-2b-fp8-v1"
    created_at: Mapped[datetime]
    completed_at: Mapped[datetime | None]
```

### Patch `asset` — alembic `0030_asset_source_type`

```python
op.add_column("asset", sa.Column("source_type", sa.String(),
              server_default="uploaded", nullable=False))
op.create_check_constraint("ck_asset_source_type",
              "asset",
              "source_type IN ('uploaded','generated_image','generated_video')")
op.add_column("asset", sa.Column("parent_asset_id", sa.Integer(),
              sa.ForeignKey("asset.id"), nullable=True))
op.add_column("asset", sa.Column("generation_job_id", sa.Integer(),
              sa.ForeignKey("generation_job.id"), nullable=True))
```

Existing assets keep `source_type="uploaded"`. The UI `is_ai_generated` badge is derived (`source_type != "uploaded"`); no separate flag is stored.

### Why store `prompt_en` per job

So a job result remains reproducible even after we tune the preset registry in code. A 6-month-old `generation_job` row still tells us exactly what prompt produced its output asset.

## API surface

| Endpoint | Method | Body / Query | Returns | Notes |
|---|---|---|---|---|
| `/generations/image` | POST | `{project_id, source_asset_id, preset_key, seed?}` | `{job_id}` | 404 if `AI_GEN_ENABLED=false` |
| `/generations/video` | POST | `{project_id, first_frame_asset_id, last_frame_asset_id, preset_key, seed?}` | `{job_id}` | same |
| `/generations/{id}` | GET | — | `{id, status, output_asset_id?, error?, queue_position?}` | `queue_position` from RQ inspection |
| `/generations/{id}` | DELETE | — | 204 | 409 if `running`, follows queue.py convention |
| `/ai/presets` | GET | `?kind=image\|video` | `[{key, label_zh, description_zh}]` | English prompts deliberately hidden |
| `/ai/health` | GET | — | `{enabled, comfyui_up, models_loaded:{flux:bool, ltx:bool}}` | FE gates wizard entry on `enabled && comfyui_up` |

When `AI_GEN_ENABLED=false`:

- `/generations/*` returns 404 with `{detail: "AI generation disabled"}`
- `/ai/presets` returns 404
- `/ai/health` always available, returns `{enabled: false}` so the FE knows to hide the wizard
- The existing `/health` endpoint additionally surfaces `{ai_gen: {enabled, comfyui_up}}` so the FE single-fetches its boot state

## FE wizard

A modal mounted at `web/src/features/ai-gen/AiGenWizard.tsx`, opened from a new "+ AI 生成 clip" button on the Project page. The button is only rendered when `/health` reports `ai_gen.enabled && ai_gen.comfyui_up`.

Three steps:

1. **Source** — pick an existing Project Asset (image type) OR upload a new image via the existing `POST /assets/upload` endpoint (no change to upload code).
2. **Image clean-up** — show dropdown of `/ai/presets?kind=image` (4 entries by default), "Generate" button, progress card (status + queue_position polled every 2s), result thumbnail, Accept / Reroll buttons. Reroll re-POSTs with a new random seed and the same preset.
3. **Video generation** — show first frame (the accepted step-2 result, locked), let user choose last frame (same image again for pure camera motion, or re-run step 2 to produce a different second image), dropdown of `/ai/presets?kind=video` (4-6 entries), "Generate" → progress → 5-10s video preview → Accept / Reroll. Accept closes the modal and emits a toast; the Asset is already in the pool. Optional "Generate one more clip" button loops back to step 3 with a fresh last-frame choice.

All wizard state is React-local + URL query string (`?ai_job=<id>` so a refresh recovers the polling target). Nothing is persisted to the DB beyond the `generation_job` rows themselves.

## Error handling

| Failure | Detection | UX |
|---|---|---|
| ComfyUI service down | `/ai/health` polled at modal open + every 30s; ComfyUI socket connect refused inside worker | Wizard entry disabled with tooltip "AI 服務未啟動"; in-flight worker job marks job `failed` with `error="ComfyUI unreachable"` |
| ComfyUI workflow error (bad weights, OOM, validation) | Worker receives error event from ComfyUI WebSocket / HTTP response | `status=failed`, `error="ComfyUI: <message>"`; FE shows red banner "請 reroll" |
| GPU lock timeout (>10 min) | `gpu_exclusive` raises `GpuLockTimeout` | `status=failed`, `error="GPU busy timeout"`; job not auto-retried |
| Output file missing after `/history` reports complete | Worker retries `/view` download 3× with 5s backoff | If still missing: `status=failed`, `error="Output not found"` |
| Worker crash mid-job | Existing v0.25.1 orphan watchdog already polls Draft rows; extend it to also poll `generation_job` rows with `status in ('queued','running')` whose RQ job has vanished, flip them to `failed` with `error="worker crashed"`. **Do NOT auto-retry** — generation is too expensive |
| ComfyUI model weights missing on startup | `/ai/health` returns `models_loaded: {flux: false, ltx: false}` with explicit reason | Wizard entry disabled; surface a console-readable hint about required model paths |

**Deliberate non-feature**: there is no automatic reroll on failure. The beta cannot reliably tell "this output is bad" from "this output failed", and burning a second GPU slot on a guess is worse UX than just telling the user to click Reroll.

## GPU contention

The lock is owned by **anything that runs forward-pass GPU work**:

```python
# services/ai_generation/gpu_lock.py
@contextmanager
def gpu_exclusive(reason: str, timeout: int = 600):
    token = redis.blpop("gpu:exclusive", timeout=timeout)
    if token is None:
        raise GpuLockTimeout(f"reason={reason} waited {timeout}s")
    try:
        yield
    finally:
        redis.rpush("gpu:exclusive", "1")
```

The token is initialised at api lifespan startup (`redis.rpush("gpu:exclusive", "1")` if list empty). Workers do NOT initialise — only api does, exactly once per boot.

Patches to existing GPU paths:

- `services/analysis/__init__.py::run_pipeline` — wrap the actual Whisper / YOLO / MediaPipe calls (each is a forward pass; one outer `with` is acceptable since they run sequentially inside the pipeline)
- `services/musicgen.py::generate` — wrap the model `.generate()` call
- `workers/imggen_jobs.py::run` and `workers/videogen_jobs.py::run` — wrap the `comfy_client.submit(...) + wait_for(...)` block

`reason` is logged on acquire and release for queue-debugging.

## Compose changes

```yaml
comfyui:
  image: <pinned-comfyui-image-tag>
  restart: unless-stopped
  ports:
    - "127.0.0.1:8188:8188"      # local only, worker talks to it
  environment:
    TZ: ${TZ:-Asia/Taipei}
  deploy:
    resources:
      reservations:
        devices:
          - driver: nvidia
            count: 1
            capabilities: [gpu]
  volumes:
    - "${MEDIA_STORAGE_DIR:-./.local/media}/model_cache/comfyui_models:/comfy/models"
    - "${MEDIA_STORAGE_DIR:-./.local/media}/comfyui_output:/comfy/output"

worker-imggen:
  image: ${DOCKERHUB_USERNAME:-kevin950805}/media-processor-worker:${IMAGE_TAG:-latest}
  command: ["python", "-m", "media_processor.workers", "imggen"]
  restart: unless-stopped
  env_file: .env
  depends_on:
    postgres: { condition: service_healthy }
    redis:    { condition: service_healthy }
    comfyui:  { condition: service_started }
  # NO GPU reservation — worker is an HTTP client
  volumes:
    - "${MEDIA_STORAGE_DIR:-./.local/media}:/app/media"

worker-videogen:
  # symmetric, command: ["python", "-m", "media_processor.workers", "videogen"]
```

Pinned ComfyUI image tag and bundled workflow JSONs are committed in this repo so we don't take silent upstream updates during beta. Model weight files (`flux1-dev-nf4.safetensors`, LTX-Video safetensors, etc.) live under `MEDIA_STORAGE_DIR/model_cache/comfyui_models/` and are NOT in git; first-boot operator must download them (~40GB total).

## Testing

| Layer | What | Notes |
|---|---|---|
| Unit | Preset registry lookup, English-prompt freezing into job, `gpu_exclusive` lock contract using fakeredis | Pure-Python, fast |
| Unit | Workflow templating: render `{{prompt}}` / `{{image_path}}` / `{{seed}}` placeholders correctly | Snapshot JSON before/after |
| Integration | Mock ComfyUI HTTP (httpx mock transport) with canned `/prompt`, `/history`, `/view` responses; drive a full `imggen_jobs.run` invocation end-to-end → assert `Asset` row written, `GenerationJob.status="done"` | Reuses existing test fixtures for Project + Asset |
| Integration | Orphan watchdog also picks up generation_job rows | Extend existing watchdog test |
| Manual E2E | One-page checklist in `tests/manual/2026-05-15-local-ai-gen-checklist.md`: upload car photo → image preset → video preset → confirm Asset → trigger Draft render → confirm mp4 | Beta period, run before each tag bump |

**Deliberately not tested**: actual model output quality (non-deterministic), real ComfyUI service (env-dependent), real GPU paths (CI has no GPU). All CI tests run without GPU, without ComfyUI, without model weights.

## Rollout

1. `AI_GEN_ENABLED=false` ships to production first; only the schema migrations and new code paths land. Production user sees no UI change.
2. Operator manually downloads model weights into `MEDIA_STORAGE_DIR/model_cache/comfyui_models/`. First-time boot is a documented one-off (10-60 min depending on network).
3. Operator flips `AI_GEN_ENABLED=true` in `.env`, recreates the api + worker-imggen + worker-videogen + comfyui containers.
4. Wizard entry appears on Project page. Beta is live.
5. Kill switch: flip back to `false` and recreate api → wizard entry vanishes, in-flight generation jobs run to completion or hit watchdog.

## Open questions deferred to writing-plans

- Pinned ComfyUI image tag (need to test which Docker image works with the chosen FLUX-NF4 + LTX workflow)
- Exact 4-6 image + 4-6 video preset content (will be drafted alongside implementation)
- Whether to also patch existing GPU workers in the same change or split into a separate prerequisite change ("add gpu_exclusive infra → patch existing workers → ship AI gen")
- Whether `prompt_en` should be encrypted at rest (low priority for single-user home lab)
- Whether wizard should be reachable from the global header or only from inside a Project page

## Out of scope for this design

These belong to future iterations, not this beta:

- Free-form prompt entry
- Multiple-output (N-up) generation
- Quality scoring / auto-reroll
- Cloud fallback / hybrid API path
- Multi-GPU support
- User accounts / per-user quotas
- Streaming progress (current design polls; WebSocket could come later)
- Saving / sharing presets across projects
- Audio generation extension (separate beta)
