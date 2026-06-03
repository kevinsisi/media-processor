## ADDED Requirements

### Requirement: StoryScript narration can drive generated audio
The system SHALL allow StoryScript narration text to drive optional generated narration audio while preserving existing subtitle-only story-mode rendering when narration audio is disabled.

#### Scenario: Render with generated narration enabled
- **WHEN** an operator renders a validated StoryScript with narration audio enabled
- **THEN** StoryScript items with narration audio intent produce generated narration audio and use it in the rendered draft

#### Scenario: Render with generated narration disabled
- **WHEN** an operator renders a validated StoryScript with narration audio disabled
- **THEN** Story mode keeps the existing behavior of rendering visual segments and narration subtitles without generated narration audio

### Requirement: StoryScript item identity supports narration artifact matching
The system SHALL provide a stable way to match narration artifacts to StoryScript items and detect stale artifacts after text or voice settings change.

#### Scenario: Item text changes after generation
- **WHEN** a StoryScript item narration text differs from the text used for an existing narration artifact
- **THEN** the system MUST NOT use the stale artifact for a new narration-enabled render
