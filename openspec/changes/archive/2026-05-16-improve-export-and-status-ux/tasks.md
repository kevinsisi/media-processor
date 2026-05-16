## 1. Export Artifact Backend

- [x] 1.1 Add draft export artifact model and Alembic migration.
- [x] 1.2 Add API schemas and draft-scoped export list endpoint.
- [x] 1.3 Update export enqueue endpoint to create artifact rows and return artifact metadata.
- [x] 1.4 Update export worker to mark artifacts running, done, and failed.
- [x] 1.5 Add or update backend tests for export artifact lifecycle and list responses.

## 2. Frontend Status UX

- [x] 2.1 Update export API types/client and ExportSheet to list artifacts, poll pending exports, and show direct downloads.
- [x] 2.2 Change draft and asset polling hooks so stale overlapping responses cannot overwrite fresh state.
- [x] 2.3 Add queue badge degraded/error state and recovery behavior.
- [x] 2.4 Tighten ProjectEdit analysis readiness gate for empty or partial analysis payloads.

## 3. Documentation And Verification

- [x] 3.1 Document P1 UX audit backlog in the change artifacts or project docs.
- [x] 3.2 Run focused backend tests for drafts/export behavior.
- [x] 3.3 Run frontend build and relevant lint/type checks.
- [x] 3.4 Update project memory with the export/status UX reliability notes.
- [x] 3.5 Commit and push the completed change.
