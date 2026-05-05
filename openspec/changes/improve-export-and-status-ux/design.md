## Context

Derivative exports currently run as RQ jobs and write files next to the rendered draft mp4, but the database records no export artifact state. The UI therefore only knows that a job was enqueued and cannot show completion, failure, or a durable download link after refresh.

The frontend also has status reliability gaps: draft and asset polling use fixed intervals that can overlap slow requests, the queue badge treats fetch failures like an empty queue, and the edit page can enable rendering when analysis payloads contain no counted steps.

## Goals / Non-Goals

**Goals:**

- Persist every derivative export request with job id, aspect, height, filename, status, and error text.
- Return a draft-scoped export list so the edit page can show queued/running/done/failed artifacts and direct download links.
- Update the export worker to mark artifacts running, done, or failed without changing the rendered source draft.
- Prevent stale overlapping frontend status requests from overwriting fresher state.
- Surface queue-status fetch failures explicitly.
- Require meaningful analysis step data before enabling edit triggers.
- Document P1 UX audit findings as follow-up backlog.

**Non-Goals:**

- Do not redesign ProjectEdit or ProjectAnalysis in this change.
- Do not replace all technical copy or browser confirms in this change.
- Do not introduce export cancellation or retention cleanup.
- Do not move exported files away from the existing drafts directory layout.

## Decisions

- Add a `draft_export` table instead of storing export metadata inside `Draft.cut_plan_json` or RQ result payloads.
  - Rationale: export artifacts need durable list/query behavior independent of queue retention and draft render status.
  - Alternative rejected: infer from files on disk. That cannot reliably represent queued/running/failed jobs.
- Keep derivative files under `${DRAFTS_DIR}/{project_id}/v{version}-{aspect}-{height}p.mp4`.
  - Rationale: this preserves the current worker behavior and public static-file mount.
  - Alternative rejected: add a separate export directory, which adds migration and URL complexity without solving the UX issue.
- Pass `export_id` to the RQ job while keeping the existing `draft_id/aspect/height` arguments.
  - Rationale: the worker can update the exact artifact row; tests and future tooling can still reason about draft-bound exports.
- Use recursive timeout-based polling or sequence guards instead of fixed `setInterval` loops.
  - Rationale: the next request starts only after the previous one resolves, and stale responses cannot overwrite current route state.
- Treat queue status fetch errors as a first-class UI state.
  - Rationale: `排隊 0` must mean the queue is actually empty, not that the status endpoint failed.

## Risks / Trade-offs

- Export job may be enqueued but worker never starts -> artifact remains `queued`; mitigated by showing queue status and allowing manual retry via another export request.
- Existing export jobs enqueued before this change have no row -> they will not appear in the export list; acceptable because derivative exports are non-authoritative and can be re-requested.
- Database migration adds a new table -> rollback drops export history only, not rendered mp4 files.
- Polling behavior changes timing slightly -> mitigated by preserving the existing fast/slow intervals and settle-tail behavior.

## P1 UX Analysis Backlog

- User-facing technical copy remains visible in several pages and should be translated into outcome-oriented language.
- Destructive browser confirms are not mobile-friendly and should become reusable modal/sheet flows.
- ProjectEdit should be simplified into a primary preview/download/regenerate path with advanced settings secondary.
- ProjectAnalysis batch actions should become a mobile bottom action bar when assets are selected.
- Legacy Review should be redirected or removed once ProjectEdit fully owns review/download.
