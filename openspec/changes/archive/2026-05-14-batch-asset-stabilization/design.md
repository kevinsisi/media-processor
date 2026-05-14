## Context

`POST /assets/{id}/stabilize` already queues one asset for full-source stabilization. Uploads also best-effort enqueue stabilization, but older projects and project forks can contain many `not_started` assets. The current per-card action scales poorly for 10+ assets.

## Goals / Non-Goals

**Goals:**

- Queue missing or failed stabilized variants for every asset in a project with one API call.
- Avoid duplicate work by skipping assets that are already `done`, `pending`, or `running` by default.
- Keep the active variant unchanged; batch generation prepares previews but does not switch raw/stabilized selection.

**Non-Goals:**

- No automatic switch to stabilized when jobs finish.
- No new worker queue; reuse the existing analysis queue and asset stabilization job.
- No concurrency fan-out changes beyond enqueueing one job per eligible asset.

## Decisions

- Add `POST /projects/{project_id}/assets/stabilize` so the operation is clearly project-scoped and sits beside existing project asset management APIs.
- Response returns per-asset results plus aggregate counts. This lets the UI communicate partial enqueue failures without guessing from polling.
- On each eligible asset, persist `pending`, clear error, and set the expected stabilized path before enqueueing, mirroring the single-asset endpoint.
- If enqueue fails for one asset, mark that asset `failed` with the enqueue error and continue processing the rest.

## Risks / Trade-offs

- A large project can enqueue many long stabilization jobs. Mitigation: skipped states prevent duplicate jobs, and the existing queue/status UI shows backlog.
- Sequential enqueue means partial success is possible. Mitigation: response lists failed items and failed rows become retryable terminal states.
