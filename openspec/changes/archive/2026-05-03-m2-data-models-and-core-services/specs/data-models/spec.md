## ADDED Requirements

### Requirement: Project entity persists pipeline-runnable cases

The system SHALL provide a `Project` ORM entity with at minimum the columns `id`, `name`, `client`, `profile_name`, `source_dir`, `status`, `created_at`. `status` SHALL be a string enum with the values `pending`, `processing`, `degraded`, `ready_for_review`, `approved`, `failed`.

#### Scenario: Insert and retrieve a project

- **WHEN** a row is inserted with `name="carsmeet-Phantom-0428"`, `profile_name="carsmeet-luxury"`, `status="pending"`
- **THEN** querying by `id` returns the same field values and a non-null `created_at`

#### Scenario: Status enum guards invalid values

- **WHEN** a caller attempts to set `status="bogus"`
- **THEN** the ORM SHALL raise before flush

### Requirement: Asset entity stores per-file metadata

The system SHALL provide an `Asset` ORM entity with `id`, `project_id` (FK → Project), `file_path`, `duration_ms` (int), `resolution` (string `WxH`), `fps` (float), `codec`, `sha256`, `thumbnail_path`, `status`. Deleting a Project SHALL cascade-delete its Assets.

#### Scenario: Cascade delete

- **WHEN** a Project with 5 Assets is deleted
- **THEN** all 5 Assets are removed from the database

### Requirement: AssetTag captures detector / classifier output

The system SHALL provide an `AssetTag` ORM entity with `id`, `asset_id` (FK → Asset), `tag_type`, `tag_name`, `confidence` (float 0–1), `source_model`, `time_ranges_ms` (JSON). Multiple tags per asset are allowed; a unique key SHALL prevent duplicate (`asset_id`, `tag_type`, `tag_name`, `source_model`) rows.

#### Scenario: Duplicate tag rejected

- **WHEN** the same (asset, tag_type, tag_name, source_model) combination is inserted twice
- **THEN** the second insert raises an integrity error

### Requirement: AssetSegment records scored sub-clips

The system SHALL provide an `AssetSegment` ORM entity with `id`, `asset_id` (FK → Asset), `start_ms`, `end_ms`, `score` (float), `used_in_draft` (bool). The constraint `start_ms < end_ms` SHALL hold.

#### Scenario: Reject reversed range

- **WHEN** inserting a segment with `start_ms=2000, end_ms=1000`
- **THEN** insertion raises a check constraint error

### Requirement: Draft and DraftSegment record AI output versions

The system SHALL provide a `Draft` entity with `id`, `project_id`, `profile_name`, `version` (int, ≥ 1), `status`, `output_zip_path`, `mp4_preview_path`, `ai_score`, `prompt_feedback` (text, nullable), `created_at`. The composite key `(project_id, version)` SHALL be unique.

The system SHALL provide a `DraftSegment` entity with `id`, `draft_id` (FK → Draft), `order` (int, 0-based), `asset_segment_id` (FK → AssetSegment), `on_timeline_start_ms`, `on_timeline_end_ms`, `reframe_keyframes` (JSON, nullable), `transition` (string, nullable), `blurred_source_path` (string, nullable). The composite key `(draft_id, order)` SHALL be unique.

#### Scenario: Two drafts share a project

- **WHEN** a project gets a v1 then a v2 draft
- **THEN** both rows coexist and `(project_id, 1)` / `(project_id, 2)` are both queryable

#### Scenario: Segment order uniqueness

- **WHEN** inserting two `DraftSegment` rows with the same `(draft_id, order)`
- **THEN** the second raises an integrity error

### Requirement: Review records reviewer actions

The system SHALL provide a `Review` entity with `id`, `draft_id` (FK → Draft), `reviewer` (string, default `"alice"`), `action` (enum: `approve`, `reject`, `repatch`, `download`), `prompt_feedback` (text, nullable), `reviewed_at`. Multiple reviews per draft SHALL be allowed (history).

#### Scenario: Append a review action

- **WHEN** an `approve` review is posted for a draft that already has a `reject`
- **THEN** both rows persist with distinct `reviewed_at` timestamps

### Requirement: BGM stores beat grids for cut planning

The system SHALL provide a `BGM` entity with `id`, `file_path`, `name`, `bpm` (float), `beat_grid_json` (JSON list of int milliseconds).

#### Scenario: Beat grid round-trips

- **WHEN** inserting a BGM row with `beat_grid_json=[0, 500, 1000, 1500]`
- **THEN** reading the row back yields the same list

### Requirement: Profile table caches loaded YAML snapshots

The system SHALL provide a `Profile` entity with `id`, `name` (unique), `description`, `yaml_text` (text), `loaded_at`. The canonical source of truth is the YAML file on disk; the table is a read-cache for workers.

#### Scenario: Same name updates instead of duplicating

- **WHEN** a profile YAML is loaded twice with the same `name`
- **THEN** the table SHALL hold a single row whose `loaded_at` was updated

### Requirement: Initial Alembic migration creates the full schema

`alembic upgrade head` from an empty database SHALL create all 9 tables, all FKs, and all indexes named in this spec.

#### Scenario: Fresh upgrade

- **WHEN** `alembic upgrade head` runs against an empty SQLite database
- **THEN** introspection lists tables `projects`, `assets`, `asset_tags`, `asset_segments`, `drafts`, `draft_segments`, `reviews`, `bgms`, `profiles`

#### Scenario: Downgrade is reversible

- **WHEN** `alembic downgrade base` runs after a fresh upgrade
- **THEN** the database has zero application tables left
