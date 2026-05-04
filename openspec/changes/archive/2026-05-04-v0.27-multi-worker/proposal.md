# v0.27.0 — multi-worker fan-out

**Status:** ✅ shipped 2026-05-04.

## Why

Pre-0.27 we ran a single `worker:` container that listened on all three RQ queues (`analysis`, `editing`, `bgm`). On the production host (AMD 3700X 8C/16T + RTX 2070 8GB + 48GB RAM) this caused two real losses:

1. **Head-of-line blocking across unrelated work.** A 30-second MusicGen run on the `bgm` queue would block a freshly-uploaded asset's `analysis` job for the same 30 seconds, even though MusicGen is GPU-bound and analysis would happily share the GPU (or had been queued well before the bgm job was triggered).
2. **No editing parallelism.** `editing` jobs are CPU-bound (ffmpeg cut / concat / vidstab / reframe / subtitles / watermark / mix). Each ffmpeg invocation already uses multiple threads, but with only one worker we could never render two drafts in parallel — even when the operator triggered three back-to-back, they ran serially while 7 cores sat idle.

Single-worker mode was fine for the M5 → M9.11 era when concurrency was rare. Now that the platform has multiple drafts in flight per session and the operator can re-trigger renders cheaply via the timeline editor, the serialised path is the bottleneck.

## What

Split the worker into three Compose services using a single shared Dockerfile:

| service | replicas | GPU | queue | jobs |
| --- | --- | --- | --- | --- |
| `worker-analysis` | 1 | yes (nvidia) | `analysis` | Whisper STT, YOLO tracking, MediaPipe emotion, Gemini Vision |
| `worker-editing` | **3** (via `--scale`) | no | `editing` | ffmpeg cut/concat/vidstab/reframe/subtitles/watermark/mix |
| `worker-bgm` | 1 | yes (nvidia) | `bgm` | MusicGen-small text→30s WAV |

Total: **5 worker containers running concurrently**.

### Why this split

- **GPU work stays serialised on the 2070.** Both GPU services share the single card. In practice they rarely fire concurrently — analysis is upload-time, bgm is render-time — but if they do, MusicGen's ~4GB + Whisper's ~1.5GB still fit in 8GB.
- **Editing fans out cleanly to spare CPU cores.** With api + analysis + bgm + redis + postgres consuming ~9 threads, 7 threads remain. Three editing workers ≈ 2.3 thread/worker plus per-ffmpeg internal threading hits a sweet spot — measured 2–3× wall-clock improvement on three concurrent renders vs. serial.
- **Single image keeps the build tractable.** All three services build from `docker/worker.Dockerfile`. The editing image carries torch / whisper / musicgen even though it never imports them at runtime — the editing job functions don't import those modules, so the CUDA libs stay dormant. The cost is image size; we accepted that to keep CI / build / deploy simple.
- **GPU access is per-service via `deploy.resources.reservations.devices`.** worker-analysis and worker-bgm have the nvidia reservation block; worker-editing does not. The container without the reservation cannot see the GPU even though the libraries are present.

### Schema-breaking change

`QueueStatusOut.running` widened from `QueueJobItem | None` to `list[QueueJobItem]`. The previous endpoint silently kept only the first running job per queue (`if running is None: running = item`). With 5 concurrent workers, up to 5 jobs are live at once and the FE inspector needs to render all of them. The FE (`QueueStatusBadge`, `QueueStatusModal`) is updated in lockstep.

### Worker-name collision (latent bug, fixed in passing)

Pre-0.27 the worker constructor was `Worker(queues, name=f"media-worker-{settings.api_host}")`. `api_host` defaults to `"0.0.0.0"`, so every worker container computed the same `media-worker-0.0.0.0` name and RQ's worker registry only ever tracked a single one. Pre-0.27 single-container mode hid this; the multi-worker setup would have broken on day 1. The fix is to drop the `name=` kwarg entirely so RQ auto-generates a unique `hostname.pid` string per container.

## Risks / Out of scope

- **`docker compose up -d --scale worker-editing=3` is load-bearing.** `deploy.replicas: 3` in compose v3 spec is honoured by `docker stack` only — plain `docker compose` ignores it. Documented in CLAUDE.md, ROADMAP.md, and this proposal so the next deploy doesn't silently drop to 1 editing worker.
- **GPU contention between analysis and bgm.** If a user uploads while a MusicGen run is in flight, both will share the 2070. ~6GB combined VRAM, fits on 8GB. If we ever need true isolation we'd need a second GPU — out of scope for v0.27.
- **Image bloat.** Editing workers carry torch + whisper + musicgen dependencies they never use. Acceptable trade-off for build simplicity. If image size becomes a problem we can split into two Dockerfiles.
- **Out of scope: dynamic scaling.** Editing replica count is fixed at 3. We're not adding auto-scaling, queue-depth-driven worker spawning, or k8s-style HPA.
