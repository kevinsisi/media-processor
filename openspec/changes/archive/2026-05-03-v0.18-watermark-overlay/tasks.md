# Tasks — v0.18-watermark-overlay (0.18.0)

## 1. Schema + migration

- [x] 1.1 Add `watermark_path` / `watermark_position` / `watermark_scale` /
  `watermark_opacity` columns to `Project`.
- [x] 1.2 `alembic/versions/0014_project_watermark.py` adds all four with
  server defaults so existing rows pick up `bottom-right / 0.10 / 1.0`
  and `watermark_path = NULL` until upload.
- [x] 1.3 Update `ProjectDetail` Pydantic schema + `_project_detail`
  builder to surface the four fields.

## 2. Renderer — `apply_watermark` stage

- [x] 2.1 Add `apply_watermark(input_path, output_path, *, png_path,
  position, scale, opacity)` to `services/video_renderer.py`.
- [x] 2.2 Insert the stage in `render(...)` between `burn_subtitles`
  and the BGM mix; no-op when `watermark_path` is NULL.
- [x] 2.3 Use `overlay=…` with the 9-grid anchor map; scale against
  `min(W,H)` so all aspect ratios render the logo at the same
  relative size.
- [x] 2.4 Log + skip on PNG read failure rather than failing the whole
  render.

## 3. Upload / management API

- [x] 3.1 `POST /projects/{id}/watermark` — multipart upload, PNG only,
  ≤ 5 MB. Streams to `${WATERMARK_DIR}/{project_id}.png`.
- [x] 3.2 `PATCH /projects/{id}/watermark` — partial layout update
  (`position` / `scale` / `opacity`); echo-back full ProjectDetail.
- [x] 3.3 `DELETE /projects/{id}/watermark` — remove PNG + null out
  `watermark_path`; keep layout fields.
- [x] 3.4 Add `WatermarkSettingsPatch` body schema with the slider /
  position bounds (scale `[0.02, 0.5]`, opacity `[0.0, 1.0]`).

## 4. Frontend

- [x] 4.1 New `web/src/components/WatermarkPicker.tsx` — 3×3 grid,
  scale + opacity sliders, file input, live preview.
- [x] 4.2 Plumb `apiClient.uploadProjectWatermark` /
  `updateProjectWatermark` / `deleteProjectWatermark`.
- [x] 4.3 Mount inside `ProjectEdit` `視覺疊加` settings group.

## 5. Container / config

- [x] 5.1 Add `WATERMARK_DIR` to `services/config.py`; default
  `/app/media/watermarks`.
- [x] 5.2 Mount `${MEDIA_STORAGE_DIR}/watermarks` into api + worker
  containers in `docker-compose.yml`.
- [x] 5.3 Static-mount `/api/media/watermarks` so the frontend preview
  can `<img src=...>` directly.

## 6. Tests

- [x] 6.1 Renderer: 9 corner positions × scale clamp × opacity clamp.
- [x] 6.2 API: POST returns 413 when over 5 MB; 400 when MIME isn't
  PNG; DELETE is idempotent.
- [x] 6.3 No-op pass: `apply_watermark` with `watermark_path = NULL`
  produces a file byte-equivalent to its input (copy path).
