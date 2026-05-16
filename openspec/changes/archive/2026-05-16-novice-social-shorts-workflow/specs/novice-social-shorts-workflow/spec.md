## ADDED Requirements

### Requirement: Ready draft page prioritizes social publishing actions
The system SHALL present completed drafts around novice-friendly publishing actions before advanced editing controls.

#### Scenario: Draft is ready for review
- **WHEN** a user opens a ready draft in the edit page
- **THEN** the primary visible actions are preview, download, platform export, and regenerate

#### Scenario: User needs advanced controls
- **WHEN** a user wants to adjust settings, subtitles, or timeline details
- **THEN** advanced controls remain available but are visually secondary to the publishing actions

### Requirement: User-facing main workflow copy is beginner-friendly
The main upload, analysis, and edit workflow SHALL use operator-facing Traditional Chinese instead of implementation-heavy terminology.

#### Scenario: User reads render progress
- **WHEN** render progress is displayed
- **THEN** labels describe the outcome in beginner language, not backend tools or model names

### Requirement: Legacy review route does not expose stale workflow
The legacy review route SHALL not show placeholder preview or outdated review actions to users.

#### Scenario: User opens legacy review URL
- **WHEN** a user navigates to `/projects/:id/review`
- **THEN** the app sends them to the current edit/publishing workflow for that project
