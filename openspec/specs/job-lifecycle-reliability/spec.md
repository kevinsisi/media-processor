# job-lifecycle-reliability Specification

## Purpose
TBD - created by archiving change harden-job-lifecycle. Update Purpose after archive.
## Requirements
### Requirement: Enqueue failure safety
The system SHALL prevent durable lifecycle rows from remaining indefinitely pending when the corresponding RQ enqueue fails.

#### Scenario: Draft render enqueue fails
- **WHEN** a draft render request creates or updates a draft row but enqueueing the render job fails
- **THEN** the draft is rolled back or marked failed with an actionable error instead of remaining pending without an RQ job

#### Scenario: Export enqueue fails
- **WHEN** an export artifact row is created but enqueueing the export job fails
- **THEN** the artifact is marked failed with the enqueue error and is not shown as queued or running

#### Scenario: BGM or point tracking enqueue fails
- **WHEN** BGM generation or point tracking intent is persisted but enqueueing its worker job fails
- **THEN** the durable status is rolled back or marked failed so polling reaches a terminal state

### Requirement: Worker adoption guard
The system SHALL ensure workers only mutate rows that still match the job intent and are in an expected in-flight state.

#### Scenario: Stale render job starts after draft finished or failed
- **WHEN** a render worker starts for a draft that is already ready, failed, cancelled, or superseded
- **THEN** the worker exits without overwriting the draft output, status, progress, or feedback

#### Scenario: Export worker starts for mismatched artifact
- **WHEN** an export worker starts with an export id that does not belong to the requested draft/aspect/height intent
- **THEN** the worker marks the artifact failed or exits safely without writing an unrelated output

### Requirement: Orphan reconciliation
The system SHALL periodically reconcile durable in-flight statuses against RQ queued and started jobs.

#### Scenario: Draft render job vanished
- **WHEN** a draft is pending or processing and no matching queued or started render job exists
- **THEN** the watchdog retries within the configured retry budget and eventually marks the draft failed after retries are exhausted

#### Scenario: Export job vanished
- **WHEN** an export artifact is queued or running and its RQ job no longer exists
- **THEN** the reconciler marks the artifact failed or retries according to the configured export policy

#### Scenario: Point tracking job vanished
- **WHEN** an asset has point tracking status pending and no matching RQ job exists
- **THEN** the reconciler marks point tracking failed with an actionable error instead of leaving the UI polling forever

#### Scenario: Analysis or BGM job vanished
- **WHEN** an analysis asset or BGM generation job is in an in-flight state but no matching RQ job exists
- **THEN** the reconciler moves the durable state to a retryable or terminal state that the UI can display truthfully

### Requirement: Cancellation state synchronization
The system SHALL keep durable row state synchronized with queue cancellation and stop requests.

#### Scenario: Queued job cancellation succeeds
- **WHEN** a user or operator cancels a queued render/export/analysis/BGM/point-tracking job
- **THEN** the corresponding durable row moves to a cancelled or failed terminal state and is no longer shown as running

#### Scenario: Running job stop is requested
- **WHEN** a running job receives a stop request
- **THEN** the durable state reflects that cancellation was requested and late worker completion cannot overwrite a terminal cancellation state

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
