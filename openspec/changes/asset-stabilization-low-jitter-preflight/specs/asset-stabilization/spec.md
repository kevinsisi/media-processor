## ADDED Requirements

### Requirement: Asset stabilization skips already-stable sources

The system SHALL measure source-level high-frequency jitter before running vidstab and SHALL skip stabilization when the source is already below the low-jitter threshold.

#### Scenario: Low-jitter source is queued for stabilization

- **WHEN** an asset stabilization worker processes a source whose residual jitter is below the calibrated threshold
- **THEN** the worker does not run vidstab
- **AND** the asset is marked with `stabilization_status="skipped"`
- **AND** the measured jitter values are recorded for operator/debug visibility
- **AND** no stabilized derivative path is selected for the asset

#### Scenario: Operator forces stabilization

- **WHEN** an asset stabilization worker receives `force=true`
- **THEN** the worker bypasses the low-jitter preflight skip decision
- **AND** vidstab runs even if the source would otherwise be considered already stable

#### Scenario: Batch stabilization sees previously skipped asset

- **WHEN** a project batch stabilization request is made without `force=true`
- **AND** an asset has `stabilization_status="skipped"`
- **THEN** the batch operation skips that asset instead of enqueueing another stabilization job
