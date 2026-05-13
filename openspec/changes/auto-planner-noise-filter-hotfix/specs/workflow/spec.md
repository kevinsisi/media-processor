# workflow Specification Delta

## MODIFIED Requirements

### Requirement: One-Click Planner Subject Reliability

One-click automatic generation SHALL only treat a project subject as present when the matching tracking row is reliable enough for automatic planning.

#### Scenario: Requested subject appears only as tracking noise

- **WHEN** a project has `subject_class` set
- **AND** an asset only has matching detections shorter than the minimum planning track length or below the planning confidence floor
- **THEN** the planner does not create a subject-presence window from that track
- **AND** the asset is not kept solely because of that noisy detection.

### Requirement: One-Click Planner Opening Stability

One-click automatic generation SHALL avoid starting a cut on initial handheld setup movement when enough duration remains after that setup beat.

#### Scenario: Candidate cut starts during handheld setup

- **WHEN** a candidate span overlaps a handheld motion tag that starts at the beginning of the asset
- **AND** the span remains at least the minimum cut duration after skipping the setup movement
- **THEN** the planner shifts the span start to the end of the initial handheld section.
