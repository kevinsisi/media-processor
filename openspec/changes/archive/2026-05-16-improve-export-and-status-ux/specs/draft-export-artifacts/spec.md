## ADDED Requirements

### Requirement: Export requests are durable artifacts
The system SHALL create a durable export artifact record whenever a user requests a derivative draft export.

#### Scenario: Export job accepted
- **WHEN** a ready draft is exported with a supported aspect and height
- **THEN** the API creates an export artifact with draft id, aspect, height, output filename, job id, and status `queued`

#### Scenario: Export job completes
- **WHEN** the export worker finishes writing the derivative mp4
- **THEN** the matching export artifact status becomes `done` and records the public download URL metadata needed by the frontend

#### Scenario: Export job fails
- **WHEN** the export worker cannot produce the derivative mp4
- **THEN** the matching export artifact status becomes `failed` and stores a human-readable error message

### Requirement: Draft exports are listable and downloadable
The system SHALL expose a draft-scoped export list that allows the edit UI to show queued, running, completed, and failed exports.

#### Scenario: Draft has completed exports
- **WHEN** the frontend requests exports for a draft with completed derivative files
- **THEN** the API returns each completed artifact with a browser-downloadable URL

#### Scenario: Draft has queued or running exports
- **WHEN** the frontend requests exports for a draft with pending work
- **THEN** the API returns those artifacts with their current status and without a download URL

#### Scenario: Draft has failed exports
- **WHEN** the frontend requests exports for a draft with failed work
- **THEN** the API returns those artifacts with status `failed` and the stored error message

### Requirement: Export UI reflects real artifact state
The edit UI SHALL show export artifact status from the API instead of promising a separate download list.

#### Scenario: User submits an export
- **WHEN** the user submits a derivative export from the edit page
- **THEN** the UI adds or refreshes the artifact list and shows the new export as queued or running

#### Scenario: Export is ready
- **WHEN** an export artifact reaches `done`
- **THEN** the UI shows a direct download action for that derivative mp4
