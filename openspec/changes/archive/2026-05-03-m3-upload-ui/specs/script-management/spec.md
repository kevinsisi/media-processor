# script-management (NEW)

## Purpose

Each project carries a single script. Operators may paste the script in a textarea or upload a `.txt` file. The frontend auto-saves on edit so a page reload does not lose progress.

## Requirements

### REQ-1: One script per project

- `scripts` has `(project_id UNIQUE)` — at most one row per project.
- `PUT /projects/{id}/script` upserts the row with body `{body: string, source_filename?: string|null}`.
- `GET /projects/{id}/script` returns the row or 404 if none exists yet.

### REQ-2: Two ingest paths, one row

- Pasted text goes through `PUT /projects/{id}/script` directly (small payload, no chunking).
- Uploaded `.txt` files go through the chunked upload protocol with `kind='script'`. On `complete` the server reads the assembled bytes as UTF-8 and writes the `scripts` row. The same row is updated.

### REQ-3: Size cap

- Scripts are capped at 1 MB UTF-8. Larger payloads return 413.

### REQ-4: Auto-save semantics

- The UI debounces edits (≥ 500 ms idle) before issuing `PUT`. The user sees a "已儲存" confirmation timestamp after each successful save.
- The endpoint is idempotent — repeated saves of the same content are safe.
