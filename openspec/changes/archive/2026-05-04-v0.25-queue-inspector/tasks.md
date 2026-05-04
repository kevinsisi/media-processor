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

## 5. Orphan-Draft watchdog (v0.25.1 follow-up)

- [x] 5.1 `services.queue.has_draft_render_job(draft_id) -> bool` factored out (mirrors the editing-queue + StartedJobRegistry scan from `cancel_draft_render`). Fails open on Redis errors so a transient blip doesn't invent a phantom orphan.
- [x] 5.2 `Draft.render_retry_count` INTEGER NOT NULL DEFAULT 0 (alembic `0023_draft_render_retry_count`). Tracks watchdog auto-retry attempts; reset to 0 on every user-initiated re-render.
- [x] 5.3 `api/watchdog.py` — FastAPI lifespan-managed background task. Sweeps at boot + every 60 s. For each in-flight Draft whose RQ job has disappeared: re-enqueue (up to `WATCHDOG_MAX_RETRIES = 3`) with snapshotted flags + `skip_plan = bool(cut_plan_json)` + `subtitles_from_db = skip_plan and flags["subtitles"]`; three strikes flip to `failed`.
- [x] 5.4 `api/main.py` lifespan hook — `asyncio.create_task(watchdog_loop())` at startup; cancellation-safe shutdown.
- [x] 5.5 Read-time fast-fail in `GET /drafts/{id}`: when `retry_count >= 3` AND `has_draft_render_job() is False`, flip to `failed` immediately so the FE surfaces the failure card on the next poll rather than waiting up to 60 s for the next watchdog tick. Read-time NEVER recovers — watchdog is the single resubmit owner.
- [x] 5.6 `Draft.render_retry_count = 0` reset added to all three skip-plan re-render endpoints in `api/routers/drafts.py` (re-render, reorder, rebuild-subtitles).
- [x] 5.7 FE `ProjectEdit.tsx` detects `prompt_feedback` prefix `watchdog:` and renders an orphan-aware failed card: title "任務已遺失", custom body, no progress bar, button "重新提交".

## 6. Queue modal mobile-overflow fix (v0.25.1)

- [x] 6.1 `QueueStatusModal.css` — backdrop padding uses `env(safe-area-inset-*)` so the modal stays clear of iPhone notch + home indicator.
- [x] 6.2 `max-height: 100%` (the backdrop's safe-area padding already trims the absolute viewport so 100% is the right answer; `85vh` was redundant + wrong on iOS Safari).
- [x] 6.3 Sticky header — close button + title stay visible while the queued list scrolls.
- [x] 6.4 `QueueStatusModal.tsx` — wrapped sections in `.queue-modal__body` so the sticky header sits in its own scroll-frozen row and the body has its own scroll.
- [x] 6.5 `@media (max-width: 480px)` full-bleed: no rounded corners, no L/R border, padding zeroed so every pixel of vertical space goes to the queue list.

## 7. Memory + docs + version bump (v0.25.1)

- [x] 7.1 `memory/v025_queue_inspector.md` extended with the watchdog corollaries: any future "background job + DB row" pair needs an orphan check; `render_retry_count` reset on user trigger is the load-bearing detail.
- [x] 7.2 `memory/MEMORY.md` index entry updated; `project_media_processor_v2.md` snapshot bumped to 0.25.1.
- [x] 7.3 `ROADMAP.md` — Phase 9.10.4 (watchdog) + 9.10.5 (modal mobile fix) subsections; current-version line; M9.10 row covers 0.25.0 – 0.25.1.
- [x] 7.4 `CLAUDE.md` — current-version line; new `api/watchdog.py` architecture pointer.
- [x] 7.5 Version bumped to 0.25.1 in `pyproject.toml` + `src/media_processor/api/main.py` + `web/package.json`.
- [x] 7.6 Branched as `claude/v0.25.1-orphan-watchdog`, merged --no-ff into `main`, pushed. Docker compose build api worker web + up -d. Verified live: trigger render → `redis-cli FLUSHALL` → wait 60 s → `docker logs api` shows `watchdog: draft 41 auto-resubmitted (retry 1/3)`; DB shows `render_retry_count=1, status=pending`; `/queue/status` shows the new RQ job. Branch pruned local + remote.
