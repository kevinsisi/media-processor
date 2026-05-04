# Tasks — v0.23-pixel-precise-point-tracking (0.23.0 – 0.23.4)

## 1. Lucas-Kanade pixel-precise tracking pipeline (0.23.0)

- [x] 1.1 `services/point_tracking.track_point(media_path, *, init_x, init_y, init_t_ms, duration_ms)` — pyramidal Lucas-Kanade with `LK_WIN_SIZE=(21, 21)`, `LK_MAX_LEVEL=3`, `LK_MAX_ERR=50.0`. Bidirectional pass (forward init→end, backward init→0); freezes at last good position on `lost` so Kalman sees a continuous measurement series.
- [x] 1.2 `TRACKING_FAKE=1` test seam — emits a deterministic stub trace (constant point at `init_xy` at fps=30) so CI / non-OpenCV dev hosts can exercise the persistence + render path.
- [x] 1.3 `services/auto_reframe.compute_crop_path_from_point_track(point_track, *, target_aspect, asset_start_ms, asset_end_ms, src_w=None, src_h=None)` — synthesises 1×1 bbox per LK frame so the existing `compute_crop_path` centre-of-bbox math + Kalman + max-delta clamp + crop_dimensions code stays unchanged.
- [x] 1.4 `services/video_renderer._cut_segment` dispatch — `point_track → custom_roi → YOLO`, matching the `tracked_object_index` sentinel order. New `point_track` kwarg flows from `cut_segments` down.
- [x] 1.5 `services/edit_orchestrator` loads `Asset.point_tracking_json` into `point_track_by_asset` and passes it through to `video_renderer.render`.
- [x] 1.6 `Asset.point_tracking_json: Mapped[Any] | None` (JSON column) — alembic `0021_asset_point_tracking` chains after `0020_watermark_presets`.
- [x] 1.7 `Asset.point_tracking_origin: Mapped[Any] | None` — verbatim user click `{x, y, frame_ms, norm_x, norm_y}` so the picker can re-render a crosshair on any thumbnail size.
- [x] 1.8 `tracked_object_index = -4` sentinel — extends the existing `null=auto / ≥0=YOLO / -1=custom_roi / -2=fixed / -3=off` set.
- [x] 1.9 API: `PATCH /assets/{id}/tracking-target` `mode: "point"` with body `{point: {norm_x, norm_y, frame_ms}}`. `_asset_native_resolution(asset)` resolves source dims; backend multiplies norm × resolution to seed LK with pixel coords. Returns 409 if `Asset.resolution` is missing (analysis must run first).
- [x] 1.10 Run `track_point` synchronously via `asyncio.to_thread`; 10-second-or-so wall-clock on a typical 5-10 s asset.
- [x] 1.11 opencv-python-headless added to api Dockerfile (was worker-only). Without it the sync endpoint 500s with `ModuleNotFoundError: cv2`.
- [x] 1.12 `TrackingDetailOut.has_point_track` + `TrackingDetailOut.point_tracking_origin` schema fields surface presence to the picker.
- [x] 1.13 Tests: `tests/unit/test_point_tracking.py` (FAKE path stub shape; bidirectional ordering; clamp to src bounds).
- [x] 1.14 Tests: `tests/unit/test_auto_reframe.py` covers `compute_crop_path_from_point_track` 1×1 wrapping + delegation to `compute_crop_path`.

## 2. Full-screen PointPickerModal (0.23.1)

- [x] 2.1 `web/src/components/PointPickerModal.tsx` — fullscreen fixed modal, `grid-template-rows: auto 1fr auto` for header / stage / footer.
- [x] 2.2 Pinch zoom (two-finger distance ratio) + wheel zoom (focal-point-anchored, zooms around mouse position) + drag pan, centre-anchored CSS `transform: translate(panX, panY) scale(zoom)`.
- [x] 2.3 Drag-vs-click discrimination: `DRAG_THRESHOLD_PX = 4`; `pointermove` accumulates and only counts as drag if total displacement ≥ threshold.
- [x] 2.4 Cancel paths: backdrop click, Esc, cancel button — all close without committing. Single click within image bounds emits norm coords.
- [x] 2.5 `onConfirm: (norm) => Promise<void>` — modal shows a busy spinner until the promise resolves / rejects, so the operator gets feedback during the LK run.
- [x] 2.6 `PointPickerModal.css` — full-screen layout + `touch-action: none` on stage so the browser doesn't intercept pinch / pan and try to zoom the page.

## 3. Modal commit math fix (0.23.2)

- [x] 3.1 Drop `transition: transform 80ms ease-out` from `.point-picker-modal__img` — a click landing mid-zoom-animation otherwise sees the partway-through bounding rect, not the final one. Keep `will-change: transform` for compositor hint.
- [x] 3.2 `fittedImageSize(naturalW, naturalH, containerW, containerH)` helper — mirrors browser's `max-width: 100%; max-height: 100%; object-fit: contain` semantics: keeps natural size unless either dim exceeds container, otherwise scales preserving aspect.
- [x] 3.3 `visibleImageRect(stage, natW, natH, zoom, pan)` helper — on-screen image rect from CSS state: `cx - w/2 + pan.x` (centre-anchored scale + translate, same model the wheel/pinch handlers use to anchor zoom).
- [x] 3.4 `onPointerUp` rewritten to use `visibleImageRect()` instead of `imgRef.current.getBoundingClientRect()`. useCallback deps gain `zoom, pan` so the closure sees current transform state on every click.

## 4. Crosshair display math fix (0.23.3)

- [x] 4.1 `AssetTrackingTarget.tsx` crosshair: replace `left: ${norm_x * 100}%` with `left: ${norm_x * renderRect.renderedW + renderRect.offsetX}px` (and `top` likewise) — matches the bbox overlay's `cssBoxFor` math, accounts for object-fit:contain letterbox bars.
- [x] 4.2 Render guard tightened to `activeMode === "point" && detail.point_tracking_origin && renderRect && (...)` so the crosshair doesn't render before the canvas has been measured.

## 5. Auto-reframe + vidstab conflict fix (0.23.4)

- [x] 5.1 `_cut_segment` returns `bool` — `True` when a dynamic `crop@reframe` chain was applied (point/custom_roi/YOLO with `crop_path is not None`), `False` when the static aspect filter was used.
- [x] 5.2 `cut_segments` returns `(list[Path], list[bool])` — the bool list is parallel to the path list, signals which segments are already subject-stabilised.
- [x] 5.3 `stabilize_segments` gains `skip_indexes: set[int] | None` kwarg. Indexes in the set are returned at their pre-stabilisation path so the concat list keeps the same order + length.
- [x] 5.4 `render()` plumbs the bool list into `skip_indexes`: `{i for i, r in enumerate(reframed_flags) if r}`.
- [x] 5.5 Test update: `tests/unit/test_video_renderer.py::test_cut_segments_writes_intermediates` unpacks the new tuple return and asserts `reframed_flags == [False, False]` for the no-tracking case.
- [x] 5.6 Verified live: re-rendered draft 40 (project 4) at 0.23.4. Lamborghini badge centred at output time 25s, 26s, 28s in v15.mp4 — symptom gone.

## 6. Sendcmd duplicate-timestamp dispatcher fix (0.23.5)

- [x] 6.1 `services/auto_reframe.write_sendcmd_file` rewritten to emit ONE directive per timestamp with `,` separating the x and y commands (`0.0000 crop@reframe x 264, crop@reframe y 436;`) instead of two same-timestamp directives. Halves the dispatch rate from 60 Hz to 30 Hz, sidestepping the ffmpeg 4.4 sendcmd dispatcher quirk that silently drops the second-and-onward directive when multiple share a start_time at high rate. Inline comment in the function explains the trap so a future cleanup doesn't naively split them back onto two lines.
- [x] 6.2 Diagnostic isolation that nailed the cause: piped the production sendcmd file straight into `ffmpeg -vf "sendcmd=…,crop@reframe=…,scale=…,setsar=1"` outside the orchestrator pipeline (rules out vidstab, concat, BGM mix, watermark) — same offset reproduced. Then sparse versions at 1 Hz / 3 Hz / 10 Hz / 15 Hz with the same two-line-per-timestamp format all WORK; 30 Hz two-lines fails; 30 Hz one-line (x only) works. Combination = high-rate dispatch + duplicate timestamps is the trigger.
- [x] 6.3 Verified live: re-rendered draft 41 / v16.mp4 at 0.23.5. Lamborghini badge centred at output time 17 s, 19 s, 20 s (was offset ~36 % from left at t=20 s on v0.23.4 even with the vidstab skip). Subject no longer drifts during the segment.

## 7. Bbox-centre rounding (0.23.6)

- [x] 7.1 `compute_crop_path_from_point_track` synthesises a degenerate `(int(round(x)), int(round(y)), 0, 0)` bbox per LK frame instead of `(int(x-0.5), int(y-0.5), 1, 1)`. Pre-fix the `int(x-0.5)` floor on a fractional LK output gave a centre 1 px LEFT of the tracked pixel; the `+ 1//2 = 0` add-back didn't correct it. Sub-pixel on its own but enough at crop_zoom=0.75 to make long pans look "almost but not quite centred."

## 8. Rotation-aware norm→pixel resolution (0.23.7)

- [x] 8.1 `services/point_tracking.track_point` signature changed to take `init_norm_x` / `init_norm_y` (0..1 normalised) instead of `init_x` / `init_y` pixel coords. The function opens `cv2.VideoCapture`, reads `CAP_PROP_FRAME_WIDTH/HEIGHT` (post-rotation, because OpenCV 4.13 defaults `CAP_PROP_ORIENTATION_AUTO=1`), and resolves to pixel coords there. Single source of truth for the seed→pixel mapping.
- [x] 8.2 `track_point` returns the resolved pixel coords in `result["init"]["x"]` / `["y"]` so the API endpoint can mirror them into `Asset.point_tracking_origin` for the FE crosshair.
- [x] 8.3 API endpoint (`PATCH /assets/{id}/tracking-target` mode=point) drops the `_asset_native_resolution` lookup, passes `norm_x` / `norm_y` straight through to `track_point`, and reads pixel coords back from the LK result.
- [x] 8.4 FAKE-path stub uses 1920×1080 as the default display resolution so existing tests' assertion shape is preserved.
- [x] 8.5 Diagnosis path that surfaced the bug: traced every coordinate hop end-to-end (origin x/y, point_tracking_json src_w/src_h, cv2 dims) for every point-tracked asset on the live project; spotted that asset 18 was the only one where `Asset.resolution.W = 3840` mismatched `point_tracking_json.src_w = 2160`. ffprobe `-show_streams` confirmed `TAG:rotate=270` + `Display Matrix rotation=90` on the asset. Other 9 assets in the project are natively portrait (no rotation), which is why the bug only surfaced on the front-of-car shot.
- [x] 8.6 Verified live: re-picked + re-rendered draft 41 / v16.mp4 at 0.23.7. Front-of-car (asset 18) segment now centres on the user's clicked point throughout (was ~38 % from left at all timestamps within the segment on v0.23.6).
- [x] 8.7 Tests: `tests/unit/test_point_tracking.py` migrated to the new `init_norm_x` / `init_norm_y` signature; behavioural assertions unchanged.
- [x] 8.8 Migration note (no code change required, FE-driven): existing `point_tracking_json` rows for rotated assets store an init pixel in the wrong coord space; user re-picks through the UI to refresh. Non-rotated assets are unaffected.

## 9. Memory + docs + version bumps

- [x] 9.1 `memory/v023_point_tracking.md` — covers all six gotchas (opencv must be in api image, modal commit math via state not getBoundingClientRect, no `transition: transform` on click target, overlay px math via renderRect not `% 100%`, dynamic crop + vidstab conflict, sendcmd duplicate-timestamp dispatcher quirk, rotation-aware norm→pixel via cv2 dims).
- [x] 9.2 `memory/MEMORY.md` + `project_media_processor_v2.md` snapshot refreshed through v0.23.7.
- [x] 9.3 `ROADMAP.md` — Phase 9.8 section with seven sub-task subsections (9.8.1–9.8.7) + table row updated.
- [x] 9.4 `CLAUDE.md` — current-version line; `services/point_tracking.py` pointer notes the cv2-dim seed contract.
- [x] 9.5 Version bumped through 0.23.0 / 0.23.1 / 0.23.2 / 0.23.3 / 0.23.4 / 0.23.5 / 0.23.6 / 0.23.7 in `pyproject.toml` + `src/media_processor/api/main.py` + `web/package.json` (one bump per release).
- [x] 9.6 Each release branched as `claude/v0.23.X-<topic>`, merged --no-ff into `main`, pushed; docker compose build + up -d on the dispatch host; `/health` smoke-tested. Branches pruned local + remote after merge.
