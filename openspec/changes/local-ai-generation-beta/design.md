## Context

media-processor runs on a single kevinhome host with one RTX 2070 8GB shared between `worker-analysis` (Whisper / YOLO / MediaPipe / Gemini Vision) and `worker-bgm` (MusicGen). Their non-collision today is enforced informally by usage patterns ("analysis is upload-time, BGM is render-time"), not by code. Local AI generation introduces two more GPU consumers (FLUX image-edit + LTX-Video I2V) that will overlap with both, so the informal pattern stops working. The hardware is fixed at 8 GB VRAM for the foreseeable future; quantised open-weight models (FLUX NF4 ≈ 6.5 GB, LTX-Video 2B fp8 ≈ 7 GB) are the only video-gen tier that fits, and only one model can be resident at a time. A full brainstorming spec (`docs/superpowers/specs/2026-05-15-local-ai-generation-design.md`, commit `03f5ff6`) precedes this design and informs every decision.

## Goals / Non-Goals

**Goals:**

- Ship a beta where a user takes a phone photo through FLUX image-edit then LTX-Video I2V, ending with `generated_video` Assets that the existing cut-plan / Smart Camera / subtitle / BGM render path consumes unchanged.
- Keep the GPU-contention story explicit: every forward-pass code path on the 2070 is wrapped in one redis-backed exclusive lock.
- Keep the beta reversible: `AI_GEN_ENABLED=false` removes all surface area; schema migrations remain safe to keep.
- Match output handoff to existing patterns (RQ queues, watchdog sweep, Asset rows, polling endpoints) so nothing downstream needs special-case logic.

**Non-Goals:**

- Output quality parity with Kling / Higgsfield. The 2070 + LTX-Video 2B tier has a known ceiling below those, and prompt tuning to close that gap is out of scope.
- Cloud fallback. Failures surface to the user, not to a hosted API.
- Free-form prompt entry. Beta ships 4 image + 4–6 video Chinese-labelled presets with bundled English prompts.
- Automatic reroll on bad output. We cannot reliably tell "bad" from "failed".
- Multi-user gating. Single-host product, env-var gate only.
- Cross-host or multi-GPU support.
- Audio generation extension (separate future beta).

## Decisions

**ComfyUI as a separate service container, not raw diffusers inside the workers.**
Alternatives considered: (a) raw `diffusers` library calls inside `worker-imggen` / `worker-videogen` Python code; (b) ComfyUI embedded as a library inside each worker. The open-weight video-gen field is moving monthly; ComfyUI workflow JSONs are the community standard and let us swap model / quantisation / sampler without a Python code change or docker rebuild. LTX-Video specifically does not ship via the `diffusers` main library — it has its own unstable `ltx-video` package whose API churns. ComfyUI also owns model swap-out / VRAM offload to RAM/disk, which is essential at 8 GB. Cost: one extra container, ~30 s cold-start latency on first request, occasional supervisor-restart need on long-running deployments. Worth it for the velocity gain in beta.

**Redis-backed exclusive GPU lock, not CUDA memory partitioning.**
Alternatives considered: (a) `CUDA_VISIBLE_DEVICES` slicing — impossible with one physical GPU; (b) MIG / MPS — Turing (2070) does not support MIG and MPS adds complexity disproportionate to the gain; (c) doing nothing — already deferred too long, four contenders will collide. The lock is one redis list with one token, `BLPOP` to acquire (10 min timeout), `RPUSH` to release. The token is initialised exactly once at api lifespan startup. Workers take the lock; ComfyUI does not (we cannot easily patch it). This means **existing `worker-analysis` and `worker-bgm` MUST acquire the lock around their forward passes** to be peers of the new workers — that is the only behaviour change to existing code, and it is internal (same external semantics, just queued).

**Generated assets join the same `Asset` table; `source_type` enum distinguishes origin.**
Alternative considered: a parallel `GeneratedAsset` table or a separate `AIProject` type. Both fork downstream code (cut-plan, Smart Camera, draft render) and bloat the surface area. Keeping one Asset table with a discriminator column is the lightest possible touch — every downstream consumer is `source_type`-blind. `parent_asset_id` lets us trace lineage; `generation_job_id` lets us replay the original prompt.

**Bundled English prompts per preset, frozen into each `generation_job` row.**
Alternative considered: live Gemini translation of the user's Chinese choice. Adds a Gemini dependency to a "local-only" feature and produces non-deterministic prompts. Hand-tuned English prompts shipped in code give us deterministic outputs, reproducibility (the row's `prompt_en` survives preset edits), and zero extra cost per generation. The user's "中文 preset，背後譯英文" intent is satisfied by translating once at preset-authoring time, not at runtime.

**Manual reroll only, one result per invocation.**
Alternative considered: 4-up batch (matching the Reel's Nano Banana Pro UI) or auto-retry on a quality heuristic. 4-up costs 4× GPU time on a tier where 5 s of video already takes 2–3 minutes; the user expectation correctly anchored at 1× cost per click. Auto-retry needs a "bad output" detector we do not have. Reroll = re-POST with a fresh random seed and the same preset; same `generation_job` shape, new row.

**Worker patches to `services/analysis/__init__.py` and `services/musicgen.py` are part of this change, not deferred.**
Alternative considered: split into a prerequisite "infrastructure" change. The lock is meaningless without its current users participating. Shipping ComfyUI workflows without locking analysis / bgm would race the moment a user uploads a video while a generation is running. Single coherent change is safer.

**Pinned ComfyUI image tag in `docker-compose.yml`, committed workflow JSONs in the repo.**
Alternative considered: `:latest` tag and stock community workflows fetched at runtime. Both create silent upstream churn during the most fragile period (beta). Pinning gives us a reproducible test baseline and a known-good image to roll back to.

## Risks / Trade-offs

- **Lock starvation under heavy load** → 10 min `BLPOP` timeout surfaces `GpuLockTimeout` as a terminal `failed` status. The user sees the failure and re-clicks; no silent hang.
- **ComfyUI crash mid-job** → orphan watchdog (extended from v0.25.1 Draft sweep) flips stuck `generation_job` rows to `failed`. No auto-retry; user-initiated only.
- **Model weights ~40 GB on first boot** → operator-only one-off; documented in compose comments and `/ai/health` reports `models_loaded: {flux: false, ltx: false}` with explicit reason until both are present.
- **Beta UI ships before quality is "good"** → mitigated by `AI_GEN_ENABLED=false` default; the wizard is invisible to anyone who has not flipped the flag. Beta-tier output quality is in the proposal copy and the wizard intro so users do not expect Kling parity.
- **Patching existing GPU workers risks regression** → integration tests for analysis and bgm run with a single-token lock prefilled so they pass identically to today. Lock acquire / release is logged with `reason` for queue debugging.
- **ComfyUI long-run instability** → workflow-level retry is NOT added (it would mask real bugs); ops-level `restart: unless-stopped` on the compose service is sufficient for the beta. If recurrent, we add a healthcheck loop later.
- **`prompt_en` frozen at enqueue means preset edits do not retroactively change past job rows** → desired; treat as audit trail.

## Migration Plan

1. **Land the schema and code with `AI_GEN_ENABLED` defaulting to `false`.** Production is unaffected — wizard hidden, endpoints return 404, no new containers needed yet because compose-level defaults can keep `comfyui` / `worker-imggen` / `worker-videogen` defined but not started (Compose only starts services in the up command).
2. **Operator manually downloads model weights** into `MEDIA_STORAGE_DIR/model_cache/comfyui_models/` (~40 GB, one-off). Documented step.
3. **Operator brings up the three new services** with `docker compose up -d comfyui worker-imggen worker-videogen` (plus `--scale worker-editing=3` for the existing fan-out as today).
4. **Operator flips `AI_GEN_ENABLED=true`** in `.env` and recreates `api`. Wizard entry appears on Project page.
5. **Kill switch**: flip to `false` and recreate `api`. Wizard entry disappears immediately. In-flight `generation_job` rows continue to completion or get swept by the watchdog. The three new containers can be stopped at the operator's leisure; nothing depends on them when the flag is off.
6. **Rollback** (catastrophic case): set flag false, stop the three new containers, run alembic downgrade of `0030` → `0029` if absolutely needed. Default migration semantics (additive columns with defaults, additive table) make the downgrade safe.

## Open Questions

- Exact pinned ComfyUI image tag — resolved during implementation by testing the chosen FLUX-family + LTX-Video workflow against candidate tags.
- Exact 4 image + 4–6 video preset contents — drafted alongside implementation with hand-tuning against real test photos.
- Whether the wizard entry should also be exposed from the global header — beta lives only on the Project page; promote later if engagement justifies.
- Whether to retire the `MusicGen-small fp32` ≈ 4 GB footprint to a quantised build now that the GPU pool has four contenders — out of scope for this change but worth tracking once we have lock contention numbers.
