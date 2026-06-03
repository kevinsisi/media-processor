# Capability: tts-synthesis

## ADDED Requirements

### Requirement: Narrato modes can synthesize optional narration audio

The system SHALL synthesize StoryScript narration audio for `story`, `documentary`, and `drama_explain` renders when narration is enabled and a supported TTS provider is configured.

#### Scenario: TTS provider is configured

- **WHEN** narration is enabled for a StoryScript-backed render
- **THEN** items with `audio_intent=narration` or `audio_intent=narration_with_original` generate or reuse durable narration artifacts
- **AND** generated artifact duration is measured and used for timeline planning

#### Scenario: TTS provider is unavailable

- **WHEN** no TTS provider is configured or provider generation fails and fallback is allowed
- **THEN** rendering continues with subtitle-only narration and records per-item failure metadata where applicable

### Requirement: Edge TTS runtime dependency is available to workers

The worker runtime SHALL include the `edge-tts` Python dependency so `story_tts_provider=edge` can run without import failure.

#### Scenario: Edge provider is selected

- **WHEN** `story_tts_provider=edge` is configured
- **THEN** the worker can import `edge_tts` and synthesize narration without requiring a separate manual package install
