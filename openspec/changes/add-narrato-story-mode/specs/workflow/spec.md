## ADDED Requirements

### Requirement: Workflow supports Story/Narrato generation path
The system SHALL expose Story/Narrato mode as an additional generation path without replacing the existing one-click edit workflow.

#### Scenario: Operator starts story-mode generation from analysis
- **WHEN** the operator opens ProjectAnalysis for a project with usable transcript, subtitle, or story inputs
- **THEN** the page offers a Story/Narrato generation action alongside the existing draft-generation action

#### Scenario: Operator keeps using existing generation
- **WHEN** the operator chooses the existing standard generation action
- **THEN** the system uses the current edit trigger behavior without requiring StoryScript generation

#### Scenario: Story mode render begins
- **WHEN** the operator renders a validated StoryScript
- **THEN** the workflow navigates to the current draft progress/preview experience and tracks progress through existing render stages
