## ADDED Requirements

### Requirement: Project can batch queue missing stabilized variants
The system SHALL provide a project-level operation that queues source-level stabilization for all eligible assets in a project.

#### Scenario: Queue all missing variants
- **WHEN** an operator requests batch stabilization for a project with assets whose stabilization status is `not_started` or `failed`
- **THEN** the system marks those assets `pending`, enqueues stabilization jobs, and returns their job ids

#### Scenario: Skip already active work
- **WHEN** an operator requests batch stabilization for assets whose stabilization status is `pending`, `running`, or `done`
- **THEN** the system skips those assets by default and reports them as skipped

#### Scenario: Source project missing
- **WHEN** an operator requests batch stabilization for a project id that does not exist
- **THEN** the system returns a not-found error and enqueues no jobs

### Requirement: Batch stabilization does not switch active variants
The system SHALL NOT change `active_asset_variant` when batch stabilization jobs are queued or completed.

#### Scenario: Raw asset is queued for stabilization
- **WHEN** an asset using the raw variant is queued by the batch operation
- **THEN** the asset remains on the raw variant until the operator explicitly switches it

### Requirement: Frontend exposes one-click batch stabilization
The system SHALL expose a ProjectAnalysis action that queues all missing or failed stabilized variants without requiring per-asset clicks.

#### Scenario: Operator clicks batch stabilize
- **WHEN** an operator clicks the batch stabilize action
- **THEN** the frontend calls the project batch endpoint, refreshes polling, and shows queued/skipped/failed counts
