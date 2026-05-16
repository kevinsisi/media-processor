## ADDED Requirements

### Requirement: Social export presets are destination-oriented
The export UI SHALL offer IG/FB destination presets before raw aspect and resolution controls.

#### Scenario: User opens export options
- **WHEN** a user opens the export panel for a ready draft
- **THEN** the UI shows presets for common Instagram and Facebook short-video destinations

#### Scenario: User chooses a social preset
- **WHEN** a user chooses a social preset
- **THEN** the export request uses the preset's aspect and height through the existing export artifact API

### Requirement: Advanced export controls remain available
The export UI SHALL keep raw aspect and resolution controls available for non-preset needs.

#### Scenario: User needs a custom ratio or resolution
- **WHEN** a user expands advanced export controls
- **THEN** the UI allows choosing the existing aspect and height options
