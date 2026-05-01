# chunked-upload (NEW)

## Purpose

Move large video files (and `.txt` scripts) from a mobile browser into the server's storage in a way that survives mobile-network drops, mid-upload reloads, and the user closing and reopening the page.

## Requirements

### REQ-1: Session lifecycle

- `POST /projects/{project_id}/uploads` opens an upload session given `{kind: 'video'|'script', filename, total_size, chunk_size, sha256?}`. Returns `{session_id, received_chunks: []}`.
- Sessions are persisted to `upload_sessions` (Postgres). The session is the single source of truth for "which chunks are in".

### REQ-2: Per-chunk PUT

- `PUT /uploads/{session_id}/chunks/{chunk_index}` writes the raw request body to disk at `${MEDIA_STORAGE_DIR}/uploads/{session_id}/chunks/{index:04d}`.
- The endpoint is idempotent — re-PUTting the same chunk index overwrites the existing chunk and does not double-add to `received_chunks`.
- Chunk indexes outside `[0, ceil(total_size/chunk_size))` are rejected 400.

### REQ-3: Resume

- `GET /uploads/{session_id}` returns `{session_id, kind, filename, total_size, chunk_size, received_chunks, status}`. The client uses `received_chunks` to skip already-uploaded indexes after a reload.
- If the session row exists but the chunk file is missing on disk, the index is removed from `received_chunks` (self-healing).

### REQ-4: Complete

- `POST /uploads/{session_id}/complete` requires every chunk index in `[0, last)` to be present.
- For `kind='video'`: assembles the chunks in order into `${MEDIA_STORAGE_DIR}/assets/{project_id}/{filename}`, runs `ffprobe` (10 s timeout) to capture `duration_ms`, `resolution`, `fps`, `codec`, creates an `Asset` row, deletes the chunk dir, and returns `AssetDetail`.
- For `kind='script'`: reads the assembled bytes as UTF-8 text, upserts the project's `Script` row, deletes the chunk dir, and returns `ScriptOut`.
- If `ffprobe` is missing or errors, the `Asset` is created with `duration_ms=0` and the missing media metadata fields left null. Status remains `pending`. The endpoint returns 200 — upload itself succeeded.

### REQ-5: Abort

- A session may be left abandoned. Cleanup of stale sessions is out of scope for M3 (tracked as follow-up).
- Client UI may issue a `POST` to complete or simply abandon the session by navigating away — no explicit abort call is required.
