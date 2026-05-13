## ADDED Requirements

### Requirement: Analysis page states the next action clearly
The system SHALL show a next-step message on ProjectAnalysis that tells the operator whether they can start editing now, preview an existing draft, or need to wait for analysis.

#### Scenario: Analysis complete and no draft yet
- **WHEN** all project assets are in an analysis terminal state and no latest draft exists
- **THEN** the page tells the operator they can start producing a draft now

#### Scenario: Draft already exists
- **WHEN** the project has a latest draft ready for review
- **THEN** the page tells the operator they can preview the existing draft now

#### Scenario: Stabilized variants still processing
- **WHEN** some project assets still have `stabilization_status` of `pending` or `running`
- **THEN** the page explains that editing can start now and that waiting will make more stabilized variants available

#### Scenario: Analysis not yet complete
- **WHEN** some project assets are still not in a terminal analysis state
- **THEN** the page tells the operator to wait for analysis before starting the first draft
