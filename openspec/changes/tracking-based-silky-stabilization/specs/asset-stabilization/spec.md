## ADDED Requirements

### Requirement: Tracking target can drive source stabilization

The system SHALL use existing explicit asset tracking data as the preferred source for generating a silky stabilized asset derivative.

#### Scenario: Operator changes tracking target after a stabilized derivative exists

- **WHEN** an operator changes an asset tracking target
- **THEN** any existing stabilized derivative is treated as stale for that asset
- **AND** the active source falls back to raw until a fresh stabilized derivative is generated
- **AND** the stale stabilized derivative is not used as the final selected source implicitly

#### Scenario: Asset has point tracking selected

- **WHEN** an asset has usable point tracking data and the operator requests stabilization
- **THEN** the stabilization worker uses the point track as the primary stabilization target
- **AND** whole-frame vidstab does not decide the framing path

#### Scenario: Point tracking completes asynchronously

- **WHEN** a queued point tracking job finishes successfully
- **THEN** the system queues a forced asset stabilization job
- **AND** that stabilization job uses the completed point track before vidstab fallback

#### Scenario: Stabilization job starts while point tracking is pending

- **WHEN** an asset stabilization job runs while the selected point track is still pending
- **THEN** the stabilization job waits/skips instead of publishing a whole-frame vidstab derivative for the stale pre-tracking intent

#### Scenario: Asset has custom ROI tracking selected

- **WHEN** an asset has usable custom ROI tracking data and no higher-priority point tracking target
- **THEN** the stabilization worker uses the custom ROI center path as the stabilization target

#### Scenario: Asset has picked object tracking selected

- **WHEN** an asset has a picked YOLO object track and no higher-priority explicit target
- **THEN** the stabilization worker uses that object track as the stabilization target

### Requirement: Tracking stabilization smooths camera path before rendering

The system SHALL smooth tracking-derived camera paths before serializing crop commands.

#### Scenario: Tracking path contains frame-level jitter

- **WHEN** the target path has high-frequency frame-to-frame noise
- **THEN** the generated camera path applies dead-zone and motion-limit smoothing
- **AND** adjacent-frame crop-center spikes are reduced before ffmpeg rendering

#### Scenario: Tracking path contains intentional slow motion

- **WHEN** the target path moves consistently over time
- **THEN** the generated camera path preserves the slow motion instead of locking the subject mechanically in one pixel position

### Requirement: Stabilized derivative is quality-gated

The system SHALL compare the generated derivative against the raw source before publishing it as an available stabilized variant.

#### Scenario: Tracking-stabilized output is worse than raw

- **WHEN** objective metrics show new adjacent-frame spikes, worse high-frequency jitter, bad subject containment, or sustained black borders
- **THEN** the system does not publish the derivative as a ready stabilized variant
- **AND** the asset records a terminal failure or skipped reason visible to the operator

#### Scenario: Tracking-stabilized output passes quality gates

- **WHEN** objective metrics show the derivative is smoother and source compatibility is preserved
- **THEN** the system marks the stabilized derivative ready for preview and selection
- **AND** the active asset variant remains unchanged until the operator explicitly switches it

### Requirement: Vidstab remains a fallback cleanup layer

The system SHALL keep whole-frame vidstab below tracking-based stabilization in the camera-motion priority order.

#### Scenario: Explicit tracking stabilization is available

- **WHEN** a tracking-based camera path is generated for an asset
- **THEN** vidstab may only run as bounded high-frequency cleanup if measurement shows it is safe
- **AND** vidstab must not override the tracking-derived framing path

#### Scenario: No usable tracking target exists

- **WHEN** an asset has no usable explicit or acceptable automatic tracking target
- **THEN** the system falls back to existing low-jitter preflight and vidstab behavior
