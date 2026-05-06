## 1. Lifecycle Audit

- [x] 1.1 Trace all enqueue call sites for analysis, translation, render, export, BGM, and point tracking.
- [x] 1.2 Document current durable states, job ids, retry counters, and terminal states per job type.
- [x] 1.3 Identify which job types can be reconciled with existing fields and which need additive metadata.

## 2. Enqueue Safety

- [x] 2.1 Add tests for render, export, BGM, and point-tracking enqueue failures.
- [x] 2.2 Make render enqueue failures roll back or mark the draft failed with actionable feedback.
- [x] 2.3 Make export enqueue failures mark the artifact failed instead of queued.
- [x] 2.4 Make BGM and point-tracking enqueue failures roll back or write terminal failed states.
- [x] 2.5 Apply the same safety pattern to asset analysis and translation enqueue paths where durable status is changed before enqueue.

## 3. Worker Adoption Guards

- [x] 3.1 Add tests for stale render jobs attempting to mutate completed, failed, cancelled, or superseded drafts.
- [x] 3.2 Guard draft adoption/render completion so stale jobs exit without overwriting terminal draft state.
- [x] 3.3 Guard export worker startup against mismatched draft/export intent.
- [x] 3.4 Ensure late worker completion cannot overwrite cancellation or terminal failure state.

## 4. Reconciliation And Cancellation

- [x] 4.1 Generalize RQ job scanning helpers for queued and started jobs by queue, job id, function, and kwargs.
- [x] 4.2 Extend the watchdog/reconciler to export artifacts, point tracking, analysis, and BGM in-flight states.
- [x] 4.3 Preserve fail-open behavior on Redis scan errors and log skipped reconciliation ticks.
- [x] 4.4 Synchronize durable state for queued cancellation and running stop-request paths.
- [x] 4.5 Surface reconciled terminal states through existing API responses and frontend polling copy.

## 5. Verification And Follow-through

- [x] 5.1 Run targeted backend lifecycle tests for enqueue failure, stale adoption, cancellation, and orphan reconciliation.
- [x] 5.2 Run full Python gates: ruff check, ruff format --check, mypy, and pytest.
- [x] 5.3 Run frontend build if any status UI copy or polling behavior changes.
- [x] 5.4 Update README/ROADMAP and project memory with lifecycle reliability behavior.
- [x] 5.5 Run reviewer pass, update OpenSpec tasks, commit, push, and watch CI.
