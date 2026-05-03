## Why

Operators producing brand content needed every rendered draft to carry the
client's logo (or their own studio mark) burned into the final mp4. Doing
this in post-production means re-encoding every reel by hand; doing it
inside the existing render pipeline costs one ffmpeg overlay node per
draft and the watermark settings can live alongside the project's other
per-project knobs (BGM, subtitle style, etc.) without dragging the asset
schema in.

A second motivation: the upload + reframe stages already produced a
clean 9:16 / 4:5 / 1:1 output, but the renderer had no extension point
between subtitle burn-in and BGM mix where a project-level overlay could
land without touching the planner. The watermark stage slots in there.

## What Changes

### 1. Project schema — four new columns

`Project.watermark_path` (nullable str) is the on-disk path to the
uploaded PNG under `${WATERMARK_DIR}`. The other three carry the layout
the renderer applies even when the file vanishes (so a re-upload picks
the previous setup back up):

- `watermark_position` — one of nine 9-grid anchors (`top-left` …
  `bottom-right`, default `bottom-right`)
- `watermark_scale` — fraction of the smaller frame edge the logo
  spans (default `0.10`, capped to `[0.02, 0.5]` server-side)
- `watermark_opacity` — float (default `1.0`, capped to `[0.0, 1.0]`)

Migration `0014_project_watermark` adds the four columns nullable / with
server defaults so existing rows pick up `bottom-right / 10 % / 100 %`
automatically and the path stays NULL until upload.

### 2. Renderer — `apply_watermark` stage

A new ffmpeg stage runs **between** `burn_subtitles` and the BGM mix:

```
intermediates → cut → stabilize → concat → burn_subtitles → apply_watermark → bgm
```

The stage is a no-op when `Project.watermark_path` is NULL (most
projects), so adding it is zero-cost for everyone who hasn't uploaded a
logo. Implementation uses `overlay=…` with the chosen anchor, scaled
against `min(W,H)` so a 9:16 export and a 1:1 export render the logo at
the same on-screen size relative to the frame.

Failure mode: a missing or unreadable PNG logs a warning and skips the
overlay rather than failing the render. The user sees a draft without
watermark and can re-upload.

### 3. Upload + management API

Three endpoints sharing the same `${WATERMARK_DIR}/{project_id}.png`
on-disk convention as the BGM upload path:

- `POST /projects/{id}/watermark` — multipart upload, PNG only, capped
  at 5 MB. Streams to disk in `WATERMARK_CHUNK_BYTES` blocks.
- `PATCH /projects/{id}/watermark` — update layout fields without
  re-uploading the PNG.
- `DELETE /projects/{id}/watermark` — remove the file + null out
  `watermark_path`. Layout fields stay so a re-upload picks the same
  position / scale.

All three return the refreshed `ProjectDetail` so the picker UI can
re-render against the new state in one round trip.

### 4. Frontend — `WatermarkPicker` component

Drops into `ProjectEdit` inside the `視覺疊加` settings group.
Renders a 3×3 grid for the position picker, two sliders for scale +
opacity, a file input for the PNG, and a live preview that mirrors the
renderer's output (using a placeholder asset thumbnail as the
backdrop). PATCHes one field at a time so the user can tweak a single
slider without echoing the rest.

### 5. Tests

- `tests/unit/test_video_renderer.py` covers the four corner positions
  + scale clamp + opacity clamp + the no-op pass when `watermark_path`
  is NULL.
- `tests/unit/test_projects_router.py` covers POST / PATCH / DELETE
  including the 5 MB cap (returns 413) and the PNG-only check
  (returns 400 on `image/jpeg`).

## Impact

- **Rendered output.** Drafts in projects with a logo set get the PNG
  overlaid in the chosen position; output remains the same fps / codec.
- **Storage.** New `${WATERMARK_DIR}` directory mounted into the api +
  worker containers (compose mount). Per-project PNGs land at
  `${WATERMARK_DIR}/{project_id}.png` so storage scales linearly with
  active projects.
- **Schema.** Four new columns on `projects` (alembic 0014). Defaults
  make the migration safe for existing rows.
- **API contract.** Three new endpoints; `ProjectDetail` gains four
  fields (all defaulted so older clients ignoring them keep working).
- **Backwards compatibility.** A project with `watermark_path = NULL`
  renders identically to pre-v0.18 — the stage is a no-op copy.
