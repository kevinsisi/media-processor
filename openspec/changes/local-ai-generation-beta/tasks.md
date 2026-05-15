## 1. Infrastructure — GPU lock + ComfyUI service

- [ ] 1.1 Add `services/ai_generation/__init__.py` package skeleton
- [ ] 1.2 Implement `services/ai_generation/gpu_lock.py` with `gpu_exclusive(reason, timeout=600)` contextmanager (BLPOP token list, RPUSH release, timeout raises `GpuLockTimeout`)
- [ ] 1.3 Initialise `gpu:exclusive` redis list with one token in `api/main.py` lifespan startup (only push if list empty)
- [ ] 1.4 Unit test the lock with fakeredis: acquire/release contract, contention serialisation, timeout path, release-on-exception path
- [ ] 1.5 Pick and pin a specific ComfyUI docker image tag in `docker-compose.yml`; verify it boots and serves `/system_stats` on port 8188
- [ ] 1.6 Add `comfyui` service block to `docker-compose.yml` with GPU device reservation, port 127.0.0.1:8188, and mounts `${MEDIA_STORAGE_DIR}/model_cache/comfyui_models` and `${MEDIA_STORAGE_DIR}/comfyui_output`
- [ ] 1.7 Document the ~40 GB model-weight download as a one-off operator step (README or CLAUDE.md addendum) listing FLUX image-edit + LTX-Video weight filenames and target paths
- [ ] 1.8 Add `AI_GEN_ENABLED` env var to `.env.example`, default `false`, with a one-line comment

## 2. Patch existing GPU workers to acquire the lock

- [ ] 2.1 Wrap the Whisper / YOLO / MediaPipe / Gemini Vision forward-pass call sites in `services/analysis/__init__.py::run_pipeline` (or its inner functions) with `gpu_exclusive(reason="analysis")`
- [ ] 2.2 Wrap the model `.generate()` call in `services/musicgen.py::generate` with `gpu_exclusive(reason="musicgen")`
- [ ] 2.3 Verify existing analysis integration tests still pass with the lock pre-populated
- [ ] 2.4 Verify existing BGM integration tests still pass with the lock pre-populated
- [ ] 2.5 Add a regression test that drives two GPU consumers in parallel (one analysis-mock, one bgm-mock) and asserts they serialise

## 3. ComfyUI HTTP client + workflow templates

- [ ] 3.1 Implement `services/ai_generation/comfy_client.py`: `submit(workflow_dict) -> prompt_id`, `wait_for(prompt_id, timeout)`, `download_output(prompt_id, target_path)`. Use `httpx`. Handle ComfyUI's WebSocket-or-poll status pattern; poll is fine for beta
- [ ] 3.2 Author `services/ai_generation/workflows/flux_image_edit.json` (FLUX-family image-edit workflow, NF4-quantised, 8 GB VRAM target). Use `{{prompt}}`, `{{image_path}}`, `{{seed}}` placeholders
- [ ] 3.3 Author `services/ai_generation/workflows/ltx_i2v.json` (LTX-Video 2B I2V workflow, fp8 quantised). Placeholders for `{{prompt}}`, `{{first_frame}}`, `{{last_frame}}`, `{{seed}}`
- [ ] 3.4 Implement `render_workflow(name, **placeholders) -> dict` to fill the JSON templates
- [ ] 3.5 Unit-test workflow templating: snapshot a rendered FLUX workflow JSON; snapshot a rendered LTX workflow JSON; verify placeholder substitution does not leak unfilled `{{...}}`
- [ ] 3.6 Integration-test `comfy_client.py` against a mocked ComfyUI HTTP transport (httpx mock): canned `/prompt` (returns prompt_id), `/history/{id}` (returns done with output path), `/view` (returns dummy mp4/jpeg bytes)

## 4. Preset registry

- [ ] 4.1 Implement `services/ai_generation/presets.py` with a `PRESETS` dict keyed by `(kind, key)` → `{label_zh, description_zh, prompt_en, workflow}`
- [ ] 4.2 Author 4 image presets with bundled English prompts (e.g. "電影感青橙調 + 移除路人", "黑白藝術片風", "高反差日落調", "乾淨棚拍白底")
- [ ] 4.3 Author 4–6 video presets with bundled English prompts (e.g. "平穩 slider 左推右", "緩慢推進", "環繞 orbit", "低角度上升", "Push-in 由遠至近", "Static 鎖定主體")
- [ ] 4.4 Unit-test preset lookup, unknown-key rejection, English-prompt isolation (must not leak via list endpoint)

## 5. Schema migrations

- [ ] 5.1 Author alembic `0029_add_generation_job.py`: create `generation_job` table with id, project_id FK CASCADE, kind, preset_key, prompt_en, source_asset_id FK, second_source_asset_id FK nullable, status, output_asset_id FK nullable, error nullable, seed, workflow_version, created_at, completed_at nullable
- [ ] 5.2 Add CHECK constraint on `generation_job.kind` for `('image','video')` and on `generation_job.status` for `('queued','running','done','failed')`
- [ ] 5.3 Author alembic `0030_asset_source_type.py`: add `source_type` (NOT NULL default `'uploaded'`), `parent_asset_id` (nullable FK to asset), `generation_job_id` (nullable FK to generation_job), and `ck_asset_source_type` CHECK constraint for the three allowed values. Use `op.batch_alter_table` to remain SQLite-compatible for tests
- [ ] 5.4 Verify upgrade then downgrade leaves the schema clean on both Postgres (production) and SQLite (test)
- [ ] 5.5 Add ORM mappings: `models/generation_job.py` (new) and extend `models/asset.py` with the three new columns + relationships

## 6. Workers (imggen + videogen)

- [ ] 6.1 Implement `workers/imggen_jobs.py::run(job_id)`: load row, acquire `gpu_exclusive(reason="imggen")`, render `flux_image_edit` workflow, submit + wait, copy output into `MEDIA_STORAGE_DIR`, create new `Asset(source_type='generated_image', parent_asset_id, generation_job_id)`, update job to done. On any exception → job failed with the exception message; release lock
- [ ] 6.2 Implement `workers/videogen_jobs.py::run(job_id)`: same pattern but `ltx_i2v` workflow, ffprobe the output, set `duration_s` and `resolution` on the new `Asset(source_type='generated_video')`
- [ ] 6.3 Register both queues with `workers/__main__.py` so `python -m media_processor.workers imggen` and `... videogen` both work
- [ ] 6.4 Add the two services to `docker-compose.yml` using the same worker image, `command:` set to the new queue names, NO GPU device reservation, depends_on `comfyui` started
- [ ] 6.5 Integration-test the full imggen worker against mocked ComfyUI: enqueue a fake job → run worker → assert Asset row written with correct source_type + parent + foreign keys → assert GenerationJob row status=done
- [ ] 6.6 Integration-test the videogen worker similarly, with a dummy mp4 output file
- [ ] 6.7 Integration-test failure path: ComfyUI returns error → row marked failed, no Asset created, lock released

## 7. API endpoints

- [ ] 7.1 Implement `api/routers/generations.py` with `POST /generations/image`, `POST /generations/video`, `GET /generations/{id}`, `DELETE /generations/{id}`. All four return 404 when `AI_GEN_ENABLED=false`
- [ ] 7.2 Implement `api/routers/ai.py` with `GET /ai/presets?kind=image|video` and `GET /ai/health`. `/ai/health` is reachable in both flag states (returns `{enabled:false}` when off)
- [ ] 7.3 Extend `/health` payload with an `ai_gen: {enabled, comfyui_up}` block (only ping ComfyUI if `enabled=true`); cache the ComfyUI ping for 5 s to avoid spamming the service
- [ ] 7.4 Wire both routers into `api/main.py` include_router list
- [ ] 7.5 API tests: 404 contract when flag off; 202 + job_id when flag on; reroll creates a new row not a mutation; cancel a queued job returns 204 and marks failed; cancel a running job returns 409; unknown preset returns 422
- [ ] 7.6 API test for `/ai/presets` excluding English prompts in the response payload
- [ ] 7.7 API test for `/ai/health` shape in all three states: flag off; flag on + ComfyUI down; flag on + ComfyUI up

## 8. Watchdog extension

- [ ] 8.1 Extend `api/watchdog.py` to also sweep `generation_job` rows with status `queued` or `running` whose RQ job has vanished from redis; flip to `status='failed'`, `error='worker crashed'`
- [ ] 8.2 Watchdog test: simulate a `running` row with an absent RQ job → after one tick, row is `failed`
- [ ] 8.3 Watchdog test: healthy queued row with present RQ job is left alone

## 9. FE wizard

- [ ] 9.1 Add a typed API client for the new endpoints in the existing `web/src/api/` layer; surface `useGenerationJob(id)` polling hook returning status + `output_asset_id`
- [ ] 9.2 Add a `useAiGenHealth()` hook reading `ai_gen` from `/health` cached at app boot
- [ ] 9.3 Add `web/src/features/ai-gen/AiGenWizard.tsx` modal with three steps; URL query `?ai_job=<id>` resumes polling on refresh
- [ ] 9.4 Step 1: existing-asset picker + dropzone upload (reuses existing `POST /assets/upload`)
- [ ] 9.5 Step 2: image preset dropdown (from `/ai/presets?kind=image`), Generate button, progress card, preview, Accept / Reroll
- [ ] 9.6 Step 3: locked first-frame display + last-frame selector (use step-2 image OR re-run step 2 for a different last frame), video preset dropdown, Generate button, progress card, 5–10 s video preview, Accept / Reroll, optional "Generate another clip" loop-back
- [ ] 9.7 Add "+ AI 生成 clip" entry button to the Project page header; render only when `ai_gen.enabled && ai_gen.comfyui_up`; disabled-with-tooltip when enabled but ComfyUI down
- [ ] 9.8 Frontend tests (component-level): step navigation, disabled-entry-button states, URL-state recovery

## 10. Manual E2E + documentation

- [ ] 10.1 Create `tests/manual/2026-05-15-local-ai-gen-checklist.md`: upload a real car photo → run image preset → run video preset → confirm generated_video Asset appears in Project → trigger a Draft render that consumes the generated Asset → confirm mp4 output plays
- [ ] 10.2 Update `CLAUDE.md` "Project Architecture Pointers" with the new generation paths, AI_GEN_ENABLED flag, GPU lock contract, and one-line summary of the new files
- [ ] 10.3 Update `ROADMAP.md` with the v0.50.0-beta entry referencing this change
- [ ] 10.4 Memory note (auto-memory): non-obvious operator gotchas — model weight download paths, "wizard hidden until flag flipped", ComfyUI cold-start latency
- [ ] 10.5 Run the manual checklist on kevinhome before tagging v0.50.0-beta; capture screenshots of each wizard step into `docs/screenshots/v0.50.0-beta-ai-gen/`

## 11. Beta exit criteria

- [ ] 11.1 At least one full reel produced end-to-end (photo → wizard → multiple clips → Draft render → playback) on kevinhome
- [ ] 11.2 GPU lock observed serialising at least one analysis ↔ generation overlap in production logs
- [ ] 11.3 Orphan watchdog observed flipping a forcibly killed `generation_job` row to `failed` within one tick
- [ ] 11.4 `AI_GEN_ENABLED=false` round-trip verified: flag off → wizard vanishes → endpoints 404 → flag on → wizard returns
