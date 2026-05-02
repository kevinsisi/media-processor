## ADDED Requirements

### Requirement: GET /projects lists projects

The API SHALL expose `GET /projects` that returns a JSON array of project summaries: `id`, `name`, `client`, `profile_name`, `status`, `created_at`, `asset_count`, `latest_draft_version` (nullable).

#### Scenario: Empty database

- **WHEN** `GET /projects` is requested with no projects in the database
- **THEN** the response status is 200 and the body is `[]`

#### Scenario: Sorted by created_at desc

- **WHEN** three projects exist with different `created_at`
- **THEN** the response orders them newest first

### Requirement: GET /projects/{id} returns project detail

The API SHALL expose `GET /projects/{id}` that returns the project row plus aggregates (`asset_count`, `draft_count`). 404 SHALL be returned for unknown ids.

#### Scenario: Unknown id

- **WHEN** `GET /projects/9999` is requested with no such project
- **THEN** the response status is 404

### Requirement: GET /projects/{id}/drafts lists drafts for a project

The API SHALL expose `GET /projects/{id}/drafts` returning the drafts of that project ordered by `version` ascending, with each draft including `id`, `version`, `status`, `mp4_preview_path`, `output_zip_path`, `ai_score`, `created_at`.

#### Scenario: No drafts yet

- **WHEN** the project exists but has no drafts
- **THEN** the response is `[]` with status 200

### Requirement: GET /drafts/{id} returns draft with segments

The API SHALL expose `GET /drafts/{id}` returning the draft fields plus an embedded `segments` array (ordered by `order` ascending) where each entry includes the underlying `asset_segment_id`, `on_timeline_start_ms`, `on_timeline_end_ms`, `transition`.

#### Scenario: Unknown draft

- **WHEN** `GET /drafts/9999` is requested with no such draft
- **THEN** the response status is 404

### Requirement: GET /assets/{id} returns asset detail

The API SHALL expose `GET /assets/{id}` returning the asset's metadata plus its tags (sorted by `confidence` descending) — used by the "AI 判斷理由" popup.

#### Scenario: Asset with tags

- **WHEN** an asset has 5 tags with varying confidence
- **THEN** the response lists them with the highest-confidence tag first

### Requirement: POST /reviews records a review action

The API SHALL expose `POST /reviews` accepting `{draft_id, action, prompt_feedback?, reviewer?}`. `action` SHALL be one of `approve`, `reject`, `repatch`, `download`. `reviewer` defaults to `"alice"`. The endpoint SHALL persist a `Review` row and return it with status 201.

#### Scenario: Approve action

- **WHEN** `POST /reviews` is called with `{"draft_id": 1, "action": "approve"}`
- **THEN** the response status is 201 and the body contains the new review id and `reviewer="alice"`

#### Scenario: Invalid action rejected

- **WHEN** `POST /reviews` is called with `action="bogus"`
- **THEN** the response status is 422

#### Scenario: Unknown draft rejected

- **WHEN** `POST /reviews` references a non-existent draft id
- **THEN** the response status is 404
