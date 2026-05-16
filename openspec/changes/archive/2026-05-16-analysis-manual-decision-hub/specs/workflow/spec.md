# workflow Specification Delta

## ADDED Requirements

### Requirement: Analysis Manual Decision Hub

ProjectAnalysis SHALL summarize manual readiness before the asset list so operators can decide whether to wait, adjust assets, or continue.

#### Scenario: Assets exist on ProjectAnalysis

- **WHEN** the operator opens ProjectAnalysis for a project with assets
- **THEN** the page shows analyzed asset count, active/available stabilized variant count, and tracking readiness count
- **AND** it keeps the existing asset cards and batch toolbar available below.

#### Scenario: Operator wants full automatic output from Analysis

- **WHEN** the operator clicks one-click generation in the decision hub
- **THEN** the frontend submits the existing edit trigger
- **AND** navigates to ProjectEdit for progress and preview.

#### Scenario: Operator wants manual continuation

- **WHEN** the operator clicks the manual continuation action
- **THEN** the frontend opens ProjectEdit without hiding the current ProjectAnalysis controls.
