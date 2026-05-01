# project-creation (NEW)

## Purpose

Operators create a project from the web UI, choosing a profile and the IG target aspect ratio. The project becomes the container under which video assets and a script are uploaded.

## Requirements

### REQ-1: HTTP create endpoint

- `POST /projects` accepts JSON body `{name: string, client?: string|null, profile_name: string, target_aspect_ratio: '9:16'|'4:5'|'1:1'}`.
- Response 201 returns the full `ProjectDetail` of the created row, including `id`, `created_at`, and the chosen `target_aspect_ratio`.
- Default `target_aspect_ratio` is `'9:16'` if the client omits it.
- Invalid `target_aspect_ratio` returns 422.

### REQ-2: Aspect ratio is persistent

- The chosen ratio is written to `projects.target_aspect_ratio` and survives the request — subsequent `GET /projects/{id}` returns it.
- The downstream pipeline reads this column; M3 itself does not act on it beyond storage and display.

### REQ-3: Source dir defaulted server-side

- Until the Ingest Watcher lands (post-M3), `source_dir` is set server-side to a deterministic path under `MEDIA_STORAGE_DIR/assets/{project_id}/`. The client does not provide it.
