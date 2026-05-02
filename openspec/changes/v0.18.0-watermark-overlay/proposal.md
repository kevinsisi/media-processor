# v0.18.0 — Watermark / Logo overlay

## Why

Brand-owner clients (the same ones already uploading project BGM and brand-specific scripts) want a persistent logo burned into the rendered deliverable so the reels stay attributable when re-shared. Without it the client has to take the auto-edit output through CapCut just to drop a PNG in the corner — exactly the kind of "after the auto-edit, finish in three taps" friction M7 was supposed to eliminate.

The renderer already has a final encode pass (BGM mix when BGM is set, otherwise the subtitle burn) so adding `overlay=…` is structurally cheap. Storage piggybacks on the existing `MEDIA_STORAGE_DIR` bind mount the BGM uploader uses — no new compose plumbing.

## What Changes

### 0.18.1 Per-project watermark settings

- New columns on `projects`:
  - `watermark_path` `String(1024)` nullable — on-disk path under `${WATERMARK_DIR}` (`/app/media/watermarks/{project_id}.png`).
  - `watermark_position` `String(16)` nullable, default `bottom-right` — one of nine grid values: `top-left | top-center | top-right | middle-left | middle-center | middle-right | bottom-left | bottom-center | bottom-right`.
  - `watermark_scale` `Float` default `0.10` — width as a fraction of the rendered canvas width (range `0.02–0.5`).
  - `watermark_opacity` `Float` default `1.0` — alpha multiplier (range `0.0–1.0`).

  Alembic migration `0014_project_watermark` (down: drop columns).

### 0.18.2 Upload + settings API

- `POST /projects/{id}/watermark` (multipart; field `file`, `image/png` only, ≤5 MB). Streams to disk in 256 KB chunks. Replaces any prior file at `${WATERMARK_DIR}/{project_id}.png`. Returns `ProjectDetail`.
- `PATCH /projects/{id}/watermark` body `{position?, scale?, opacity?}` — partial update. Same validation bounds as the model. Returns `ProjectDetail`.
- `DELETE /projects/{id}/watermark` — idempotent; clears `watermark_path` and unlinks the on-disk file. Settings (position/scale/opacity) are kept so a re-upload picks up the previous layout. 204.
- `GET /projects/{id}` and `GET /projects/{id}/assets` surface the four new fields on `ProjectDetail`.

### 0.18.3 Renderer integration

- New `services/video_renderer.py::apply_watermark(input_mp4, output_mp4, *, watermark_path, position, scale, opacity, target_aspect)` — single ffmpeg subprocess that overlays the PNG using `[1:v]format=rgba,colorchannelmixer=aa={opacity},scale={target_w}:-1[wm];[0:v][wm]overlay={x}:{y}` and re-encodes with the renderer's standard knobs (libx264, crf 20, faststart, audio copy).
- Position → `(x, y)` mapping uses ffmpeg expressions with a margin equal to `2%` of the canvas width (clamped to ≥12 px).
- Orchestrator wires it as a final stage AFTER BGM mix (or in place of it when no BGM): `apply_watermark` runs only when `project.watermark_path` is set and the file exists. Failure is non-fatal — logs + leaves the un-watermarked mp4 in place (matches BGM-mix failure semantics).
- `services/exports.py::export_render` learns the same parameters so derivative-aspect / -resolution exports also get the watermark. The aspect change recomputes the watermark canvas width so the logo stays at the requested fraction of the export's width.

### 0.18.4 Static serve

- `app.mount("/media/watermarks", …)` in `api/main.py`, mirroring the BGM mount. Lets the frontend display the current watermark thumbnail in the picker without a separate signed-URL endpoint.

### 0.18.5 Frontend settings UI

- New `web/src/components/WatermarkPicker.tsx` (+ CSS): file input (PNG only), 3×3 position grid (radio-tile), scale slider (`2%–50%`, step `1%`), opacity slider (`0–100%`, step `5%`). Shows the current watermark thumbnail with the chosen position drawn as a faint frame so the user can visualise placement before re-rendering.
- Hook up to `apiClient.uploadProjectWatermark`, `updateProjectWatermark`, `deleteProjectWatermark`. Mounted into `pages/ProjectEdit.tsx` next to `<BgmSourcePicker>` so the brand panel and BGM panel sit together.
- Types: extend `ProjectDetail` with the four fields; add `WatermarkSettingsPatch` mirroring the PATCH body.

### Cross-cutting

- Version bump `0.17.1 → 0.18.0` (`pyproject.toml`, `web/package.json`, `api/main.py` `FastAPI(version=…)`).
- New `WATERMARK_DIR=/app/media/watermarks` field on `Settings` (auto-created in `api/main.py` startup, alongside the existing thumbnails / drafts / bgm mkdir).

## Impact

- **DB:** new alembic migration `0014_project_watermark` (4 nullable columns on `projects`). API container auto-runs `alembic upgrade head` on boot.
- **Services:** `video_renderer` (new `apply_watermark` + helper for position→x/y), `edit_orchestrator` (final stage hook), `exports` (extra ffmpeg flag).
- **Frontend:** new `WatermarkPicker` component; `ProjectEdit` integrates it; `api/types.ts` + `api/client.ts` extended.
- **Docker:** no compose changes (uses existing media bind mount).

## Non-goals (deferred)

- Animated / video watermarks — PNG only for v0.18.0.
- Per-draft watermark override — settings live on Project; a re-render picks up whatever's currently set. Snapshot semantics (like BGM in v0.16.2) can come if clients ask.
- SVG / multi-frame logos — would need rasterisation logic; PNG is enough for the brand-logo use case.
- Auto-positioning around subtitles / face — fixed grid only, the user picks.
