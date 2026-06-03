## Context

`add-narrato-story-mode` introduced a validated StoryScript layer and renders story-mode videos through the existing CutPlan/DraftSegment renderer path. In that first phase, `audio_intent=narration` and `narration_with_original` only produce narration subtitles; no spoken narration audio is generated. NarratoAI showed that real narration makes story-mode output much stronger, but it also exposed a critical timeline bug class: if generated narration is longer than the selected source span, clipping audio produces broken output, while extending the visual timeline requires explicit renderer semantics.

The current renderer already supports per-segment source audio gain, BGM mixing, subtitle burn-in, and draft artifacts. This phase should add narration audio as a native `media-processor` artifact and preserve the existing renderer/review/export workflow instead of importing NarratoAI's MoviePy path.

## Goals / Non-Goals

**Goals:**

- Generate TTS audio for StoryScript items that request narration.
- Store generated narration audio with enough metadata to reuse, retry, inspect, and invalidate it safely.
- Measure actual generated audio duration and use it as the authoritative narration duration.
- Extend story-mode visual timeline when narration is longer than the source range, without hard-clipping spoken audio.
- Mix narration, retained original audio, and BGM according to `audio_intent`.
- Keep Story/Narrato rendering usable without TTS by preserving subtitle-only fallback behavior.
- Surface TTS status and actionable failure messages in operator-facing Traditional Chinese.

**Non-Goals:**

- Do not replace the existing ffmpeg renderer, review UI, export flow, or draft model with NarratoAI runtime code.
- Do not make TTS mandatory for Story mode.
- Do not add sampled-frame visual analysis in this change.
- Do not add local GPU voice models in this change unless an existing lightweight provider path already supports them.
- Do not implement advanced voice acting controls, multi-speaker casting, or word-level karaoke timing in this phase.

## Decisions

### Decision: Add narration artifacts as durable records, not transient temp files

Generated narration audio should be stored as draft/story-script-item artifacts with provider, model, voice, text hash, source item identity, file path, duration, status, and error fields. A table such as `story_narration_assets` is preferred over embedding everything in `DraftSegment` because narration generation can be retried independently and reused across render retries.

Alternatives considered:

- Store only files in draft folders: simpler, but difficult to detect stale text/voice/provider changes and difficult to surface status in the UI.
- Store audio metadata only in `StoryScript.metadata`: keeps schema small, but mixes generated render artifacts with editable story content.

### Decision: Actual audio duration controls narration timeline

TTS services return approximate or no duration metadata, so generated files must be probed after writing. The measured audio duration is the source of truth for timeline extension. If narration duration exceeds the source clip duration, story-mode plan conversion should use the narration duration for the output timeline while preserving the original asset source range.

Alternatives considered:

- Estimate duration from text length: fast but unsafe; this is exactly how narration can be cut off.
- Always keep source range duration and let audio overflow: creates audio/video/subtitle desync.

### Decision: Extend visuals without changing source media semantics

For narration segments that are longer than their source range, the render path should extend the visual output by safely freezing the last frame or looping within the selected span, depending on which is already easier in the renderer. The first implementation should prefer the least invasive renderer change that prevents black tails and audio cuts.

Alternatives considered:

- Re-prompt StoryScript for longer source spans: useful later, but not reliable enough as the only protection.
- Trim narration to source span: explicitly rejected because it produces broken speech.

### Decision: Audio intent controls mixing defaults

`audio_intent=original` keeps source audio and does not generate narration. `audio_intent=narration` generates narration and mutes or ducks source audio for that segment. `audio_intent=narration_with_original` generates narration and keeps original audio under it at a ducked level. BGM remains governed by existing BGM mixer rules, with narration treated as voice content for ducking.

Alternatives considered:

- One global source-audio volume for all story segments: too blunt; it loses the meaning of StoryScript audio intent.
- Separate manual UI sliders only: useful after render, but the automated first render still needs correct defaults.

### Decision: Provider abstraction starts minimal

Use a small TTS provider interface that can support the first configured provider and later add more. Provider calls must have timeouts and actionable errors. The first provider can be chosen during implementation based on existing dependencies and deployment fit, but the contract must not hard-code a single vendor into StoryScript or renderer models.

Alternatives considered:

- Directly wire one provider into orchestrator: fastest, but makes retries, settings, and future provider changes harder.
- Build a full voice marketplace UI now: too broad for this phase.

## Risks / Trade-offs

- TTS provider latency or quota failures -> Persist per-item status and allow subtitle-only fallback or retry without corrupting the draft.
- Audio/video duration mismatch -> Probe generated audio and use measured duration for timeline planning.
- Black tails from visual extension -> Add renderer tests around extended narration segments and prefer freeze/loop behavior that always emits frames.
- Loud or muddy mixes -> Set conservative default source ducking for narration modes and reuse existing BGM ducking semantics where possible.
- Stale narration after script edits -> Invalidate by text hash, voice/provider/model settings, and StoryScript item identity.
- Cost surprise -> Make narration generation explicit or clearly indicated in story-mode controls; do not silently enable for non-story modes.

## Migration Plan

1. Add narration artifact persistence and migration without changing default render behavior.
2. Add TTS generation service and tests behind story-mode narration settings.
3. Add story-mode plan conversion support for narration durations while keeping subtitle-only fallback.
4. Add renderer/mixer support for narration tracks and extended visual output.
5. Add API/UI status, retry, and disable controls.
6. Deploy with TTS disabled or opt-in by default if provider configuration is missing.
7. Rollback by disabling narration generation; existing StoryScript subtitle-only rendering remains available.

## Open Questions

- Which first TTS provider should be enabled in production: Edge TTS, Gemini/Google TTS, OpenAI-compatible TTS, or another existing HomeProject provider?
- Should TTS be enabled by default for Story mode when provider config exists, or require an explicit checkbox per render?
- For overlong narration, should the first renderer behavior freeze last frame or loop the selected source span?
- Should narration artifacts be reusable across drafts for the same StoryScript item, or scoped strictly to a draft render?
