## ADDED Requirements

### Requirement: UI explains Story narration generation in beginner language
The Story/Narrato user interface SHALL explain generated narration, fallback behavior, retry actions, and provider errors in operator-facing Traditional Chinese rather than backend implementation terms.

#### Scenario: Narration generation is available
- **WHEN** narration audio can be generated for a StoryScript
- **THEN** the UI offers a clear publishing-oriented action such as generating a spoken narration version of the short video

#### Scenario: Narration generation is unavailable
- **WHEN** TTS provider configuration is missing or disabled
- **THEN** the UI explains that the video can still be generated with captions only and that spoken narration can be enabled later

#### Scenario: Narration generation fails
- **WHEN** TTS generation fails for one or more StoryScript items
- **THEN** the UI shows a clear failure reason and offers retry or caption-only render actions

### Requirement: UI previews narration impact before render
The Story/Narrato UI SHALL help the operator understand which story items will use generated narration, retained original audio, or both.

#### Scenario: StoryScript preview includes audio mode
- **WHEN** a StoryScript is shown before render
- **THEN** each item indicates whether it will use generated narration, original audio, or narration plus original audio
