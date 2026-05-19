# Change: tracking-based-silky-stabilization

## Why

Operators already can set an explicit tracking target with point tracking, custom ROI, or a picked YOLO object. Today that tracking target is used mainly at render time for auto-reframe/crop composition, while asset-level stabilization still runs whole-frame vidstab. Whole-frame vidstab can remove some high-frequency shake, but it cannot know which subject should feel locked, and it can make already-stable clips worse.

The desired user experience is closer to a video editor's "track this" stabilization: pick a subject, smooth the camera path around it, preserve intentional motion, and use crop margin to absorb jitter. Existing tracking data should become the source of truth for a silky stabilization path instead of treating vidstab as the primary solution.

## What Changes

- Add a tracking-based stabilization path that converts existing explicit tracking data into a smoothed crop/camera path.
- Prefer explicit tracking targets in this order: point tracking, custom ROI, picked YOLO object. Automatic YOLO may be used only as a fallback when there is no explicit target.
- Apply dead zone, speed limit, acceleration limit, jerk/step-spike suppression, and drift guard before serializing crop commands.
- Produce a stabilized derivative that is tied to the tracking source variant and can be previewed/selected like current stabilized variants.
- Keep vidstab as an optional final high-frequency cleanup layer only when measurement shows it is safe; it must not define framing or override tracking.
- Surface enough metrics/logs to prove which path ran and whether the result is smoother than raw.

## Non-Goals

- No AI frame generation or border hallucination.
- No replacement of point/custom/object tracking UI in this change.
- No automatic switching of active variants after a derivative is generated.
- No removal of current vidstab-based stabilization; it remains the fallback when tracking data is unavailable or unusable.

## Impact

- Backend worker/render pipeline: new tracking-stabilization derivative path.
- API/schema: may need to expose stabilization mode and metrics so the UI can distinguish `vidstab` vs `tracking` results.
- Frontend: reuse existing tracking setup UI; add mode/status labels only if backend exposes them.
- Storage: derivative output remains under project-owned asset storage; raw upload remains immutable.
