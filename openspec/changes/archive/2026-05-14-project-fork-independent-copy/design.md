## Context

Projects own user-facing settings, uploaded assets, optional BGM/watermark files, script text, analysis metadata, transcripts, coverage rows, and rendered drafts. Operators currently have to modify the original project when they want to compare different edit settings or asset variants, which is risky because asset deletion, variant switching, and draft regeneration can invalidate existing state.

## Goals / Non-Goals

**Goals:**

- Create a forked project that can be edited, re-analyzed, rendered, and deleted independently from the source project.
- Preserve source project settings and analysis metadata so the fork is immediately useful without re-uploading media.
- Copy media files into paths owned by the forked project so destructive actions on either project do not remove files still needed by the other.
- Start the fork without rendered drafts so old outputs are not mistaken for results from the copied project.

**Non-Goals:**

- No database schema migration; the fork is represented by existing project, asset, script, tag, transcript, coverage, and segment rows.
- No deep-copy of draft rows, reviews, comments, subtitle cue rows, draft exports, or rendered output files.
- No background render or analysis job is automatically triggered by forking.

## Decisions

- Fork via `POST /projects/{project_id}/fork` and return `ProjectDetail` for the new project. This keeps the operation explicit and lets the frontend navigate to the new project's existing pages without introducing a new route.
- Create the new `Project` first, flush to obtain its id, then copy files into `assets/{new_project_id}`, `assets/{new_project_id}/_stabilized`, `bgm`, and `watermarks` paths named for the new project. This preserves URL conventions and avoids shared filesystem references.
- Copy asset metadata rows after file copies succeed. Each copied `Asset` gets a new id and points at copied raw/stabilized files; child `AssetTag`, `AssetSegment`, `AssetTranscript`, and `ScriptCoverage` rows are recreated against the new asset ids.
- Copy `Script` before `ScriptCoverage` so coverage rows can reference the fork's script id. If the source has no script, coverage rows are omitted because their foreign key would otherwise point back to source state.
- Do not copy `Draft` rows. A fork should be a clean experiment base; the first render on the fork creates version 1 from the fork's copied assets/settings.
- Fail the request if a referenced source media file is missing. Silent partial forks would be harder to reason about than a clear error, and the database transaction can roll back newly inserted rows while best-effort cleanup removes copied files.

## Risks / Trade-offs

- Copying large assets can take noticeable time in the API request. Mitigation: keep the first version synchronous for correctness and testability; if production usage shows long copies, move the same service into a worker job later.
- A filesystem copy can succeed before a later database error rolls back. Mitigation: track copied paths and best-effort delete them on failure.
- Forked analysis metadata can be stale if source files were manually changed out-of-band. Mitigation: uploaded asset paths are treated as immutable elsewhere; this feature follows that existing assumption.
