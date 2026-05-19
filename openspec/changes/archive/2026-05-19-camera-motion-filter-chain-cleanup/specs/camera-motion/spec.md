# camera-motion Specification Delta

## ADDED Requirements

### Requirement: Single Dynamic Crop Chain

The renderer SHALL NOT layer emotion zoompan on top of tracking or Smart Camera crop chains.

#### Scenario: Emotion zoompan is eligible on a tracked cut

- **WHEN** a cut qualifies for emotion zoompan and also has an active tracking crop path
- **THEN** the renderer keeps the tracking crop path
- **AND** it does not append an additional emotion zoompan filter.

#### Scenario: Emotion zoompan is eligible on a Smart Camera cut

- **WHEN** a cut qualifies for emotion zoompan and also has an active Smart Camera movement directive
- **THEN** the renderer keeps the Smart Camera filter chain
- **AND** it does not append a second emotion zoompan filter.

### Requirement: Crop Region Applies To Dynamic Crop Paths

The project `crop_region` anchor SHALL affect dynamic crop paths as well as static aspect crop paths.

#### Scenario: Auto-reframe uses a project crop anchor

- **WHEN** a project has `crop_region` set and a cut uses automatic, point, custom ROI, or user-picked object reframe
- **THEN** the computed crop path biases available slack toward the project anchor
- **AND** it continues to clamp the crop window inside the source frame.

#### Scenario: Smart Camera uses a project crop anchor

- **WHEN** a project has `crop_region` set and a cut uses Smart Camera pan or zoom movement
- **THEN** the Smart Camera crop expression biases centered directive endpoints toward the project anchor
- **AND** it avoids over-correcting directive endpoints that are already near the source edges.
