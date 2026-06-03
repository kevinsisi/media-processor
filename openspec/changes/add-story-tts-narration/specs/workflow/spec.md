## ADDED Requirements

### Requirement: Story render workflow includes optional narration generation
The story-mode render workflow SHALL include an optional narration generation stage before final audio mixing when generated narration is enabled.

#### Scenario: Narration stage succeeds
- **WHEN** all required narration artifacts are generated or reused successfully
- **THEN** the workflow continues through existing cut, concat, subtitle, BGM, review, and export stages with narration audio included

#### Scenario: Narration stage falls back
- **WHEN** narration generation is disabled or provider configuration is missing
- **THEN** the workflow continues through the existing subtitle-only Story/Narrato render path

#### Scenario: Narration stage fails
- **WHEN** narration generation fails and fallback is not allowed for the current render
- **THEN** the draft records an actionable failure state and MUST NOT create a misleading ready-for-review video without the requested narration

### Requirement: Existing non-story workflows are unchanged
The system SHALL NOT change standard, luxury-auto, viral-short, skip-plan re-render, subtitle rebuild, export, or review behavior unless a draft is explicitly rendered with Story/Narrato narration audio.

#### Scenario: Standard render requested
- **WHEN** an operator starts a standard, luxury-auto, or viral-short render
- **THEN** the system uses the current render workflow without generating StoryScript narration audio
