# v0.28.0 — async point tracking on worker-analysis

**Status:** ✅ shipped 2026-05-05.

## Why

v0.27.3 added a 30-second cooperative wall-clock budget to `track_point` so a stuck LK loop returned a clean 504 instead of grinding the API thread past nginx's 60 s `proxy_read_timeout`. The operator who reported the original bug came back with a clearer requirement: pixel-precise tracking has to actually complete on their 1728×3072 portrait / 2-minute clip — falling back to a different mode is not acceptable. The whole point of the manual point-pick is "I've decided this asset deserves the surgical option."

A 30 s budget on a synchronous endpoint cannot satisfy that requirement. The fundamental problem is that the LK loop is bounded by frame count × per-frame decode cost, and at full source fps × 5.3 MP frames that's many minutes. There's no tuning of the budget that covers both "5-second clip on a 720p screen recording" and "2-minute portrait at 30 fps" without either losing UX or losing tracking.

The right fix is to take the synchronous endpoint out of the picture entirely.

## What

`PATCH /assets/{id}/tracking-target` mode=point now enqueues an RQ job on the `analysis` queue and returns immediately:

```
PATCH /assets/{id}/tracking-target  (mode=point)
  ↓
202 Accepted + TrackingTargetResponse{point_tracking_status: "pending"}
  ↓
worker-analysis picks up track_point_job
  ↓ (1 minute … several minutes)
worker writes point_tracking_json + status="done"
  ↓
FE polling sees status flip → renders crosshair
```

### Schema (alembic 0024)

Two new nullable columns on `assets`:

- `point_tracking_status: VARCHAR(16) | NULL` — `NULL` (pre-0.28 / never tried) / `"pending"` / `"done"` / `"failed"`
- `point_tracking_error: TEXT | NULL` — populated on `"failed"`

The renderer (`services/auto_reframe.compute_crop_path_from_point_track`) does NOT read `status` — its check is unchanged: "is `point_tracking_json` populated?". Pre-0.28 rows have NULL status but valid traces; they continue rendering correctly. Status is FE/operator-facing only.

### Backend

- `services/queue.py` — new `enqueue_point_tracking(asset_id, init_norm_x, init_norm_y, init_t_ms)` mirroring `enqueue_asset_analysis`. RQ default_timeout = 30 min. Job target string = `media_processor.workers.point_tracking_jobs.track_point_job`.
- `workers/point_tracking_jobs.py` — thin sync RQ entry that wraps `asyncio.run(run_point_tracking(...))`. Lazy-imports the runner so the api container's import graph stays clean.
- `services/point_tracking_runner.py` — async orchestrator. Loads Asset, calls `track_point` with `time_budget_s=3600` (1 h, defense-in-depth against corrupt cv2 reads), writes back `point_tracking_json` + `point_tracking_origin` (with cv2-resolved x/y) + `status="done"`. Catches every exception and writes `status="failed"` + `error` so the FE polling sees a terminal state.
- `api/routers/assets.py` — `PATCH /assets/{id}/tracking-target` mode=point: stages `tracked_object_index=-4`, `status="pending"`, clears `point_tracking_json` + `point_tracking_error`, stores operator's intent in `point_tracking_origin = {frame_ms, norm_x, norm_y}` (no x/y yet), commits, enqueues, sets `response.status_code = 202`. Other modes unchanged. Removes the inline `asyncio.to_thread(track_point)` call + the v0.27.3 504-on-timeout path.
- `api/routers/queue.py` — adds `point_track` to the kind label map so the queue inspector shows "精準像素追蹤" instead of "其他".

### FE

- `web/src/api/types.ts`: `TrackingDetailOut` + `TrackingTargetResponse` get `point_tracking_status` + `point_tracking_error` fields. `point_tracking_origin.x` / `.y` become optional (worker fills them after cv2 resolution).
- `web/src/components/AssetTrackingTarget.tsx`: `applyTarget` for mode=point doesn't fetchDetail eagerly anymore — the polling effect handles it. New effect: when `detail.point_tracking_status === "pending"`, refetch every 2 s; stop on terminal state. Another effect: when status flips to `"failed"`, copy `point_tracking_error` into `error` for the toast. Card surfaces "精準像素追蹤分析中…（worker 正在跑 LK 光流，較長 / 高解析度的素材需要幾分鐘）" while pending.
- `web/src/components/QueueStatusModal.tsx`: KIND_LABEL adds `point_track: "精準像素追蹤"`.

## Risks / Out of scope

- **Orphan-Draft watchdog doesn't watch Asset point-tracking jobs.** v0.25.1's watchdog sweeps Drafts for stuck pending/processing rows whose RQ job has disappeared. If a worker crashes mid-tracking, the Asset stays in `point_tracking_status="pending"` forever until the operator manually re-PATCHes. Documented in memory; extending the watchdog to watch Assets too is a separate phase.
- **Concurrency on the analysis queue.** The user explicitly asked for `analysis` queue. `worker-analysis` runs Whisper / YOLO / MediaPipe / Gemini Vision sequentially (1 worker, GPU-bound), so a long point-tracking job head-of-line blocks an upload-time analysis. If this becomes painful in practice, options are (a) move point-tracking to `worker-editing` (3 replicas, CPU only), (b) carve a new dedicated queue. Both are revisitable.
- **The 30 s in-process budget stays.** `MAX_LK_DURATION_S = 30` remains the `track_point` default so any future in-process caller (CI tests, a debug REPL) gets the cheap-bail behaviour. Worker explicitly overrides to 1 h. We deliberately did not delete the v0.27.3 timeout machinery.
- **No support for cancelling a running job.** The queue inspector's `DELETE /queue/jobs/{id}` already 409s on running jobs (v0.25.0); a stuck point-track has to ride out the 30 min RQ timeout. If a per-job cancel is needed, follow the v0.25.0 pattern + `rq.command.send_stop_job_command` — out of scope here.
- **Asset deletion mid-tracking is undefined.** If the operator deletes the asset while a job is in flight, `services/asset_management.delete_asset` removes the row; the job will read `None` from `session.get(Asset, asset_id)` and return `{"status": "missing"}`. The runner explicitly handles this case.

## Verification on prod deploy 2026-05-05

- `alembic upgrade head` ran clean: `0023 → 0024_asset_point_tracking_status`.
- `GET /health` → `version=0.28.0`.
- `PATCH /assets/14/tracking-target` (1728×3072 portrait, real fps higher than the 5 fps Whisper sample shown in tracking_json): returned 202; status flipped through `pending` → `done` in 1 min 10 s; `has_point_track=true`, `error=null`. Pre-0.27.3 sync version would have been killed by nginx; v0.27.3 sync version would have hit the 30 s budget.
- All 5 worker containers running (1 analysis + 3 editing + 1 bgm); `--scale worker-editing=3` deploy command honoured.
