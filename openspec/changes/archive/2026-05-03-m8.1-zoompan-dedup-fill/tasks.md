# Tasks — m8.1-zoompan-dedup-fill (0.14.1)

## 1. Renderer — zoompan freeze fix + smarter trigger

- [x] 1.1 Flip `d={total_frames}` to `d=1` inside `_zoompan_filter` so each input frame becomes one output frame (video keeps playing while zoom progresses).
- [x] 1.2 Add `ZOOMPAN_DYNAMIC_MOTIONS` mirror set + `_should_zoompan(cut)` helper combining emotion + (motion OR face).
- [x] 1.3 Replace the inline `if cut.dominant_emotion in ZOOMPAN_EMOTIONS` check in `_cut_segment` with `_should_zoompan(cut)`.
- [x] 1.4 Export `ZOOMPAN_DYNAMIC_MOTIONS` from `__all__`.

## 2. Planner — segment metadata propagation

- [x] 2.1 Add `dominant_motion` (default `"static"`) and `has_face` (default `False`) fields to `CutPlanSegment`.
- [x] 2.2 Add `asset_duration_ms` and `has_face` fields to `_AssetScore`.
- [x] 2.3 Add `_has_face_in_span(asset, span_ms)` helper that ignores the `dominant` sentinel row.
- [x] 2.4 Populate the new `_AssetScore` fields inside `_score_one_asset` via the `replace(...)` after parsing.
- [x] 2.5 Round-trip the new `CutPlanSegment` fields through `serialise_plan` / `deserialise_plan`.
- [x] 2.6 Populate the new fields on the `heuristic_fallback` cuts too.

## 3. Planner — `_assemble_plan` rewrite

- [x] 3.1 Add `_dedup_by_asset` (highest-score wins) called at the top of `_assemble_plan`.
- [x] 3.2 Add `_TRANSITION_OVERLAP_MS = 500` constant + `_effective_target_ms(target, num_chosen)` helper that biases the stop threshold up by total xfade overlap.
- [x] 3.3 Add a duration-fill pass that pulls from below-`MIN_KEEP_SCORE` non-skip rows first, then `position="skip"` rows.
- [x] 3.4 Add a span-extend pass via `_extended_span` (grows forward first, then backward) capped by `MAX_SPAN_MS` and the asset's actual duration.
- [x] 3.5 Materialise `CutPlanSegment` with `dominant_motion` + `has_face` carried from the chosen `_AssetScore`.

## 4. Frontend — upload page beforeunload guard

- [x] 4.1 Compute `pendingUploadCount` from rows in `queued` / `uploading` state.
- [x] 4.2 Register a `beforeunload` listener whenever `pendingUploadCount > 0`; unregister on cleanup.
- [x] 4.3 Use the zh-Hant message `「有 N 個影片還在上傳中，離開會放棄未完成的上傳。確定離開嗎？」` for the `returnValue`.

## 5. Tests

- [x] 5.1 `tests/unit/test_video_renderer.py` — pin `d=1`; four `_should_zoompan` cases (static no-face → false; static + face → true; dynamic motion → true; non-dynamic emotion → false).
- [x] 5.2 `tests/unit/test_edit_planner.py` — dedup-by-asset-id, duration-fill-from-dropped-pool, span-extend-when-pool-exhausted, motion+face-carry, serialise round-trip including new fields.
- [x] 5.3 Drive-by: shrink `test_plan_happy_path` target to 3 s so the new fill / extend passes don't trigger on the existing assertion.

## 6. Version + docs + deploy

- [x] 6.1 Bump version to `0.14.1` in `pyproject.toml`, `src/media_processor/api/main.py`, `web/package.json`, `web/package-lock.json`.
- [x] 6.2 Update auto-memory: refresh M8.1 entry to mention zoompan freeze fix + dedup/fill semantics.
- [x] 6.3 Run unit suite (121 passed / 7 skipped) + `cd web && npm run build`.
- [ ] 6.4 Commit + push to main worktree branch + rebuild + deploy.
