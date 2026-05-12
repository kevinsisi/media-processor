# Camera Motion Decisions

Decide what camera move (zoom_in / zoom_out / pan / none) belongs on a cut.
This skill is the source of truth for camera-motion *decisions*; the sibling
skill `skills/video-camera-movement/SKILL.md` is the guardrail for *changing*
the camera-related code that implements those decisions.

**Audience:** the Smart Camera planner (currently driven by Gemini Vision),
any replacement camera planner, and any human reviewing a rendered cut.

**Conformance:** when a code change touches `services/smart_camera_planner.py`,
`services/auto_reframe.py`, `services/video_renderer.py` (camera/crop/zoompan
paths), or `services/beat_sync.py`, the implementer MUST verify that the
behaviour still matches the principles + decision table below. Drift between
this skill and code is a bug in either the skill or the code; pick one and
resolve it in the same PR.

---

## Layer 1 — Principles

These six principles are the taste-level judgement criteria. Any motion that
violates a principle is wrong even if the decision table or thresholds say
otherwise.

1. **Source preservation.** A camera move must improve the cut over leaving
   it static. A perfectly composed static shot with a single clear subject
   does not need a move. Adding one for the sake of "the AI must do
   something" is worse than nothing.

2. **Intent over inference.** When the operator explicitly chose what to
   follow (point tracking, custom ROI, or picked YOLO object), AI-inferred
   camera moves must yield. The operator's pixel-precise intent always
   outranks any visual-energy heuristic.

3. **Motion follows visual energy across time, not split attention in
   space.** A pan exists to track a subject moving across the frame over
   time. Two simultaneous focal regions (car body + brand badge, speaker +
   on-screen text) are a composition cue, not a pan path. Panning across
   them creates a fake camera shove that the source did not have.

4. **No fallback motion.** When the planner cannot determine a move
   (Vision failed, returned empty, returned ambiguous mid-band area), the
   correct answer is `none`. Never substitute a deterministic
   modulo-by-cut-order pan or zoom. A fallback motion is by definition
   uncorrelated with the source content, so it cannot satisfy principle 1.

5. **Beat sync is decoration, not foundation.** A camera move whose timing
   is wrong without BGM is not improved by snapping its completion point
   to a BGM beat. Beat sync polishes a move that is already correct; it
   does not rescue a wrong one.

6. **Vidstab is shake removal, not framing.** Digital stabilization may
   only cancel high-frequency translation jitter. It must not decide
   framing, must not change the long-term pan/zoom path, and must not run
   on a cut whose framing is already dynamically authored (Smart Camera or
   auto-reframe). Running it on top of authored motion turns intentional
   motion into "shake to correct" and produces mid-cut shoves.

---

## Layer 2 — Decision Table

The Smart Camera planner classifies each cut into one of the rows below.
Inputs come from Gemini Vision's `focus_regions` output already collected
by `services.smart_camera_planner._call_vision`; no new analysis stage is
required.

**Input axes (existing in code today):**

- `mean_area` — average of each focus region's `w_norm * h_norm` across
  sampled frames. Range `[0, 1]`.
- `cluster_count` — number of disjoint clusters after greedy single-link
  on bbox IoU (`_cluster_regions`).
- `time_relation` — for `cluster_count ≥ 2`, one of:
  - `simultaneous` — clusters present at the same time window
    (`_is_chronological_pan` returns False).
  - `chronological` — clusters separated in time
    (`_is_chronological_pan` returns True).

| # | mean_area | cluster_count | time_relation | → directive | Why |
|---|---|---|---|---|---|
| 1 | < `ZOOM_IN_AREA_MAX` | 1 | n/a | **zoom_in** | Subject is a small fraction of the frame → push in to emphasise. |
| 2 | between thresholds | 1 | n/a | **none** | Mid-band composition is already comfortable; an added move reads as fidgety. |
| 3 | > `ZOOM_OUT_AREA_MIN` | 1 | n/a | **zoom_out** | Subject fills the frame → reveal context to give the shot air. |
| 4 | any | ≥ 2 | simultaneous | **none** | Two-focus composition (subject + badge, speaker + text). Panning fakes a shove the source did not have. |
| 5 | any | ≥ 2 | chronological | **pan** | Subject moved from one focal region to another across the cut → follow it. |
| 6 | (empty / vision failed / parse error) | — | — | **none** | Per principle 4 — no fallback motion. |

**Output schema:** `Directive.kind ∈ {"zoom_in", "zoom_out", "pan", "none"}`.
`kind="none"` is a new value introduced by the
`camera-motion-priority-and-static-kind` change (schema version
`smart-camera.v3`). Pre-v3 stored blobs without `kind` or with `kind`
missing are treated as `none` by the renderer.

**Composition with other camera layers** (per principle 2):

- If the cut has explicit user tracking (point / custom_roi / picked YOLO),
  the operator's tracking crop path wins regardless of what this table
  produces. The Smart Camera directive on that cut is discarded at render
  time. This is the priority correction introduced by the same
  `camera-motion-priority-and-static-kind` change.
- If the cut has no explicit tracking, the directive from this table drives
  the render directly.
- `kind="none"` means the static aspect crop (plus any project-level
  `crop_region` anchor) renders the cut. No zoompan, no sendcmd path.

---

## Layer 3 — Parameters

Constants live in `services/smart_camera_planner.py` and
`services/video_renderer.py`. Any change to a value below requires updating
the rationale in this section in the same PR.

| Constant | Value (0.30.22) | Purpose | Rationale |
|---|---|---|---|
| `ZOOM_IN_AREA_MAX` | 0.25 | Decision row 1 trigger. | A subject covering less than 25 % of the frame is small enough that a push-in reads as deliberate emphasis. Above 25 % it reads as "the AI is fidgeting". |
| `ZOOM_OUT_AREA_MIN` | 0.60 | Decision row 3 trigger. | A subject covering more than 60 % of the frame leaves no breathing room; pulling out adds context without losing the subject. 0.60 (not 0.50) avoids triggering on close-medium shots that were intentionally tight. |
| `CLUSTER_DISJOINT_IOU` | 0.10 | Greedy cluster split threshold. | Bbox IoU below 10 % is a hard split — two regions overlapping less than that are looking at genuinely different parts of the frame. Above 10 % they are almost the same target with slight bbox drift. |
| `PAN_MIN_MEAN_T_DELTA` | 0.35 | Decision row 5 chronological-vs-simultaneous threshold. | Mean-time gap between two clusters must exceed 35 % of the cut duration to count as "across time". Below that the clusters are too overlapped temporally — likely composition, not motion. |
| `PAN_MAX_TIME_OVERLAP` | 0.10 | Decision row 5 strictness on temporal separation. | The earlier cluster's last sample must end before the later cluster's first sample, with at most 10 % overlap allowed. Stricter than `PAN_MIN_MEAN_T_DELTA` alone — guards against two clusters whose means are far apart but whose tails overlap. |
| `ZOOM_IN_END_SCALE` | 1.85 | zoom_in final zoom factor. | 0.30.22 raised this from 1.45 because operators reported "I can't tell if Smart Camera is on". Below 1.5 the move is technically present but visually subliminal. 1.85 is the lowest value that reads as "the AI is doing something" without going into Ken-Burns territory. |
| `ZOOM_OUT_START_SCALE` | 1.65 | zoom_out initial zoom factor. | Pulling out from 1.65 to 1.0 is enough range to communicate "reveal". A smaller start scale (< 1.4) makes the reveal feel like a small drift rather than an intentional shot change. |
| `PAN_SCALE` | 1.65 | pan constant zoom factor. | A pan needs source margin to translate the crop window across; at 1.0 there is no margin (output fills the source) and the pan cannot move. 1.65 leaves enough lateral room to traverse the typical between-clusters distance. |
| `SMART_CAMERA_BEAT_SYNC_*_RATIO` | 0.35 / 0.80 / 0.95 | Beat-sync window inside the cut for the move's completion point. | Move should complete near 80 % of cut duration so the visual "hit" lands on a beat without rushing the move. Lower bound 35 % stops snapping to a too-early beat; upper bound 95 % stops snapping past the cut's end. |

Constants NOT controlled by this skill (they belong to layers other than
the camera-motion decision):

- `KALMAN_Q`, `KALMAN_R`, `MAX_DELTA_PX_PER_FRAME`, `CROP_PATH_SMOOTHING_WINDOW_S`,
  `CROP_PATH_DEADBAND_PX`, `CROP_ZOOM_FACTOR` — these belong to auto-reframe
  (focus tracking smoothing), not motion decision. See
  `services/auto_reframe.py` and the guardrail skill.
- `STABILIZE_*` — vidstab parameters; see guardrail skill.
- Visible-motion-floor constants in the renderer
  (`SMART_CAMERA_VISIBLE_*_MIN`) — these post-process directives at render
  time and are an A17-type duplication of layer 3 values. The
  `camera-motion-priority-and-static-kind` change collapses them so the
  planner's directive is the single source of truth.

---

## When you change something here

1. If you change a **principle**: this is a taste shift — discuss with the
   end user (审片人) before changing. Open a PR that updates this file plus
   the decision table + thresholds that the new principle would invalidate.
2. If you change the **decision table**: update the table and add the new
   row's rationale. Add a regression test in
   `tests/unit/test_smart_camera_planner.py` exercising the new row.
3. If you change a **threshold**: update the value AND the rationale cell.
   A threshold without a rationale is a magic number.
4. If you add a **new directive kind** (e.g., `tilt`, `dolly`): bump
   `SMART_CAMERA_SCHEMA_VERSION` and document the migration path here. The
   existing `apply_smart_camera_to_plan` accepts unknown kinds at write
   time but the renderer (`SMART_CAMERA_KINDS`) will reject them — both
   must be updated together.

---

## Cross-reference

- **Guardrail for code changes:** `skills/video-camera-movement/SKILL.md`
- **Implementation entry points:**
  - `services/smart_camera_planner.py` — directive derivation (`_derive_directive`)
  - `services/video_renderer.py` — directive → ffmpeg expression (`_smart_camera_filter`)
- **Active OpenSpec changes implementing this skill:**
  - `openspec/changes/camera-motion-priority-and-static-kind/` — adds `kind="none"`, removes modulo fallback, flips Smart-Camera-vs-explicit-tracking priority.
  - `openspec/changes/camera-motion-filter-chain-cleanup/` — stops emotion zoompan from stacking on tracking crops, threads `crop_region` through dynamic-crop paths.
