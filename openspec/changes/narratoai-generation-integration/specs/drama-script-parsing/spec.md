# Capability: drama-script-parsing

## ADDED Requirements

### Requirement: Drama explain mode identifies short-drama beats from transcripts

The system SHALL support `edit_mode=drama_explain` by transforming existing transcript segments into a drama-focused StoryScript that highlights hooks, conflicts, reversals, and payoff moments.

#### Scenario: Drama explain planning succeeds

- **WHEN** a draft is triggered with `edit_mode=drama_explain`
- **THEN** the planner uses transcript-derived inputs and a drama explanation prompt to produce a validated StoryScript
- **AND** the resulting CutPlan uses the same renderer and review/export flow as other edit modes

#### Scenario: Drama explain input is insufficient

- **WHEN** transcript inputs are missing or unusable
- **THEN** the system surfaces the same input error behavior as StoryScript generation rather than producing an invalid draft plan
