# Capability: frame-analysis

## ADDED Requirements

### Requirement: Assets can be analyzed into reusable frame observations

The system SHALL extract sampled frames from a project asset, call a Vision LLM in batches, and persist a structured `frame_analysis_json` result with `frame_analysis_status` and `frame_analysis_error` lifecycle fields.

#### Scenario: Frame analysis is triggered for an asset

- **WHEN** an operator calls `POST /assets/{asset_id}/frame-analysis`
- **THEN** the API enqueues an analysis job and marks the asset `frame_analysis_status` as `pending`
- **AND** the worker extracts keyframes into the configured frame cache and stores batch observations when complete

#### Scenario: Completed analysis is queried

- **WHEN** an operator calls `GET /assets/{asset_id}/frame-analysis` for an analyzed asset
- **THEN** the response includes status, error, frame count, batch count, and interval seconds

### Requirement: Documentary rendering can use frame analysis without breaking fallback

Documentary mode SHALL use completed frame analysis when available and SHALL fall back to the existing StoryScript path when frame analysis or Vision calls are unavailable.

#### Scenario: Documentary render has no usable frame analysis

- **WHEN** a documentary render starts and no asset has completed frame analysis
- **THEN** the system may attempt inline analysis for one asset
- **AND** if that fails, the draft planning stage falls back to the subtitle/script StoryScript path instead of failing solely due to frame analysis
