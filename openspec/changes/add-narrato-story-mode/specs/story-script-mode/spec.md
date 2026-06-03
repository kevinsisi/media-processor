## ADDED Requirements

### Requirement: Story mode generates structured short-form scripts
The system SHALL provide a Story/Narrato mode that generates a validated short-form StoryScript from available transcript, subtitle, and optional visual-analysis inputs.

#### Scenario: Transcript inputs are available
- **WHEN** an operator requests Story/Narrato script generation for a project with analyzed transcript segments
- **THEN** the system generates a StoryScript containing ordered story items with source ranges, narration text, picture descriptions, audio intent, and beat metadata

#### Scenario: Uploaded subtitles are available
- **WHEN** an operator provides subtitle content for Story/Narrato script generation
- **THEN** the system can use those subtitles as the primary story input without requiring local GPU transcription

#### Scenario: Optional visual analysis is unavailable
- **WHEN** transcripts or subtitles are available but sampled-frame story analysis is unavailable
- **THEN** the system still attempts StoryScript generation and records that visual context was not used

### Requirement: StoryScript uses a versioned validated schema
The system SHALL validate generated StoryScript content against a versioned schema before allowing it to drive a render.

#### Scenario: Model returns valid StoryScript JSON
- **WHEN** the story model response contains all required fields with valid ranges and enums
- **THEN** the system persists the StoryScript with its schema version and makes it available for preview or rendering

#### Scenario: Model returns invalid StoryScript JSON
- **WHEN** the story model response cannot be repaired into the required schema
- **THEN** the system fails the generation step with an actionable error and MUST NOT create renderable draft segments from invalid content

#### Scenario: Generated ranges exceed source media
- **WHEN** a StoryScript item references a source range outside the associated asset duration
- **THEN** the system rejects or clamps the item according to validation rules and records the correction or failure

### Requirement: StoryScript converts to existing render plans
The system SHALL convert validated StoryScript items into the existing render planning model so current rendering stages remain the source of truth.

#### Scenario: Operator renders from StoryScript
- **WHEN** an operator starts rendering from a validated StoryScript
- **THEN** the system creates draft plan/segment data equivalent to a CutPlan and runs the existing render pipeline

#### Scenario: Story item keeps original audio
- **WHEN** a StoryScript item has audio intent `original`
- **THEN** the generated draft segment preserves the source audio intent for that segment

#### Scenario: Story item uses narration intent before TTS exists
- **WHEN** a StoryScript item has audio intent `narration` or `narration_with_original` and TTS is not enabled
- **THEN** the system still renders the visual segment and subtitle/script cue using existing renderer capabilities without requiring generated narration audio

### Requirement: Core Story mode does not require GPU analysis
The system SHALL allow Story/Narrato mode generation to proceed without local GPU-only analysis steps when sufficient transcript, subtitle, or external-provider inputs exist.

#### Scenario: Local GPU analysis is unavailable
- **WHEN** local Whisper, object tracking, emotion, or other GPU-backed analysis is unavailable
- **THEN** Story/Narrato mode can still generate a script from external ASR, uploaded subtitles, or existing text inputs

#### Scenario: Optional local analysis is available
- **WHEN** local tracking, emotion, or scene analysis exists for the project
- **THEN** the system may include those signals as enhancement context without making them mandatory
