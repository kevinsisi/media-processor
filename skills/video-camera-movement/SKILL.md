---
name: video-camera-movement
description: Use whenever designing, reviewing, or debugging video camera motion, AI Smart Camera, auto-reframe behavior, crop paths, pan/tilt/zoom/dolly choices, or AI-generated focus/camera directives. This skill converts cinematography principles into conservative, user-intent-preserving rules: camera movement must be motivated, explicit subject tracking stays primary, zoom is not a fallback, and uncertain shots should stay static.
related_code: src/media_processor/services/smart_camera_planner.py; src/media_processor/services/video_renderer.py; src/media_processor/services/auto_reframe.py
---

# Video Camera Movement

Use this skill to decide whether a video cut should move at all, what kind of movement is appropriate, and how to keep AI-driven reframing from fighting the operator's intent.

## Operating stance

- Start from `why move?`, not `which move?`.
- Treat a static shot as a valid, often best, answer when the frame is already readable.
- Prefer one clear move over layered moves.
- Prefer less motion over motion that calls attention to itself.
- Preserve explicit user intent before improving composition.
- Do not make movement visible just to prove the feature exists.

## Decision sequence

1. Identify operator intent. Point tracking, custom ROI, and user-picked object tracking define the target the viewer expects to keep watching.
2. Identify the story reason. Valid reasons include following subject motion, revealing new information, emphasizing a face/product/detail, connecting two subjects, showing scale, matching an emotional beat, or landing on a music/edit beat.
3. Check visual evidence across the cut. Require a clear primary target, deliberate start/end targets, or stable subject motion. If evidence is weak, contradictory, or only created by noisy saliency boxes, keep the shot static.
4. Choose exactly one movement family. Do not compose independent tracking, zoompan, stabilization, and saliency paths unless there is one final crop path that can be verified frame by frame.
5. Verify viewer comfort. Reject movement that produces micro-jitter, crop hunting, repeated direction changes, or a target switch the user did not ask for.

## Movement Grammar

- `static`: Default for stable composition, explicit tracking targets, ambiguous salience, talking-head continuity, or interiors/details where movement adds no information.
- `pan` / digital horizontal reframe: Use to follow real horizontal subject motion or reveal a meaningful relationship between separated subjects. Start and end on semantically important targets, not arbitrary clusters.
- `tilt` / digital vertical reframe: Use for vertical reveals, tall subjects, floor-to-ceiling context, or a real subject moving vertically. Do not use as a generic fallback.
- `zoom_in`: Use to emphasize an already-recognized subject, face, product, text, or decisive detail. It should feel like intentional attention, not breathing or drift.
- `zoom_out`: Use to reveal context, scale, environment, or an ending beat after a detail has been established.
- `dolly` / tracking simulation: Use only when the intended effect is following a subject through space. In digital crop systems, remember this is not true perspective movement; avoid claiming dolly behavior when the system only zooms/crops.
- `handheld` / shake: Do not synthesize this in Smart Camera. Real handheld may be stabilized or preserved intentionally, but artificial shake is almost always a regression.

## Smart Camera Rules

- No forced fallback motion. If Vision returns no clear move, output no move.
- Persist no-move as an explicit analysed result, not as a missing directive; missing means not analysed, `kind="none"` means analysed and intentionally static.
- Do not convert `no move` into lateral pan, tilt pan, or zoom just to avoid a null directive.
- Do not infer pan from multiple focus clusters unless their time order forms a meaningful visual sentence.
- Do not zoom into low-salience background, walls, furniture, or interior details unless the script/user intent explicitly makes that detail important.
- Do not amplify tiny crop deltas. If motion is below the threshold where it reads as intentional, keep it static rather than creating subtle drift.
- Do not stack Smart Camera over explicit tracking unless the implementation produces a single stable crop path and frame-by-frame metrics prove it does not jitter.
- Do not run stabilization over already-reframed AI motion unless the stabilization step is proven not to counteract the intended move.
- Explicit point / ROI / picked-object tracking must feel like digital stabilization, not raw tracker lock. Preserve the stable v0.30.22-like viewer comfort by smoothing high-frequency crop-path jitter before writing per-frame crop commands.
- If frame-by-frame output analysis still shows high-frequency background/rotation shake after crop-path smoothing, apply a tracking-aware post-stabilization pass to explicit tracking cuts instead of further increasing Smart Camera motion or raw tracker gain.
- Tracking-aware post-stabilization is an expensive fallback, not the default production path. Only enable it behind a bounded/focused gate; never brute-force every explicit tracking cut in a normal render.
- Tracking-aware post-stabilization must be accepted per cut based on measured output jitter, including adjacent-frame high-percentile spike checks. Reject candidates that improve p95 but introduce a single-frame shove.
- If post-stabilization still leaves visible micro-jitter, measured source-motion-compensated crop candidates may be tested offline or behind a bounded gate. Prefer candidate selection over one global magic stabilizer setting, but do not ship unbounded multi-candidate searches.
- Low-pass tracking crop candidates may improve viewer comfort, but must be bounded by a target-drift guard against the normal explicit-tracking crop so they do not quietly stop following the user's requested point/ROI/object.

## Priority Rules

- Explicit point tracking, custom ROI, and user-picked object tracking have highest priority. Smart Camera may annotate or skip, but must not change the viewer's target.
- Automatic YOLO tracking is a helper. Smart Camera may override it when saliency and story evidence are stronger.
- Static aspect crop has no temporal intent. Smart Camera may replace or adjust it when a motivated directive exists.
- Emotion zoompan is weaker than explicit visual evidence. Smart Camera may replace it, but only with a motivated move.
- If two systems want different targets, do not blend them. Pick the higher-priority target or keep the shot static.

## Prompt Design

- Ask the vision model for the reason a movement is needed, not only boxes.
- Allow `none` as a first-class answer and tell the model to choose it when no motivated movement exists.
- Ask for target roles such as `person`, `face`, `product`, `text`, `action`, `environment`, or `background_detail`.
- Ask for confidence and reject low-confidence moves locally.
- Ensure sampled frame labels match actual timestamps. Do not label a 75% sample as 100% or treat it as the cut endpoint.
- Prefer outputs that describe one intended viewer attention path over many independent saliency boxes.

## Diagnostics Checklist

- Overlay the vision focus boxes, final render crop window, and user tracking point/ROI on the same cut frames.
- Measure frame-to-frame crop center and zoom deltas; inspect spikes and low-amplitude jitter numerically.
- Compare raw footage motion and output motion at the same scaled width before blaming the source asset.
- Verify the implemented mutex order against the intended order: explicit tracking > Smart Camera > automatic YOLO > emotion zoompan.
- Confirm whether stabilization is skipped for dynamically reframed cuts.
- For explicit tracking cuts, confirm crop-path smoothing/deadband is active if vidstab is skipped.
- For explicit tracking cuts that receive post-stabilization, measure actual output optical-flow jitter and adjacent-frame step jitter, including p99/near-maximum spikes, not only crop-command deltas or whole-cut p95.
- For explicit tracking cuts with measured steady-crop candidates, verify the accepted path improves actual output jitter and still preserves the requested target.
- For low-pass tracking crop candidates, verify the target-drift guard against the baseline crop path before trusting output jitter alone.
- For post-stabilized tracking cuts, compare before/after jitter per cut and reject regressions instead of relying only on aggregate draft improvement.
- For multi-preset post-stabilized tracking cuts, verify logs show the accepted preset and compare actual output scores, not assumed stabilizer strength.
- For source-motion-compensated tracking cuts, verify the accepted candidate through actual output jitter metrics and logs showing accepted/rejected candidates.
- Confirm frame samples correspond to the real start, middle, and end of the cut.
- Re-render a representative failing draft and inspect both the video and the crop-motion metrics.

## Anti-Patterns

- Every cut gets a visible move.
- Vision returned no move, so the system adds fallback pan/tilt/zoom.
- Smart Camera follows a different subject than the user's point/ROI target.
- Multiple saliency clusters automatically become a pan.
- Low-salience interior detail becomes a zoom target.
- Independent tracking and zoompan filters are chained without one verified final crop path.
- Movement gain is increased until the feature is noticeable.
- A cut is considered fixed because it looks okay once by eye, without frame-delta checks.

## Reporting Format

When proposing or reviewing camera movement logic, report:

- Intended user-visible behavior.
- Priority/mutex rule.
- Confidence gate.
- Fallback behavior.
- Verification method.

## Source Notes

- StudioBinder, `50+ Types of Camera Shots, Angles, and Techniques`: shot size, framing, angle, movement, lens, composition, and focus together shape perception, pacing, tone, continuity, and spatial relationships. Source: https://www.studiobinder.com/blog/ultimate-guide-to-camera-shots/
- StudioBinder, `Guide to Camera Shots: Every Shot Size Explained`: shot size and movement choices communicate narrative values, character motivation, tone, and setting. Source: https://www.studiobinder.com/blog/types-of-camera-shots-sizes-in-film/
- Boords, `The 16 Types of Camera Shots & Angles`: camera moves influence viewer emotion and immersion; zoom can be useful but should not be the default; pan, tilt, dolly, truck, and pedestal each carry distinct purposes. Source: https://boords.com/blog/16-types-of-camera-shots-and-angles-with-gifs
- Robert C. Morton, `Role of Camera Movements In Crafting Powerful Visuals & Tales`: movement guides attention, reveals information, establishes geography, affects pacing/rhythm, and can reflect character emotion. Source: https://www.robertcmorton.com/camera-movement/
- Wikipedia, `Cinematography`: glossary-level reference for cinematography aspects including aspect ratio, framing, lens, focus, lighting, and camera movement. Use only as broad orientation, not as the primary authority. Source: https://en.wikipedia.org/wiki/Cinematography
