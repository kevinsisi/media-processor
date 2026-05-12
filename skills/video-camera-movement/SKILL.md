# Video Camera Movement

Use this skill before changing Smart Camera, focus tracking, auto-reframe, crop smoothing, or digital stabilization behavior in Media Processor.

## Current Baseline

- Production is intentionally back on `0.30.22`.
- Draft `49` on project `10` is the known regression canary for camera-motion changes.
- The `0.30.23` through `0.30.37` camera-motion experiments were rejected for visible shake or fallback regressions. Do not revive them by copying code paths back in.

## Priority Order

1. Explicit user intent.
2. Creative camera movement.
3. Smoothed tracking crop path.
4. Digital stabilization cleanup.
5. Final output crop and encode.

## Rules

- User-selected point tracking, custom ROI, or picked object tracking is the highest framing intent.
- Tracking data is a composition target, not a raw per-frame crop command.
- Smart Camera is a creative movement layer. It may add pan, zoom in, or zoom out only when doing so does not override explicit user tracking.
- Smart Camera `none` means no extra AI movement. It must not force static crop, vidstab fallback, post-stabilization, or any other correction.
- Digital stabilization may only remove high-frequency shake. It must not decide framing or change the long-term pan/zoom path.
- A fallback is acceptable only if it is less distracting than the source and does not create single-frame jumps.
- Prefer no extra correction over a correction that looks mechanically locked, rubber-banded, or nervous.

## Implementation Guidance

- Build one authoritative crop path per cut before ffmpeg execution.
- Apply smoothing, dead zones, speed limits, acceleration limits, and drift guards before serializing crop commands.
- Do not layer Smart Camera zoompan on top of explicit tracking crop paths.
- Do not run vidstab or post-stabilization over a segment that already has intentional dynamic framing unless there is a bounded, measured-safe design.
- Keep `none` as pass-through for the creative movement layer.
- Treat localized adjacent-frame spikes as failures even when whole-cut p95 looks acceptable.

## Verification Requirements

- Verify with production-like renders, not only unit tests.
- Inspect worker logs to confirm which path ran for each canary cut.
- Measure adjacent-frame step p95, p99, and max for the known problem windows.
- Review the rendered MP4 or contact sheets for single-frame jumps.
- If draft `49` gets worse than `0.30.22`, stop and roll back before trying a broader fix.
