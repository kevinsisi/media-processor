## Why

The product direction is now explicit: help non-technical beginners produce many IG/FB short videos with minimal manual editing. The existing system is powerful, but the UI still exposes editor/operator complexity and does not clearly guide a novice from upload to social-ready output.

## What Changes

- Reframe the ready draft page as a social publishing workbench: preview, download, platform exports, and regenerate are primary; advanced editing/settings are secondary.
- Add novice-friendly IG/FB export presets so users choose a destination instead of raw aspect/resolution first.
- Protect script edits during upload so a beginner cannot paste text and lose the latest changes by tapping next too quickly.
- Redirect the legacy review route to the current edit/publishing workflow.
- Replace the most visible implementation-heavy copy with operator-facing Traditional Chinese.
- Document larger follow-up work for batch generation, publish packages, captions/hashtags, and long-video mode.

## Capabilities

### New Capabilities
- `novice-social-shorts-workflow`: Beginner-facing flow for producing social short videos with clear primary actions and safe defaults.
- `social-export-presets`: Platform-oriented export presets for IG/FB destinations using the existing export artifact backend.
- `upload-save-safety`: Upload/script flow prevents unsaved script edits from being lost during navigation.

### Modified Capabilities
None.

## Impact

- Frontend: `Upload`, `ProjectEdit`, `ExportSheet`, routing, and selected operator copy.
- Backend: no new database tables in this slice; social presets use the existing `POST /drafts/{id}/export` and `GET /drafts/{id}/exports` APIs.
- Docs/spec: record novice user target, IG/FB short-video positioning, and follow-up roadmap.

## Follow-up Backlog

- Generate many variations from one long video using batch render jobs and style/platform presets.
- Add publish packages: caption, title, hashtags, thumbnail, platform readiness, and eventual OAuth publishing.
- Add long-video mode with duration-aware ETA and timeout budgets.
- Replace remaining browser confirms with mobile sheets.
- Add guided “upload complete -> auto analyze -> auto edit” wizard mode.
