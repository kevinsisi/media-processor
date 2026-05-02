# M3 — Design notes

## Chunked upload protocol

Chosen over a single multipart POST because:

- Mobile networks drop. Per-chunk PUTs with idempotent indexes are forgiving.
- Browser progress events on a 1 GB single POST are unreliable across providers; per-chunk slicing gives crisp progress bars.
- Reload survival needs explicit server-side state, not just an open TCP stream.

Sequence:

```
POST /projects/{pid}/uploads
  body { kind, filename, total_size, chunk_size, sha256? }
  → { session_id, received_chunks: [] }

(client slices file into chunks of chunk_size bytes)
(for each missing index)
PUT /uploads/{sid}/chunks/{idx}
  body: raw chunk bytes
  → { received_chunks: [...] }

(client may reload here — the server still has session + chunks on disk)
GET /uploads/{sid}
  → { received_chunks: [...] }
(client skips already-received indexes)

POST /uploads/{sid}/complete
  → assembles, ffprobes, creates Asset (or persists Script), deletes scratch
  → { asset: AssetDetail }      // for video
  → { script: ScriptOut }       // for script
```

## Disk layout

```
${MEDIA_STORAGE_DIR}/
  uploads/
    {session_id}/
      chunks/
        0000
        0001
        ...
  assets/
    {project_id}/
      {filename}
```

Chunks are written as fixed-width (4-digit) zero-padded names so `os.listdir` sorts deterministically. After complete, the assembled file is moved to `assets/{project_id}/{filename}` and the chunk dir is deleted.

## Reload survival

Three layers:

1. **Postgres** — `upload_sessions.received_chunks` is the source of truth.
2. **Disk** — the chunks themselves are durable until `complete` succeeds.
3. **Browser localStorage** — a map `{fileFingerprint → session_id}` lets a fresh-loaded page find the session it had been writing to. Fingerprint = `${name}:${size}:${lastModified}`. This is the cheapest correct identifier without forcing a slow client-side hash.

If the user picks a *different* file, the fingerprint changes and a fresh session opens — old session is left to expire (cleanup is a follow-up; out of scope for M3).

## Aspect ratio choice

Stored once at project creation and immutable from the UI for now. The downstream pipeline (M4+) reads `project.target_aspect_ratio` to drive Reframe / Crop. Three values cover IG: `9:16` Reels (default), `4:5` Feed, `1:1` Feed. Visual radio in the form shows little frames at correct ratio so the user picks by shape, not by reading "9:16".

## ffprobe handling

We shell out with a 10 s timeout. If ffprobe is missing or errors, the asset row still gets created with `duration_ms = 0`, `resolution = null`, etc., and `status = 'pending'`. The user sees the file landed; the downstream worker re-probes properly. This avoids the upload UX blocking on a dev box without ffmpeg installed.

## Why `XMLHttpRequest`, not `fetch`

`fetch` does not expose request-progress events. `XMLHttpRequest.upload.onprogress` is the only browser-portable way to drive a per-chunk progress bar. We isolate it in a small `chunked.ts` module behind a Promise interface.

## Why script lives in DB, not files

A script is short text (≤ 1 MB). Putting it in a `scripts` table keeps the script-editing flow trivially atomic with the project, supports `PUT` debounced auto-save without disk IO, and avoids racing `assets/` directory layout. The `source_filename` column records the upload origin if any.

## Testing strategy

- Unit: model creation, migration upgrade/downgrade on SQLite.
- Service unit: `services.uploads.assemble_chunks` over a tmpdir.
- API integration via the live api container: `POST /projects` → `POST /uploads` → `PUT /chunks/0` → `POST /complete` → `GET /assets/{id}`.
- Manual browser smoke for the resume path (kill request, reload, observe skip).
