## ADDED Requirements

### Requirement: Story mode uses beginner-friendly social-short language
The Story/Narrato mode user interface SHALL describe analysis, subtitles, scripts, and render actions in operator-facing Traditional Chinese rather than backend implementation terms.

#### Scenario: StoryScript is ready
- **WHEN** a generated StoryScript is available for preview
- **THEN** the UI describes it as a short-video script or narration plan and shows actions to preview, edit, regenerate, and render

#### Scenario: Story generation lacks optional analysis
- **WHEN** Story/Narrato generation proceeds without optional visual or local GPU analysis
- **THEN** the UI explains that the script can still be generated and that advanced analysis may improve visual matching

#### Scenario: Draft from story mode is ready
- **WHEN** a Story/Narrato draft is ready for review
- **THEN** the primary visible actions remain preview, download, platform export, regenerate, and publish-oriented next steps
