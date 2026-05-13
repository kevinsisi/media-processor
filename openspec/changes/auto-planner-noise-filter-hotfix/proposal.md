## Why

One-click auto generation can still pick visually bad clips when YOLO produces a very short or low-confidence tracking row for the requested project subject. In the project 11 car test, the rejected sections came from assets whose tracking was dominated by weak `person` detections while the project subject was `car`. Separately, cuts can begin on the first handheld setup beat of a clip, making otherwise usable material feel unstable.

## What Changes

- Require subject-filter tracking rows to be long enough and confident enough before they can create a subject-presence window.
- Treat very short or low-confidence subject tracks as YOLO noise for one-click planning.
- Shift cut starts past initial handheld setup movement when enough clip duration remains.

## Capabilities

### Modified Capabilities

- `dual-path-production-entry`: One-click automatic generation must avoid clips whose requested subject is only supported by weak tracking noise.
- `subject-class-auto-trim`: Subject windows must be based on reliable object tracks, not transient detections.

## Impact

- Planner-only hotfix in `services.edit_planner`.
- No schema or migration change.
- Existing manual review/tracking data remains intact; this only changes auto-planning eligibility.
