# Capability: narration-script-generation

## ADDED Requirements

### Requirement: Documentary mode generates StoryScript from frame analysis

The system SHALL convert completed frame analysis into a markdown summary and use the configured text LLM path to generate a validated StoryScript for documentary narration.

#### Scenario: Frame analysis exists for an asset

- **WHEN** documentary mode plans a draft and finds an asset with completed frame analysis
- **THEN** it generates a StoryScript whose items reference valid asset source ranges
- **AND** generated narration text is treated as voice-over content with `audio_intent=narration` unless the script explicitly uses a supported intent

#### Scenario: Documentary LLM output is invalid

- **WHEN** the LLM output cannot be parsed into a valid StoryScript
- **THEN** the system uses a heuristic fallback StoryScript rather than failing the draft solely because of malformed LLM JSON

### Requirement: Drama explain mode generates StoryScript from transcripts

The system SHALL generate a drama-explanation StoryScript from existing asset transcripts, using a drama-focused prompt and the same StoryScript validation path.

#### Scenario: Transcripts are available

- **WHEN** `edit_mode=drama_explain` plans a draft
- **THEN** the system uses transcript segments and the project brief to generate a short-drama explanation StoryScript
- **AND** the resulting StoryScript is saved for preview, subtitles, and render reuse
