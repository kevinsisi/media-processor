# camera-motion-filter-chain-cleanup — Filter-chain composition fixes

## Why

Two latent bugs in `services/video_renderer.py` `_cut_segment` arise from
the if/elif filter-chain selection treating crop selection and emotion
zoompan as independent decisions. Neither bug is covered by the
0.30.23–0.30.37 hotfix burst (those targeted tracking smoothness); both
are cleanly fixable in isolation without touching Smart Camera or vidstab
behaviour.

### A11 — Emotion zoompan stacks on top of tracking crop

`video_renderer.py:749-753`:

```python
if smart_chain is not None:
    vf_chain = smart_chain
elif _should_zoompan(cut):
    vf_chain = f"{vf_chain},{_zoompan_filter(target_aspect, duration_s)}"
```

The `elif` only checks for `smart_chain`; it does NOT check for an active
auto-reframe `crop_path`. When a cut has emotion-driven zoompan enabled AND
an auto-reframe chain (point / custom_roi / picked YOLO / automatic YOLO),
emotion zoompan is appended to the already-cropped-and-scaled output.
Visually: the subject pulls into the centre, then the emotion zoompan
zooms again on top. Double zoom, mechanical-looking.

### A5 — Project `crop_region` static anchor swallowed by dynamic crop paths

`video_renderer.py:668` builds the base chain with
`aspect_filter(target_aspect, crop_region=...)`. Lines 702-706 (auto-reframe)
and 744-748 (Smart Camera) both REPLACE `vf_chain` outright with their own
crop chain. The `crop_region` operator's static-anchor choice is gone the
moment any dynamic crop path takes over. Operators who set a crop anchor on
`ProjectEdit` see it work for static-crop cuts and silently disappear for
Smart Camera or tracking cuts within the same draft.

This was not noticed during the 0.29.0 `crop_region` work because the test
fixtures used static aspect crop; nobody exercised
`crop_region + tracking_by_asset` or `crop_region + smart_camera_enabled`
together.

## What changes

1. **Emotion zoompan only on static-crop cuts.**
   - `services/video_renderer.py` `_cut_segment`: the emotion-zoompan
     branch becomes
     `elif _should_zoompan(cut) and crop_path is None and smart_chain is None`.
   - On a cut that has either a tracking crop_path or a Smart Camera
     directive, emotion zoompan is dropped silently. Log line at `INFO`:
     `"emotion-zoompan suppressed on cut N: <reason>"` where reason is
     `"tracking active"` or `"smart camera active"`.
   - No test fixture currently covers `tracking + emotion zoompan
     combined`; add coverage in T2.

2. **`crop_region` threaded through every crop chain.**
   - `services/auto_reframe.py`:
     - `compute_crop_path`,
       `compute_crop_path_from_custom_roi`,
       `compute_crop_path_from_point_track` accept a new
       `crop_region: tuple[float, float] | None = None` kwarg.
     - When set, the per-frame `(target_x, target_y)` final clamp uses
       the crop_region anchor as the centre-of-translation rather than
       the source centre. Mechanics: instead of clamping `target_x` to
       `[0, sw - crop_w]` symmetrically, the available slack is
       distributed asymmetrically per `crop_region.x_norm`:
       `bias_x = x_norm * (sw - crop_w)` becomes the rest position
       (when no subject motion would override). For subject-following
       paths the Kalman output still wins; `crop_region` is only the
       anchor for the slack not consumed by tracking.
   - `services/video_renderer.py`:
     - `cut_segments` thread `crop_region` through to each
       `_cut_segment` call.
     - `_cut_segment` passes `crop_region` into all three
       `compute_crop_path*` calls.
     - `_smart_camera_filter` (called for `kind` in `zoom_in`,
       `zoom_out`, `pan`) accepts `crop_region` and biases the
       interpolated `(cx, cy)` centre toward the anchor when the
       directive's interpolation endpoints land near the source
       centre. Concretely: `f_cx_biased = f_cx + (anchor_x - 0.5) *
       (1 - |2*f_cx - 1|)` so an anchor near 0.5 (centre) does not
       move anything; an anchor near 0 (left) shifts a centred Smart
       Camera frame leftward; a Smart Camera directive already biased
       far from centre is unaffected (the `(1 - |2*f_cx - 1|)` factor
       collapses to 0 at the edges).

3. **Make `_should_zoompan` callers self-document the suppression.**
   - The new condition in item 1 lives in `_cut_segment` not inside
     `_should_zoompan` itself — `_should_zoompan` continues to mean
     "this cut's content would benefit from emotion zoompan IF nothing
     else is composing it". Separation lets future debug logs / metrics
     count "would have zoomed" vs "actually zoomed".

## Conformance to skills

- `skills/video-camera-movement/SKILL.md` "Do not layer emotion zoompan
  on top of any dynamic crop chain" rule is implemented by item 1.
- `skills/video-camera-movement/SKILL.md` "Thread the project's
  `crop_region` static anchor through every crop chain" rule is
  implemented by item 2.

## Non-goals

- **`crop_region` for `kind="none"` cuts.** Already works today through
  the base static aspect crop chain. No change needed; item 2 only
  threads through the dynamic crop paths.
- **`crop_region` interactive picking in the editor.** UI is unchanged;
  this is purely a render-time fix.
- **Vidstab interaction with `crop_region`.** Vidstab operates on the
  already-cropped intermediate, so `crop_region` is already implicitly
  applied by the time stabilization runs. No change.
- **Schema / API changes.** No DB migration, no Pydantic schema edits,
  no `ProjectDetail` shape change.

## Verification

Behaviour-equivalence tests at the `-vf` string level:

- Cut with emotion zoompan eligible (`dominant_emotion` ∈
  `ZOOMPAN_EMOTIONS`, dynamic motion or face) AND `point_track` set →
  resulting chain contains `crop@reframe`, does NOT contain `zoompan=`.
- Cut with emotion zoompan eligible AND Smart Camera `kind="zoom_in"` →
  resulting chain contains the Smart Camera `zoompan=…`, does NOT
  contain a chained second `zoompan=`.
- Cut with emotion zoompan eligible AND no tracking + no Smart Camera →
  resulting chain contains the emotion `zoompan=` exactly once
  (preserves 0.30.22 behaviour).
- Project `crop_region=(0.2, 0.5)` (left-third anchor) + auto-reframe
  on subject in source-right region → final sendcmd `crop_path.points`
  has `x` values biased toward `0.2 * (sw - crop_w)` when the subject
  is centred, drifting right as the subject moves right.
- Project `crop_region=(0.5, 0.5)` (centre, default) + auto-reframe →
  output identical to 0.30.22 (sanity check).
- Smart Camera `pan` directive `from_rect=(0.3, 0.5, 0.4, 0.4)`
  `to_rect=(0.5, 0.5, 0.4, 0.4)` + `crop_region=(0.2, 0.5)` → the
  interpolated centre at `t=0` shifts toward 0.2 but not all the way
  (per the `(1 - |2*f_cx - 1|)` collapse factor).

Visual canary (at home, OpenCode operator):

- Create a project with `crop_region=top` on a portrait → 9:16 mix
  source. Render with Smart Camera enabled. Pass = the top of the
  source consistently visible across cuts regardless of which crop path
  ran.
- Re-render a draft that has emotion-tagged cuts AND a YOLO subject.
  Pass = no double-zoom artefact; subject stays at consistent size
  through the cut.
