# Change: asset-stabilization-low-jitter-preflight

## Why

Production raw/stabilized comparison showed source-level vidstab is real but not universally beneficial: low-jitter DJI clips that were already below the feature-tracking noise floor became slightly worse after stabilization. The system needs a preflight gate so batch/manual stabilization does not create stabilized derivatives for clips where the correction is more likely to invent compensation jitter than remove shake.

## What Changes

- Measure raw high-frequency jitter before worker-side source stabilization.
- Skip vidstab when residual translation jitter is already below the calibrated low-jitter threshold.
- Persist skipped assets as terminal `stabilization_status="skipped"` with the measured jitter values in `stabilization_error`.
- Preserve `force=true` as an operator override that bypasses the low-jitter gate.

## Impact

- Affects asset-level stabilization jobs only.
- Render-level stabilization behavior is unchanged.
- No database migration is required because stabilization status is an unconstrained string column.
