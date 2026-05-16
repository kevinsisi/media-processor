## Why

The approved redesign requires ProjectAnalysis to become the manual-control decision point, not only a status list. After the production dual-path entry landed, users who choose manual control still need a clear screen-level hub showing what is ready: analysis, stabilized variants, tracking decisions, and the next step toward editing.

## What Changes

- Add a ProjectAnalysis decision hub above the existing asset list.
- Show material decision counters for analyzed assets, available/active stabilized variants, and tracking readiness.
- Provide direct actions for one-click automatic generation and manual continuation to editing settings.
- Keep all existing per-asset controls and batch toolbar behavior.

## Capabilities

### New Capabilities

- `analysis-manual-decision-hub`: ProjectAnalysis exposes a concrete manual workflow hub after users choose to control materials/segments.

### Modified Capabilities

- `dual-path-production-entry`: Manual entry now lands on a page that summarizes manual readiness and offers the next action.

## Impact

- Frontend ProjectAnalysis layout and copy.
- No backend schema changes.
