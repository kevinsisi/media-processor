## ADDED Requirements

### Requirement: Fork creates an independent project copy
The system SHALL provide an API operation that creates a new project from an existing project while assigning the fork its own project id, source directory, media file paths, and database rows.

#### Scenario: Successful project fork
- **WHEN** an operator requests a fork for an existing project
- **THEN** the system creates a new project with copied project settings, copied assets, and copied analysis metadata that reference the new project and asset ids

#### Scenario: Source project missing
- **WHEN** an operator requests a fork for a project id that does not exist
- **THEN** the system returns a not-found error and does not create a project

### Requirement: Forked files are not shared with the source project
The system SHALL copy raw asset files, stabilized derivative files, and project-level media files into fork-owned paths instead of reusing source project file paths.

#### Scenario: Source asset has raw and stabilized files
- **WHEN** a project asset has both a raw file and a completed stabilized derivative
- **THEN** the forked asset references copied raw and stabilized files under the fork's media locations

#### Scenario: Source media file missing
- **WHEN** a source row references a media file that is required for the fork but missing from disk
- **THEN** the fork request fails without leaving a committed partial project

### Requirement: Fork excludes rendered draft history
The system SHALL NOT copy drafts, draft segments, reviews, comments, subtitle cue rows, export rows, or rendered output artifacts into the fork.

#### Scenario: Source project has rendered drafts
- **WHEN** an operator forks a project that has one or more drafts
- **THEN** the forked project has zero drafts and is ready for a fresh render starting at version 1

### Requirement: Fork action is available from the frontend
The system SHALL expose a frontend action that lets an operator fork an existing project and open the copied project after the API operation completes.

#### Scenario: Operator forks from project list
- **WHEN** an operator activates the fork action for a project from the project list
- **THEN** the frontend calls the fork API, refreshes project data, and navigates to the copied project's edit or analysis flow

#### Scenario: Fork API fails
- **WHEN** the fork API returns an error
- **THEN** the frontend shows an actionable error and keeps the operator on the current page
