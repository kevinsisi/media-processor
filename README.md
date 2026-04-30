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
