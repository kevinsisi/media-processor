## Why

The app now supports durable export state and multiple worker queues, but several job lifecycle edges can still leave drafts, exports, analysis, or tracking work stuck or misleading after enqueue failures, worker crashes, cancellation gaps, or stale RQ state. Large-scale novice usage needs jobs to recover or fail clearly without manual database cleanup.

## What Changes

- Make enqueue operations transactional: if RQ enqueue fails, related database rows must roll back or move to an explicit failed state instead of staying pending forever.
- Harden draft adoption so a render worker cannot overwrite or complete the wrong draft when queue/job state is stale or duplicated.
- Synchronize cancellation semantics across draft render jobs, export jobs, analysis jobs, and point-tracking jobs where supported.
- Add reconciliation/watchdog coverage for render, export, analysis, and point-tracking lifecycle rows so missing RQ jobs become retryable or terminal states.
- Improve API/UI status truthfulness by exposing terminal failures and recoverable stale states consistently.
- Do not introduce breaking API response removals; any new fields must be additive.

## Capabilities

### New Capabilities
- `job-lifecycle-reliability`: Background jobs must have durable, recoverable lifecycle state across enqueue, running, completion, failure, cancellation, and orphan reconciliation.

### Modified Capabilities

None.

## Impact

- Affected code: `src/media_processor/services/queue.py`, `src/media_processor/workers/*.py`, `src/media_processor/api/watchdog.py`, draft/export/asset/project routers, lifecycle models as needed, and frontend polling/status components if new states are surfaced.
- Possible database impact: additive migration only if existing models cannot represent needed lifecycle metadata.
- Verification: backend unit tests for enqueue failure, missing RQ job reconciliation, cancellation state sync, and stale worker adoption guards; frontend build if status UI changes.
