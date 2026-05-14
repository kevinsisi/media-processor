## 1. Backend Fork Service

- [x] 1.1 Add a service that copies project settings, script, assets, asset files, tags, transcripts, coverage, and asset segments into fork-owned rows and paths.
- [x] 1.2 Add failure handling that rolls back database changes and best-effort removes copied files when required source media is missing or copy fails.
- [x] 1.3 Add `POST /projects/{project_id}/fork` and response schema wiring.

## 2. Frontend Fork Action

- [x] 2.1 Add API client/types for project forking.
- [x] 2.2 Add a project-list fork button with loading/error states and navigation to the copied project.

## 3. Verification And Documentation

- [x] 3.1 Add backend tests for successful fork, file independence, draft exclusion, and missing-source failure.
- [x] 3.2 Update roadmap and durable memory for the new fork workflow.
- [x] 3.3 Run backend/frontend validation and mark OpenSpec tasks complete.
