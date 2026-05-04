## Why

Operator pain point reported repeatedly during testing: when a draft is "排隊中…" the only feedback is the status string. None of the actually useful questions have answers in the UI:

1. **What's the worker actually doing?** A render that's been "queued" for 3 minutes might be queued behind a 30 s analysis run, or behind another operator's 5 min render — there's no way to tell.
2. **How deep is the line?** No visibility into "you're behind 3 jobs" vs "you're next."
3. **Can I drop a job that's no longer wanted?** Operators were resorting to refreshing the page hoping the worker would skip — there was no real cancel path.

The worker container is single-process and listens on three queues (`analysis` → `editing` → `bgm`) in order, so the answers all live in Redis already; we just had no endpoint surfacing them. v0.25.0 adds that endpoint plus an FE that polls it.

## What Changes

### 1. New `api/routers/queue.py`

#### `GET /queue/status`

Walks the three queues in the worker's listen order — `analysis → editing → bgm` — and returns:

```
{
  "running": QueueJobItem | null,
  "queued": [QueueJobItem, ...]
}
```

`running` is the at-most-one job from `StartedJobRegistry` across the three queues (single-worker invariant — see "Impact" for what changes if we ever scale to multiple workers). `queued` is the in-order list across all three, with `position` matching the worker's actual dispatch order.

`QueueJobItem` carries:
- `job_id`, `queue` (`"analysis" | "editing" | "bgm"`), `kind` (mapped server-side from the fully-qualified `func_name`: `analyze` / `translate` / `render` / `export` / `bgm` / `unknown`).
- `state` (`"running" | "queued"`), `position` (None for running), `enqueued_at`, `started_at`, `elapsed_s`.
- Best-effort entity context: `project_id`, `project_name`, `asset_id`, `draft_id`. Asset-bound jobs (analyze / translate) carry `asset_id` in their args; draft-bound (export) carry `draft_id`. The endpoint backfills `project_id` in one batch SQL each, then resolves `project_name` in a final batch — typical "no jobs" response is a single Redis call with zero DB queries.

#### `DELETE /queue/jobs/{job_id}`

Calls `rq.Job.cancel()`. Three response shapes:
- 204 — job dropped from the queue.
- 404 — job_id doesn't exist.
- 409 — job is already running. The error body explicitly redirects to `POST /drafts/{id}/cancel` for live render kills, because the work-horse has in-flight ffmpeg / Whisper subprocesses and the generic queue-cancel only drops the row.

`InvalidJobOperation` from RQ (e.g. cancelling a job already in a finished state) also maps to 409 so the FE can refresh and re-render rather than hit a 500.

#### `_QUEUE_ORDER`

Tuple `("analysis", "editing", "bgm")`. Mirrors the worker's listen order. **If `python -m media_processor.workers` ever changes its listen list, this tuple must change with it or `position` becomes a lie.**

### 2. Frontend — `<QueueStatusModal>` + `<QueueStatusBadge>`

#### `<QueueStatusBadge>`

Small chip in the app header. Polls `/queue/status` every 5 s. Three colour variants:
- **Idle** — quiet outline, label "排隊 0".
- **Queued** — amber, label "排隊 N".
- **Running** — green with a soft pulse animation (`@keyframes queue-badge-pulse`), label "處理中 +N".

Click pops `<QueueStatusModal>` — same modal as ProjectEdit's "查看排隊" button.

#### `<QueueStatusModal>`

Centred dialog, `max-width: 560px`, `max-height: 85vh`. Two sections:

- **目前處理中** — at most one row, green-tinted, with a pulse bullet. Shows `{project_name} 的 {kind}` and `已進行 N 分 N 秒` (recomputed every 1 s without re-fetching).
- **排隊中（N）** — ordered list. Each row: position number, label, `已排 N 秒/分`, "取消" button. Caller-supplied `highlightDraftId` adds an amber outline + "你的任務" tag to the row whose `draft_id` matches.

While the modal is open it polls `/queue/status` every 3 s. A separate `setInterval` ticks `Date.now()` every 1 s so the elapsed-time strings update without API churn. Cancel is optimistic: drops the row locally, then `await refresh()` for canonical state.

#### Wiring

- `AppHeader.tsx` mounts `<QueueStatusBadge />` between the nav links and the version chip.
- `ProjectEdit.tsx`'s "排隊中…" card gains a "查看排隊" button that pops the modal with `highlightDraftId={selectedDraftId}` so the user's own job lights up amber. The local `queueModalOpen` state is at the page level so the modal is mounted once at the page bottom and props-drilled the highlight id in.

### 3. Client + types

- `apiClient.getQueueStatus(): Promise<QueueStatusOut>` and `apiClient.cancelQueuedJob(jobId): Promise<void>`. The cancel uses the raw `fetchImpl` path because the response is 204 No Content and the JSON-deserialising `request` helper would throw on the empty body.
- `QueueJobItem` and `QueueStatusOut` types in `web/src/api/types.ts` mirror the server schemas exactly.

## Impact

- **API surface**: two new endpoints under `/queue/`. Additive — older clients ignore them.
- **No DB changes**: everything reads from Redis (RQ's job + registry data) plus three batch SELECTs against existing tables for project name resolution. No schema migration.
- **No worker changes**: the worker process is unchanged; the inspector reads RQ's existing queue + registry state.
- **Single-worker invariant**: `running` returns at most one item because we run one worker process listening on all three queues. If we ever scale to multiple worker processes (e.g. dedicate a separate process to BGM so MusicGen doesn't head-of-line-block analysis), the schema needs `running: list[QueueJobItem]` and the FE needs to render N concurrent runs. Today's schema would still be correct semantically (`running[0]` if any), but `position` interpretation changes — it becomes "position within this queue" rather than "global dispatch order across all queues."
- **Cost of polling**: 5 s badge poll + 3 s modal poll. RQ stores queue depth in Redis hashes; a read is O(queue depth). DB cost is bounded by the number of jobs across all queues × 1 round-trip per entity-id batch (worst case: 3 batches each with ~queue-depth entries). With single-digit queue depth in practice this is single-digit milliseconds per poll.
- **Backwards compat**: the new endpoints don't depend on or modify any existing behaviour. FE can be deployed independently of the API and vice versa.
