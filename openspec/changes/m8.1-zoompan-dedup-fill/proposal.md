## Why

The M8.1 emotion + zoompan stage shipped in 0.14.0 surfaced three production-visible failure modes that a two-line fix can't address — they need surgery on the planner's assembly pass and the renderer's zoompan filter:

1. **Renders look "frozen".** `_zoompan_filter` was emitting `d=total_frames`, which is the Ken-Burns "zoom on a still photo" mode: ffmpeg holds the first input frame for the entire clip duration, so the underlying video stops playing while the zoom progresses. Users saw a 5-second photo with a slow Ken Burns effect instead of the intended 5-second moving clip with a slow zoom layered on top.
2. **Zoompan kicks in on static, faceless clips.** Even after fixing the freeze, the gate `dominant_emotion ∈ {happy, surprised}` fires on assets that scored as "happy" but whose chosen span has neither camera movement nor a visible face — e.g. a product shot that happens to share an asset with a smiling face elsewhere. The result is a slow zoom on essentially still video, which still reads as artificial.
3. **Reels are too short and contain duplicate content.** When an auto-target lands at 60-180 s but the per-asset best-spans only sum to 20 s of high-score content, the planner emits a too-short reel. Worse, a defensive bug across rerolls / future shapes can produce two cuts pointing at the same `asset_id`, which prints as "duplicate content" in the rendered preview.

A separate UX papercut: the upload page lets users navigate away mid-upload and silently abandon in-flight chunked uploads with no warning.

## What Changes

### 1. Renderer — zoompan freeze fix (the actual bug)

- `_zoompan_filter` now emits `d=1` so each input frame produces one output frame; the underlying video keeps playing while zoom progresses from `1.0 → ZOOMPAN_END_ZOOM (1.15)`. The per-frame increment math is unchanged (over `total_frames` output frames the zoom still lands at `ZOOMPAN_END_ZOOM`); only the `d=` value flipped.
- New `test_zoompan_filter_uses_d_eq_one_to_avoid_freeze` pins the value so the next regression is caught at PR time.

### 2. Renderer — zoompan trigger now requires actual movement OR a face

- New `_should_zoompan(cut)` helper combines three signals: `dominant_emotion ∈ ZOOMPAN_EMOTIONS`, AND (`dominant_motion ∈ ZOOMPAN_DYNAMIC_MOTIONS` OR `has_face`). Only when both clauses hold does the segment get the zoompan chain.
- Without the second clause we'd zoom on static, faceless clips and the result reads as a frozen photo no matter how the underlying filter is configured.

### 3. Planner — propagate motion + face presence to the segment

- `CutPlanSegment` gains `dominant_motion: str` (default `"static"`) and `has_face: bool` (default `False`). Both round-trip through `serialise_plan` / `deserialise_plan` so the M7.1 skip-plan path keeps zoompan metadata across reorders.
- `_AssetScore` carries the same fields plus `asset_duration_ms` (needed by the new span-extend pass). `_score_one_asset` populates them server-side from the asset's tag rows — `_dominant_motion_for_span` already existed; new `_has_face_in_span` checks whether any non-`dominant` emotion-tag range overlaps the chosen `best_span_ms`.
- `heuristic_fallback` populates the same fields so the renderer treats fallback drafts identically.

### 4. Planner — `_assemble_plan` rewrite (dedup + duration-fill)

- **Dedup pass** (defensive). `_dedup_by_asset` collapses multiple `_AssetScore` rows for the same asset into the highest-scoring one, so duplicates from future shapes (multi-span scoring, malformed serialised plans fed back through the path) can never produce two cuts pointing at the same asset.
- **Duration-fill pass.** When the primary bucketed pass leaves the timeline short of target, pull from the dropped pool sorted by score: below-`MIN_KEEP_SCORE` non-skip rows first, then `position="skip"` rows as a last resort. Better to ship a mediocre 60-second reel than a polished 12-second one.
- **Span-extend pass.** If the dropped pool is also exhausted and we're still short, stretch each chosen span up to `MAX_SPAN_MS` and the asset's actual duration. New `_extended_span` helper grows the span forwards first (more natural for talking-head footage), then backwards if room remains.
- **Effective-target accounting.** New `_effective_target_ms(target, num_chosen)` biases the stop-threshold up by `num_chosen × _TRANSITION_OVERLAP_MS (500)` so the rendered timeline (which xfade-overlaps adjacent cuts) lands at the user's intended target rather than ~5–15 % short.

### 5. Frontend — upload-page beforeunload guard

- `pages/Upload.tsx` registers a `beforeunload` listener whenever any video row is in `queued` or `uploading` state. The browser's native confirm dialog fires with "有 N 個影片還在上傳中，離開會放棄未完成的上傳。確定離開嗎？" so users can't silently abandon mid-upload.
- Listener registers/unregisters based on `pendingUploadCount` so clean navigation (no in-flight uploads) stays unblocked.

### 6. Tests

- `tests/unit/test_video_renderer.py` — `d=1` invariant + four `_should_zoompan` truth-table cases (static + no face → false; static + face → true; dynamic motion → true; non-dynamic emotion → false regardless).
- `tests/unit/test_edit_planner.py` — dedup-by-asset-id, duration-fill-from-dropped-pool, span-extend-when-pool-exhausted, motion+face-flow-to-segment, serialise round-trip including new fields.
- Existing `test_plan_happy_path` updated: target shrunk to 3 s so the new fill / extend passes don't kick in for the original assertion.

## Impact

- **Rendered output.** Zoompan no longer freezes; only fires on motion-OR-face clips. Shorter shoots can no longer ship a 12-second reel against a 60-second target.
- **Storage / API contract.** `Draft.cut_plan_json` gains `dominant_motion` + `has_face` fields per segment. Defaults are safe so older serialised plans stay loadable and just won't get zoompan (the conservative outcome).
- **No new dependencies.** Pure Python + ffmpeg-filter changes.
- **No DB migration.** Both new fields live inside the existing `cut_plan_json` blob.
