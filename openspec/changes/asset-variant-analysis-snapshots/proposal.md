## Why

Switching an asset between `raw` and `stabilized` currently clears source-coordinate-dependent analysis and immediately re-enqueues analysis. Repeatedly comparing the two versions burns GPU work and Gemini quota even when that version has already been analyzed before.

## What Changes

- Add a persisted DB JSON snapshot per asset variant.
- Before switching variants, save the current variant's analysis rows/columns into the DB snapshot.
- When switching to a target variant, restore its DB snapshot if present instead of enqueueing analysis.
- Only enqueue analysis when the target variant has no stored DB snapshot.

## Capabilities

### Modified Capabilities

- `asset-variant-workflow`: Variant switching must persist and restore per-version analysis results instead of treating them as disposable cache.
- `analysis-cost-control`: Switching back to an already analyzed version must not re-run GPU or Gemini analysis.

## Impact

- Adds `assets.variant_analysis_json` via Alembic migration.
- API response for variant switching adds `restored_from_snapshot`.
- Frontend copy now tells the operator whether analysis was restored from DB or newly queued.
