# v0.27.0 — tasks (all done)

## Backend

- [x] `src/media_processor/workers/__main__.py` — accept `sys.argv[1:]` queue names; validate against `VALID_QUEUES`; default to all-3 when no args (backward-compat for local dev). Drop `name=f"media-worker-{settings.api_host}"` so RQ auto-generates a unique `hostname.pid` worker name per container.
- [x] `src/media_processor/api/routers/queue.py` — widen `QueueStatusOut.running` from `QueueJobItem | None` to `list[QueueJobItem]`. Endpoint walks `StartedJobRegistry.get_job_ids()` for each queue and collects every running job (was: kept only the first per queue).

## Compose

- [x] `docker-compose.yml` — replace single `worker:` service with three:
  - `worker-analysis` — 1 replica, `command: ["python", "-m", "media_processor.workers", "analysis"]`, `deploy.resources.reservations.devices` with nvidia driver.
  - `worker-editing` — `deploy.replicas: 3` (documented as load-bearing-only-with-`--scale`), no GPU reservation.
  - `worker-bgm` — 1 replica, GPU reservation, listens on `bgm`.
- [x] All three share `docker/worker.Dockerfile` and the same volume mounts as the old worker.

## FE

- [x] `web/src/api/types.ts` — `QueueStatusOut.running` is now `QueueJobItem[]`.
- [x] `web/src/components/QueueStatusBadge.tsx` — render `處理中 N +M` when `runningCount > 0`, else `排隊 M`. Pulse animation gated on `runningCount > 0`. Variant `--running` / `--queued` / `--idle` for colour shift.
- [x] `web/src/components/QueueStatusModal.tsx` — running section is now a `<ul>` of `QueueJobItem` rows (was: single optional row). Title shows count. Each row supports `highlightDraftId` matching for the "你的任務" tag.

## Verify

- [x] `npm run build` clean (TypeScript compiles, vite bundles).
- [x] `docker compose build api worker-analysis worker-editing worker-bgm web` — all 5 images build.
- [x] `docker compose up -d --scale worker-editing=3 api worker-analysis worker-editing worker-bgm web` — 5 worker containers start.
- [x] Stop and remove orphan `media-processor-worker-1` container left over from the pre-0.27 single-worker setup (compose no longer tracks it after the service rename).
- [x] `docker compose ps` shows: api ×1, postgres ×1, redis ×1, web ×1, worker-analysis ×1, worker-editing ×3, worker-bgm ×1.
- [x] Each worker logs `*** Listening on <queue>...` with a unique auto-generated worker name (UUID-style, not `media-worker-0.0.0.0`).
- [x] `GET /health` returns `version=0.27.0`.
- [x] `GET /queue/status` returns the new `{running: [], queued: []}` shape.

## Docs / memory

- [x] `ROADMAP.md` — bump current version to 0.27.0, add M9.12 row to phase table, append `## ✅ Phase 9.12（M9.12）` section, push M10 to 0.28.x+.
- [x] `CLAUDE.md` — bump current-version line, add v0.27 multi-worker fan-out to the archived-milestones list, append docker-compose pointer to the project-architecture pointers.
- [x] `memory/v027_multi_worker.md` — load-bearing notes on `--scale`, GPU-on-image-but-dormant pattern, worker name collision fix, `running` shape change.
- [x] `memory/MEMORY.md` — index entry for v0.27 memory; bump snapshot title.
- [x] `memory/project_media_processor_v2.md` — frontmatter version bump + current-version paragraph rewritten + new "Where to look" pointer.
- [x] This proposal + tasks file under `openspec/changes/archive/2026-05-04-v0.27-multi-worker/`.

## Versions

- [x] `pyproject.toml`: `0.26.0` → `0.27.0`
- [x] `src/media_processor/api/main.py`: FastAPI `version=` → `0.27.0`
- [x] `web/package.json`: `0.26.0` → `0.27.0`
