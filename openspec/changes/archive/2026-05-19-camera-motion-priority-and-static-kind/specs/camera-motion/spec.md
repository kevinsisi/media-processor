# camera-motion Specification Delta

## ADDED Requirements

### Requirement: Smart Camera No-Move Directive

Smart Camera SHALL represent an intentional no-move decision as a persisted `kind="none"` directive instead of synthesizing fallback pan or zoom motion.

#### Scenario: Vision produces no usable movement decision

- **WHEN** Smart Camera analysis returns no focus regions, ambiguous regions, a mid-band area, malformed output, or quota failure for a cut
- **THEN** the planner emits a serializable `kind="none"` directive with full-frame placeholder rectangles and a reason note
- **AND** it does not synthesize fallback pan or zoom motion.

#### Scenario: Renderer receives a no-move directive

- **WHEN** the renderer deserializes a Smart Camera directive with `kind="none"`
- **THEN** it renders the cut without adding a Smart Camera crop or zoompan chain
- **AND** it treats missing or unknown stored directive kinds as no-move for read-time safety.

### Requirement: Explicit Tracking Priority

Explicit operator tracking SHALL take priority over Smart Camera movement.

#### Scenario: Cut has explicit tracking and Smart Camera

- **WHEN** a cut has point tracking, custom ROI tracking, or a user-picked YOLO object and also has a Smart Camera directive
- **THEN** the renderer uses the explicit tracking crop path
- **AND** Smart Camera does not replace that crop path.

#### Scenario: Cut has automatic YOLO and Smart Camera

- **WHEN** a cut has only automatic YOLO auto-reframe and also has a Smart Camera movement directive
- **THEN** Smart Camera remains allowed to replace automatic YOLO framing
- **AND** the renderer preserves the existing automatic-YOLO override behavior.
