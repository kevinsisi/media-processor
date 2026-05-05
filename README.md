# Media Processor

Content factory pipeline for short-form video production.

**Status:** v0.28.1 / M9.13.1 — durable export artifacts and reliable status UX.

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
curl http://127.0.0.1:8623/health
curl http://127.0.0.1:8523/api/health
```

The health response includes `status`, `version`, and dependency status. `status`
is `ok` only when Postgres and Redis are both reachable; otherwise it is
`degraded`.

## Web App

The React/Vite app is API-backed. Main routes:

| Route | Purpose |
|-------|---------|
| `/` | Project list |
| `/projects/new` | Create project |
| `/projects/:id/upload` | Upload videos and script |
| `/projects/:id/assets` | Asset analysis, transcript, tracking, delete |
| `/projects/:id/edit` | Render settings, draft preview, re-render, export downloads |
| `/projects/:projectId/edit/timeline/:draftId` | Advanced timeline editor |
| `/settings` | LLM key settings |
| `/health` | Developer-facing status dashboard |

`/projects/:id/review` still exists as a legacy route, but the current preview
and download workflow lives under `/projects/:id/edit`.

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
