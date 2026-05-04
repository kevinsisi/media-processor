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

## 6. Memory + docs + version bumps

- [x] 6.1 `memory/v023_point_tracking.md` — covers all four gotchas (opencv must be in api image, modal commit math via state not getBoundingClientRect, no `transition: transform` on click target, overlay px math via renderRect not `% 100%`, dynamic crop + vidstab conflict).
- [x] 6.2 `memory/MEMORY.md` index entry refreshed for v0.23.4 final state.
- [x] 6.3 `ROADMAP.md` — Phase 9.8 section + sub-task headings + table row.
- [x] 6.4 `CLAUDE.md` — current-version, archive list, render pipeline pointers, asset model alembic chain.
- [x] 6.5 Version bumped to 0.23.0 / 0.23.1 / 0.23.2 / 0.23.3 / 0.23.4 in `pyproject.toml` + `src/media_processor/api/main.py` + `web/package.json` (one bump per release).
- [x] 6.6 Each release branched as `claude/v0.23.X-<topic>`, merged --no-ff into `main`, pushed; docker compose build + up -d on the dispatch host; `/health` smoke-tested.
