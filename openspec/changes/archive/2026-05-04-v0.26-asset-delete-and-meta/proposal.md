## Why

Two operator-feedback items shipped together because they share the same scan path on the analysis page:

1. **No way to delete uploaded assets.** Wrong file uploaded → stuck with it forever. The trigger is high (each clip is 50–80 MB) and the workaround (drop the project + start over) is heavy.
2. **Analysis list doesn't surface duration / resolution / size.** Three identical-looking DJI filenames → the operator has to remember which one was the back-of-car shot vs the close-up of the wheel.

Both are about the operator being able to *see* and *control* the asset inventory.

## What Changes

### 1. Backend: single + batch asset delete

- **`DELETE /assets/{asset_id}`** — wipes on-disk source + thumbnails dir + DB row. 409 with the offending draft versions when the asset is still used by an active draft.
- **`DELETE /projects/{project_id}/assets/batch`** — body `{asset_ids: int[]}`, returns per-row outcomes `{deleted_count, blocked_count, results: [{asset_id, deleted, reason}]}`. Partial-failure surfaces row-by-row instead of a blanket 409.
- **`services/asset_management.py`** — shared service module with `delete_asset(session, id)` and `batch_delete_assets(session, ids)`. The active-draft check classifies drafts:
  - `pending / processing / ready_for_review / approved` → block, raise `AssetInUseError(blocking_versions)`. Endpoint translates to 409 with a "v3, v5" version list.
  - `failed / rejected` → cascade-delete via `await session.delete(draft)`, which walks `Draft.segments` cascade and frees the FK constraint on `DraftSegment.asset_id` (which is `ondelete="RESTRICT"`).
- Cross-project ids (an `asset_id` in the request body that doesn't belong to the project) are filtered out at the endpoint level so a request body can't reach into another project's rows.

### 2. Backend: AssetAnalysisItem extended

- `resolution: str | None` — already on `Asset.resolution` from upload-time ffprobe; just propagate.
- `file_size_bytes: int | None` — `Path(asset.file_path).stat().st_size` at request time. Best-effort; `None` when the file is missing on disk. Not cached on the row because the file lifecycle is owned by upload + delete and a stale cached value would lie about current state.

### 3. Frontend: analysis card meta line + bulk-delete button

- New `formatBytes(bytes)` helper (B / KB / MB / GB, one decimal place).
- Asset card shows a one-line spec under the filename: `05:38 · 1728×3072 · 67.6 MB`. Each segment falls back to `—` when the value is null so the line keeps a stable shape.
- The existing batch toolbar (which already has `重新分析所選` / `強制重跑`) gains a `刪除所選（N）` button styled with the existing `cta--danger` class. `window.confirm` reminds the user the action is irreversible. Partial-failure (some assets blocked by an active draft) lists the offending rows in the existing `triggerError` panel so the user sees `素材 #18：still used by active draft(s): v3, v5`.
- Selection state and the per-row checkboxes already existed for the analysis-rerun batch flow — the delete handler reuses the same `selectedIds` set + `clearSelection` / `polling.refresh` hooks.

## Impact

- **Schema**: no migrations. `Asset` already had `resolution`; `file_size_bytes` is statted at request time.
- **API surface**: two new DELETE endpoints + three new schemas (`AssetBatchDeleteRequest`, `AssetBatchDeleteResultItem`, `AssetBatchDeleteOut`). Backwards compatible — older clients ignore the new endpoints + the new fields on `AssetAnalysisItem` (added with `= None` defaults).
- **Frontend**: one new button in an existing toolbar; one new line on each asset card. No layout shift on existing flows.
- **Backwards compat**: `AssetAnalysisItem.resolution` and `.file_size_bytes` are `null`-able on legacy data (assets uploaded before ffprobe was wired up; assets whose source file was manually pruned). FE renders `—` for either case. Existing endpoints and behaviours unchanged.

## Non-obvious gotchas (worth remembering)

### `select(Draft).distinct()` 500s on PostgreSQL

The first cut of `_blocking_drafts_for(asset_id)` did:

```python
select(Draft).join(DraftSegment).where(...).distinct()
```

…and 500'd at runtime with `asyncpg.exceptions.UndefinedFunctionError: could not identify an equality operator for type json`. PostgreSQL ships no `=` operator for the `json` type (it does for `jsonb`); DISTINCT needs equality on every projected column. `Draft` has three JSON columns (`cut_plan_json` / `progress_steps_json` / `render_flags_json`), so a row-DISTINCT on `Draft` can't deduplicate.

Fix: project scalar tuples that have well-defined equality:

```python
select(Draft.id, Draft.version).join(...).distinct()
```

For the cascade-delete path that needs full ORM rows, do it in two steps: SELECT the ids first (cheap, no JSON issue), then re-fetch full rows by id. Same rule applies to any future query that wants DISTINCT on a row containing a JSON column.

### Disk cleanup runs BEFORE the DB delete

```python
_delete_on_disk(asset)        # 1. unlink mp4 + rmtree thumbnails
await session.delete(asset)   # 2. drop DB row
```

If the disk write fails (permissions, full disk, NFS hiccup), we want the row to stay so the user can retry. The opposite order would lose `Asset.file_path` — the row is the only canonical record of where the source file actually lives. Both `Path.unlink` and `shutil.rmtree` are wrapped in `OSError`-swallowing try blocks so a missing-file (already cleaned) state is not a failure; the row delete still proceeds.

### `BLOCKING_DRAFT_STATUSES` is the classification rule

`DraftSegment.asset_id` is `ondelete="RESTRICT"`, so any draft segment referencing an asset blocks the asset delete. The service draws the line at four "active" draft statuses; failed and rejected drafts are auto-cascade-deleted in the same transaction. If a future PR adds a new `DraftStatus` enum value, it MUST be classified — either added to `BLOCKING_DRAFT_STATUSES` (refuses asset delete; user must reject the draft first) or left out (auto-cascade-deletes when the asset is removed). The default of "leaving it out" silently flips the new status into the auto-cleanup bucket; if the new status is meant to be active, the asset-delete path will silently nuke those drafts.
