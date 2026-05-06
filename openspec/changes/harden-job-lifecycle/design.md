## Context

The current system uses RQ queues for analysis, editing, export, BGM, and point tracking. Draft render orphan recovery exists, and export artifacts are durable, but enqueue failure, cancellation, stale worker adoption, and orphan reconciliation are inconsistent across job types. The main reliability risk is rows that stay pending/running forever or workers that complete stale work after a user has moved on.

## Goals / Non-Goals

**Goals:**
- Make enqueue failure atomic or explicitly terminal for all job types that create durable rows.
- Guard render workers from adopting or completing a draft when the row is no longer in an expected in-flight state.
- Extend reconciliation beyond drafts to export artifacts, point tracking, analysis assets, and BGM jobs where state can become stale.
- Keep API response changes additive and preserve current frontend flows.
- Add tests that simulate missing RQ jobs and enqueue failures without needing live workers.

**Non-Goals:**
- No migration away from RQ.
- No distributed scheduler or external workflow engine.
- No direct social posting, batch generation, or thumbnail generation.
- No destructive cleanup of existing output files beyond existing behavior.

## Decisions

- Introduce small lifecycle helpers rather than a new framework. Shared helpers in the queue/watchdog layer can scan queued and started registries by function name, kwargs, or job id while preserving the current lazy worker import pattern.
- Prefer additive metadata only when required. DraftExport already has `job_id`, status, started/completed/error; BgmGenerationJob has `rq_job_id`. Asset analysis and point tracking may need existing fields reused first, with a migration only if tests prove missing metadata prevents reliable reconciliation.
- Reconcile with conservative fail-open behavior on Redis outages. Redis scan failure must not mark work failed; it should skip the tick and log.
- Cancellation should update durable state after the queue operation where possible, and should surface when cancellation could not stop a running job.
- Stale render adoption should be guarded in the orchestrator adoption path: the worker should only run against the intended draft/project/version when the row is still pending/processing and not already ready, failed, cancelled, or superseded.

## Risks / Trade-offs

- [Risk] Watchdog retries could duplicate work. -> Mitigation: scan both queued and started registries and guard worker adoption against stale rows.
- [Risk] Marking missing jobs failed too aggressively could punish transient Redis errors. -> Mitigation: fail open on Redis exceptions and require retry counters or stale-age thresholds before terminal failure.
- [Risk] Adding metadata migrations can complicate existing rows. -> Mitigation: prefer existing fields and use nullable additive columns only if necessary.
- [Risk] Running jobs may not stop immediately on cancellation. -> Mitigation: keep DB state truthful and prevent late completion from overwriting cancelled/failed rows.
