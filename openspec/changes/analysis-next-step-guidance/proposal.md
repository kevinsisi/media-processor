## Why

The ProjectAnalysis page shows analysis progress and asset statuses, but it does not clearly tell operators what they should do next. When analysis is done but stabilized variants are still running, users can misread the page and wait unnecessarily or start editing without understanding the tradeoff.

## What Changes

- Add a top-of-page next-step message on ProjectAnalysis.
- Tell the operator whether they can start editing now, preview an existing draft, or should wait for analysis.
- When stabilized variants are still pending/running, explain that editing can start now but waiting will make more stabilized sources available.

## Capabilities

### New Capabilities

- `analysis-next-step-guidance`: Covers explicit next-step messaging on the analysis page.

### Modified Capabilities

None.

## Impact

- Frontend ProjectAnalysis UX and copy.
- Docs/memory for the clarified workflow.
