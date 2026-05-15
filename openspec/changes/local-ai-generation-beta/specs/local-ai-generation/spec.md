## ADDED Requirements

### Requirement: Beta feature gating via environment flag

The system SHALL gate all local AI generation surface area behind the `AI_GEN_ENABLED` environment variable, which MUST default to `false`. When the flag is `false`, the system MUST NOT expose any AI generation entry point to end users and MUST NOT consume GPU resources for generation work.

#### Scenario: Flag is false

- **WHEN** `AI_GEN_ENABLED=false` (or unset) and a client calls `POST /generations/image`, `POST /generations/video`, `GET /generations/{id}`, `DELETE /generations/{id}`, or `GET /ai/presets`
- **THEN** the system SHALL return HTTP 404 with body `{detail: "AI generation disabled"}`

#### Scenario: Flag is true and ComfyUI reachable

- **WHEN** `AI_GEN_ENABLED=true`, the api container is running, and the `comfyui` service is responding on its configured port
- **THEN** `GET /ai/health` SHALL return `{enabled: true, comfyui_up: true, models_loaded: {flux: <bool>, ltx: <bool>}}` and the existing `/health` payload SHALL include an `ai_gen: {enabled: true, comfyui_up: true}` block

#### Scenario: Flag is false health surface

- **WHEN** `AI_GEN_ENABLED=false` and `GET /ai/health` is called
- **THEN** the system SHALL return `{enabled: false}` and SHALL NOT attempt to reach the `comfyui` service

### Requirement: GPU exclusivity across all forward-pass consumers

The system SHALL serialise GPU forward-pass work across `worker-analysis`, `worker-bgm`, `worker-imggen`, and `worker-videogen` using a single redis-backed exclusive lock keyed `gpu:exclusive`. Every GPU forward pass MUST be wrapped in an acquire-release block; lock acquire MUST block until the lock is free or a 600-second timeout elapses; lock release MUST happen even when the wrapped work raises.

#### Scenario: Lock initialisation on api startup

- **WHEN** the api container starts and its FastAPI lifespan runs
- **THEN** the system SHALL ensure the redis list at `gpu:exclusive` contains exactly one token, initialising it only if the list is empty

#### Scenario: Concurrent generation and analysis

- **WHEN** a `worker-imggen` job holds the lock and a `worker-analysis` job becomes ready
- **THEN** the analysis job SHALL block on lock acquire and SHALL only begin its forward-pass work after the imggen worker releases the lock

#### Scenario: Lock acquire timeout

- **WHEN** a worker calls `gpu_exclusive(...)` and the lock is not released within 600 seconds
- **THEN** the worker SHALL raise `GpuLockTimeout` and the corresponding `generation_job` (if any) SHALL be marked `status="failed"` with `error="GPU busy timeout"`; the job SHALL NOT be auto-retried

#### Scenario: Lock release on exception

- **WHEN** GPU work inside `gpu_exclusive(...)` raises any exception
- **THEN** the lock SHALL still be released so the next waiter proceeds; the original exception SHALL propagate to the worker job runner unchanged

### Requirement: Preset registry with Chinese labels and bundled English prompts

The system SHALL expose a curated set of presets for image edits and video camera motions. Each preset MUST have a stable kebab-case `key`, a Chinese label, a Chinese description, a `kind` of `image` or `video`, and a bundled English prompt used by the underlying workflow. The English prompt MUST NOT be exposed through `/ai/presets`.

#### Scenario: Listing image presets

- **WHEN** a client calls `GET /ai/presets?kind=image`
- **THEN** the system SHALL return an array of `{key, label_zh, description_zh}` objects covering exactly the registered image presets and SHALL NOT include English prompts

#### Scenario: Listing video presets

- **WHEN** a client calls `GET /ai/presets?kind=video`
- **THEN** the system SHALL return the registered video presets in the same `{key, label_zh, description_zh}` shape

#### Scenario: Unknown preset

- **WHEN** a client submits `POST /generations/image` or `POST /generations/video` with a `preset_key` not in the registry
- **THEN** the system SHALL return HTTP 422 with a body identifying the unknown key and SHALL NOT create a `generation_job` row

### Requirement: Image generation job lifecycle

The system SHALL accept image generation requests, persist them as `generation_job` rows with `kind="image"`, enqueue them on the `imggen` RQ queue, and execute them via ComfyUI using the FLUX image-edit workflow. The `prompt_en` MUST be frozen onto the row at enqueue time using the preset registry; subsequent preset changes MUST NOT alter past rows.

#### Scenario: Successful image generation

- **WHEN** a client calls `POST /generations/image` with a valid `project_id`, `source_asset_id`, and registered `preset_key`
- **THEN** the system SHALL create a `generation_job` row with `status="queued"`, `kind="image"`, `prompt_en` equal to the registry's English prompt for that preset, a random `seed`, and the current `workflow_version`; SHALL enqueue the job to `imggen`; AND SHALL return `{job_id: <id>}` with HTTP 202

#### Scenario: Worker completes a successful image job

- **WHEN** the `worker-imggen` runner picks up a queued job and the ComfyUI workflow completes without error
- **THEN** the system SHALL register a new `Asset` row with `source_type="generated_image"`, `parent_asset_id=<source_asset_id>`, and `generation_job_id=<job_id>`; SHALL update the `generation_job` row with `status="done"`, `output_asset_id=<new_asset_id>`, and `completed_at=<now>`

#### Scenario: ComfyUI workflow failure

- **WHEN** the ComfyUI HTTP API returns an error or the workflow execution fails
- **THEN** the worker SHALL update the `generation_job` row with `status="failed"`, `error="ComfyUI: <message>"`, and `completed_at=<now>`; SHALL NOT create an Asset; AND SHALL NOT auto-retry

#### Scenario: Reroll

- **WHEN** a client re-issues `POST /generations/image` with the same `source_asset_id` and `preset_key`
- **THEN** the system SHALL create a brand-new `generation_job` row with a fresh random seed; SHALL NOT modify or delete previous rows for the same source

### Requirement: Video generation job lifecycle

The system SHALL accept video generation requests, persist them as `generation_job` rows with `kind="video"`, enqueue them on the `videogen` RQ queue, and execute them via ComfyUI using the LTX-Video I2V workflow. Each request MUST include two image Asset ids — `first_frame_asset_id` (the same image is permitted as the second) and `last_frame_asset_id`. The output Asset MUST be a video file with non-null `duration_s` and `resolution` populated.

#### Scenario: Successful video generation

- **WHEN** a client calls `POST /generations/video` with valid `project_id`, `first_frame_asset_id`, `last_frame_asset_id`, and a registered video `preset_key`
- **THEN** the system SHALL create a `generation_job` row with `kind="video"`, `source_asset_id=<first_frame_asset_id>`, `second_source_asset_id=<last_frame_asset_id>`, frozen `prompt_en`, fresh random `seed`, and `status="queued"`; SHALL enqueue the job; AND SHALL return `{job_id}` with HTTP 202

#### Scenario: Worker completes a successful video job

- **WHEN** the `worker-videogen` runner completes the LTX I2V workflow and downloads the output mp4
- **THEN** the system SHALL probe the file with ffprobe; SHALL register a new `Asset` with `source_type="generated_video"`, `duration_s` and `resolution` filled from the probe, `parent_asset_id=<first_frame_asset_id>`, and `generation_job_id=<job_id>`; SHALL update the `generation_job` row to `status="done"` with `output_asset_id` and `completed_at`

#### Scenario: Output asset is consumable by existing render pipeline

- **WHEN** a `source_type="generated_video"` Asset exists in a Project
- **THEN** the existing cut-plan, Smart Camera, subtitle, and BGM-mix code paths SHALL treat the Asset identically to any `source_type="uploaded"` Asset; downstream consumers MUST NOT branch on `source_type`

### Requirement: Generation job status polling and cancellation

The system SHALL expose status of each `generation_job` row to clients and SHALL permit cancellation of queued jobs that have not yet started executing. Running jobs MUST NOT be cancellable through the generation API (consistent with the existing `/queue/jobs/{id}` DELETE convention).

#### Scenario: Polling a queued job

- **WHEN** a client calls `GET /generations/{id}` for a job with `status="queued"`
- **THEN** the system SHALL return the job row plus `queue_position` derived from the RQ queue inspection

#### Scenario: Polling a completed job

- **WHEN** a client calls `GET /generations/{id}` for a job with `status="done"`
- **THEN** the system SHALL return the row including `output_asset_id`, `completed_at`, and a `queue_position` of null

#### Scenario: Cancelling a queued job

- **WHEN** a client calls `DELETE /generations/{id}` for a job with `status="queued"`
- **THEN** the system SHALL remove the RQ job from its queue; SHALL update the row to `status="failed"` with `error="cancelled by user"`; AND SHALL return HTTP 204

#### Scenario: Cancellation request on a running job

- **WHEN** a client calls `DELETE /generations/{id}` for a job whose status is `running`
- **THEN** the system SHALL return HTTP 409 with `{detail: "cannot cancel running job"}` and SHALL NOT modify the job state

### Requirement: Orphan watchdog covers generation jobs

The orphan-watchdog background task introduced in v0.25.1 (which sweeps stuck `Draft` rows) SHALL also sweep `generation_job` rows whose status is `queued` or `running` and whose underlying RQ job has disappeared. Detected orphans MUST be flipped to `status="failed"` with `error="worker crashed"`; the watchdog MUST NOT auto-enqueue a replacement.

#### Scenario: Worker container crashes mid-job

- **WHEN** a `worker-imggen` or `worker-videogen` container dies while holding a `generation_job` in `status="running"`, and the associated RQ job entry has been purged from redis
- **THEN** within one watchdog tick (≤60 s), the system SHALL mark the row `status="failed"` with `error="worker crashed"` and `completed_at=<now>`

#### Scenario: Healthy queued job is not touched

- **WHEN** a `generation_job` row is in `status="queued"` and the corresponding RQ job entry still exists
- **THEN** the watchdog SHALL leave the row untouched

### Requirement: Wizard entry visibility derived from health

The system SHALL gate the FE wizard's entry button on the boot-time `/health` response. The entry button MUST render only when `ai_gen.enabled` is true AND `ai_gen.comfyui_up` is true.

#### Scenario: AI disabled

- **WHEN** the FE loads `/health` and the response includes `ai_gen: {enabled: false}`
- **THEN** the project page SHALL NOT render the "+ AI 生成 clip" entry button

#### Scenario: AI enabled but ComfyUI down

- **WHEN** the FE loads `/health` and the response includes `ai_gen: {enabled: true, comfyui_up: false}`
- **THEN** the project page SHALL render the entry button in a disabled state with a tooltip explaining the ComfyUI service is not reachable

#### Scenario: AI enabled and ComfyUI reachable

- **WHEN** `/health` reports `ai_gen: {enabled: true, comfyui_up: true}`
- **THEN** the project page SHALL render the entry button enabled; clicking it SHALL open the three-step wizard

### Requirement: Three-step wizard flow

The FE SHALL implement a three-step wizard at `web/src/features/ai-gen/AiGenWizard.tsx`. Step 1 selects a source image; step 2 runs image clean-up with preview and reroll; step 3 runs video generation with preview and reroll. Wizard state MUST live in React local state plus a URL query parameter (`?ai_job=<id>`) so a refresh recovers the current polling target without persisting wizard-internal state to the database.

#### Scenario: Step 1 picks an existing Asset

- **WHEN** the user opens the wizard and selects an existing Project Asset of an image MIME type
- **THEN** the wizard SHALL advance to step 2 carrying the selected `source_asset_id`

#### Scenario: Step 1 uploads a new image

- **WHEN** the user drops an image file into the step 1 dropzone
- **THEN** the wizard SHALL upload the file through the existing `POST /assets/upload` endpoint, register the resulting Asset id as the step 2 `source_asset_id`, AND SHALL NOT add a new upload pathway

#### Scenario: Step 2 reroll preserves state on refresh

- **WHEN** the user has triggered step 2 generation and the page is refreshed while polling
- **THEN** the wizard SHALL read `?ai_job=<id>` from the URL, resume polling that job, and render the previous preview when the job reaches `status="done"`

#### Scenario: Step 3 accept closes the wizard

- **WHEN** the user clicks "Accept" on a successful step 3 video preview
- **THEN** the wizard SHALL close without further user action; the generated `Asset` SHALL already be present in the Project's Asset pool

### Requirement: Generated assets are first-class in the Asset model

The `asset` table SHALL gain a non-null `source_type` column constrained to `'uploaded'`, `'generated_image'`, or `'generated_video'`; existing rows MUST default to `'uploaded'`. The `asset` table SHALL gain nullable `parent_asset_id` and `generation_job_id` foreign keys. UI badges identifying AI-generated content MUST be derived from `source_type != 'uploaded'`; the system MUST NOT store a separate `is_ai_generated` flag.

#### Scenario: Existing asset rows after migration

- **WHEN** alembic revision `0030_asset_source_type` is applied to a database with existing Asset rows
- **THEN** every pre-existing row SHALL have `source_type='uploaded'`, `parent_asset_id=NULL`, and `generation_job_id=NULL`

#### Scenario: New generated image asset

- **WHEN** a successful image generation job completes
- **THEN** the resulting Asset row SHALL have `source_type='generated_image'`, `parent_asset_id=<source_asset_id>`, and a non-null `generation_job_id` pointing to the row that produced it

#### Scenario: Invalid source_type rejected by check constraint

- **WHEN** any code attempts to insert an Asset with `source_type` outside the allowed set
- **THEN** the database SHALL reject the insert via the `ck_asset_source_type` check constraint
