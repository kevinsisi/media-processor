# Tasks — v0.29.0

- [x] T1 — Backend enums + literal types
  - [x] `models/enums.py`: `TargetAspectRatio` REELS / LANDSCAPE
  - [x] `api/schemas.py`: `TargetAspectRatioLiteral`,
    `DraftExportRequest.aspect`, `CropRegionOut` / `CropRegionPatch`
- [x] T2 — Project model + migration
  - [x] `models/project.py`: `crop_region_json` JSON column
  - [x] alembic 0026: rewrite legacy 4:5/1:1 to 9:16; rebuild
    `ck_projects_target_aspect_ratio` (batch mode on SQLite,
    direct on Postgres); add `crop_region_json`
- [x] T3 — Renderer + auto-reframe + exports + subtitles
  - [x] `services/video_renderer.py`: `ASPECT_DIMENSIONS["16:9"]`
    + drop 4:5/1:1; `subtitle_force_style` 16:9 branch;
    `aspect_filter` accepts an `(x_norm, y_norm)` crop offset
    with clamped expressions; `_cut_segment` / `cut_segments` /
    `render` thread the new param through
  - [x] `services/auto_reframe.py`: `ASPECT_RATIOS["16:9"]`,
    drop 4:5/1:1
  - [x] `services/exports.py`: `VALID_ASPECTS = ("9:16", "16:9")`
  - [x] `services/edit_orchestrator.py`: parse
    `Project.crop_region_json` and pass into `render(...)`
- [x] T4 — Projects router
  - [x] `_project_detail` projects `crop_region` (via
    `_crop_region_out` helper)
  - [x] `PATCH /projects/{id}/crop-region` with both-null
    clear + 400 on partial payload
- [x] T5 — Frontend types + API client
  - [x] `web/src/api/types.ts`: `TargetAspectRatio` /
    `ExportAspect` unions; `CropRegion` + `CropRegionPatch`;
    `ProjectDetail.crop_region`
  - [x] `web/src/api/client.ts`: `patchProjectCropRegion`
- [x] T6 — Frontend pages
  - [x] `NewProject.tsx`: 2-option ratio grid (9:16 + 16:9)
  - [x] `ExportSheet.tsx`: 2-option chips + 2 social presets
    (Reels + 橫向)
  - [x] `ProjectEdit.tsx`: orientation aggregation +
    `cropDirection` memo + thread into both EditSettingsBlock
    sites; mounts `CropRegionPicker` only when source ≠ target
    orientation
- [x] T7 — `CropRegionPicker.tsx` + `.css` (3 preset buttons,
  vertical or horizontal axis based on direction prop)
- [x] T8 — Tests
  - [x] `test_video_renderer.py`: drop 4:5/1:1 paths; 16:9
    added; centre vs off-centre crop_region cases
  - [x] `test_edit_planner.py`: replace `target_aspect_ratio="1:1"`
    with `"16:9"`
  - [x] `test_routers.py`: 16:9 export round-trip; 4:5 export
    rejection (422); crop-region PATCH round-trip + partial-
    payload 400
- [x] T9 — Lint / typecheck / tests green
  - [x] ruff check src tests → All checks passed
  - [x] ruff format src tests → no diff
  - [x] tsc -b --noEmit → exit 0
  - [x] pytest tests/unit → 187 passed, 7 skipped
- [x] T10 — Memory / CLAUDE.md / ROADMAP / pyproject / package.json
  bumped to 0.29.0
- [x] T11 — Commit + push
