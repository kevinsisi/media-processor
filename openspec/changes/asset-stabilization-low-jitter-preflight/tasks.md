# Tasks

- [x] Add worker-side raw motion preflight using optical-flow/RANSAC global frame translation.
- [x] Separate slow intentional camera movement from high-frequency jitter with a rolling median residual.
- [x] Skip low-jitter assets with terminal `stabilization_status="skipped"` and measured jitter details.
- [x] Ensure `force=true` bypasses the preflight and still runs vidstab.
- [x] Skip previously `skipped` assets in project batch stabilization unless forced.
- [x] Add focused unit tests for skip and force paths.
- [x] Validate against production sample assets where one low-jitter clip should skip and one shaky clip should run.
