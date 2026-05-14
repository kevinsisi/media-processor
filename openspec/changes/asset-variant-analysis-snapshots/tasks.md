## 1. Persistence

- [x] 1.1 Add DB column for per-variant analysis snapshots.
- [x] 1.2 Save current variant analysis before switching source variant.
- [x] 1.3 Restore target variant analysis from DB when available.
- [x] 1.4 Enqueue re-analysis only when no target snapshot exists.

## 2. UI / API

- [x] 2.1 Return `restored_from_snapshot` from variant switch API.
- [x] 2.2 Update UI confirmation/status copy to explain DB restore vs new analysis.

## 3. Verification

- [x] 3.1 Add route regression test for switching back without enqueueing analysis.
- [x] 3.2 Run backend tests and frontend build.
- [x] 3.3 Validate OpenSpec changes.

## 4. Completion

- [x] 4.1 Update roadmap and memory.
- [x] 4.2 Commit and push to main.
