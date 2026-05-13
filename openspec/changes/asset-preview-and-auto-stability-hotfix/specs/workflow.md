# workflow Specification Delta

## MODIFIED Requirements

### Requirement: Asset Preview Playback

Asset preview videos SHALL allow native browser media controls to handle playback without app-level click handlers toggling playback on the same video element.

#### Scenario: Mobile operator taps native video play control

- **WHEN** the operator taps the native play control on an asset preview video
- **THEN** the app does not run an additional video click toggle
- **AND** the browser media control owns playback.

### Requirement: One-Click Stable Output Default

One-click automatic generation SHALL avoid adding extra auto-reframe/tracking motion by default.

#### Scenario: Operator submits one-click generation

- **WHEN** the frontend triggers the project edit endpoint from a one-click entry point
- **THEN** the payload sets `auto_reframe` to `false`
- **AND** render uses the currently active asset variant sources.
