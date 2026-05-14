# workflow Specification Delta

## ADDED Requirements

### Requirement: Production Dual-Path Entry

The production UI SHALL expose a clear dual-path decision from real workflow entry points after a project has assets: one-click automatic draft generation and manual asset/clip control.

#### Scenario: Project has uploaded assets

- **WHEN** the operator views the project list
- **THEN** the project row/card provides a one-click automatic generation action
- **AND** it provides a manual control action that opens asset decisions / analysis.

#### Scenario: Upload is complete

- **WHEN** at least one asset is uploaded and no uploads are in flight
- **THEN** Upload provides the same one-click automatic generation action
- **AND** Upload provides the manual asset/clip control action.

#### Scenario: One-click action is submitted

- **WHEN** the operator clicks the one-click automatic generation action
- **THEN** the frontend submits the existing project edit trigger
- **AND** navigates to the edit page where queue/progress/preview state is already handled.
