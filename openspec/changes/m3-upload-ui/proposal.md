## Why

M2 stood up the read-side of the API and the Review Inbox UI on top of seeded data. Before any AI pipeline can run we need a way for the operator (主要使用者：晴晴，手機操作為主) to put real footage and a script into the system. M3 closes that gap with a project-creation + chunked upload flow that survives mobile network drops, lets the script be pasted or uploaded as a file, and captures the IG target aspect ratio (9:16 Reels, 4:5 Feed, 1:1 Feed) at project creation time.

This is the bridge from "AI pipeline foundations" (M2) to "real content goes in" (M3 → M4).

## What Changes

### Data model

- Add `target_aspect_ratio` column to `projects` (`'9:16' | '4:5' | '1:1'`, default `'9:16'`).
- New `scripts` table — one script per project (text body, optional source filename, updated_at). Pasted text and uploaded `.txt` files end up in the same row.
- New `upload_sessions` table — tracks chunked upload state for resumability:
  - `id` (uuid), `project_id`, `kind` (`'video' | 'script'`), `filename`, `total_size`, `chunk_size`, `received_chunks` (JSON int array), `sha256` (optional), `status` (`'pending' | 'complete' | 'aborted'`), `created_at`, `completed_at`.
- One Alembic migration `0002_m3_uploads.py`.

### API (new endpoints)

- `POST /projects` — create a project (`name`, optional `client`, `profile_name`, `target_aspect_ratio`). Returns `ProjectDetail`.
- `POST /projects/{id}/uploads` — open an upload session. Body: `{kind, filename, total_size, chunk_size, sha256?}`. Returns `{session_id, received_chunks: []}`.
- `GET /uploads/{session_id}` — fetch state for resume. Returns `{session_id, kind, filename, total_size, chunk_size, received_chunks, status}`.
- `PUT /uploads/{session_id}/chunks/{chunk_index}` — upload a single chunk (raw bytes, `Content-Type: application/octet-stream`). Idempotent on retry of the same index.
- `POST /uploads/{session_id}/complete` — assemble the chunks, run `ffprobe` for video kind, create the `Asset` (or persist the `Script`), delete chunk scratch dir. Returns the resulting asset/script row.
- `GET /projects/{id}/script` / `PUT /projects/{id}/script` — read and write the project's script directly (paste path; bypasses chunked upload for ≤ 1 MB text).

All upload state persists in Postgres so the UI can resume after page reload.

### Storage layout

Under `MEDIA_STORAGE_DIR` (already bind-mounted at `/app/media`):

- `uploads/{session_id}/chunks/{index}` — chunk scratch (deleted on complete/abort)
- `assets/{project_id}/{filename}` — final video asset
- Script body lives in DB only (max ~1 MB).

### Web UI (mobile-first, 繁體中文)

- New page `/projects/new` — create project: 名稱、客戶（選填）、風格檔（下拉）、IG 輸出比例（9:16 / 4:5 / 1:1 視覺化選擇）。儲存後跳轉 `/projects/:id/upload`.
- New page `/projects/:id/upload` — three tabs in vertical stack:
  - 影片上傳：tap-to-pick or drag area, multi-file, per-file progress bar, resumable on reload.
  - 腳本：貼上文字 textarea OR 上傳 `.txt`. 自動儲存（debounced PUT to `/projects/:id/script`).
  - 完成度卡片：顯示已上傳幾個影片、是否有腳本、`target_aspect_ratio`，「進入審核」按鈕（M4 接 AI pipeline）.
- `ProjectList` gains a sticky bottom-right "新增專案" button on mobile, top-right on desktop.
- Uploader module uses `XMLHttpRequest` (for `progress` events) with a slice loop:
  - On mount, `GET /uploads/{session_id}` to pull `received_chunks` and skip them.
  - On each chunk completion, persist `session_id` + `local_file_id` mapping in `localStorage` so a hard reload still finds the session.
- Touch targets ≥ 44 px, 16 px base font, single-column layout, sticky action bar at bottom on mobile.

Out of scope (deferred):

- Real AI pipeline processing (still M4).
- Multi-script / chapter scripts — single script body per project for now.
- WebSocket push notifications on upload progress (poll-on-resume is enough).
- Direct camera capture (the native file picker handles this on iOS/Android already).

## Capabilities

### New Capabilities

- `project-creation`: HTTP create endpoint + UI flow to bootstrap a project with target aspect ratio.
- `chunked-upload`: Init / put / state / complete protocol over HTTP, persisted to Postgres so progress survives reloads.
- `script-management`: Read/write script body per project, with paste-or-file UI.
- `mobile-upload-ui`: Mobile-first React pages for project creation, video upload, and script editing in 繁體中文.

### Modified Capabilities

- `data-models`: extend `projects` with `target_aspect_ratio`; add `scripts` and `upload_sessions` tables.
- `core-api-routers`: extend `/projects` with `POST`, add `/uploads/*` and `/projects/{id}/script`.

## Impact

- **Code**: new modules `src/media_processor/services/uploads.py`, `src/media_processor/api/routers/uploads.py`; extend `projects.py`; new model files `models/script.py`, `models/upload_session.py`. New web pages `web/src/pages/NewProject.tsx`, `web/src/pages/Upload.tsx`; new module `web/src/upload/chunked.ts`.
- **DB**: alembic 0002 migration.
- **Disk**: `assets/` and `uploads/` directories under `MEDIA_STORAGE_DIR`. Worker still runs against the same paths.
- **Dependencies**: no new Python deps (`ffprobe` already needed for M4; for M3 we shell out via `subprocess` and tolerate it being missing — degraded asset row without media metadata).
- **Risk**: chunked uploads on flaky mobile networks → mitigated by per-chunk PUT, server-side state, idempotent retries, and frontend `localStorage` mapping.
- **Version**: 0.6.0 → 0.7.0.
