# v0.18.0 — Tasks

## DB / model
- [ ] Add `watermark_path`, `watermark_position`, `watermark_scale`, `watermark_opacity` to `models/project.py::Project`.
- [ ] Alembic migration `0014_project_watermark.py` (down: drop columns).
- [ ] `WATERMARK_DIR` field on `api/config.Settings`; add to `api/main.py` startup `mkdir` loop.
- [ ] `app.mount("/media/watermarks", …)` in `api/main.py`.

## API
- [ ] Extend `ProjectDetail` schema with the four watermark fields.
- [ ] New `WatermarkSettingsPatch` schema (all fields optional, validated bounds).
- [ ] Routes in `routers/projects.py`:
  - `POST /projects/{id}/watermark` — multipart upload, PNG only, ≤5 MB.
  - `PATCH /projects/{id}/watermark` — settings update.
  - `DELETE /projects/{id}/watermark` — clears file + path; preserves position/scale/opacity.
- [ ] Populate the four fields in every `ProjectDetail` constructor in `routers/projects.py` (list, get, BGM upload, analysis page).

## Renderer
- [ ] `services/video_renderer.py::WATERMARK_*` constants + `_position_to_xy(position, *, margin_px)` helper.
- [ ] `services/video_renderer.py::apply_watermark(input, output, *, watermark_path, position, scale, opacity, target_aspect)` — single ffmpeg pass.
- [ ] `services/edit_orchestrator.py::run_render` — call `apply_watermark` after BGM mix when configured (non-fatal on failure).
- [ ] `services/exports.py::export_render` — accept watermark params and inject the overlay into the same encode pass.

## Frontend
- [ ] `web/src/api/types.ts` — extend `ProjectDetail`; add `WatermarkSettingsPatch` + `WatermarkPosition`.
- [ ] `web/src/api/client.ts` — `uploadProjectWatermark`, `updateProjectWatermark`, `deleteProjectWatermark`.
- [ ] `web/src/components/WatermarkPicker.tsx` (+ CSS) — upload + 3×3 position grid + scale slider + opacity slider + thumbnail preview.
- [ ] `pages/ProjectEdit.tsx` — mount `<WatermarkPicker>` near `<BgmSourcePicker>`; refresh project on changes.

## Verification + ship
- [ ] `pytest tests/unit/test_video_renderer.py tests/unit/test_projects_edit_router.py` passes (and any new tests for watermark helpers).
- [ ] `ruff check src tests` clean.
- [ ] Bump `0.17.1 → 0.18.0` in `pyproject.toml`, `web/package.json`, `api/main.py` (`FastAPI(version=…)`).
- [ ] Update `MEMORY.md` index + new memory file `v018_watermark_overlay.md`.
- [ ] Commit + push.
- [ ] `docker compose up -d --build api worker web` from `D:\GitClone\_HomeProject\media-processor` (deploy host).
