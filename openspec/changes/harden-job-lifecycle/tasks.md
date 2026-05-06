## 1. Lifecycle Audit

- [ ] 1.1 Trace all enqueue call sites for analysis, translation, render, export, BGM, and point tracking.
- [ ] 1.2 Document current durable states, job ids, retry counters, and terminal states per job type.
- [ ] 1.3 Identify which job types can be reconciled with existing fields and which need additive metadata.

## 2. Enqueue Safety

- [ ] 2.1 Add tests for render, export, BGM, and point-tracking enqueue failures.
- [ ] 2.2 Make render enqueue failures roll back or mark the draft failed with actionable feedback.
- [ ] 2.3 Make export enqueue failures mark the artifact failed instead of queued.
- [ ] 2.4 Make BGM and point-tracking enqueue failures roll back or write terminal failed states.
- [ ] 2.5 Apply the same safety pattern to asset analysis and translation enqueue paths where durable status is changed before enqueue.

## 3. Worker Adoption Guards

- [ ] 3.1 Add tests for stale render jobs attempting to mutate completed, failed, cancelled, or superseded drafts.
- [ ] 3.2 Guard draft adoption/render completion so stale jobs exit without overwriting terminal draft state.
- [ ] 3.3 Guard export worker startup against mismatched draft/export intent.
- [ ] 3.4 Ensure late worker completion cannot overwrite cancellation or terminal failure state.

## 4. Reconciliation And Cancellation

- [ ] 4.1 Generalize RQ job scanning helpers for queued and started jobs by queue, job id, function, and kwargs.
- [ ] 4.2 Extend the watchdog/reconciler to export artifacts, point tracking, analysis, and BGM in-flight states.
- [ ] 4.3 Preserve fail-open behavior on Redis scan errors and log skipped reconciliation ticks.
- [ ] 4.4 Synchronize durable state for queued cancellation and running stop-request paths.
- [ ] 4.5 Surface reconciled terminal states through existing API responses and frontend polling copy.

## 5. Verification And Follow-through

- [ ] 5.1 Run targeted backend lifecycle tests for enqueue failure, stale adoption, cancellation, and orphan reconciliation.
- [ ] 5.2 Run full Python gates: ruff check, ruff format --check, mypy, and pytest.
- [ ] 5.3 Run frontend build if any status UI copy or polling behavior changes.
- [ ] 5.4 Update README/ROADMAP and project memory with lifecycle reliability behavior.
- [ ] 5.5 Run reviewer pass, update OpenSpec tasks, commit, push, and watch CI.
