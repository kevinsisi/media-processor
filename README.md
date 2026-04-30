# Media Processor

Content factory pipeline for short-form video production.

**Status:** Phase α MVP, Step 0 + M1 infrastructure.

## Spec

See `docs/superpowers/specs/2026-04-30-media-processor-design.md`.

## Quick start

```bash
cp .env.example .env
make up         # Start docker-compose stack
make dev-api    # Run API in dev mode (hot reload)
make dev-web    # Run web in dev mode
make test       # Run all tests
```

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

## Step 0 verification status

| Check | Status | Doc |
|-------|--------|-----|
| SMB share Mac ↔ Windows | PENDING (girlfriend) | `scripts/verify_smb.md` |
| WSL2 + NVIDIA GPU passthrough | PENDING (Windows host) | `scripts/verify_gpu.sh` |
| CapCut draft schema captured | PENDING (girlfriend's sample) | `docs/capcut_draft_schema_findings.md` |
| CLIP zero-shot probe | PENDING (30 carsmeet screenshots) | `docs/clip_zero_shot_findings.md` |
| File System Access API on her Mac | PENDING (girlfriend Chrome) | `docs/fs_access_api_findings.md` |

Update each row to PASS / FAIL with a date once the corresponding
artefact is captured.

## M1 acceptance

- [x] `docker compose config` validates the stack (services declared correctly)
- [x] `pytest -v` passes (2 health tests pass, 2 capcut tests skip pending sample)
- [x] `ruff check src tests` and `ruff format --check src tests` are clean
- [x] `mypy src` is clean (strict mode)
- [x] `npm run build` produces a working web bundle
- [ ] `docker compose up -d --build` brings all services up — depends on Step 0 GPU + SMB checks
- [ ] `curl http://127.0.0.1:8000/health` returns `{"status":"ok"}` once Postgres + Redis are running
- [ ] `curl http://127.0.0.1:8080/api/health` returns the same through the Nginx proxy
- [ ] CI green on `main`

The remaining items run on the developer's Windows host once Step 0
verification completes.

## Web preview (mockup, no backend required)

A non-functional UI mockup ships with this milestone so the end user
can see what the review-inbox flow will look like before the AI
pipeline lands. Three routes:

| Route | Purpose |
|-------|---------|
| `/` | ProjectList — editorial TOC of mock issues across statuses |
| `/projects/:id/review` | Review — 9:16 player + AI intel sidebar + interactive timeline + 5-action row + prompt modal |
| `/health` | Developer-facing status dashboard (formerly the landing) |

Mock data lives at `web/src/data/mockData.ts` and mirrors the entity
shape from spec §4 — swapping to real API later is a data-source
change, not a UI rewrite.

Run locally:

```bash
cd web && npm install && npm run dev
# open http://localhost:5173/
```
