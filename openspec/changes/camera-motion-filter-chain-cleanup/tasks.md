# Tasks — camera-motion-filter-chain-cleanup

Implementable independently from `camera-motion-priority-and-static-kind`.
If both changes land in the same release, this one should land second so
its tests can assert on the v3 schema directly; if landing first, swap the
`kind="none"` assertions for the equivalent `directive is None` form.

- [ ] T1 — Emotion zoompan gating in `_cut_segment`
  - [ ] `services/video_renderer.py` `_cut_segment`:
    - [ ] Change the emotion-zoompan branch condition to
      `elif _should_zoompan(cut) and crop_path is None and smart_chain is None`.
    - [ ] Add `INFO` log when `_should_zoompan(cut)` was true but the
      branch was skipped: `"emotion-zoompan suppressed on cut %d:
      %s"` with reason in `{"tracking active", "smart camera active"}`.
  - [ ] `tests/unit/test_video_renderer.py`:
    - [ ] Add `test_emotion_zoompan_suppressed_on_tracking_cut`:
      construct a cut with `dominant_emotion="joy"`, `has_face=True`,
      provide a `point_track`, assert the `-vf` chain contains
      `crop@reframe` and not `zoompan=`.
    - [ ] Add `test_emotion_zoompan_suppressed_on_smart_camera_cut`:
      similar but with `smart_camera_json` carrying a `kind="zoom_in"`
      directive, assert exactly one `zoompan=` (the Smart Camera one)
      in the chain.
    - [ ] Keep `test_should_zoompan_*` tests as-is — `_should_zoompan`
      itself is unchanged.

- [ ] T2 — Thread `crop_region` through `auto_reframe`
  - [ ] `services/auto_reframe.py`:
    - [ ] Add `crop_region: tuple[float, float] | None = None` param to
      `compute_crop_path`, `compute_crop_path_from_custom_roi`,
      `compute_crop_path_from_point_track`. Default `None` preserves
      v0.30.22 behaviour.
    - [ ] Inside `compute_crop_path`, after the Kalman + smoothing pass
      and before the final per-frame clamp, compute
      `bias_x = (anchor_x - 0.5) * (sw - crop_w)`,
      `bias_y = (anchor_y - 0.5) * (sh - crop_h)` (where `anchor_*`
      come from `crop_region`, default 0.5). Add the bias to each
      `target_x`/`target_y` before the existing `max(0, min(max_x,…))`
      clamp. The clamp absorbs over-bias on near-edge subjects so the
      crop window never spills off the source.
    - [ ] `compute_crop_path_from_custom_roi` and
      `compute_crop_path_from_point_track` pass `crop_region` through
      verbatim to `compute_crop_path`.
  - [ ] `tests/unit/test_auto_reframe.py`:
    - [ ] Add `test_compute_crop_path_centre_anchor_matches_v0_30_22`:
      run with `crop_region=(0.5, 0.5)`, assert points identical to
      `crop_region=None`.
    - [ ] Add `test_compute_crop_path_left_anchor_shifts_idle_x`:
      synthetic input where YOLO bbox sits at exact source centre,
      `crop_region=(0.2, 0.5)`. Assert returned points have
      `x ≈ 0.2 * (sw - crop_w)` rather than `(sw - crop_w) / 2`.
    - [ ] Add `test_compute_crop_path_bias_does_not_break_subject_lock`:
      moving subject across frame, `crop_region=(0.2, 0.5)`. Assert
      Kalman-tracked motion still dominates (subject is still framed
      centre-of-crop), the bias only fills the slack.

- [ ] T3 — Thread `crop_region` through Smart Camera renderer
  - [ ] `services/video_renderer.py`:
    - [ ] `_smart_camera_filter` accepts `crop_region` kwarg.
    - [ ] After computing `f_cx`, `f_cy`, `t_cx`, `t_cy`, apply the
      collapse-at-edges bias:
      `bias = anchor - 0.5; collapse = 1 - abs(2*c - 1);
      c_biased = c + bias * collapse` for each of the four centres.
      Clamp to `[0, 1]` afterward.
    - [ ] `_cut_segment` passes its `crop_region` arg down to
      `_smart_camera_filter`.
    - [ ] `cut_segments` already threads `crop_region` to
      `_cut_segment` (0.29.0). Verify the call site passes it to
      `_smart_camera_filter`.
  - [ ] `tests/unit/test_video_renderer.py`:
    - [ ] Add `test_smart_camera_centre_anchor_unchanged`: anchor
      `(0.5, 0.5)`, assert `-vf` identical to no-anchor case.
    - [ ] Add `test_smart_camera_left_anchor_biases_centred_pan`:
      a `pan` directive whose `from_rect`/`to_rect` are both centred
      (`f_cx=0.5`, `t_cx=0.5`); anchor `(0.2, 0.5)`. Assert the
      resulting expression's centre-X expressions evaluate (at the
      directive endpoints) to values shifted toward 0.2.
    - [ ] Add `test_smart_camera_edge_directive_anchor_collapses`:
      a `zoom_in` directive whose `to_rect` is biased left
      (centre at 0.1); anchor `(0.2, 0.5)`. Assert the collapse
      factor at the biased centre (0.1) is small
      (`1 - |2*0.1 - 1| = 0.2`), so the bias contribution at that
      endpoint is at most `(0.2 - 0.5) * 0.2 = -0.06`. Sanity check
      that directives already biased far from centre are not
      over-corrected toward the anchor.

- [ ] T4 — Threading `crop_region` end-to-end
  - [ ] `services/video_renderer.py` `cut_segments`: keep existing
    `crop_region` param (added in 0.29.0). Pass to `_cut_segment`.
  - [ ] `services/video_renderer.py` `_cut_segment`: accept
    `crop_region` (already present per 0.29.0). Pass to all three
    auto_reframe `compute_crop_path*` calls and to `_smart_camera_filter`.
  - [ ] `services/edit_orchestrator.py`: verify the existing
    `crop_region` resolved from `Project.crop_region_json` reaches
    `render(...)`. (Already done in 0.29.0; verify no regression.)
  - [ ] No new public API; no Pydantic / FE / migration change.

- [ ] T5 — Skill / contract docs
  - [ ] Verify `skills/video-camera-movement/SKILL.md` "Do not layer
    emotion zoompan…" rule matches T1 behaviour.
  - [ ] Verify `skills/video-camera-movement/SKILL.md` "Thread the
    project's `crop_region` static anchor through every crop chain"
    rule matches T2 + T3 behaviour.

- [ ] T6 — CI / local validation
  - [ ] `make typecheck`, `make lint`, `make test` all green.
  - [ ] Auto-reframe tests + video-renderer tests must include all
    new T1-T3 cases.

- [ ] T7 — Visual canary (at home, OpenCode operator)
  - [ ] Re-render `draft 49 / project 10` with `crop_region=None` —
    expectation: identical to 0.30.22 baseline (sanity).
  - [ ] Set `crop_region={x_norm: 0.5, y_norm: 0.2}` (top anchor),
    re-render. Expectation: the top of the source consistently sits
    in-frame across cuts that use Smart Camera AND cuts that use the
    static aspect crop AND cuts that use auto-reframe.
  - [ ] Add a point track on a cut with `dominant_emotion="joy"` AND
    `has_face=True`. Render. Expectation: subject is centred (tracked),
    no second emotion-zoompan visible on top.
  - [ ] Diff `worker-editing` logs for `INFO` lines containing
    "emotion-zoompan suppressed". Sanity: at least one such line for a
    tracked emotional cut.
  - [ ] If any canary regresses, stop. Do not chase through
    micro-versions.
