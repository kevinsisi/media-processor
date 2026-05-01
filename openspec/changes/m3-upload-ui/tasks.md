# M3 — Upload UI tasks

## 1. Data model + migration
- [x] 1.1 Add `target_aspect_ratio` enum + column to `Project` ORM.
- [x] 1.2 New `Script` model (`scripts` table).
- [x] 1.3 New `UploadSession` model (`upload_sessions` table).
- [x] 1.4 Update `models/__init__.py` exports.
- [x] 1.5 Alembic migration `0002_m3_uploads.py` (add column + 2 tables, downgrade reverses).

## 2. API — projects + script
- [x] 2.1 `POST /projects` (create) → 201 with `ProjectDetail`.
- [x] 2.2 `GET /projects/{id}/script` → `ScriptOut | null`.
- [x] 2.3 `PUT /projects/{id}/script` → upsert body, returns `ScriptOut`.
- [x] 2.4 Add `target_aspect_ratio` to `ProjectSummary` + `ProjectDetail` response schemas.

## 3. API — chunked upload
- [x] 3.1 `POST /projects/{id}/uploads` → create session, return `UploadSessionOut`.
- [x] 3.2 `GET /uploads/{session_id}` → state for resume (with self-heal of missing chunks).
- [x] 3.3 `PUT /uploads/{session_id}/chunks/{index}` → write chunk to disk (idempotent).
- [x] 3.4 `POST /uploads/{session_id}/complete` → assemble + ffprobe + create Asset (or persist Script for kind=script).
- [x] 3.5 Service module `services/uploads.py` — disk layout, assemble, ffprobe shell-out with timeout.

## 4. Web — API client + types
- [x] 4.1 Extend `web/src/api/types.ts` with new shapes.
- [x] 4.2 Extend `ApiClient` with `createProject`, `fetchScript`, `putScript`, `createUploadSession`, `fetchUploadSession`, `completeUploadSession`, `uploadChunkUrl`.

## 5. Web — chunked uploader module
- [x] 5.1 `web/src/upload/chunked.ts` — XHR-based slice loop with retry-with-backoff per chunk.
- [x] 5.2 LocalStorage mapping `{file fingerprint → session_id}` for hard-reload resumability.
- [x] 5.3 On mount of upload page: `runChunkedUpload` calls `GET /uploads/{session_id}` to skip received chunks.

## 6. Web — pages
- [x] 6.1 `pages/NewProject.tsx` + CSS — form with name, client, profile select, aspect-ratio visual radio.
- [x] 6.2 `pages/Upload.tsx` + CSS — video tab (multi-file, per-file progress, resume), script tab (paste textarea + upload .txt), summary card.
- [x] 6.3 `ProjectList` — "新增專案 +" CTA in hero.
- [x] 6.4 `App.tsx` routes for `/projects/new` and `/projects/:id/upload`.

## 7. Mobile-first polish
- [x] 7.1 All touch targets ≥ 44 px (CTAs, retry, script-upload pill, summary-back).
- [x] 7.2 Sticky bottom action on small viewports for NewProject submit.
- [x] 7.3 Single-column layout < 600 px; aspect-ratio cards stay 3-column at small viewports (visual frames already fit).
- [x] 7.4 16 px base inputs; comfortable line-height for Chinese (1.6+ on textarea, 1.55 on body lede).

## 8. Verification
- [x] 8.1 `alembic upgrade head` runs clean inside the api container (head = `0002_m3_uploads`).
- [x] 8.2 Smoke: create project → upload chunked file → complete → asset row exists with sha256 + (degraded) media metadata, asset_count visible on project detail.
- [x] 8.3 Smoke: upload chunks 0 + 2, attempt complete (409 missing [1]), re-fetch state, upload chunk 1, complete succeeds.
- [x] 8.4 Smoke: paste a script → GET returns it → 繁體中文 round-trips.
- [x] 8.5 `web` build passes (`tsc -b && vite build`).
- [ ] 8.6 Browser check at `http://127.0.0.1:8523/` for the golden path — deferred (Chrome extension was not connected at verification time; HTTP-level coverage substitutes for this milestone, the UI is reachable at 200 OK on `/`, `/projects/new`).
