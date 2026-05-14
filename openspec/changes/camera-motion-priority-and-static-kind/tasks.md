# Tasks — camera-motion-priority-and-static-kind

Implement in the order below. Each task is small enough to land as its
own commit with green CI; the bundled PR squashes them.

- [ ] T1 — Schema v3 + `kind="none"` in planner
  - [ ] `services/smart_camera_planner.py`:
    - [ ] Bump `SMART_CAMERA_SCHEMA_VERSION` to `"smart-camera.v3"`.
    - [ ] Allow `kind="none"` in `Directive` (relax the
      `_derive_directive` return-type contract and the
      `apply_smart_camera_to_plan` writer to pass through).
    - [ ] `_derive_directive` returns `Directive(kind="none",
      from_rect=(0,0,1,1), to_rect=(0,0,1,1), ease="linear",
      notes="<row 2 or row 4 or row 6 reason>")` in every code path
      that today returns `None`. Add the `notes` reason matching the
      decisions skill row (mid-band / simultaneous / empty).
    - [ ] `serialise_directive` accepts `kind="none"` and emits the
      blob normally (no `focus_regions` required).
  - [ ] `tests/unit/test_smart_camera_planner.py`:
    - [ ] Replace `test_derive_returns_none_when_mid_band_area` ↦
      `test_derive_returns_none_kind_when_mid_band_area`
      (asserts `directive.kind == "none"`).
    - [ ] Replace `test_derive_does_not_pan_for_simultaneous_clusters` ↦
      `test_derive_returns_none_kind_for_simultaneous_clusters`.
    - [ ] Replace `test_derive_returns_none_for_empty_regions` ↦
      `test_derive_returns_none_kind_for_empty_regions`.
    - [ ] Add `test_serialise_round_trip_none_kind` covering the
      `kind="none"` blob shape.

- [ ] T2 — Remove `_fallback_directive_for_cut`
  - [ ] `services/smart_camera_planner.py`:
    - [ ] Delete `_fallback_directive_for_cut`.
    - [ ] Delete `FALLBACK_ZOOM_IN_END_SCALE`,
      `FALLBACK_ZOOM_OUT_START_SCALE`, `FALLBACK_PAN_SCALE`.
    - [ ] In `plan_smart_camera`, replace every call site
      (`directive = _fallback_directive_for_cut(cut, reason=...)`) with
      `directive = Directive(kind="none", from_rect=(0,0,1,1),
      to_rect=(0,0,1,1), ease="linear", notes=f"no-move: {reason}")`.
      Affects the three except branches (`SmartCameraError`,
      `SmartCameraQuotaError`-via-_call_vision, and the bare
      `Exception`) plus the inner `if directive is None` post-derive
      check.
    - [ ] Rename `build_fallback_directives` ↦
      `build_no_move_directives`. Update the call site in
      `services/edit_orchestrator.py` AND the public export name in
      the `__all__` list at the bottom of
      `services/smart_camera_planner.py`.
  - [ ] `tests/unit/test_smart_camera_planner.py`:
    - [ ] Replace `test_fallback_directives_cover_every_cut` with
      `test_no_move_directives_cover_every_cut` asserting every cut
      gets `kind="none"`.

- [ ] T3 — Renderer accepts and short-circuits `kind="none"`
  - [ ] `services/video_renderer.py`:
    - [ ] `SMART_CAMERA_KINDS = frozenset({"zoom_in", "zoom_out", "pan", "none"})`.
    - [ ] `_smart_camera_filter` returns `None` immediately when
      `kind == "none"`, without going through the rect / zoom math.
    - [ ] `deserialise_directive` accepts `kind="none"` and returns a
      `Directive` with that kind plus full-frame rects.
    - [ ] Pre-v3 forward-compatibility: when a stored directive blob
      has `kind` missing OR `kind not in SMART_CAMERA_KINDS`, the
      renderer treats it as `kind="none"`. No SQL migration.
    - [ ] Delete `SMART_CAMERA_VISIBLE_ZOOM_IN_MIN`,
      `SMART_CAMERA_VISIBLE_ZOOM_OUT_MIN`,
      `SMART_CAMERA_VISIBLE_PAN_ZOOM_MIN`,
      `SMART_CAMERA_VISIBLE_PAN_GAIN` constants.
    - [ ] Remove the `t_zoom = max(t_zoom, SMART_CAMERA_VISIBLE_*)`
      and pan-gain post-processing inside `_smart_camera_filter`. Use
      the directive's rects directly.
  - [ ] `tests/unit/test_video_renderer.py`:
    - [ ] Add `test_smart_camera_filter_returns_none_for_none_kind`.
    - [ ] Add `test_smart_camera_filter_treats_pre_v3_blob_as_none`.
    - [ ] Remove `test_smart_camera_filter_boosts_subtle_zoom_out`
      (the boost no longer exists).
    - [ ] Adjust existing visible-zoom test fixtures so the directives
      already carry the documented Layer-3 scales (1.85 / 1.65 / 1.65).

- [x] T4 — Priority flip: explicit tracking wins
  - [x] `services/video_renderer.py` `_cut_segment`:
    - [x] Compute `has_explicit_tracking` = `point_track is not None or
      custom_roi is not None or (tracking is not None and
      tracking_object_index is not None)`.
    - [x] If `has_explicit_tracking and smart_chain is not None`: log
      `INFO` "explicit-tracking overrides Smart Camera on cut N", set
      `smart_chain = None`, keep the existing `crop_path`-based
      `vf_chain`.
    - [x] If `not has_explicit_tracking and smart_chain is not None and
      crop_path is not None` (this is automatic YOLO + Smart Camera):
      Smart Camera still wins, preserving v0.30.9 behaviour. Log
      `INFO` "smart-camera overrides automatic auto-reframe".
    - [x] `reframed_flags` value for the cut: `True` iff the segment must
      skip vidstab: tracking chain, non-`none` Smart Camera directive, or
      a Smart Camera `kind="none"` no-extra-correction decision.
  - [x] `tests/unit/test_video_renderer.py`:
    - [x] Rename `test_smart_camera_overrides_explicit_tracking` →
      `test_explicit_tracking_overrides_smart_camera` and flip the
      expectation: with a `point_track` set, the resulting `-vf`
      chain MUST contain `crop@reframe` (the sendcmd chain) and MUST
      NOT contain `zoompan`. (This test in 0.30.22 was enforcing the
      wrong direction.)
    - [x] Keep `test_smart_camera_overrides_automatic_auto_reframe`
      unchanged — automatic YOLO is not explicit tracking.
    - [x] Add `test_kind_none_sets_stabilization_skip_flag` asserting
      vidstab does NOT run on a `kind="none"` cut when stabilize is
      enabled and no tracking is set.

- [ ] T5 — Skill / contract docs
  - [ ] Verify `skills/camera-motion-decisions/SKILL.md` Layer 2 row 6
    matches T2 behaviour.
  - [ ] Verify `skills/video-camera-movement/SKILL.md` "Smart Camera
    `none` (schema v3 `kind="none"`)" rule matches T1+T3 behaviour.
  - [ ] If T1/T2/T3/T4 needed any deviation from the skills (e.g., the
    `kind="none"` placeholder rects had to be set differently), update
    the corresponding skill section in the same commit.

- [ ] T6 — Migration + ROADMAP entry
  - [ ] `ROADMAP.md`: add a row "M9.15.23 — Smart Camera priority
    correction + `kind="none"` schema, removes 0.30.22's modulo
    fallback. ✅ done @ <version>".
  - [ ] No DB migration required (directives are JSON in
    `cut_plan_segments.smart_camera_json`; forward-compatibility lives
    in the renderer's read-time fallback per T3).

- [ ] T7 — CI / local validation
  - [ ] `make typecheck` (`mypy src`) clean.
  - [ ] `make lint` (`ruff check src tests`) clean.
  - [ ] `make test` (`pytest -v`) all green; smart-camera planner +
    video renderer test files MUST run.
  - [ ] `TRACKING_FAKE=1 pytest tests/unit/test_video_renderer.py`
    explicitly runs the smart-camera + auto-reframe interaction tests.

- [ ] T8 — Visual canary (at home, after CI green)
  - [ ] Re-render `draft 49 / project 10` baseline (Smart Camera
    enabled, default settings). Compare to 0.30.22 baseline render.
  - [ ] Re-render the same draft after adding a point track on cut 3.
    Expectation: subject stays locked.
  - [ ] Re-render with Vision quota artificially exhausted
    (`GEMINI_API_KEYS=""` on worker-editing). Expectation: cuts render
    as static aspect crop, no modulo pan.
  - [ ] If any canary regresses, stop and roll back. Do NOT chase a
    fix through additional micro-versions (the 0.30.23–0.30.37
    anti-pattern).

- [x] T9 — 0.30.39 failed-canary correction: suppress stale tracking on Smart `none`
  - [x] Confirmed `0.30.38` worsened rendered-video high-frequency motion versus raw/0.30.23 on draft `49` (`cut2_late`, `cut7`).
  - [x] Identified root cause: persisted asset-level point tracks (`tracked_object_index=-4`) were treated as current render intent for every cut, so Smart Camera `kind="none"` fell through to direct point-tracking crop.
  - [x] Restored schema-v3 `kind="none"` as a real no-move directive instead of deterministic fallback motion.
  - [x] Renderer now treats `kind="none"` as static Smart Camera composition and suppresses stale tracking crop + emotion zoompan for that cut.
  - [x] `kind="none"` did not set the reframed flag in `0.30.39`; later vidstab still ran as cleanup when stabilization was enabled. Superseded by T10 after the 11s canary exposed vidstab-induced drift.

- [x] T10 — 0.30.40 failed-canary correction: Smart `none` also skips vidstab
  - [x] Confirmed draft `49` around timeline `11.0–12.0s` regressed after render even though the raw asset window was already stable (`raw hf_p95 ~= 0.52`, rendered hf_p95 ~= 5.14).
  - [x] Identified root cause: `kind="none"` suppressed tracking/zoompan but still returned the static-crop flag, so the later vidstab pass added a compensation move on a low-texture/high-glare no-move cut.
  - [x] Renderer now treats Smart Camera `kind="none"` as a no-extra-correction decision for the stabilization skip flag too.
