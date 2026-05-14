# workflow Specification Delta

## MODIFIED Requirements

### Requirement: Asset Variant Preview Switching

The analysis page SHALL make raw/stabilized preview switching visibly change the asset preview video source.

#### Scenario: Operator previews a different asset variant

- **WHEN** the operator presses `預覽原始` or `預覽防抖`
- **THEN** the page updates the current-preview label
- **AND** the selected preview button is visibly selected
- **AND** the video element reloads with the selected variant URL.

#### Scenario: Operator also sees tracking controls

- **WHEN** an asset has tracking controls
- **THEN** the raw/stabilized variant preview appears before the tracking target picker
- **AND** the tracking picker is not presented as the variant preview surface.
