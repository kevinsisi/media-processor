# v0.27.1 — asset force-delete

**Status:** ✅ shipped 2026-05-04.

## Why

v0.26's asset deletion refused outright (`409 Conflict`) when an active draft (`pending` / `processing` / `ready_for_review` / `approved`) referenced the asset via `DraftSegment.asset_id`. Cleaning out a project that had been through multiple draft rounds turned into a multi-page chore: the operator had to switch to `ProjectEdit`, manually reject every blocking draft, then return to `ProjectAnalysis` and re-attempt the delete. With several assets to clean, that's 4–5 page switches per asset.

The hard-block was a fine v0.26 default — better to refuse than silently corrupt a render in flight — but the workflow it forces on the operator is wrong for "I'm decisively cleaning house and I know what I'm killing."

## What

Replace the hard-409 with a confirm-and-force flow:

1. **Default (`force=false`)** — same protective behavior as before, but communicated as data instead of an HTTP error. The endpoint always returns 200 with an `AssetDeleteOut` body; if `affected_drafts` is non-empty and `deleted=false`, the FE knows it needs to confirm with the user before retrying.

2. **`?force=true`** — for each affected draft:
   - Delete every `DraftSegment` row whose `(draft_id, asset_id)` pair points at this asset.
   - Recount remaining segments on that draft. If zero, flip `Draft.status = "failed"` and `Draft.prompt_feedback = "素材已被刪除"`. **Don't delete the draft row** — the operator needs the message to understand what died.
   - Then proceed with the normal asset cleanup (failed/rejected drafts cascade-deleted as before, `AssetTranscript` / `ScriptCoverage` cleared, source file + thumbnails wiped, `Asset` row deleted).

3. **FE flow:** one call without `force` → if any rows came back as needs-force, show a single grouped confirm listing every blocked asset's affected versions ("素材 #39 被 v1、v2 使用中") → second call with `force=true` re-using the same id list.

### Backend changes

- `services/asset_management.py`:
  - `AssetDeleteResult` dataclass replaces the exception-throw shape. Fields: `asset_id`, `deleted`, `affected_drafts: list[BlockingDraft]`, `invalidated_versions: list[int]`, `not_found: bool`, `error_message: str | None`.
  - `delete_asset(session, id, *, force=False)` returns `AssetDeleteResult`. The `not_found` flag drives the endpoint's 404 path; everything else surfaces in the body.
  - `_force_invalidate_drafts` is the new helper: per blocking draft, it deletes the relevant segments, flushes, recounts, and flips status/feedback when the count hits zero.
  - `AssetInUseError` is kept as an importable class for any external caller but is no longer raised. Removable on a future cleanup pass.
- `api/routers/assets.py` and `api/routers/projects.py`:
  - Both endpoints accept a `force: bool = False` query param.
  - Single-asset response goes from 204 No Content to 200 + `AssetDeleteOut`. 404 on missing row is preserved.
  - Batch response adds `affected_drafts` + `invalidated_versions` per row, plus `needs_force_count` + `error_count` at the top level so the FE can distinguish "user must confirm" from "actually broken".

### FE changes

- `web/src/api/types.ts`: new `AffectedDraftOut`, `AssetDeleteOut`; `AssetBatchDeleteResultItem` and `AssetBatchDeleteOut` extended.
- `web/src/api/client.ts`: `deleteAsset(id, {force?})` returns `AssetDeleteOut`; `batchDeleteAssets(projectId, ids, {force?})` returns the extended summary.
- `web/src/pages/ProjectAnalysis.tsx`: `runBatchDelete` rewritten as a two-call flow. The first confirm is the "really delete N items?" affordance; the second is the "these versions will be marked failed" warning.

## Risks / Out of scope

- **Half-wired drafts after force-delete.** A draft that loses _some_ but not _all_ of its segments to a force-delete is left in its current status with the orphaned segment list. The next render attempt will fail. We accepted this rather than auto-flipping such drafts to `failed` (which would hide the breakage from the operator) or auto-rerunning the planner (which is a much bigger surgery and not asked for).
- **Operator slip.** The confirm dialog is the only safety net. Two confirms (one initial "delete N items?", one specific "v1, v2 will be marked failed?") is the deliberate friction; we don't add a typed-confirm box because that's overkill for the volume of cleanup we expect.
- **Failed drafts referencing other (non-deleted) assets.** Their segments are untouched. They show in `ProjectList` as failed with `素材已被刪除`. The operator can dismiss them by triggering a fresh render or explicit reject.
- **No alembic migration.** Schema is unchanged — we're using existing columns (`Draft.status`, `Draft.prompt_feedback`, `DraftSegment.asset_id`).
- **Out of scope:** auto-replacement (a "use this other asset instead" dialog), partial-segment trimming, or saving the deleted asset's source elsewhere. Those are content-management features, not cleanup.
