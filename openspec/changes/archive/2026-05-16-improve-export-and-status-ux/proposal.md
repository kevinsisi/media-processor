## Why

The current edit flow can lose user trust because background jobs may appear idle or complete without a reliable download path. Export, polling, queue status, and analysis readiness must present truthful state before the app can feel like a one-click publishing tool.

## What Changes

- Persist derivative export artifacts so queued, running, completed, and failed exports are visible after refresh.
- Add API support to list a draft's export artifacts with public download URLs when files are ready.
- Update the export sheet to show real export status and direct downloads instead of referencing a non-existent download list.
- Prevent polling hooks from applying stale overlapping responses.
- Show an explicit degraded state when queue status cannot be fetched.
- Tighten the edit-page analysis gate so an empty or partially populated analysis payload cannot enable rendering.

## Capabilities

### New Capabilities
- `draft-export-artifacts`: Draft derivative exports are tracked, listed, and downloadable from the edit UI.
- `status-polling-reliability`: Draft, asset, queue, and edit readiness status UI avoids stale or misleading state.

### Modified Capabilities
None.

## Impact

- Backend: draft export endpoint, export worker, schemas, database model/migration, tests.
- Frontend: export sheet, polling hooks, queue badge, edit analysis readiness gate, TypeScript API types.
- Storage: derivative export files continue to live beside rendered draft mp4s; database stores artifact metadata and job status.

## Follow-up UX Backlog

These P1 findings are documented for the next change, but are not part of this implementation slice:

- Replace user-facing technical copy such as worker, FFmpeg, Gemini, YOLO, vidstab, STT, and Whisper with operator-friendly wording.
- Replace browser `confirm` dialogs with inline modal or mobile sheet flows for destructive actions.
- Rebalance ProjectEdit around preview/download/regenerate first, with advanced settings collapsed behind secondary controls.
- Improve ProjectAnalysis mobile batch selection with a bottom action bar when assets are selected.
- Retire or redirect the legacy Review route, which still uses placeholder preview and outdated download copy.
