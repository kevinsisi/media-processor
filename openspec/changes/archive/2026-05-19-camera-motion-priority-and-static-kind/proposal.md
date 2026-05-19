# camera-motion-priority-and-static-kind — Smart Camera priority + `kind="none"`

## Why

Three latent defects in 0.30.22 violate `skills/camera-motion-decisions/SKILL.md`
principles 2 (intent over inference) and 4 (no fallback motion). The
0.30.23–0.30.37 hotfix burst tried to address them in the renderer's
if/elif crop dispatch, churned through 15 commits, and was reverted whole
because every attempt fixed one symptom and broke another. The actual
problems are in the planner's directive schema and the renderer's priority
ordering, not in tracking smoothness. This change addresses the
priority/schema axis cleanly; the tracking-smoothness axis is left for a
future trajectory refactor.

### A1 — Smart Camera overrides explicit user tracking

`video_renderer.py:744-748`: `if smart_chain is not None: vf_chain = smart_chain`
unconditionally replaces whatever crop chain was built for explicit user
tracking (point / custom_roi / picked YOLO). The skill states user tracking
is the highest framing intent, but the code lets Smart Camera win whenever
it is enabled. Operators who draw a point track find Smart Camera silently
overriding it.

### A3 — Smart Camera fallback synthesises motion when Vision fails

`smart_camera_planner.py:573-627` `_fallback_directive_for_cut` uses
`order % 2` and `order % 3` to deterministically synthesise a pan or zoom
directive when Vision returns no usable focus regions (quota error,
malformed JSON, empty regions, mid-band area). The decision skill principle
4 forbids fallback motion — a mechanical move uncorrelated with the source
content cannot satisfy principle 1 (source preservation).

### A4 — Smart Camera schema has no `kind="none"`

`smart_camera_planner.py:366` `SMART_CAMERA_KINDS = frozenset({"zoom_in", "zoom_out", "pan"})`.
There is no way for the planner to record "Vision analysed this cut and
the right answer is no move". The only `None`-like outcome is `directive=None`,
which today drops through to the modulo fallback in A3. Without `kind="none"`
in the schema, removing the fallback would leave the renderer unable to
distinguish "Vision intentionally declined to move the camera" from "Vision
failed to produce a directive". The renderer would then have to invent a
behaviour for `None`, which historically has always been "do something".

## What changes

1. **Schema bump to `smart-camera.v3` with `kind="none"`.**
   - `SMART_CAMERA_SCHEMA_VERSION` → `"smart-camera.v3"` in
     `services/smart_camera_planner.py`.
   - `SMART_CAMERA_KINDS` in `services/video_renderer.py` extends to
     `frozenset({"zoom_in", "zoom_out", "pan", "none"})`.
   - New `Directive` instance with `kind="none"` is the planner output
     whenever `_derive_directive` would have returned `None` or
     `_fallback_directive_for_cut` would have fired.
   - `_smart_camera_filter` returns `None` for `kind="none"` AND records
     in the directive blob `{"kind": "none", "notes": "<why>"}` so the
     blob is still inspectable in stored draft data.
   - `deserialise_directive` accepts `kind="none"` and returns a
     `Directive(kind="none", from_rect=(0,0,1,1), to_rect=(0,0,1,1), …)`
     placeholder; callers MUST check `kind` before applying.
   - Stored pre-v3 blobs: any blob with `kind` missing or `kind not in
     SMART_CAMERA_KINDS` is treated as `kind="none"` at read time
     (forward-compatibility for any in-flight drafts). No SQL migration
     is required.

2. **Remove `_fallback_directive_for_cut` entirely.**
   - `services/smart_camera_planner.py`: delete the function and the
     `FALLBACK_*_SCALE` constants. All callers
     (`plan_smart_camera`, `build_fallback_directives`) emit a
     `Directive(kind="none", notes=<reason>)` instead.
   - `build_fallback_directives` is kept but renamed
     `build_no_move_directives` and emits `kind="none"` for every cut
     when the toggle is enabled but Vision cannot be reached
     (preserves the "directive present, no motion" semantic).
   - Vision partial-success path: a cut whose Vision call raises
     `SmartCameraQuotaError` / `SmartCameraInvalidError` / any other
     exception ends up with a `kind="none"` directive instead of the
     modulo move. Log message changes from "fallback X: <reason>" to
     "no-move: <reason>".

3. **Flip priority: explicit tracking wins over Smart Camera.**
   - `services/video_renderer.py` `_cut_segment` reorders the chain
     selection so that when `crop_path is not None` (i.e., the cut has
     point / custom_roi / picked-YOLO tracking), Smart Camera is
     discarded and the existing tracking sendcmd chain renders. The
     `smart_chain is not None and crop_path is not None` log line stays
     as a one-line `INFO` so operators can still see when Smart Camera
     deferred.
   - Automatic YOLO auto-reframe (the dominant-track path entered with
     no `tracking_object_index` and no point/custom_roi) is NOT
     considered explicit tracking. When Smart Camera is enabled it still
     overrides automatic YOLO; the v0.30.9 override-of-automatic-YOLO
     behaviour is preserved.
   - `reframed_flags[i]` semantics expand: now `True` when *either*
     Smart Camera directive (other than `kind="none"`) OR a tracking
     chain rendered the cut. Vidstab skip semantics unchanged.

4. **Drop the renderer-side visible-floor boost constants.**
   - `services/video_renderer.py` `SMART_CAMERA_VISIBLE_ZOOM_IN_MIN`,
     `SMART_CAMERA_VISIBLE_ZOOM_OUT_MIN`, `SMART_CAMERA_VISIBLE_PAN_ZOOM_MIN`,
     `SMART_CAMERA_VISIBLE_PAN_GAIN` are removed.
   - `_smart_camera_filter` honours the directive's `from_rect`/`to_rect`
     verbatim. The 0.30.22 "make motion visible" goal is preserved by
     the planner producing directives at the documented Layer-3 scales
     (`ZOOM_IN_END_SCALE=1.85` etc.) directly. Single source of truth.

## Conformance to skills

- `skills/camera-motion-decisions/SKILL.md` Layer 1 principle 2 (intent
  over inference) is implemented by item 3.
- `skills/camera-motion-decisions/SKILL.md` Layer 1 principle 4 (no
  fallback motion) is implemented by items 1 + 2.
- `skills/camera-motion-decisions/SKILL.md` Layer 2 row 6 (Vision failure
  → `none`) is implemented by item 2.
- `skills/video-camera-movement/SKILL.md` "Smart Camera `none`" rule is
  implemented as a real schema kind by item 1 rather than the
  current `directive is None` sentinel.

## Non-goals

- **Vidstab on tracking segments (A2).** The hand-held shake leaking
  through Kalman + velocity cap on explicit-tracking cuts is the real
  pain that 0.30.25–0.30.34 tried to fix. It is NOT in scope for this
  change. It belongs to a future `camera-trajectory-unification` change
  that introduces an authoritative crop trajectory before render.
- **Smoothing parameter changes.** No `KALMAN_Q`, `KALMAN_R`,
  `MAX_DELTA_PX_PER_FRAME`, `CROP_PATH_SMOOTHING_WINDOW_S`, or
  `CROP_PATH_DEADBAND_PX` values change.
- **Vision prompt or `_derive_directive` rule changes.** The decision
  table in the decisions skill matches the current `_derive_directive`
  behaviour; this change only collapses what happens to "no answer" / "no
  call possible" / "ambiguous" cases.
- **Beat sync changes.** Beat-sync logic stays as-is.

## Verification

Behaviour-equivalence tests at the `_cut_segment` `-vf` string level (no
ffmpeg execution required) cover:

- Smart Camera enabled + Vision returns mid-band area → directive is
  `kind="none"`, `_cut_segment` produces the static aspect crop chain,
  `reframed_flags[i]` is `False`.
- Smart Camera enabled + Vision raises `SmartCameraQuotaError` → same
  outcome as above; no modulo pan emitted.
- Smart Camera enabled + explicit point track on cut → tracking sendcmd
  chain renders; Smart Camera directive is logged-and-discarded.
- Smart Camera enabled + automatic YOLO only (no explicit tracking) →
  Smart Camera directive wins (preserves v0.30.9 behaviour).
- Pre-v3 stored directive (no `kind`, schema `smart-camera.v2`) →
  renderer treats as `kind="none"`, static aspect crop renders.

Visual canary (at home, OpenCode operator):

- Re-render `draft 49 / project 10` with Smart Camera enabled. Compare
  against `0.30.22` baseline. Pass = no regression in
  adjacent-frame step p95/p99/max OR visible shake on subjective review.
- Re-render the same draft with Smart Camera enabled + a point track
  added on cut 3. Pass = subject stays locked to the tracked point;
  Smart Camera does not override.
- Repeat with Vision quota artificially exhausted (set
  `GEMINI_API_KEYS=""` for the editing worker). Pass = no modulo pan,
  cuts render as static.
