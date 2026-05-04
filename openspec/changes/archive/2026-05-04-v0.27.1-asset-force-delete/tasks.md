# v0.27.1 — tasks (all done)

## Backend

- [x] `services/asset_management.py` — replace exception shape with `AssetDeleteResult` dataclass + `BlockingDraft`. `delete_asset(session, id, *, force=False)` returns the result; `not_found=True` drives the endpoint 404, `deleted=False` + `affected_drafts` is the FE-confirm signal, `deleted=True` + `invalidated_versions` is the success-with-side-effects shape. New `_force_invalidate_drafts` helper handles the segment-wipe + zero-segment status-flip per blocking draft. Pre-existing `_drop_dead_drafts_referencing` survives untouched (its join on `DraftSegment.asset_id == asset_id` correctly skips the drafts whose segments we just wiped). `AssetInUseError` kept import-compat-only.
- [x] `services/asset_management.batch_delete_assets` — threads `force` kwarg through to per-asset call; returns `dict[int, AssetDeleteResult]`. Internal exceptions stash on `AssetDeleteResult.error_message`.
- [x] `api/schemas.py` — new `AffectedDraftOut`, `AssetDeleteOut`. `AssetBatchDeleteResultItem` adds `affected_drafts` + `invalidated_versions`. `AssetBatchDeleteOut` adds `needs_force_count` + `error_count`; `blocked_count` retained as the sum (back-compat).
- [x] `api/routers/assets.py` — `DELETE /assets/{id}` returns `AssetDeleteOut` (200) instead of 204. Accepts `force: bool = False` query. 404 on missing row preserved.
- [x] `api/routers/projects.py` — `DELETE /projects/{id}/assets/batch` accepts `force: bool = False`. Per-row reason logic distinguishes `not_found` / `error_message` / blocked-on-active-drafts / success.

## FE

- [x] `web/src/api/types.ts` — `AffectedDraftOut`, `AssetDeleteOut`, extended `AssetBatchDeleteResultItem` + `AssetBatchDeleteOut`.
- [x] `web/src/api/client.ts` — `deleteAsset(id, {force?})` returns `AssetDeleteOut`; `batchDeleteAssets(projectId, ids, {force?})` adds the option. Both append `?force=true` when set.
- [x] `web/src/pages/ProjectAnalysis.tsx` — `runBatchDelete` rewritten:
  - First call without force.
  - If `summary.needs_force_count > 0`: build a confirm dialog listing every blocked asset's affected versions ("素材 #39 被 v1、v2 使用中") with the tail "刪除後上述版本將被標為「失敗（素材已被刪除）」。確定刪除？".
  - On confirm, re-issue the SAME id list with `force=true`.
  - Result panel surfaces `invalidated_versions` ("素材 #39：連帶將 v1 標為失敗") + non-affected-draft errors separately.
- [x] Bulk-delete button title updated to reflect the new flow.

## Verify

- [x] `npm run build` clean.
- [x] `docker compose build api web` — both images build.
- [x] `docker compose up -d api web` — api 0.27.1 + web 0.27.1 up; workers from v0.27.0 still running.
- [x] `GET /health` returns `version=0.27.1`.
- [x] OpenAPI shows `force: bool = false` query param on both delete endpoints; response references `AssetDeleteOut` / `AssetBatchDeleteOut`.
- [x] `DELETE /assets/99999` (non-existent) returns 404 (404 path preserved).

## Docs / memory

- [x] `ROADMAP.md` — bump current version to 0.27.1, add M9.12.1 row + section, push table marker.
- [x] `CLAUDE.md` — bump current version, add `v0.27.1 asset force-delete` to archived list, rewrite `services/asset_management.py` pointer to describe the new force-delete shape.
- [x] `memory/v0271_asset_force_delete.md` — load-bearing notes on confirm-and-force flow, why we keep the failed draft row, why the cascade-cleanup doesn't accidentally re-delete the just-marked-failed drafts.
- [x] `memory/MEMORY.md` — index entry.
- [x] `memory/project_media_processor_v2.md` — frontmatter version bump, current-version paragraph rewritten, "Where to look" pointer.
- [x] This proposal + tasks under `openspec/changes/archive/2026-05-04-v0.27.1-asset-force-delete/`.

## Versions

- [x] `pyproject.toml`: `0.27.0` → `0.27.1`
- [x] `src/media_processor/api/main.py`: FastAPI `version=` → `0.27.1`
- [x] `web/package.json`: `0.27.0` → `0.27.1`
