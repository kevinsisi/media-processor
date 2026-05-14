## Why

Operators need a safe way to duplicate an existing project before trying different editing settings, asset variants, or timeline changes. A fork should be independently editable and deletable so experiments never mutate the original project's assets, analysis state, scripts, or drafts.

## What Changes

- Add a project fork capability that creates a new project from an existing one.
- Copy project-level settings, script/coverage data, assets, source files, stabilized derivatives, tags, transcripts, and segment metadata into the fork.
- Do not copy rendered draft outputs or draft rows; the fork starts ready for fresh editing/rendering from copied source state.
- Add a frontend action from the project list/detail flow so an operator can fork and open the copied project.

## Capabilities

### New Capabilities

- `project-forking`: Covers creation of independent project copies for experimentation without affecting the source project.

### Modified Capabilities

None.

## Impact

- Backend API: new project fork endpoint and response shape.
- Backend services: copy project metadata, related rows, and media files into a new project asset directory.
- Frontend API/client and project UI: expose fork action and navigation to the new project.
- Tests: cover copied data independence, copied files, and draft exclusion.
