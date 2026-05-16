# beginner-copy-system Specification

## Purpose
TBD - created by archiving change complete-beginner-copy-cleanup. Update Purpose after archive.
## Requirements
### Requirement: Beginner-facing terminology
The web UI SHALL use consistent Traditional Chinese outcome-oriented terminology for novice users and SHALL avoid prominent technical implementation names outside advanced or diagnostic contexts.

#### Scenario: Main workflow avoids technical names
- **WHEN** a user follows the upload, analysis, edit, ready, and export workflow
- **THEN** primary headings, CTAs, status summaries, and helper text do not prominently show terms such as Gemini, FFmpeg, vidstab, YOLO, RQ, worker, or raw render jargon

#### Scenario: Advanced controls remain understandable
- **WHEN** a user opens advanced editing, tracking, subtitle, BGM, watermark, or export controls
- **THEN** labels describe the user outcome first and may include technical detail only as secondary explanation

### Requirement: Status and failure copy clarity
The web UI SHALL present loading, queued, running, done, failed, retry, and cancelled states with clear next actions in Traditional Chinese.

#### Scenario: Recoverable failure tells the user what to do
- **WHEN** a user sees a failed analysis, generation, tracking, BGM, export, or queue status
- **THEN** the visible copy explains the practical next action, such as retrying, waiting, checking source material, or downloading an available output

#### Scenario: Diagnostic detail remains available
- **WHEN** a backend error message is useful for support or debugging
- **THEN** the UI keeps the raw reason available in a secondary detail area without making it the primary message

### Requirement: Mobile copy fit
The beginner-facing copy SHALL fit the existing mobile-first layout without introducing horizontal overflow or hiding primary actions.

#### Scenario: Mobile viewport keeps primary actions visible
- **WHEN** the app is viewed at a narrow mobile width
- **THEN** revised labels wrap or compress safely and the primary next action remains visible and tappable

