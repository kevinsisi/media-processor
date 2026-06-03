## Why

Story/Narrato mode can now generate a narration script and render subtitles, but the final video still lacks generated spoken narration. To get closer to NarratoAI output quality without reintroducing the known failure mode where narration is hard-cut, the next phase needs real TTS audio, measured audio durations, and a timeline contract that can safely extend visual clips to match narration.

## What Changes

- Add TTS narration generation for StoryScript items whose `audio_intent` is `narration` or `narration_with_original`.
- Persist generated narration audio artifacts with provider/model/voice metadata and source StoryScript item identity.
- Measure actual generated audio duration and use it when building story-mode draft timelines.
- Extend visual segment duration when narration audio is longer than the source range, using safe loop/freeze/extension behavior instead of clipping narration.
- Mix narration, retained original audio, and BGM according to `audio_intent` without changing standard/luxury/viral edit behavior.
- Surface TTS generation status and actionable errors in the Story/Narrato UI.
- Keep TTS optional and provider-configured; Story mode must still render subtitle-only output when TTS is disabled or unavailable.

## Capabilities

### New Capabilities
- `story-tts-narration`: Generate, persist, measure, and render StoryScript narration audio while preserving audio intent semantics and safe timeline extension.

### Modified Capabilities
- `story-script-mode`: Story mode can render generated narration audio when enabled, while retaining the existing subtitle-only fallback.
- `workflow`: Story-mode render workflow includes optional narration-audio generation before cut/concat/mix stages and reports status/errors through existing draft progress surfaces.
- `novice-social-shorts-workflow`: Beginner-facing UI explains narration voice generation, fallback behavior, and how to retry or disable narration.

## Impact

- Backend services: StoryScript conversion, edit orchestrator, renderer/mixer integration, new TTS narration service, artifact lifecycle, settings/provider configuration.
- Data model: likely new narration artifact table or JSON artifact records tied to project/draft/story script item identity.
- API: endpoints or render options to enable/disable narration, inspect narration status, and retry failed narration generation.
- Frontend: ProjectAnalysis/ProjectEdit story-mode controls for voice generation status, fallback copy, and retry/disable actions.
- Runtime: may add provider dependencies or use existing HTTP provider patterns; no local GPU requirement.
- Tests: service tests for TTS artifacts, duration measurement, timeline extension, audio-intent mixing, API contract, and frontend build checks.
