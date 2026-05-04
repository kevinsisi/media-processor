# Tasks — v0.25.0 (RQ queue inspector + queued-job cancel)

## 1. Backend

- [x] 1.1 New router `api/routers/queue.py`. Mounted on the FastAPI app via `app.include_router(queue.router)` in `main.py` after the other routers.
- [x] 1.2 `_JOB_KIND_BY_FUNC` dict mapping the canonical RQ `func_name` strings to the operator-facing kind label (analyze / translate / render / export / bgm); falls back to `"unknown"`.
- [x] 1.3 `_QUEUE_ORDER = ("analysis", "editing", "bgm")` matching the worker's listen list. **Inline comment + memory entry flag this as the place to update when the worker's listen list changes** — drift would break the `position` field's promise.
- [x] 1.4 `_job_to_item(job, queue_name, state, position=None)` helper: pulls `job_id`, `enqueued_at`, `started_at`, `elapsed_s` (only when running), and the entity ids (`asset_id` / `draft_id` / `project_id`) off the job's `args` / `kwargs` mirroring the call signatures in `services.queue`.
- [x] 1.5 `_resolve_project_links(session, items)` — backfills `project_id` from `Asset.project_id` / `Draft.project_id` for asset-bound and draft-bound jobs in two batch SELECTs, then resolves `project_name` in a final batch. Short-circuits on empty id sets so the typical "no jobs" response makes zero DB queries.
- [x] 1.6 `GET /queue/status` walks `_QUEUE_ORDER`, drains the StartedJobRegistry for the running job, then `queue.get_job_ids()` for queued. `running` is the first started-job found across the three registries (single-worker invariant).
- [x] 1.7 `DELETE /queue/jobs/{job_id}` — `Job.fetch` 404 mapped, `is_started` 409 with explicit pointer to `POST /drafts/{id}/cancel`, `Job.cancel()` happy path 204. `InvalidJobOperation` (job in unrelatable state) also 409.

## 2. Frontend

- [x] 2.1 `web/src/api/types.ts` — `QueueName`, `QueueJobState`, `QueueJobItem`, `QueueStatusOut` mirror the server schemas exactly.
- [x] 2.2 `web/src/api/client.ts` — `getQueueStatus(): Promise<QueueStatusOut>` (uses the standard `request` helper) and `cancelQueuedJob(jobId): Promise<void>` (uses the raw `fetchImpl` path because the 204 No Content response would throw in the JSON-deserialising helper).
- [x] 2.3 `web/src/components/QueueStatusModal.tsx` + `.css` — full modal with running highlight + queued list + per-row cancel + `highlightDraftId` outline. Polls every 3 s while open; ticks elapsed-time strings every 1 s without re-fetching.
- [x] 2.4 `web/src/components/QueueStatusBadge.tsx` + `.css` — header chip with three colour variants (idle / queued / running) + soft pulse animation on the running variant. Polls every 5 s. `prefers-reduced-motion` disables the pulse.
- [x] 2.5 `web/src/components/AppHeader.tsx` — mounts `<QueueStatusBadge />` between the nav links and the version chip.
- [x] 2.6 `web/src/pages/ProjectEdit.tsx` — `queueModalOpen` state at the page level, "查看排隊" button on the "排隊中…" card, modal mounted at page bottom with `highlightDraftId={selectedDraftId}`.

## 3. Verification

- [x] 3.1 Live: `GET /queue/status` while idle returns `{running:null, queued:[]}`.
- [x] 3.2 Live: stack 3 renders quickly; response shows `running.draft_id=41` plus `queued[0].draft_id=41` (same draft, different invocation) and `queued[1].draft_id=40` with `position` 0 and 1 respectively. `project_name="P17車子影片"` resolved on every row.
- [x] 3.3 Live: `elapsed_s` ticks up across successive polls (verified 2.4 → 31.9 across two requests).
- [x] 3.4 Live: `DELETE /queue/jobs/{running_id}` → 409. `DELETE /queue/jobs/{queued_id}` → 204; the row disappears from the next `GET /queue/status`. `DELETE /queue/jobs/no-such-id` → 404.

## 4. Memory + docs + version bumps

- [x] 4.1 `memory/v025_queue_inspector.md` — new memory file. Front-loaded with "if the worker's listen order changes, `_QUEUE_ORDER` must change with it" because that's the trap a future contributor most likely walks into.
- [x] 4.2 `memory/MEMORY.md` index entry; `project_media_processor_v2.md` snapshot bumped to 0.25.0.
- [x] 4.3 `ROADMAP.md` — Phase 9.10 row + 9.10.1 / 9.10.2 / 9.10.3 subsections; current-version line; M10 deferred to 0.26.x+.
- [x] 4.4 `CLAUDE.md` — current-version line; new `api/routers/queue.py` pointer in the architecture-pointers section.
- [x] 4.5 Version bumped to 0.25.0 in `pyproject.toml` + `src/media_processor/api/main.py` + `web/package.json`.
- [x] 4.6 Branched as `claude/v0.25.0-queue-inspector`, merged --no-ff into `main`, pushed; docker compose build api web + up -d on the dispatch host; `/health` smoke-tested at 0.25.0; `/queue/status` smoke-tested with idle + stacked + post-cancel states. Branch pruned local + remote after merge.
