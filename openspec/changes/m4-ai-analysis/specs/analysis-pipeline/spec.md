# analysis-pipeline (NEW)

## Purpose

Coordinate the four AI analysis steps (STT → scene → motion → coverage) for an uploaded asset, persist per-step status so failures don't lose earlier results, and run the work in a dedicated GPU-enabled worker container so the API stays responsive.

## Requirements

### REQ-1: Job entry point

- The worker exposes `analyze_asset(asset_id: int, *, steps: list[str] | None = None, force: bool = False) -> None` as the RQ job target.
- `steps` defaults to `["stt", "scene", "motion", "coverage"]` when omitted; otherwise restricts execution to the named subset (any unknown step is rejected before any work runs).
- The job is enqueued by `POST /uploads/{sid}/complete` for `kind=video` and by `POST /assets/{id}/analyze`.

### REQ-2: Sequential step execution with isolation

- Steps run in the canonical order `stt → scene → motion → coverage`. A later step is not skipped because an earlier step failed (with the documented exception in REQ-4).
- Each step runs inside its own `try/except`. On exception, the job records `failed:{reason}` for that step in `assets.analysis_steps_json` and continues to the next step. The job does NOT propagate the exception out to RQ — it exits successfully.
- Each step has a 30-minute wall-clock budget; exceeding it records `failed:timeout` for that step and continues.

### REQ-3: Progressive status persistence

- Before any step runs, the job sets `assets.status='analyzing'` and `analysis_steps_json` to `{stt:"pending", scene:"pending", motion:"pending", coverage:"pending"}` for the requested steps (others stay at their existing value).
- On step entry, the corresponding key flips to `"running"`. On step success it flips to `"done"`. On step failure it flips to `"failed:{reason}"`.
- After all requested steps complete, `assets.status` is set to:
  - `analyzed` if no requested step ended in `failed:*` AND no other step is currently `running`/`pending`,
  - `analysis_failed` only if every requested step ended in `failed:*` (so a partial success still reaches `analyzed`).

### REQ-4: Skip rules

- The `coverage` step records `failed:missing-script` and exits early when the asset's project has no `Script` row OR the row has empty body.
- The `stt` step is skipped (recorded as `done` without writing) when `force=False` AND `asset_transcripts.edited=true`. The reasoning: the operator's hand edit must not be overwritten by an unforced re-run.

### REQ-5: Force semantics

- `force=True` re-runs every requested step even if previously `done`:
  - `stt`: replaces `asset_transcripts` row, sets `edited=false`.
  - `scene`: deletes `asset_tags` rows where `tag_type='scene' AND source_model LIKE 'gemini-vision-%'` for the asset, then refills.
  - `motion`: deletes `asset_tags` rows where `tag_type='motion' AND source_model='opencv-optical-flow'` for the asset, then refills.
  - `coverage`: replaces the `script_coverage` row.

### REQ-6: Worker container isolation

- Analysis code does not import from `api/routers/*`. Worker process imports models, services, and a thin `analysis.run_pipeline` orchestrator only.
- Worker container starts with `python -m media_processor.workers` which runs `rq worker analysis` against the project's Redis URL and registers signal handlers so SIGTERM stops the worker between jobs (not mid-job) and exits cleanly.

### REQ-7: Failure visibility

- The `failed:{reason}` reasons are drawn from a documented set: `gpu-unavailable`, `quota-exhausted`, `model-error:{short}`, `disk-error:{short}`, `timeout`, `missing-script`. The orchestrator maps caught exception types to these tokens; unknown exceptions fall back to `model-error:{exception_class_name}`.
- `GET /assets/{id}` includes the raw `analysis_steps_json` so the operator can read the reason string; the UI surfaces a localised summary chip per failure class.
