# asset-level-stabilized-variants — asset-level raw/stabilized workflow

## Why

The 0.30.x camera-motion canaries showed that cut-level vidstab can introduce
visible drift on footage that is already stable, especially low-texture or
high-glare DJI shots. Tuning the render-time crop/stabilization chain kept
mixing concerns: tracking chose composition, Smart Camera chose creative
motion, and vidstab tried to clean high-frequency shake after those decisions.

v0.40.0 moves stabilization to the asset lifecycle. Each uploaded video keeps
the raw source immutable and may have one source-level stabilized derivative.
The operator previews raw versus stabilized and chooses the active variant
before downstream analysis, tracking, Smart Camera planning, or render. That
keeps coordinates and framing decisions tied to one source version.

## What Changes

1. Add asset variant metadata.
   - `assets.stabilized_path`
   - `assets.stabilization_status`
   - `assets.stabilization_error`
   - `assets.active_asset_variant`
   - Existing rows default to `raw` / `not_started`.

2. Add source-level stabilization jobs.
   - `POST /assets/{id}/stabilize` marks the asset pending and enqueues a worker-analysis job.
   - The worker runs two-pass vidstab over the full source into the stabilized derivative path.
   - Upload completion also schedules stabilization best-effort; raw remains active.
   - Enqueue failures are terminal `failed` states so UI polling cannot hang on `pending`.

3. Add active variant switching.
   - `PATCH /assets/{id}/variant` accepts `raw` or `stabilized`.
   - Selecting `stabilized` requires `stabilization_status="done"` and a present file.
   - Switching variants clears coordinate-dependent state: scene/motion/emotion tags, coverage, tracking JSON, custom ROI, point tracking, tracking target, and analysis steps.
   - The endpoint re-enqueues analysis by default so new coordinates are generated for the selected source.

4. Route all source reads through one helper.
   - `services.asset_variants.selected_media_path(asset)` is the source of truth.
   - Analysis, point tracking, edit-orchestrator render input gathering, and file-size display use the active variant.
   - Raw `Asset.file_path` remains the immutable upload path.

5. Add operator UI.
   - Project analysis asset cards show raw/stabilized previews.
   - Operators can generate/retry stabilization and switch the active variant.
   - Polling remains active while stabilization is `pending` or `running`.

## Non-Goals

- Do not solve tracking trajectory smoothing in this change.
- Do not add automatic selection of stabilized over raw; the operator chooses.
- Do not stack cut-level vidstab on top of a stabilized source unless a future measured design explicitly proves it safe.
- Do not change Smart Camera directive semantics beyond ensuring it reads the selected source version.

## Verification

- `py -3 -m ruff check src tests`
- `py -3 -m ruff format --check src tests`
- `py -3 -m mypy src`
- `npm run build`
- `py -3 -m pytest` → `253 passed, 7 skipped`
- `git diff --check`
- `py -3 -m py_compile alembic/versions/0028_asset_stabilized_variants.py`
