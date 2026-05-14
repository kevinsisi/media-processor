# workflow Specification Delta

## MODIFIED Requirements

### Requirement: Asset Variant Analysis Persistence

The system SHALL persist analysis results separately for each asset source variant in the database.

#### Scenario: Operator switches away from an analyzed variant

- **WHEN** the operator changes an asset from one source variant to another
- **THEN** the system saves the current variant's analysis state into the asset's DB snapshot before clearing active rows.

#### Scenario: Operator switches back to an already analyzed variant

- **WHEN** the target source variant has a stored DB analysis snapshot
- **THEN** the system restores tags, transcript, coverage, tracking state, analysis steps, and asset status from the DB snapshot
- **AND** no new asset analysis job is enqueued.

#### Scenario: Operator switches to a never-analyzed variant

- **WHEN** the target source variant has no stored DB analysis snapshot
- **THEN** the system clears variant-dependent analysis state
- **AND** enqueues analysis only if the request asked for reanalysis.
