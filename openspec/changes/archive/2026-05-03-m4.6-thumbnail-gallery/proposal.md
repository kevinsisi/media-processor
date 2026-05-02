## Why

Asset cards on `/projects/:id/assets` today have no visual preview. The operator scans through 10–30 cards on her phone trying to find a specific clip; filenames alone aren't enough, and many videos in a shoot share an identical first frame (locked-off camera, slate, intro card), so a single first-frame poster wouldn't help either.

Per-asset gallery of 4–6 evenly-distributed keyframes solves this in one stroke: even when the first frame collides, frames at 30 % / 50 % / 70 % diverge enough to identify the clip at a glance.

## What Changes

### FFmpeg-driven keyframe extraction

- New service `services/thumbnails.py` extracts 5 frames per asset at 10 %, 30 %, 50 %, 70 %, 90 % of `duration_ms` using ffmpeg `-ss` seek + `-frames:v 1`.
- Output: `${THUMBNAILS_DIR}/{asset_id}/frame_{n}.jpg` (n = 0..4), JPEG quality 80, scaled to 320 px wide preserving aspect ratio.
- New env `THUMBNAILS_DIR` defaults to `/app/media/thumbnails` (mounts to `G:\MediaStorage\thumbnails` on the dispatch host through the existing `MEDIA_STORAGE_DIR` bind).
- Idempotent: re-running on an asset whose frames already exist is a no-op unless `force=True`.
- Per-asset wall-clock timeout (60 s — 5 short ffmpeg seeks complete in well under that).

### API container gets ffmpeg

- Add ffmpeg to `docker/api.Dockerfile`. Side-effect benefit: `services/uploads.probe_media` (which silently degrades when ffprobe is missing) now produces real metadata at upload time. Keeps the api image growth bounded (~85 MB compressed).

### API endpoints + static serving

- New `GET /assets/{id}/thumbnails` → `{ asset_id, count, thumbnails: [{ index, url }, …] }`. Reads disk; returns 200 with `count: 0` and an empty list when no frames exist yet (rather than 404 — the gallery just renders the placeholder card).
- Mount `StaticFiles` on the FastAPI app at `/media/thumbnails` → `${THUMBNAILS_DIR}` so the browser fetches `GET /api/media/thumbnails/{asset_id}/frame_{n}.jpg` through the existing nginx `/api/` proxy. Cache headers: `public, max-age=86400, immutable` (frame paths are stable; force re-runs use a separate dir convention if ever needed, but we'll keep it simple — operator clears the dir manually).
- Embed `thumbnail_urls: string[]` on each row of `GET /projects/{id}/assets` so the polling page renders galleries without N extra round-trips.

### Upload pipeline integration

- `POST /uploads/{sid}/complete` (kind=video) calls `thumbnails.generate(asset_id, file_path)` via `asyncio.to_thread` after the Asset row is committed but before returning. ~3–8 s of synchronous work. If thumbnail generation fails, upload still succeeds and the operator can backfill later (the failure is logged + recorded as a `thumbnails: failed` step in `analysis_steps_json` so the UI can surface it; OK to be best-effort here).

### Backfill for existing assets

- `scripts/backfill_thumbnails.py` — iterates over all `Asset` rows, skips those whose `${THUMBNAILS_DIR}/{asset_id}/` directory already contains the expected 5 frames, and generates the rest. Designed to be re-runnable safely. Run once after deploy via `docker compose exec api python -m scripts.backfill_thumbnails`.

### Web — gallery on every asset card

- New `<AssetThumbnailGallery thumbnails={…} />` component above the existing card header. Uses pure CSS `display: flex; overflow-x: auto; scroll-snap-type: x mandatory; -webkit-overflow-scrolling: touch;` with `scroll-snap-align: start` on each `<img>`. No carousel library.
- Each thumbnail is a fixed-height (90 px on mobile, 110 px on tablet+) `<img loading="lazy">` whose width flows from the underlying aspect ratio.
- Empty state: when `thumbnails` is empty, show a single muted placeholder (`縮圖產生中…` or `尚未產生縮圖`) so the row layout doesn't shift after generation finishes.
- Touch-friendly: container has min-height to avoid layout shift, `overscroll-behavior-x: contain` to keep page scroll smooth.

### Out of scope (deferred)

- Live thumbnail regeneration on transcript/scene/motion changes — frames are deterministic per file.
- Sprite-sheet seek-bar style thumbnails — single static gallery is enough.
- WebP / AVIF output — JPEG 80 is universally fast and good enough for 320 px previews.
- Hashing frames for dedupe across assets — out of scope.

## Capabilities

### New Capabilities

- `asset-thumbnails`: ffmpeg-extracted keyframe gallery per asset, served as static files through the api container, surfaced inline on the asset analysis cards.

### Modified Capabilities

- `chunked-upload`: `POST /uploads/{sid}/complete` triggers thumbnail extraction for `kind=video`.
- `core-api-routers`: new `/assets/{id}/thumbnails` route + StaticFiles mount.
- `transcript-editor-ui`: each asset card now renders a horizontal keyframe gallery above the existing analysis chips.

## Impact

- **Code** — new `services/thumbnails.py`, new `routers` extension on `assets.py`, mount in `api/main.py`, `scripts/backfill_thumbnails.py`, new `pages/ProjectAnalysis.tsx` gallery sub-component + CSS.
- **DB** — none. Disk presence is the source of truth; we re-derive URLs at request time.
- **Disk** — new `${MEDIA_STORAGE_DIR}/thumbnails/{asset_id}/` dirs (≈ 50 KB × 5 = 250 KB per asset).
- **Docker** — `docker/api.Dockerfile` installs ffmpeg.
- **Env** — new `THUMBNAILS_DIR` (default `/app/media/thumbnails`).
- **Risk** — api image grows ~85 MB. Upload-complete adds ~3–8 s of synchronous work; mitigated by running it in a thread and tolerating failures.
- **Version** — 0.9.0 → 0.9.1.
