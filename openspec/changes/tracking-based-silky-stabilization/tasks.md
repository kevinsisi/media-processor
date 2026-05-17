# Tasks

## 1. Tracking Target Resolution

- [x] 1.1 Add a backend resolver that returns the best stabilization target from existing asset tracking data: point, custom ROI, picked object, optional automatic object fallback.
- [ ] 1.2 Add tests for target priority and unusable/missing tracking data fallback.
- [x] 1.3 Ensure all source reads use `asset_variants.selected_media_path(asset)`.
- [x] 1.4 Invalidate stale stabilized derivatives when the operator changes the tracking target.
- [x] 1.5 Auto-enqueue forced tracking-based stabilization after async point tracking completes.

## 2. Smooth Camera Path Generation

- [x] 2.1 Convert resolved target tracks into source-coordinate center paths.
- [x] 2.2 Add smoothing with dead zone, velocity limit, acceleration limit, jerk/spike clamp, and drift guard.
- [ ] 2.3 Add metrics for adjacent-step p95/p99/max and residual jitter.
- [ ] 2.4 Add unit tests with synthetic shaky tracks and intentional slow pans.

## 3. Derivative Rendering

- [x] 3.1 Render tracking-stabilized derivatives via ffmpeg crop/sendcmd with bounded crop margin.
- [x] 3.2 Preserve duration/fps/resolution compatibility and raw immutability.
- [ ] 3.3 Ensure render-level vidstab skips assets already stabilized by tracking mode.
- [x] 3.4 Add failure handling that never publishes a tracking candidate that fails quality gates.
- [x] 3.5 Tune vidstab fallback smoothing with project 11 validation after tracking candidates are rejected.
- [x] 3.6 Ensure non-force automatic tracking stabilization respects low-jitter preflight before rendering.

## 4. API / Data Model

- [ ] 4.1 Decide whether to add `stabilization_mode` and `stabilization_metrics_json` columns before implementation.
- [ ] 4.2 Expose mode/metrics in asset detail if columns are added.
- [ ] 4.3 Keep `force=true` semantics clear: force attempts generation, but final quality gates can still reject unsafe output.

## 5. UI / Operator Flow

- [ ] 5.1 Reuse existing tracking setup UI; do not require a new tracking picker for this change.
- [ ] 5.2 Label generated derivatives as `tracking` vs `vidstab` if backend exposes mode.
- [ ] 5.3 Show skipped/failed reasons in user-facing Chinese without raw implementation jargon.
- [x] 5.4 Ensure selecting a new tracking target does not leave the old vidstab/stabilized derivative as the active source.

## 6. Verification

- [x] 6.1 Validate with project 11 DJI assets and at least one asset with explicit point/custom ROI tracking.
- [x] 6.2 Compare raw, current vidstab derivative, and tracking-stabilized derivative with objective metrics.
- [ ] 6.3 Produce side-by-side review MP4s for representative pass/fail clips.
- [ ] 6.4 Confirm `openspec validate --all --strict`, focused tests, web build, and production health after deploy.
