## ADDED Requirements

### Requirement: Draft completion records trust outcome
The system SHALL synchronize draft terminal status with a trust outcome for every new render attempt.

#### Scenario: Render output succeeds with degradations
- **WHEN** a render job produces a playable output while one or more requested optional stages used fallback
- **THEN** the draft may be marked ready for review
- **AND** the draft trust report marks the output as degraded with visible events

#### Scenario: Required render stage fails
- **WHEN** a required planning, render, mux, or output-write stage fails
- **THEN** the draft is marked failed
- **AND** the trust report marks the failing stage as failed

#### Scenario: Worker exits stale job without mutation
- **WHEN** a stale render job exits because the draft is already terminal or superseded
- **THEN** the worker does not overwrite an existing trust report

### Requirement: Fallback policy is explicit
The system SHALL decide fallback behavior from render flags, edit mode, and stage criticality instead of catching exceptions silently.

#### Scenario: TTS fallback disabled
- **WHEN** Story/Narrato TTS is requested with fallback disabled and TTS generation fails
- **THEN** the draft render fails with an actionable trust report event

#### Scenario: Smart camera fallback occurs
- **WHEN** smart-camera planning fails but smart camera is optional for the selected render
- **THEN** the draft can continue only if a degradation event records the static-camera fallback

#### Scenario: BGM mix fallback occurs
- **WHEN** BGM mixing fails and the render continues without BGM
- **THEN** the draft trust report records the audio-stage degradation
