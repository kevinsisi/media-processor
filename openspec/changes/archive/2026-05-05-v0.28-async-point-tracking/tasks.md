# v0.28.0 — tasks (all done)

## Schema

- [x] `alembic/versions/0024_asset_point_tracking_status.py` — adds `assets.point_tracking_status VARCHAR(16) NULL` + `assets.point_tracking_error TEXT NULL`. Down-revision: `0023_draft_render_retry_count`.
- [x] `models/project.py:Asset` — add `point_tracking_status: Mapped[str | None]` + `point_tracking_error: Mapped[str | None]`. Add `Text` to the sqlalchemy import list.

## Backend

- [x] `services/queue.py` — add `TRACK_POINT_FN` constant, `TRACK_POINT_JOB_TIMEOUT_SECONDS = 60 * 30`, and `enqueue_point_tracking(asset_id, *, init_norm_x, init_norm_y, init_t_ms)` mirroring `enqueue_asset_analysis`.
- [x] `workers/point_tracking_jobs.py` — new file with `track_point_job(asset_id, *, init_norm_x, init_norm_y, init_t_ms)` that `asyncio.run(run_point_tracking(...))`. Lazy-imports the runner.
- [x] `services/point_tracking_runner.py` — new file. `async def run_point_tracking(asset_id, *, init_norm_x, init_norm_y, init_t_ms)`. Two session phases: load Asset to get `file_path` + `duration_ms`; release session before the cv2 work; reopen a session afterward to write the result. On exception writes `status="failed"` + `error=<reason>`. Defense-in-depth `time_budget_s=3600` on `track_point`.
- [x] `api/routers/assets.py` — replace inline `asyncio.to_thread(track_point)` block with state-set + `enqueue_point_tracking` + `response.status_code = 202`. Remove the `point_tracking_svc` import that's no longer used. Keep `Path` import (still used by the custom-ROI branch). Add `Response` to the FastAPI imports.
- [x] `api/routers/queue.py` — add `point_track` entry to `_JOB_KIND_BY_FUNC`; add `"point_track"` to the `kind in ("analyze", "translate", ...)` tuple in `_job_to_item` so asset_id is extracted.
- [x] `api/schemas.py` — `TrackingTargetResponse` gets `point_tracking_status: str | None`. `TrackingDetailOut` gets `point_tracking_status: str | None` + `point_tracking_error: str | None`.

## FE

- [x] `web/src/api/types.ts` — extend `TrackingDetailOut` + `TrackingTargetResponse` with the new status/error fields. Make `point_tracking_origin.x` / `.y` optional.
- [x] `web/src/components/AssetTrackingTarget.tsx` — new effect: poll `fetchDetail` every 2 s while `detail.point_tracking_status === "pending"`. New effect: copy `point_tracking_error` into `error` when status flips to `"failed"`. Update `applyTarget` to skip the eager `fetchDetail` for mode=point (the polling effect handles it). Add the "精準像素追蹤分析中…" banner below the bbox area.
- [x] `web/src/components/QueueStatusModal.tsx` — KIND_LABEL adds `point_track: "精準像素追蹤"`.

## Verify

- [x] `npm run build` clean.
- [x] `docker compose build api web worker-analysis worker-editing worker-bgm` — all images build.
- [x] `docker compose up -d --scale worker-editing=3 ...` — alembic 0024 ran on api boot, all 5 worker containers + api + web up, `GET /health` returns 0.28.0.
- [x] e2e: `PATCH /assets/14/tracking-target` mode=point returned **202**; `point_tracking_status` flipped `pending` → `done` in 1 min 10 s; `has_point_track=true`; no error. Worker logs show `track_point_job(14, ...) Job OK`.

## Docs / memory

- [x] `ROADMAP.md` — bump current-version line to 0.28.0, add M9.13 row + section, push M10 target to 0.29.x+.
- [x] `CLAUDE.md` — bump current version, add `v0.28.0 async point-tracking on worker-analysis` to archive list, rewrite `services/point_tracking.py` pointer to mention async migration, add `services/point_tracking_runner.py` pointer.
- [x] `memory/v028_async_point_tracking.md` — pipeline shape + don'ts (don't read status from renderer; don't put on editing queue without revisiting; orphan-watchdog doesn't cover Assets yet).
- [x] `memory/MEMORY.md` — index entry.
- [x] `memory/project_media_processor_v2.md` — frontmatter + current-version paragraph + "Where to look" pointer.
- [x] This proposal + tasks under `openspec/changes/archive/2026-05-05-v0.28-async-point-tracking/`.

## Versions

- [x] `pyproject.toml`: `0.27.3` → `0.28.0`
- [x] `src/media_processor/api/main.py`: FastAPI `version=` → `0.28.0`
- [x] `web/package.json`: `0.27.3` → `0.28.0`
