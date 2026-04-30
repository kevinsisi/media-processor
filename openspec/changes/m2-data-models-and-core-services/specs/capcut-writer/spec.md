## ADDED Requirements

### Requirement: Writer produces a CapCut-compatible draft zip

The system SHALL provide `media_processor.services.capcut_writer.CapCutDraftWriter.write(draft, segments, output_path)` that emits a zip file containing at minimum `draft_content.json` and `draft_meta_info.json`.

#### Scenario: Zip layout

- **WHEN** the writer is invoked with a draft of N segments
- **THEN** the produced zip contains `draft_content.json` and `draft_meta_info.json` at the archive root

### Requirement: draft_content.json includes a schema marker

`draft_content.json` SHALL include a top-level `version` field whose value is the writer's `SCHEMA_VERSION`. While Step 0 reverse engineering is pending the value SHALL be the string `"step0-pending"`; once a real CapCut version is locked, the writer SHALL emit that version string.

#### Scenario: Marker present

- **WHEN** the writer emits a draft
- **THEN** `draft_content.json["version"]` equals `CapCutDraftWriter.SCHEMA_VERSION`

### Requirement: draft_content.json declares track structure

`draft_content.json` SHALL include a `tracks` array containing at minimum one video track and the segment list. When captions are provided, a text track SHALL appear; when BGM is provided, an audio track SHALL appear.

#### Scenario: Video-only draft

- **WHEN** the writer is given segments and no BGM and no captions
- **THEN** `tracks` contains exactly one entry whose `type` is `"video"` and whose `segments` length equals the input segment count

#### Scenario: Full draft

- **WHEN** the writer is given segments, BGM, and captions
- **THEN** `tracks` contains entries with types `"video"`, `"audio"`, and `"text"`

### Requirement: Segment file paths reference SMB-shared sources

Segment entries in `draft_content.json` SHALL preserve absolute or platform-canonical paths; the writer SHALL NOT copy media files into the zip.

#### Scenario: No media bytes in zip

- **WHEN** a segment references `/Volumes/MediaProcessor/assets/foo.mp4`
- **THEN** the path appears verbatim in `draft_content.json` and the zip contains no media bytes

### Requirement: Writer is idempotent on identical input

Calling `write` twice with the same input SHALL produce zips with byte-identical `draft_content.json` payloads (sorted keys, deterministic timestamps).

#### Scenario: Determinism

- **WHEN** the writer is invoked twice with the same draft and segments
- **THEN** the two `draft_content.json` byte-strings are equal
