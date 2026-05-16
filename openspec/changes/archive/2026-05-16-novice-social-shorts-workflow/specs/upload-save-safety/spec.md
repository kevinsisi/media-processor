## ADDED Requirements

### Requirement: Script edits are saved before leaving upload
The upload page SHALL prevent recent script edits from being lost when a user proceeds to analysis.

#### Scenario: User clicks next with unsaved script edits
- **WHEN** the script textarea has unsaved changes and the user clicks the next-step action
- **THEN** the app saves the current script text before navigating away

#### Scenario: Script save fails before next step
- **WHEN** the app cannot save the current script text
- **THEN** the user remains on the upload page and sees a save error instead of navigating away

### Requirement: Upload page communicates save state clearly
The upload page SHALL make script saving state understandable to a novice user.

#### Scenario: Script save is pending
- **WHEN** script edits are queued or saving
- **THEN** the next-step action communicates that saving is in progress or temporarily waits for save completion
