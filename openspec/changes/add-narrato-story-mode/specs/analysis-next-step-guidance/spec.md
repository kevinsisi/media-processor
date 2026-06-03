## ADDED Requirements

### Requirement: Analysis guidance explains Story/Narrato readiness
The analysis page SHALL tell the operator whether Story/Narrato generation can start now, whether it is waiting for required text inputs, or whether optional advanced analysis can improve results.

#### Scenario: Transcript or subtitle input is ready
- **WHEN** a project has transcript segments, uploaded subtitles, or another usable text input
- **THEN** the analysis page indicates that Story/Narrato script generation can start without waiting for local GPU analysis

#### Scenario: No text input is ready
- **WHEN** a project has no transcript, uploaded subtitle, or usable story text input
- **THEN** the analysis page explains what input is needed before Story/Narrato script generation can start

#### Scenario: Optional analysis is still running
- **WHEN** optional visual, tracking, emotion, or stabilization analysis is pending or running
- **THEN** the analysis page explains that Story/Narrato generation can proceed from text inputs and that waiting may improve visual selection or camera behavior
