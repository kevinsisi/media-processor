# Media Processor

Content factory pipeline for novice-friendly Instagram and Facebook short-video production.

**Status:** v0.30.8 / M9.15.8 — production compose avoids Windows-reserved API port 8623.

## Spec

Start with `ROADMAP.md` for current state. Historical OpenSpec changes live under
`openspec/changes/archive/`.

## Quick start

```bash
cp .env.example .env
docker compose up -d --scale worker-editing=3
make dev-api    # Run API in dev mode (hot reload)
make dev-web    # Run web in dev mode
make test       # Run all tests
```

`--scale worker-editing=3` is required for the current multi-worker deployment.
Plain `make up` is still available for quick local boot, but it does not encode
the production editing-worker scale.

## Repository layout

- `src/media_processor/` — Python services (api, worker, watcher, core)
- `web/` — React + Vite web UI
- `profiles/` — YAML profile rule files
- `docker/` — Dockerfiles per service
- `scripts/` — One-off verification scripts
- `tools/` — Developer utilities (e.g. CapCut schema parser)
- `samples/` — Local-only sample data (gitignored)
- `docs/superpowers/` — Design specs and implementation plans

## Verification scripts

- `scripts/verify_gpu.sh` — confirms WSL2 + NVIDIA Container Toolkit work
- `scripts/verify_smb.md` — manual SMB share checklist
- `scripts/clip_zero_shot_probe.py` — measures CLIP accuracy on carsmeet tags
- `scripts/verify_fs_access_api.html` — browser smoke test for FS Access API

## Tooling

- Lint: `ruff check src tests`
- Format: `ruff format src tests`
- Type check: `mypy src`
- Tests: `pytest`

## Verification

Local checks:

```bash
pytest -v
ruff check src tests
ruff format --check src tests
mypy src
cd web && npm ci && npm run build
```

Runtime smoke checks after Docker boot:

```bash
curl http://127.0.0.1:19023/health
curl http://127.0.0.1:8523/api/health
```

The health response includes `status`, `version`, and dependency status. `status`
is `ok` only when Postgres and Redis are both reachable; otherwise it is
`degraded`.

## Production CI/CD

Pushes to `main` build and publish three Docker Hub images:

- `kevin950805/media-processor-api:latest`
- `kevin950805/media-processor-worker:latest`
- `kevin950805/media-processor-web:latest`

After image publishing succeeds, `Deploy Production` runs on the `kevinhome`
self-hosted runner (`100.83.112.20`), copies `docker-compose.yml` to
`D:/GitClone/_HomeProject/media-processor`, validates the existing production
`.env`, then runs Docker Compose locally:

```bash
docker compose pull api web worker-analysis worker-editing worker-bgm
docker compose up -d --scale worker-editing=3 postgres redis api web worker-analysis worker-editing worker-bgm
```

The deploy workflow intentionally refuses to proceed unless the desktop `.env`
keeps the live data mounts on the existing G drive:

```env
MEDIA_STORAGE_DIR=G:/MediaStorage
PGDATA_DIR=G:/MediaStorage/pgdata
```

This guard prevents CD from accidentally falling back to repo-local `.local/`
media storage or the `postgres_data` named volume and losing the currently live
data mapping.

`worker-bgm` stores HuggingFace / MusicGen cache under
`G:/MediaStorage/model_cache/huggingface` via the `/app/media` mount. The first
generation after enabling this cache can still download the model once; later
deploys should reuse it instead of re-downloading.

Production containers run with `TZ=Asia/Taipei` so logs and filesystem times
match the operator's GMT+8 context. Draft preview URLs include the rendered
file mtime as a cache-busting query because `vN.mp4` paths are overwritten by
settings re-renders.

The web nginx config serves the SPA shell and route fallbacks with `no-store`
so operators do not keep using old JS/CSS after deployment; Vite hashed assets
under `/assets/` remain long-cacheable with `immutable`.

## Web App

The React/Vite app is API-backed. Main routes:

| Route | Purpose |
|-------|---------|
| `/` | Project list |
| `/projects/new` | Create project |
| `/projects/:id/upload` | Upload videos and script with safe script save before analysis |
| `/projects/:id/assets` | Asset analysis, transcript, tracking, delete |
| `/projects/:id/edit` | Publishing workbench, draft preview, IG/FB export presets, downloads |
| `/projects/:projectId/edit/timeline/:draftId` | Advanced timeline editor |
| `/settings` | LLM key settings |
| `/health` | Developer-facing status dashboard |

`/projects/:id/review` is a legacy route and redirects to `/projects/:id/edit`.

## v0.30.0 AI Smart Camera Notes

- AI Smart Camera is implemented as an opt-in project/render flag and remains off
  by default to protect Gemini quota and preserve static-camera user preference.
- The planner samples each cut, asks Gemini Vision for focus regions, and derives
  `zoom_in`, `zoom_out`, or `pan` directives when the visual evidence is strong.
- Rendering uses a zoompan-driven crop path and falls back to the existing static
  crop when a directive fails, so a single bad camera move should not fail the
  full render.

## v0.29.0 Aspect Ratio Notes

- Output ratios are narrowed to `9:16` and `16:9`.
- Static crop-region anchors are available when source and output orientation
  differ; dynamic subject/point tracking still takes priority.

## v0.28.4 Beginner Copy Notes

- Main workflow copy now uses the same beginner-facing terms throughout the app:
  `素材檢查`, `產生成品`, `建立下載版本`, `品牌標誌`, `畫面防手震`, and `自動跟住主角`.
- Queue, worker, render, export, BGM, tracking, and failure states lead with what
  the user can do next. Raw backend details remain secondary in settings,
  diagnostics, or developer-facing error details.
- Technical component/API names stay unchanged in code; only visible labels,
  helper text, aria labels, empty states, and error copy are normalized.

## v0.28.3 Job Lifecycle Reliability Notes

- Enqueue failures no longer leave durable rows stuck in pending states for
  draft renders, exports, BGM generation, point tracking, analysis, or subtitle
  translation triggers.
- The watchdog now reconciles missing RQ jobs for export artifacts, BGM jobs,
  point tracking, and in-flight analysis in addition to draft renders.
- Worker entry points guard against stale jobs overwriting terminal draft/export
  states after cancellation, retry, or duplicate queue records.
- Generic queued-job cancellation now syncs durable row state for render, export,
  BGM, point tracking, and analysis jobs.

## v0.28.2 Novice Social Shorts Notes

- Upload now saves the latest script text before moving to analysis; if saving
  fails, the user stays on upload with a clear error instead of losing edits.
- The completed draft page is centered on a publishing workbench: preview,
  download, IG/FB export, and regenerate are the first visible actions.
- Export presets now start with Instagram/Facebook destinations: Reels, feed
  portrait, and square post. Raw aspect/resolution controls remain under an
  advanced section.
- Main-path copy avoids backend/tool terminology and speaks in short-video
  outcomes for non-technical users.

## v0.28.1 UX Reliability Notes

- Derivative exports are now durable artifacts under `draft_exports`; the edit
  page lists queued/running/done/failed exports and shows direct downloads when
  files are ready.
- Draft and asset polling ignore stale overlapping responses so older status
  payloads cannot roll the UI back.
- Queue badge failures show an explicit unavailable state instead of `排隊 0`.
- The edit page requires meaningful terminal analysis step data before enabling
  render triggers.
- P1 UX audit backlog is documented in
  `openspec/changes/improve-export-and-status-ux/` for the next implementation
  slice.

Run locally:

```bash
cd web && npm install && npm run dev
# open http://localhost:5173/
```
