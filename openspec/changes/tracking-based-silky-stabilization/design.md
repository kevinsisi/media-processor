# Design: Tracking-Based Silky Stabilization

## Current State

Existing tracking capabilities:

- `tracked_object_index = -4` uses `point_tracking_json` from Lucas-Kanade point tracking.
- `custom_roi_json` stores user-defined ROI tracking.
- `tracked_object_index >= 0` selects a YOLO object track from `tracking_json`.
- Render-time auto-reframe converts these tracks into `crop@reframe` sendcmd paths.
- Asset-level stabilization currently uses whole-frame vidstab and does not consume tracking intent.

## Proposed Pipeline

1. Resolve the source path via `asset_variants.selected_media_path(asset)`.
2. Resolve a stabilization target from existing tracking data:
   - point tracking if `tracked_object_index == -4` and `point_tracking_json` is usable;
   - custom ROI if present and usable;
   - picked object track if `tracked_object_index >= 0` and matching track data exists;
   - automatic fallback track only when no explicit tracking target exists.
3. Convert the target to a center path in source coordinates.
4. Smooth the center path into a camera path:
   - dead zone for tiny target movement;
   - max velocity per frame;
   - max acceleration per frame;
   - jerk/adjacent-step spike clamp;
   - drift guard so long-term framing does not slowly crawl away from the subject.
5. Convert the camera path into a crop/sendcmd chain using configurable crop margin.
6. Encode a stabilized derivative.
7. Run objective metrics comparing raw vs derivative:
   - adjacent crop-center step p95/p99/max;
   - high-frequency residual jitter RMS/p95;
   - subject containment rate inside a safe central region;
   - border/black-edge check.
8. If the derivative fails quality gates, mark stabilization failed or keep vidstab fallback rather than presenting a worse stabilized variant.

## Mode Selection

Suggested mode names:

- `vidstab`: current whole-frame two-pass vidstab.
- `tracking`: explicit tracking target drives the smoothed camera path.
- `auto_tracking`: automatic object fallback drives the smoothed camera path.
- `skipped`: source was already stable or no safe improvement is available.

Default behavior:

- If explicit tracking target exists, run `tracking` mode.
- Else if automatic object track is high-confidence and stable, run `auto_tracking` mode.
- Else run existing low-jitter preflight and vidstab fallback.

`force=true` should force a job attempt, but it should not bypass final quality rejection if the output is objectively worse than raw. If a hard override is needed later, add a separate `unsafe_force` operator-only flag.

## Crop Margin

Tracking stabilization requires margin. Without margin there is no room to absorb shake.

Initial conservative defaults:

- 9:16 / 16:9 source-to-same-aspect derivative: start with 1.08x zoom equivalent.
- Allow 1.05x to 1.15x based on measured motion amplitude.
- Reject if required margin exceeds the configured cap, because over-cropping looks worse than raw.

## Interaction With Smart Camera And Render

- Source-level tracking stabilization prepares an asset variant; it does not add creative movement.
- Render-time Smart Camera remains below explicit tracking intent.
- When a stabilized derivative was created from a tracking path, later render-level vidstab should skip that segment to avoid double stabilization.
- `kind="none"` remains no extra AI movement; it must not trigger tracking stabilization by itself.

## Quality Gates

A generated derivative is accepted only if it satisfies all of these:

- adjacent-step p95 improves or stays within a tiny tolerance;
- adjacent-step max has no new large spike;
- residual high-frequency jitter improves meaningfully unless raw is already below low-jitter threshold;
- subject containment remains above threshold for explicit tracking targets;
- no sustained black borders are introduced;
- duration/fps/resolution remain compatible with raw.

## Open Questions

- Should tracking-stabilized derivatives use the existing `stabilized_path` column or add a separate `stabilization_mode` / metrics JSON column first?
- Should UI expose crop margin as an advanced setting, or should backend choose it automatically from path amplitude?
- Should automatic object fallback be enabled by default, or only after explicit opt-in because wrong subject selection is worse than no stabilization?
