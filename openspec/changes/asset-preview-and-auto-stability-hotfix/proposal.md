## Why

On mobile Safari, asset preview videos show native controls but tapping playback can be intercepted by the app-level click handler. Also, the production one-click generation path still enables automatic reframe, which can consume existing tracking/point decisions and reintroduce visible left/right motion even when stabilized asset variants are selected.

## What Changes

- Remove the app-level video click toggle from asset preview videos so native video controls own playback.
- Keep a separate explicit Play/Pause button for keyboard/non-native interaction.
- Change production one-click generation defaults to disable `auto_reframe` while still using the active asset variant source.

## Capabilities

### Modified Capabilities

- `asset-preview-playback`: Native media controls must not be intercepted.
- `dual-path-production-entry`: One-click automatic generation must avoid adding extra tracking/reframe motion by default.

## Impact

- Frontend ProjectAnalysis video preview behavior.
- Frontend one-click edit trigger payloads from ProjectList, Upload, and ProjectAnalysis.
