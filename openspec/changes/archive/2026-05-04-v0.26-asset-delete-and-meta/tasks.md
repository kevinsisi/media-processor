# Tasks — v0.26.0 (asset delete + analysis-card meta)

## 1. Backend: shared service module

- [x] 1.1 New `services/asset_management.py` with `AssetDeleteError`, `AssetNotFoundError`, `AssetInUseError(asset_id, blocking_draft_versions)`, `BLOCKING_DRAFT_STATUSES`, `delete_asset(session, asset_id)`, `batch_delete_assets(session, asset_ids)`.
- [x] 1.2 `_blocking_draft_versions_for(session, asset_id)` projects `(Draft.id, Draft.version)` tuples with `.distinct()` so the PostgreSQL "no equality operator for type json" error from a row-DISTINCT on `Draft` doesn't trip the request. Returns the sorted unique version list directly.
- [x] 1.3 `_drop_dead_drafts_referencing(session, asset_id)` two-step: SELECT distinct draft ids first (no JSON issue), then re-fetch by id and `await session.delete(d)` so the ORM cascade walks Draft.segments + Draft.comments + Draft.reviews.
- [x] 1.4 `_delete_on_disk(asset)` runs BEFORE the DB delete so a disk error leaves the row intact. Both `Path.unlink` and `shutil.rmtree` wrapped in `OSError`-swallowing try blocks; missing files are not failures.
- [x] 1.5 Service deletes orphaned `AssetTranscript` + `ScriptCoverage` rows explicitly because those tables don't have a relationship cascade onto Asset.

## 2. Backend: endpoints

- [x] 2.1 `DELETE /assets/{asset_id}` in `api/routers/assets.py`. Maps `AssetNotFoundError` → 404, `AssetInUseError` → 409 with a "v3, v5" message. Commits on success.
- [x] 2.2 `DELETE /projects/{project_id}/assets/batch` in `api/routers/projects.py`. Body schema `AssetBatchDeleteRequest` with `asset_ids: list[int] = Field(min_length=1)`.
- [x] 2.3 The batch endpoint filters out cross-project ids with one SELECT (`Asset.project_id == project_id AND Asset.id IN (...)`), records each rejected id with `reason="asset not in this project"`, then calls `batch_delete_assets` for the survivors.
- [x] 2.4 Response schema `AssetBatchDeleteOut(deleted_count, blocked_count, results: list[AssetBatchDeleteResultItem])`. Always 200 — partial-failure is a normal response, not an error.

## 3. Backend: AssetAnalysisItem extended

- [x] 3.1 `AssetAnalysisItem.resolution: str | None = None` and `AssetAnalysisItem.file_size_bytes: int | None = None` added to `api/schemas.py`.
- [x] 3.2 The `_assets_for_project_response` builder in `api/routers/projects.py` propagates `asset.resolution` and statics-call `Path(asset.file_path).stat().st_size` per asset, falling back to `None` on `OSError`.

## 4. Frontend

- [x] 4.1 `web/src/api/types.ts` — `AssetAnalysisItem.resolution` + `AssetAnalysisItem.file_size_bytes` (both `?: T | null`); new `AssetBatchDeleteRequest`, `AssetBatchDeleteResultItem`, `AssetBatchDeleteOut` interfaces.
- [x] 4.2 `web/src/api/client.ts` — `deleteAsset(assetId)` (raw fetch path because 204 No Content) and `batchDeleteAssets(projectId, assetIds)` (returns the per-row outcomes).
- [x] 4.3 `web/src/pages/ProjectAnalysis.tsx` — new `formatBytes(bytes)` helper (B / KB / MB / GB, one decimal); `AssetCard` meta line `MM:SS · {resolution} · {size}` with `—` fallbacks.
- [x] 4.4 `runBatchDelete` callback — `window.confirm` first, then `apiClient.batchDeleteAssets`, surface partial-failure rows in the existing `triggerError` panel, refresh polling on completion.
- [x] 4.5 `刪除所選（N）` button in the existing batch toolbar with `cta--danger` style; disabled when nothing is selected or batch is running.

## 5. Verification (live)

- [x] 5.1 `DELETE /assets/20` (project 4, used by ready_for_review drafts v2, v5) → 409 `{"detail":"asset is still used by active draft(s) v2, v5; reject those drafts first or trigger a fresh render…"}`.
- [x] 5.2 `DELETE /projects/4/assets/batch` body `{"asset_ids":[99999, 888]}` (cross-project ids) → 200 with `{"deleted_count":0, "blocked_count":2, "results":[{"asset_id":99999, "deleted":false, "reason":"asset not in this project"}, …]}`.
- [x] 5.3 `GET /projects/4/assets` returns each asset with `resolution="1728x3072"` and `file_size_bytes=67600546` etc.
- [x] 5.4 OpenAPI lists the two new DELETE routes.

## 6. Memory + docs + version bump

- [x] 6.1 `memory/v026_asset_delete.md` — load-bearing gotchas: the JSON-DISTINCT trap, the disk-before-DB ordering, the `BLOCKING_DRAFT_STATUSES` classification rule for new draft statuses.
- [x] 6.2 `memory/MEMORY.md` index entry; `project_media_processor_v2.md` snapshot bumped to 0.26.0.
- [x] 6.3 `ROADMAP.md` — Phase 9.11 with subsections 9.11.1 (backend endpoints) / 9.11.2 (meta fields) / 9.11.3 (FE); current-version line; M10 deferred to 0.27.x+.
- [x] 6.4 `CLAUDE.md` — current-version line; new `services/asset_management.py` architecture pointer mentioning the JSON-DISTINCT trap.
- [x] 6.5 Version bumped to 0.26.0 in `pyproject.toml` + `src/media_processor/api/main.py` + `web/package.json`.
- [x] 6.6 Branched as `claude/v0.26.0-asset-delete-and-meta`, merged --no-ff into `main`, pushed; docker compose build api worker web + up -d; `/health` returns 0.26.0; the two new endpoints smoke-tested for 404/409/200/cross-project rejection. Branch pruned local + remote.
