# Tasks — asset-level-stabilized-variants

Status: `0.40.0` deployed; `0.40.1` fixes preview video clickability.

- [x] Add asset-level stabilized derivative metadata (`stabilized_path`, `stabilization_status`, `stabilization_error`, `active_asset_variant`).
- [x] Add analysis-queue stabilization worker and API endpoints to generate a stabilized source variant.
- [x] Add active-variant switching that clears coordinate-dependent tracking/analysis and re-enqueues analysis.
- [x] Resolve analysis, point/custom tracking, Smart Camera, and render source paths through the selected asset variant.
- [x] Add project assets UI to preview raw/stabilized versions and choose the active variant before tracking/render.
- [x] Bump release to `0.40.0`.
- [x] Handle upload-time stabilization enqueue failures as terminal `failed` states instead of leaving assets stuck in `pending`.
- [x] Add focused router coverage for stabilization enqueue, variant switching, and coordinate-dependent state clearing.
- [x] Verify backend lint/format/type checks, frontend build, full tests, diff whitespace, and Alembic migration syntax.
- [x] 0.40.1 UX correction: make the raw/stabilized preview video itself clickable/playable and keyboard-operable.
