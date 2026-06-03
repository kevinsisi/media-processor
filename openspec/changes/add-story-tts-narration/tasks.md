## 1. Narration Artifact Persistence

- [x] 1.1 Define narration artifact schema/model with project, draft, story script, story item identity, narration text hash, provider, model, voice, status, error, file path, and measured duration fields
- [x] 1.2 Add database migration for narration artifacts and indexes needed for reuse/staleness lookup
- [x] 1.3 Add artifact path conventions under existing media storage without changing StoryScript JSON as the render artifact store
- [x] 1.4 Add stale-artifact detection based on text hash, provider, model, voice, and StoryScript item identity

## 2. TTS Provider Service

- [x] 2.1 Define a minimal provider interface for generating one narration audio file from text, voice, and settings
- [x] 2.2 Implement the first provider adapter using the selected deployment-safe TTS provider
- [ ] 2.3 Add per-item timeout, retry/backoff, and actionable error handling for provider calls
- [x] 2.4 Probe generated audio files with ffprobe or existing media utilities and persist measured duration in milliseconds
- [x] 2.5 Implement generation orchestration that reuses valid artifacts and regenerates missing or stale artifacts

## 3. Story Timeline And Renderer Integration

- [x] 3.1 Extend StoryScript-to-plan conversion to accept measured narration durations without changing non-story planner behavior
- [x] 3.2 Add timeline extension semantics so narration longer than source range extends visual output instead of clipping audio
- [x] 3.3 Implement renderer support for extended story visuals using the least invasive safe strategy, such as freeze-frame or looped content
- [x] 3.4 Add narration audio track assembly aligned to the generated draft timeline
- [x] 3.5 Mix narration, original source audio, and BGM according to `audio_intent` semantics
- [x] 3.6 Preserve subtitle cue alignment with measured narration-driven segment durations

## 4. API And Frontend Workflow

- [x] 4.1 Add render/request options to enable, disable, or fall back from generated narration audio for Story mode
- [ ] 4.2 Add API response fields or endpoint support for narration artifact status and retryable errors
- [x] 4.3 Update ProjectAnalysis Story/Narrato controls with Traditional Chinese copy for spoken narration availability, fallback, retry, and caption-only rendering
- [x] 4.4 Update ProjectEdit story-mode controls to show or configure narration generation without affecting standard/luxury/viral modes
- [x] 4.5 Ensure queue/watchdog/draft progress surfaces communicate narration-stage failures clearly

## 5. Tests And Verification

- [ ] 5.1 Add unit tests for narration artifact reuse, stale invalidation, and failed status recording
- [ ] 5.2 Add unit tests for provider timeout/error handling with mocked TTS calls
- [ ] 5.3 Add unit tests for measured-duration timeline extension and no hard-clipping of narration audio
- [ ] 5.4 Add renderer or service tests proving extended visuals produce video frames for the full narration duration
- [ ] 5.5 Add mix-policy tests for `narration`, `original`, and `narration_with_original`
- [ ] 5.6 Add API tests for narration-enabled story render, fallback behavior, and non-story mode non-regression
- [x] 5.7 Add frontend build or focused UI tests for narration controls and failure/fallback copy
- [x] 5.8 Run backend unit tests, frontend build, and OpenSpec strict validation before completion

## 6. Operational Documentation

- [x] 6.1 Document required TTS provider configuration and disabled-provider fallback behavior
- [x] 6.2 Document rollback path: disable narration generation while preserving subtitle-only Story mode
- [x] 6.3 Document known first-phase limitations such as no multi-speaker casting and no word-level karaoke timing
