# status-polling-reliability Specification

## Purpose
TBD - created by archiving change improve-export-and-status-ux. Update Purpose after archive.
## Requirements
### Requirement: Polling ignores stale responses
Frontend polling SHALL NOT allow older overlapping status responses to overwrite newer state.

#### Scenario: Slow draft request resolves after a newer request
- **WHEN** a draft polling request resolves after a newer request for the same hook instance
- **THEN** the older response is ignored and does not update visible draft state

#### Scenario: Asset polling request is still in flight
- **WHEN** asset analysis polling has an in-flight request
- **THEN** the hook does not start another overlapping request for the same project state

### Requirement: Queue badge distinguishes fetch failure from empty queue
The queue status badge SHALL show an explicit degraded state when queue status cannot be fetched.

#### Scenario: Queue status API fails
- **WHEN** the queue status request fails
- **THEN** the badge indicates that queue status is unavailable instead of displaying `排隊 0`

#### Scenario: Queue status API recovers
- **WHEN** a later queue status request succeeds
- **THEN** the badge clears the degraded state and displays running and queued counts from the latest response

### Requirement: Edit readiness requires meaningful analysis data
The edit page SHALL only enable edit triggers when the analysis status response contains meaningful terminal analysis data.

#### Scenario: Analysis response has no step cells
- **WHEN** the edit page receives an analysis response with assets but zero counted analysis steps
- **THEN** the edit trigger remains disabled and the page treats analysis as not ready

#### Scenario: Analysis response is fully terminal
- **WHEN** the edit page receives assets with all expected analysis steps terminal and no pending or running steps
- **THEN** the edit trigger can be enabled

