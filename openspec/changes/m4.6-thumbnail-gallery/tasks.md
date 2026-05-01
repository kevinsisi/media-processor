# M4.6 — Thumbnail gallery tasks

## 1. Backend — config + service
- [x] 1.1 Add `THUMBNAILS_DIR` to `api/config.Settings` (default `/app/media/thumbnails`).
- [x] 1.2 New `services/thumbnails.py` — `generate(asset_id, video_path, duration_ms, *, force=False)` extracts 5 frames at 10/30/50/70/90 % using ffmpeg subprocess. Width 320 px, JPEG q=80, idempotent.
- [x] 1.3 Constants for frame count + percentages + width + quality (no inline magic numbers).
- [x] 1.4 Per-frame ffmpeg call timeout of 15 s; whole-asset timeout of 60 s.
- [x] 1.5 Return a small dataclass `{ asset_id, frames_written, frames_skipped, failed_reason | None }`.

## 2. Backend — API + static serving
- [x] 2.1 Mount `StaticFiles` at `/media/thumbnails` in `api/main.py`.
- [x] 2.2 New `GET /assets/{id}/thumbnails` endpoint returning `{ asset_id, count, thumbnails: [{ index, url }] }`. URLs are `/api/media/thumbnails/{asset_id}/frame_{n}.jpg`.
- [x] 2.3 Disk-safe listing — sort by `frame_{n}.jpg` numeric suffix.
- [x] 2.4 Pydantic schema `AssetThumbnailsOut` + `ThumbnailUrl` in `api/schemas.py`.

## 3. Backend — upload integration + projects analysis
- [x] 3.1 `POST /uploads/{sid}/complete` (kind=video) calls thumbnail generation in `asyncio.to_thread` after the Asset commit; failures logged but non-fatal.
- [x] 3.2 Embed `thumbnail_urls: list[str]` per row in `GET /projects/{id}/assets`.

## 4. Backend — Docker + ffmpeg
- [x] 4.1 `docker/api.Dockerfile` installs ffmpeg.
- [x] 4.2 `.env.example` documents `THUMBNAILS_DIR`.

## 5. Backend — backfill
- [x] 5.1 `scripts/backfill_thumbnails.py` iterates assets, skips when 5 frames already present, generates the rest. Re-runnable. Uses async session.

## 6. Frontend — types + client
- [x] 6.1 Extend `AssetAnalysisItem` in `web/src/api/types.ts` with `thumbnail_urls: string[]`.
- [x] 6.2 Add `fetchAssetThumbnails(assetId)` to `ApiClient` (used by future detail view; gallery on the analysis page reads embedded URLs).

## 7. Frontend — gallery component
- [x] 7.1 `<AssetThumbnailGallery thumbnails={...} />` in `pages/ProjectAnalysis.tsx`.
- [x] 7.2 CSS `overflow-x: auto; scroll-snap-type: x mandatory;` per-image `scroll-snap-align: start`.
- [x] 7.3 Empty-state placeholder text `尚未產生縮圖` or `縮圖產生中…` based on asset status.
- [x] 7.4 `loading="lazy"` on each `<img>`; touch scrolling smooth on mobile (`-webkit-overflow-scrolling: touch`, `overscroll-behavior-x: contain`).

## 8. Verification
- [x] 8.1 `ruff check src tests` clean.
- [x] 8.2 `pytest -q` passes (new tests + existing).
- [x] 8.3 `cd web && npm run build` (`tsc -b && vite build`) passes.
- [x] 8.4 `docker compose build api` succeeds with ffmpeg installed.

## 9. Memory + commit + deploy
- [x] 9.1 Update memory with thumbnails-dir convention + url path.
- [x] 9.2 Commit + push on the worktree branch.
- [x] 9.3 Rebuild + redeploy on the production main worktree.
- [x] 9.4 Converge worktree.
