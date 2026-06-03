# Capability: narration-render

## ADDED Requirements

### Requirement: Narration audio is mixed into StoryScript-backed renders

The renderer workflow SHALL mix generated narration artifacts into the rendered MP4 after visual rendering and before optional BGM mixing for StoryScript-backed modes.

#### Scenario: Narration clips exist

- **WHEN** a rendered plan contains `narration_audio_path` values
- **THEN** the BGM stage first overlays those narration clips at their timeline positions
- **AND** source audio gain follows each segment's `audio_intent` policy before optional BGM is mixed

#### Scenario: Narration mix fails with fallback enabled

- **WHEN** narration audio mixing fails and narration fallback is enabled
- **THEN** the draft keeps the subtitle-only MP4 instead of failing the full render

### Requirement: New Narrato edit modes are valid API and UI values

The system SHALL accept and preserve `documentary` and `drama_explain` as draft-scoped `edit_mode` values in API requests, queued render jobs, draft summaries, and the ProjectEdit mode picker.

#### Scenario: Documentary mode is selected from ProjectEdit

- **WHEN** an operator selects `紀錄片解說` and starts a render
- **THEN** the frontend sends `edit_mode=documentary`
- **AND** the backend accepts the request, snapshots the mode, and returns it in draft summaries

#### Scenario: Drama explain mode is selected from ProjectEdit

- **WHEN** an operator selects `短劇解說` and starts a render
- **THEN** the frontend sends `edit_mode=drama_explain`
- **AND** the backend accepts the request, snapshots the mode, and returns it in draft summaries
