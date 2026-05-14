# v0.29.0 — Aspect-ratio redux: 9:16 + 16:9 with crop-region picker

## Why

The 4:5 and 1:1 IG-feed variants haven't been used since v0.20 — operators
ship Reels (9:16) and want a horizontal landscape variant (16:9) for
YouTube / FB-feed-on-desktop / web embeds. The two unused IG-feed sizes
add UI clutter and force test maintenance for code paths nobody renders
through.

When the source orientation differs from the output orientation
(9:16 source → 16:9 target, or 16:9 source → 9:16 target), the
existing `aspect_filter` chain centre-crops via ffmpeg's
`crop=W:H:(in_w-W)/2:(in_h-H)/2`. That centre is wrong for many shots
(action at the top of a portrait clip; speaker on the right of a
landscape clip). Operators currently work around this by re-shooting
or skipping the asset — neither is acceptable.

## What changes

1. **Aspect ratio set is now `9:16` (REELS) + `16:9` (LANDSCAPE)**,
   replacing `9:16` + `4:5` + `1:1`.
   - `TargetAspectRatio` enum in models/enums.py.
   - `Pydantic Literal` types in api/schemas.py (`ProjectCreate`,
     `DraftExportRequest`, `target_aspect_ratio` strings).
   - `ASPECT_DIMENSIONS` in services/video_renderer.py — `16:9` →
     `(1920, 1080)`.
   - `ASPECT_RATIOS` in services/auto_reframe.py — adds `(16, 9)`.
   - `VALID_ASPECTS` in services/exports.py — `("9:16", "16:9")`.
   - `subtitle_force_style` adds a 16:9-tuned `Fontsize` / `MarginV`
     branch; `_resolve_subtitle_style`'s position math is canvas-height
     based and works unchanged.
   - Frontend `TargetAspectRatio` / `ExportAspect` unions in
     `web/src/api/types.ts`.
   - `NewProject.tsx` / `ExportSheet.tsx` / `ProjectEdit.tsx` ratio
     choices.

2. **Migration converts old `4:5` / `1:1` projects to `9:16`**.
   - Alembic 0026 (`UPDATE projects SET target_aspect_ratio='9:16'
     WHERE target_aspect_ratio IN ('4:5','1:1')`) so nothing 500s on
     load.
   - Existing draft mp4 / SRT files at `${DRAFTS_DIR}/{pid}/v{N}.mp4`
     stay on disk untouched — they remain playable. Re-render on the
     project picks 9:16.
   - Already-emitted exports under
     `${DRAFTS_DIR}/{pid}/v{N}-{aspect}-{height}p.mp4` are not
     deleted — they remain downloadable as historical artefacts.

3. **Project-level `crop_region` selector** drives the static crop
   anchor when source orientation ≠ target orientation.
   - New nullable JSON column `Project.crop_region_json` (alembic
     0026 same migration). Shape:
     `{"x_norm": float, "y_norm": float}` — both 0..1, semantics
     "fraction of the source where the crop window's anchor lands".
     `null` (default) ≡ `{x_norm: 0.5, y_norm: 0.5}` (centre).
   - ffmpeg crop coordinates resolve to
     `x = round(x_norm * (in_w - out_w))`, `y = round(y_norm *
     (in_h - out_h))`, clamped to `[0, in_w-out_w]` / `[0, in_h-out_h]`.
   - Renderer applies it ONLY when the static aspect crop is being
     used (auto_reframe path is unchanged — its YOLO / point /
     custom_roi paths already track a subject and don't need a
     static fallback).
   - PATCH `/projects/{id}/crop-region` endpoint with body
     `{x_norm: float | null, y_norm: float | null}`. Body of `null`
     for both clears the override (returns to centre).
   - Surfaced in `ProjectDetail.crop_region` as
     `{x_norm, y_norm} | null`.

4. **Frontend `CropRegionPicker` component** appears on `ProjectEdit`
   beneath the existing visual settings group.
   - Only mounts when source-vs-target orientation differs (we read
     each project's `target_aspect_ratio` plus the orientation of any
     analysed asset; if every analysed asset matches the target the
     picker stays hidden).
   - Three preset buttons: `top` / `middle` / `bottom` (when
     vertical crop) or `left` / `center` / `right` (when horizontal
     crop). Custom drag is out of scope for v0.29.0 — operator
     feedback so far has been "the three presets are enough"; a
     future minor can add a click-to-pick on the thumbnail.

## Non-goals

- Per-asset crop region. The picker is project-wide for v0.29 to
  match the existing `subject_class` / `bgm_path` model. Per-asset
  override is a follow-up.
- Custom drag-to-pick UI on the thumbnail. The three presets cover
  the operator's stated workflow; click-to-pick is queued for
  v0.29.1+.
- 4:5 / 1:1 export "convenience" buttons. Existing exported files
  under `v{N}-4x5-*.mp4` / `v{N}-1x1-*.mp4` remain downloadable but
  there's no UI to produce new ones.

## Migration / back-compat

- A live project with `target_aspect_ratio` IN (`4:5`, `1:1`) gets
  rewritten to `9:16` by alembic 0026. Already-rendered draft mp4
  files at the old aspect stay playable but the next re-render
  produces 9:16. Frontend ratio dropdowns no longer offer the dropped
  values, so a manual revert is impossible from the UI — the column's
  `CheckConstraint` is updated to the new 2-value tuple, blocking it
  at the DB level too.
- A pre-0.29 frontend POSTing `target_aspect_ratio="4:5"` will get
  422 from the new `Literal["9:16","16:9"]`. Acceptable — the API +
  FE always ship together and this branch updates both.
