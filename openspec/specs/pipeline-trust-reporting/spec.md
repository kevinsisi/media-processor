# pipeline-trust-reporting Specification

## Purpose

Specify draft trust reporting so generated videos expose whether requested pipeline stages completed as planned, degraded with explicit fallback, failed, or have no evidence available.

## Requirements

### Requirement: Draft trust report

The system SHALL create a trust report for every new draft render attempt that reaches planning or rendering.

#### Scenario: Fully planned draft finishes

- **WHEN** all requested render stages complete without fallback or output-changing degradation
- **THEN** the draft trust report records trust status `planned`
- **AND** the report contains stage outcomes for planning, stabilization selection, tracking/camera, subtitles, audio mix, and render output

#### Scenario: Draft finishes with allowed fallback

- **WHEN** a requested optional stage fails and an allowed fallback is used to produce a video
- **THEN** the draft can become ready for review
- **AND** the trust report records trust status `degraded`
- **AND** the report includes a degradation event with the stage, code, user-facing message, fallback used, and evidence available for that stage

#### Scenario: Draft fails before render output

- **WHEN** a required stage fails and no allowed fallback exists
- **THEN** the draft is marked failed
- **AND** the trust report records trust status `failed` with the failing stage and actionable error message

### Requirement: No fabricated success artifacts

The system MUST NOT mark fabricated or placeholder analysis artifacts as successful stage outputs.

#### Scenario: Frame analysis provider fails

- **WHEN** frame analysis cannot obtain a valid provider result for an asset
- **THEN** the asset frame-analysis status is terminally failed or unavailable
- **AND** downstream script generation records missing frame-analysis coverage instead of reading placeholder success JSON

#### Scenario: Evidence metric unavailable

- **WHEN** a stage cannot compute a metric such as jitter delta or tracking lost-frame ratio
- **THEN** the trust report marks that metric as unavailable
- **AND** it does not write synthetic values that imply the metric was measured

### Requirement: Degradation event taxonomy

The system SHALL record degradation events using stable stage and code identifiers that the API and UI can group reliably.

#### Scenario: TTS row fails with fallback allowed

- **WHEN** Story/Narrato TTS is requested and one or more narration items fail while fallback is allowed
- **THEN** the report records a `story_tts` degradation with failed item count and narration coverage evidence

#### Scenario: AI plan falls back to heuristic selection

- **WHEN** AI planning fails and the system uses heuristic cut selection
- **THEN** the report records a `plan_generation` degradation with `fallback_used=heuristic`

#### Scenario: Stabilized variant is unavailable

- **WHEN** a source asset cannot use a stabilized variant because stabilization failed, was skipped by quality gate, or is still unavailable
- **THEN** the report records stabilization evidence for that asset or marks the evidence unavailable

### Requirement: Trust report API visibility

The system SHALL expose draft trust summary and report details through draft APIs used by the review UI.

#### Scenario: Draft list or latest draft response includes trust summary

- **WHEN** the frontend loads latest draft state
- **THEN** the API response includes trust status, degradation count, and highest severity for the draft when a report exists

#### Scenario: Draft detail response includes report details

- **WHEN** the frontend loads a draft detail or review page
- **THEN** the API response includes stage outcomes, degradation events, and evidence metrics needed to explain the output

#### Scenario: Existing draft has no report

- **WHEN** an older draft has no trust report
- **THEN** the API exposes trust status `unknown` instead of pretending the draft is fully planned

### Requirement: Trust report UI visibility

The system SHALL show draft trust status prominently in the edit/review experience.

#### Scenario: Draft is fully planned

- **WHEN** a draft has trust status `planned`
- **THEN** the UI indicates that the output completed according to the requested plan

#### Scenario: Draft is degraded

- **WHEN** a draft has trust status `degraded`
- **THEN** the UI shows a visible warning with degradation count
- **AND** the operator can open details grouped by stage

#### Scenario: Draft trust is unknown

- **WHEN** a draft has no trust report
- **THEN** the UI labels trust as unknown rather than fully successful
