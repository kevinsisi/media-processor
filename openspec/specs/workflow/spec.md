# workflow Specification

## Purpose
TBD - created by archiving change analysis-manual-decision-hub. Update Purpose after archive.
## Requirements
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

### Requirement: Generation workflow shows trust outcome

The ProjectEdit and ProjectAnalysis workflows SHALL show whether generated drafts are fully planned, degraded, failed, or unknown.

#### Scenario: One-click generation completes with no degradation

- **WHEN** the operator uses one-click generation and the draft trust status is `planned`
- **THEN** the completion UI presents the draft as successfully produced according to plan

#### Scenario: One-click generation completes with degradation

- **WHEN** the operator uses one-click generation and the draft trust status is `degraded`
- **THEN** the completion UI presents the draft as usable but degraded
- **AND** it provides the stage-level reasons before the operator approves or exports the video

#### Scenario: Manual render completes with degradation

- **WHEN** the operator triggers a manual render and optional stages fall back
- **THEN** the review UI shows the same degradation summary and details as one-click generation

### Requirement: Workflow copy avoids false success language

The frontend SHALL avoid copy that implies a draft is fully successful when backend trust status is degraded, failed, or unknown.

#### Scenario: Draft trust status degraded

- **WHEN** the latest draft is ready for review with trust status `degraded`
- **THEN** the UI text indicates that the output was produced with listed compromises

#### Scenario: Draft trust status unknown

- **WHEN** the latest draft predates trust reporting and has trust status `unknown`
- **THEN** the UI tells the operator that detailed production evidence is unavailable
