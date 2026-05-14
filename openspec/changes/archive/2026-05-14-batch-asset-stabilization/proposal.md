## Why

Asset-level stabilization is useful, but requiring operators to open each asset card and click "產生防抖版" one by one is too much work for projects with many clips. Existing and forked projects need a project-level action that queues all missing stabilized variants at once.

## What Changes

- Add a project-level batch stabilization API for all assets in a project.
- Skip assets that are already stabilized or currently pending/running unless the request explicitly forces regeneration.
- Add a ProjectAnalysis batch action that queues every missing/failed stabilized variant in one click and reports how many were queued/skipped/failed.

## Capabilities

### New Capabilities

- `batch-asset-stabilization`: Covers batch queuing of source-level stabilized derivatives for project assets.

### Modified Capabilities

None.

## Impact

- Backend API: new project-level batch stabilization endpoint and response schema.
- Frontend API/client and ProjectAnalysis UI: one-click batch stabilization control.
- Tests: cover enqueue/skip/error behavior for batch stabilization.
