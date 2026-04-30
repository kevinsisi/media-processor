# Step 0 + M1 Infrastructure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Verify the five Step 0 prerequisites and stand up the docker-compose foundation (Postgres, Redis, FastAPI hello, web shell) so any downstream AI pipeline work has a working host.

**Architecture:** Single Python project under `src/media_processor/` with submodules (`api`, `worker`, `watcher`, `core`, `adapters`). Separate React/Vite web app under `web/`. Docker Compose orchestrates services on Windows via WSL2 with NVIDIA Container Toolkit. SMB share between Mac (girlfriend) and Windows host (developer) carries assets. All Step 0 verifications produce committed evidence (logs, sample files, parser outputs) before any feature work begins.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy 2.x, Alembic, Postgres 16, Redis 7, RQ, Docker Compose v2, WSL2, NVIDIA Container Toolkit, React 18, Vite 5, TypeScript 5, pytest, ruff, mypy.

**Spec reference:** `docs/superpowers/specs/2026-04-30-media-processor-design.md`

---

## File Structure (created by this plan)

```
media-processor/
├── .env.example                       # Env var template (no secrets)
├── .gitignore                         # Python + Node + IDE + samples/
├── docker-compose.yml                 # Postgres, Redis, api, web
├── docker-compose.override.yml.example # Local dev overrides
├── Makefile                           # Common commands
├── pyproject.toml                     # Python project metadata + tool config
├── README.md                          # How to run, what's where
├── alembic.ini                        # Alembic config
├── docker/
│   ├── api.Dockerfile                 # FastAPI service image
│   └── web.Dockerfile                 # Vite static build + Nginx
├── src/
│   └── media_processor/
│       ├── __init__.py
│       ├── api/
│       │   ├── __init__.py
│       │   ├── main.py                # FastAPI app entry
│       │   ├── config.py              # Settings via pydantic-settings
│       │   └── routers/
│       │       ├── __init__.py
│       │       └── health.py          # /health endpoint
│       └── core/
│           ├── __init__.py
│           └── db.py                  # SQLAlchemy engine + session
├── alembic/
│   ├── env.py
│   ├── script.py.mako
│   └── versions/                      # Empty initially
├── web/
│   ├── package.json
│   ├── tsconfig.json
│   ├── vite.config.ts
│   ├── index.html
│   ├── nginx.conf                     # For Docker prod build
│   └── src/
│       ├── main.tsx
│       └── App.tsx
├── profiles/
│   ├── carsmeet-luxury.yaml           # From spec §5.1
│   └── universal.yaml                 # From spec §5.2
├── scripts/
│   ├── verify_gpu.sh                  # Run nvidia-smi inside container
│   ├── verify_smb.md                  # Manual SMB checklist
│   ├── verify_fs_access_api.html      # Smoke test page for File System Access API
│   └── clip_zero_shot_probe.py        # Run CLIP on carsmeet screenshots
├── samples/
│   ├── .gitkeep
│   ├── capcut_draft/                  # Sample from her Mac (gitignored)
│   ├── carsmeet_screenshots/          # 30 screenshots for CLIP probe (gitignored)
│   └── README.md                      # What goes here
├── tools/
│   └── capcut_schema_parser/
│       ├── __init__.py
│       └── parse_sample.py            # Inspect draft_content.json structure
├── tests/
│   ├── __init__.py
│   ├── conftest.py
│   ├── unit/
│   │   ├── __init__.py
│   │   └── test_health.py             # Smoke test for /health
│   └── integration/
│       └── __init__.py
└── docs/superpowers/
    ├── specs/2026-04-30-media-processor-design.md     # (already exists)
    └── plans/2026-04-30-step0-and-m1-infrastructure.md # (this file)
```

**Why this layout:**
- Single Python project = simpler imports, one `pyproject.toml`, one venv for development
- `src/` layout ensures clean import paths and prevents accidental import of test code
- `web/` is sibling to `src/` because Vite + React has its own toolchain
- `scripts/` and `tools/` are one-off verification helpers that don't ship in containers
- `samples/` is gitignored except `.gitkeep` and `README.md` — sensitive data and large files don't go in git

---

## Prerequisites (do before starting Task 1)

- [ ] Confirm working directory is the `media-processor` repo (`cd D:/Projects/_HomeProject/media-processor`)
- [ ] Confirm git is clean: `git status` returns "nothing to commit, working tree clean"
- [ ] Confirm Docker Desktop is installed on the Windows host
- [ ] Confirm WSL2 is installed: `wsl -l -v` shows at least one distro
- [ ] Confirm Python 3.11+ available: `python --version`
- [ ] Confirm Node 20+ available: `node --version`

---

## Task 1: Repository scaffolding (`.gitignore`, `pyproject.toml`, `README.md`, `Makefile`, `.env.example`)

**Files:**
- Create: `.gitignore`
- Create: `pyproject.toml`
- Create: `README.md`
- Create: `Makefile`
- Create: `.env.example`

- [ ] **Step 1.1: Write `.gitignore`**

Path: `.gitignore`
```gitignore
# Python
__pycache__/
*.py[cod]
*$py.class
*.so
.Python
.venv/
venv/
env/
*.egg-info/
.pytest_cache/
.mypy_cache/
.ruff_cache/

# Node
node_modules/
dist/
*.log
npm-debug.log*

# IDE
.vscode/
.idea/
*.swp
.DS_Store
Thumbs.db

# Project
.env
.env.local
samples/capcut_draft/
samples/carsmeet_screenshots/
samples/blurred/
*.mp4
*.MOV
*.mov
!tests/fixtures/*.mp4

# Docker
*.log
```

- [ ] **Step 1.2: Write `pyproject.toml`**

Path: `pyproject.toml`
```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "media-processor"
version = "0.1.0"
description = "Content factory pipeline for short-form video production"
requires-python = ">=3.11"
dependencies = [
    "fastapi>=0.115.0",
    "uvicorn[standard]>=0.32.0",
    "pydantic>=2.9.0",
    "pydantic-settings>=2.6.0",
    "sqlalchemy>=2.0.36",
    "alembic>=1.14.0",
    "asyncpg>=0.30.0",
    "psycopg2-binary>=2.9.10",
    "redis>=5.2.0",
    "rq>=2.0.0",
    "python-multipart>=0.0.17",
    "pyyaml>=6.0.2",
    "httpx>=0.28.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.3.3",
    "pytest-asyncio>=0.24.0",
    "ruff>=0.7.0",
    "mypy>=1.13.0",
    "types-pyyaml>=6.0.12",
]

[tool.hatch.build.targets.wheel]
packages = ["src/media_processor"]

[tool.ruff]
line-length = 100
target-version = "py311"
src = ["src", "tests"]

[tool.ruff.lint]
select = ["E", "F", "W", "I", "N", "UP", "B", "C4", "SIM", "RET"]
ignore = ["E501"]

[tool.mypy]
python_version = "3.11"
strict = true
ignore_missing_imports = true

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
pythonpath = ["src"]
```

- [ ] **Step 1.3: Write `README.md`**

Path: `README.md`
```markdown
# Media Processor

Content factory pipeline for short-form video production.

**Status:** Phase α MVP, Step 0 + M1 infrastructure.

## Spec

See `docs/superpowers/specs/2026-04-30-media-processor-design.md`.

## Quick start

\```bash
cp .env.example .env
make up         # Start docker-compose stack
make dev-api    # Run API in dev mode (hot reload)
make dev-web    # Run web in dev mode
make test       # Run all tests
\```

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
```

- [ ] **Step 1.4: Write `Makefile`**

Path: `Makefile`
```makefile
.PHONY: help up down logs ps test lint fmt typecheck dev-api dev-web migrate

help:
	@echo "Targets: up down logs ps test lint fmt typecheck dev-api dev-web migrate"

up:
	docker compose up -d

down:
	docker compose down

logs:
	docker compose logs -f --tail=100

ps:
	docker compose ps

test:
	pytest -v

lint:
	ruff check src tests

fmt:
	ruff format src tests

typecheck:
	mypy src

dev-api:
	uvicorn media_processor.api.main:app --reload --host 0.0.0.0 --port 8000

dev-web:
	cd web && npm run dev

migrate:
	alembic upgrade head
```

- [ ] **Step 1.5: Write `.env.example`**

Path: `.env.example`
```bash
# Postgres
POSTGRES_USER=media
POSTGRES_PASSWORD=changeme
POSTGRES_DB=media_processor
POSTGRES_HOST=postgres
POSTGRES_PORT=5432

# Redis
REDIS_HOST=redis
REDIS_PORT=6379

# API
API_HOST=0.0.0.0
API_PORT=8000

# Anthropic (Stage 4.5 Prompt Patch)
ANTHROPIC_API_KEY=

# Pipeline
MEDIA_PROCESSOR_GPU_SERIAL_MODE=1

# Storage paths (Windows host paths under WSL2 mount)
ASSETS_DIR=/mnt/c/MediaProcessor/assets
DRAFTS_DIR=/mnt/c/MediaProcessor/drafts
```

- [ ] **Step 1.6: Verify files exist**

Run: `ls -la .gitignore pyproject.toml README.md Makefile .env.example`

Expected: all five files present.

- [ ] **Step 1.7: Commit**

```bash
git add .gitignore pyproject.toml README.md Makefile .env.example
git commit -m "scaffold: add repo top-level config and tooling"
```

---

## Task 2: Profile YAML files from spec

**Files:**
- Create: `profiles/carsmeet-luxury.yaml`
- Create: `profiles/universal.yaml`

- [ ] **Step 2.1: Write `profiles/carsmeet-luxury.yaml` (verbatim from spec §5.1)**

Path: `profiles/carsmeet-luxury.yaml`
```yaml
name: carsmeet-luxury
description: 豪車經銷展間 cinematic 風（Rolls-Royce / Bentley / Lambo / Porsche）

tag_weights:
  logo_close_up: 1.5
  integral_hero_shot: 1.4
  body_line_pan: 1.2
  light_reflection: 1.1
  wheel_caliper: 0.8
  interior_leather: 0.8
  dashboard: 0.7
  star_ceiling: 0.9
  exhaust_pipe: 0.7
  stranger_face: -0.8
  parking_lot_other_car: -0.6
  blur: -1.0
  overexposed: -0.7

filters:
  min_quality_score: 0.5
  max_blur: 0.4
  min_segment_duration_ms: 200
  max_segment_duration_ms: 2000

editing_rules:
  target_duration_ms: 30000
  min_cuts: 25
  max_cuts: 50
  diversity_penalty:
    same_tag_consecutive: 0.3
  required_segments:
    opening_hero: true
    closing_hero: true

reframe:
  subject_class: car
  subject_padding_pct: 15
  smoothing_window_frames: 30
  fallback: center_crop

captions:
  enabled: true
  language: zh
  font: PingFangTC-Regular
  font_size: 48
  position: bottom_center
  outline: true
  outline_color: "#000000"

face_blur:
  mode: selective
  blur_identities_dir: ./profiles/carsmeet-luxury/blur_faces/
  blur_style: gaussian
  blur_strength: 25
```

- [ ] **Step 2.2: Write `profiles/universal.yaml` (verbatim from spec §5.2)**

Path: `profiles/universal.yaml`
```yaml
name: universal
description: 通用，畫面品質為主，無特定主題偏好

tag_weights:
  face_clear: 0.5
  composition_centered: 0.3
  motion_smooth: 0.4
  blur: -1.0
  overexposed: -0.7
  underexposed: -0.6

filters:
  min_quality_score: 0.5
  max_blur: 0.4
  min_segment_duration_ms: 300
  max_segment_duration_ms: 3000

editing_rules:
  target_duration_ms: 30000
  min_cuts: 15
  max_cuts: 40

reframe:
  subject_class: auto
  subject_padding_pct: 20
  smoothing_window_frames: 30
  fallback: center_crop

captions:
  enabled: true
  language: zh

face_blur:
  mode: off
```

- [ ] **Step 2.3: Verify YAMLs parse**

Run: `python -c "import yaml; print(yaml.safe_load(open('profiles/carsmeet-luxury.yaml'))['name']); print(yaml.safe_load(open('profiles/universal.yaml'))['name'])"`

Expected output:
```
carsmeet-luxury
universal
```

- [ ] **Step 2.4: Commit**

```bash
git add profiles/
git commit -m "feat: add carsmeet-luxury and universal profile YAMLs from spec"
```

---

## Task 3: SMB share setup verification (manual checklist + doc)

**Files:**
- Create: `scripts/verify_smb.md`

This is a **manual verification task** — produces a checklist document, not code. The girlfriend's Mac and the developer's Windows host must communicate over SMB before any pipeline work makes sense.

- [ ] **Step 3.1: Write `scripts/verify_smb.md`**

Path: `scripts/verify_smb.md`
````markdown
# SMB Share Verification (Mac ↔ Windows)

## Goal
Confirm Mac can mount Windows SMB share and read/write a 5GB test file.

## Windows host (developer) setup

1. Create the share folder: `mkdir C:\MediaProcessor\assets`
2. Right-click the folder → Properties → Sharing tab → Advanced Sharing
3. Tick "Share this folder", set Share name = `MediaProcessor`
4. Permissions → Add the user account that the Mac will authenticate as → grant "Change" + "Read"
5. Confirm Windows firewall allows File and Printer Sharing on Private network:
   ```powershell
   Get-NetFirewallRule -DisplayGroup "File and Printer Sharing" |
     Where-Object Enabled -eq True | Format-Table Name, DisplayName, Profile
   ```
6. Check Windows IP on the LAN: `ipconfig` → note the LAN IPv4 (e.g. `192.168.1.50`)

## Mac (girlfriend) verification

1. Finder → "前往" → "連線到伺服器" → enter `smb://192.168.1.50/MediaProcessor`
2. Authenticate with the Windows user credentials
3. Folder appears in Finder. Check it's writable: drag a small file in.
4. Time a 5GB file copy:
   ```bash
   # On Mac terminal
   mkfile -n 5g /tmp/test_5gb.bin
   time cp /tmp/test_5gb.bin /Volumes/MediaProcessor/test_5gb.bin
   ```
5. Record observed throughput (e.g. 110 MB/s on gigabit LAN).

## Acceptance criteria
- [ ] Mac mounts the share without errors
- [ ] Mac can read and write files
- [ ] 5GB transfer completes; record actual MB/s in this checklist
- [ ] Mac can re-mount the share after restart (Finder remembers credentials)

## WSL2 mount (Windows host side)
The Docker containers will read assets via the WSL2 mount of the same path:
```bash
ls /mnt/c/MediaProcessor/assets
```
Confirm the same files dropped from Mac appear under `/mnt/c/MediaProcessor/assets`.

## Result log

Record the verification date and observed throughput here:

```
Date: _____________
Mac → Windows transfer: ___ MB/s
WSL2 mount path access: PASS / FAIL
Notes: _____________
```
````

- [ ] **Step 3.2: Execute the checklist with the girlfriend**

Walk through `scripts/verify_smb.md` together. Record actual throughput in the "Result log" section before committing.

- [ ] **Step 3.3: Commit**

```bash
git add scripts/verify_smb.md
git commit -m "docs: add SMB share verification checklist"
```

---

## Task 4: WSL2 + Docker + NVIDIA GPU passthrough verification

**Files:**
- Create: `scripts/verify_gpu.sh`

- [ ] **Step 4.1: Confirm NVIDIA Container Toolkit is installed for WSL2**

Run on Windows PowerShell:
```powershell
wsl -d Ubuntu -- nvidia-smi
```
Expected: a table showing the RTX 2070, driver version, CUDA version. If not installed, follow `https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html#installing-with-apt` inside WSL2 to install.

- [ ] **Step 4.2: Write `scripts/verify_gpu.sh`**

Path: `scripts/verify_gpu.sh`
```bash
#!/usr/bin/env bash
set -euo pipefail

echo "[1/3] Host nvidia-smi (WSL2 should show RTX 2070):"
nvidia-smi || { echo "FAIL: nvidia-smi not available on host"; exit 1; }

echo
echo "[2/3] Docker GPU passthrough via nvidia/cuda image:"
docker run --rm --gpus all nvidia/cuda:12.6.0-base-ubuntu22.04 nvidia-smi || {
  echo "FAIL: docker --gpus all did not surface GPU";
  exit 1;
}

echo
echo "[3/3] PyTorch CUDA availability:"
docker run --rm --gpus all pytorch/pytorch:2.5.0-cuda12.4-cudnn9-runtime python -c \
  "import torch; print('cuda_available:', torch.cuda.is_available()); print('device:', torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'NONE')"

echo
echo "All GPU checks passed."
```

- [ ] **Step 4.3: Make script executable and run it**

```bash
chmod +x scripts/verify_gpu.sh
bash scripts/verify_gpu.sh 2>&1 | tee scripts/verify_gpu.log
```

Expected: all three checks output without FAIL. The PyTorch check should print `cuda_available: True` and `device: NVIDIA GeForce RTX 2070`.

- [ ] **Step 4.4: Commit**

```bash
git add scripts/verify_gpu.sh
git commit -m "scripts: verify WSL2 NVIDIA Container Toolkit GPU passthrough"
```

---

## Task 5: CapCut/JianyingPro draft sample capture and parser

**Files:**
- Create: `samples/README.md`
- Create: `samples/.gitkeep`
- Create: `tools/capcut_schema_parser/__init__.py`
- Create: `tools/capcut_schema_parser/parse_sample.py`
- Create: `docs/capcut_draft_schema_findings.md`

This is the **single highest-risk Step 0 task**. Without a real `draft_content.json` sample, the Stage 7 adapter cannot be built.

- [ ] **Step 5.1: Write `samples/README.md`**

Path: `samples/README.md`
```markdown
# Local Samples (gitignored)

This folder holds sample data used for verification only. Never commit:

- `capcut_draft/` — sample CapCut/JianyingPro draft from her Mac
- `carsmeet_screenshots/` — 30 carsmeet IG reel screenshots for CLIP probe
- `blurred/` — output of face blur tests

## Required samples

### CapCut draft (Task 5)
Ask the girlfriend to:
1. Open 剪映/CapCut Pro on her Mac.
2. Create a new draft named `mp_sample_001`.
3. Drag in 3 short clips, 1 BGM track, add 1 text/caption layer.
4. Save and close.
5. Locate the draft folder on her Mac:
   - 剪映 (CN): `~/Movies/JianyingPro/User Data/Projects/com.lveditor.draft/mp_sample_001/`
   - CapCut (intl): `~/Movies/CapCut/User Data/Projects/com.lveditor.draft/mp_sample_001/`
6. Zip the entire folder and send it.
7. Record the exact 剪映/CapCut version number.
8. Place the unzipped folder at `samples/capcut_draft/mp_sample_001/`.

### Carsmeet screenshots (Task 6)
30 screenshots from public carsmeet.tw IG reels for CLIP zero-shot probing.
Place under `samples/carsmeet_screenshots/`.
```

- [ ] **Step 5.2: Add `samples/.gitkeep` and confirm .gitignore covers content**

```bash
touch samples/.gitkeep
git check-ignore samples/capcut_draft/foo.json && echo "gitignore OK"
```

Expected: prints `gitignore OK` (or path matched). If not, fix `.gitignore`.

- [ ] **Step 5.3: Coordinate with girlfriend to obtain the sample**

Send the steps in `samples/README.md` to her. Wait for the zip. Record her CapCut/JianyingPro **exact version** in `docs/capcut_draft_schema_findings.md` (created in Step 5.5).

- [ ] **Step 5.4: Place unzipped sample at `samples/capcut_draft/mp_sample_001/`**

Verify: `ls samples/capcut_draft/mp_sample_001/draft_content.json` exists.

- [ ] **Step 5.5: Write parser test (TDD)**

Path: `tests/unit/test_capcut_parser.py`
```python
"""Snapshot test for CapCut draft schema parsing."""
from pathlib import Path

import pytest

from tools.capcut_schema_parser.parse_sample import parse_draft

SAMPLE = Path("samples/capcut_draft/mp_sample_001")


@pytest.mark.skipif(not SAMPLE.exists(), reason="sample not present")
def test_parse_sample_returns_top_level_keys():
    result = parse_draft(SAMPLE)
    assert "version" in result, "draft_content.json should expose a version field"
    assert "tracks" in result, "draft_content.json should expose a tracks list"
    assert isinstance(result["tracks"], list), "tracks must be a list"


@pytest.mark.skipif(not SAMPLE.exists(), reason="sample not present")
def test_parse_sample_has_video_audio_text_tracks():
    result = parse_draft(SAMPLE)
    track_types = {t.get("type") for t in result["tracks"]}
    # Sample created with 3 video clips + 1 BGM + 1 text layer:
    # we expect at least these track types to appear.
    assert "video" in track_types, f"missing video track; got {track_types}"
    assert "audio" in track_types, f"missing audio track; got {track_types}"
    assert "text" in track_types, f"missing text track; got {track_types}"
```

- [ ] **Step 5.6: Run the test (it will fail — module does not exist)**

Run: `pytest tests/unit/test_capcut_parser.py -v`

Expected: `ModuleNotFoundError: No module named 'tools.capcut_schema_parser.parse_sample'` or similar import failure.

- [ ] **Step 5.7: Implement `tools/capcut_schema_parser/parse_sample.py`**

Path: `tools/capcut_schema_parser/__init__.py`
```python
```

Path: `tools/capcut_schema_parser/parse_sample.py`
```python
"""Read a CapCut/JianyingPro draft folder and extract its top-level structure."""
import json
from pathlib import Path
from typing import Any


def parse_draft(draft_dir: Path) -> dict[str, Any]:
    """Return the parsed content of `draft_content.json` from a draft folder.

    Falls back to `draft_content.json` at the top level. If the file uses an
    alternate name (some versions ship `draft_info.json` companion files), the
    primary `draft_content.json` is still the canonical source.
    """
    content_path = draft_dir / "draft_content.json"
    if not content_path.exists():
        raise FileNotFoundError(f"draft_content.json not found in {draft_dir}")
    return json.loads(content_path.read_text(encoding="utf-8"))


def summarize(draft_dir: Path) -> None:
    """Print top-level structure of a draft for human inspection."""
    data = parse_draft(draft_dir)
    print(f"Top-level keys: {sorted(data.keys())}")
    print(f"Schema version: {data.get('version', 'UNKNOWN')}")
    tracks = data.get("tracks", [])
    print(f"Track count: {len(tracks)}")
    for i, t in enumerate(tracks):
        print(f"  Track {i}: type={t.get('type')}, segments={len(t.get('segments', []))}")
    materials = data.get("materials", {})
    print(f"Material categories: {sorted(materials.keys())}")


if __name__ == "__main__":
    import sys
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("samples/capcut_draft/mp_sample_001")
    summarize(target)
```

- [ ] **Step 5.8: Run tests again (should pass if sample is present)**

Run: `pytest tests/unit/test_capcut_parser.py -v`

If sample is missing, the tests will be skipped (PASS via skip). If present, both should PASS. If they FAIL, the schema is different from expected — record findings before committing.

- [ ] **Step 5.9: Run summary against the real sample and document findings**

```bash
python -m tools.capcut_schema_parser.parse_sample samples/capcut_draft/mp_sample_001
```

Capture the output. Then write findings to `docs/capcut_draft_schema_findings.md`:

Path: `docs/capcut_draft_schema_findings.md`
```markdown
# CapCut/JianyingPro Draft Schema — Findings (mp_sample_001)

| Field | Value |
|-------|-------|
| Date captured | 2026-__-__ |
| Source app | (剪映 / CapCut Pro) |
| Version | x.x.x |
| Platform | macOS (girlfriend's machine) |

## Top-level structure

(Paste output from `parse_sample.py` here.)

## Track structure

(Notes on how video / audio / text tracks differ.)

## Materials section

(What goes in `materials`? Links between tracks and materials.)

## Position / scale keyframes

(Where do reframe keyframes live? Field names? Time units?)

## Open questions

- [ ] Are start/end times in microseconds or milliseconds?
- [ ] Is asset path absolute or relative? How are asset moves handled?
- [ ] Where do transitions live? Per-segment or separate track?
- [ ] How are captions stored — embedded in text track or separate file?
```

Fill in based on actual sample. **The completeness of this document gates Stage 7 implementation.**

- [ ] **Step 5.10: Commit**

```bash
git add tests/unit/test_capcut_parser.py tools/capcut_schema_parser/ samples/README.md samples/.gitkeep docs/capcut_draft_schema_findings.md
git commit -m "tools: add CapCut draft schema parser and findings doc

Captures top-level structure of draft_content.json from a real
mp_sample_001 created on the target Mac. Schema findings documented
for use by the future Stage 7 (Draft Assembly) adapter."
```

---

## Task 6: CLIP zero-shot accuracy probe on carsmeet screenshots

**Files:**
- Create: `scripts/clip_zero_shot_probe.py`
- Create: `docs/clip_zero_shot_findings.md`

- [ ] **Step 6.1: Place 30 carsmeet screenshots under `samples/carsmeet_screenshots/`**

Manually capture 30 frames from existing public carsmeet.tw IG reels (e.g. via QuickTime screenshot or VLC frame export). Save as `001.jpg` … `030.jpg`. Aim for variety: Logo close-ups, wheels, leather seats, full car shots, exhaust pipes, dashboard, plus 5 negative examples (stranger faces, parking lot other cars).

For each screenshot, record the **expected dominant tag** in a CSV alongside.

Path: `samples/carsmeet_screenshots/labels.csv`
```csv
file,expected_tag
001.jpg,logo_close_up
002.jpg,wheel_caliper
003.jpg,interior_leather
...
030.jpg,stranger_face
```

(Fill in 30 rows. The file is gitignored along with the screenshots.)

- [ ] **Step 6.2: Write `scripts/clip_zero_shot_probe.py`**

Path: `scripts/clip_zero_shot_probe.py`
```python
"""Run CLIP zero-shot classification against carsmeet screenshots.

Outputs a per-file table of (expected_tag, predicted_tag, score) and a
summary of accuracy. Intended as a one-off accuracy probe for the
profile tag list — not production code.
"""
from __future__ import annotations

import argparse
import csv
from pathlib import Path

import torch
from PIL import Image
from open_clip import create_model_and_transforms, get_tokenizer

# Tags pulled from carsmeet-luxury.yaml plus prompt phrasing.
PROMPTS = {
    "logo_close_up": "a close-up of a luxury car emblem or hood ornament",
    "integral_hero_shot": "a full luxury car at a 45 degree front angle in a showroom",
    "body_line_pan": "a close-up tracking shot of luxury car body curves and reflections",
    "light_reflection": "showroom lights reflecting off a glossy car body",
    "wheel_caliper": "a close-up of an alloy car wheel and brake caliper",
    "interior_leather": "the inside of a luxury car showing diamond-stitched leather seats",
    "dashboard": "a close-up of a luxury car dashboard and steering wheel",
    "star_ceiling": "starlight headliner or constellation roof of a Rolls-Royce",
    "exhaust_pipe": "a close-up of a car exhaust pipe and rear bumper",
    "stranger_face": "a candid photograph of a person's face",
    "parking_lot_other_car": "an outdoor parking lot with multiple unrelated cars",
}

LABELS = list(PROMPTS.keys())
TEXT_PROMPTS = list(PROMPTS.values())


def main(samples_dir: Path, labels_csv: Path) -> None:
    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"Device: {device}")

    model, _, preprocess = create_model_and_transforms(
        "ViT-L-14", pretrained="laion2b_s32b_b82k"
    )
    model = model.to(device).eval()
    tokenizer = get_tokenizer("ViT-L-14")

    text_tokens = tokenizer(TEXT_PROMPTS).to(device)
    with torch.no_grad():
        text_features = model.encode_text(text_tokens)
        text_features = text_features / text_features.norm(dim=-1, keepdim=True)

    # Load expected labels
    expected: dict[str, str] = {}
    with labels_csv.open() as f:
        reader = csv.DictReader(f)
        for row in reader:
            expected[row["file"]] = row["expected_tag"]

    correct = 0
    total = 0
    rows: list[tuple[str, str, str, float]] = []
    for img_path in sorted(samples_dir.glob("*.jpg")):
        if img_path.name == "labels.csv":
            continue
        img = preprocess(Image.open(img_path).convert("RGB")).unsqueeze(0).to(device)
        with torch.no_grad():
            image_features = model.encode_image(img)
            image_features = image_features / image_features.norm(dim=-1, keepdim=True)
            sims = (image_features @ text_features.T).squeeze(0)
            probs = sims.softmax(dim=-1)
            top_idx = int(probs.argmax().item())
            top_label = LABELS[top_idx]
            top_score = float(probs[top_idx].item())

        exp = expected.get(img_path.name, "?")
        ok = "OK" if exp == top_label else "MISS"
        if exp == top_label:
            correct += 1
        total += 1
        rows.append((img_path.name, exp, top_label, top_score))
        print(f"{img_path.name:>10}  expected={exp:>22}  predicted={top_label:>22}  score={top_score:.2f}  {ok}")

    accuracy = correct / total if total else 0.0
    print()
    print(f"Accuracy: {correct}/{total} = {accuracy:.1%}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--samples", type=Path, default=Path("samples/carsmeet_screenshots"))
    p.add_argument("--labels", type=Path, default=Path("samples/carsmeet_screenshots/labels.csv"))
    args = p.parse_args()
    main(args.samples, args.labels)
```

- [ ] **Step 6.3: Add open_clip and torch to dev deps temporarily**

This script is one-off, not part of `pyproject.toml` runtime deps. Install in a side venv:
```bash
python -m venv .venv-clip
.venv-clip\Scripts\activate         # Windows
# OR: source .venv-clip/bin/activate # Mac/Linux
pip install open_clip_torch torch torchvision pillow
```

- [ ] **Step 6.4: Run the probe**

```bash
python scripts/clip_zero_shot_probe.py 2>&1 | tee scripts/clip_zero_shot.log
```

Record output. Note the per-tag accuracy.

- [ ] **Step 6.5: Document findings**

Path: `docs/clip_zero_shot_findings.md`
```markdown
# CLIP Zero-shot Probe — Findings

| Field | Value |
|-------|-------|
| Date | 2026-__-__ |
| Model | open_clip ViT-L-14 / laion2b_s32b_b82k |
| Sample size | 30 carsmeet screenshots |
| Overall accuracy | __.__% |

## Per-tag accuracy

| Tag | Correct / Total | Notes |
|-----|-----------------|-------|
| logo_close_up | _ / _ | |
| integral_hero_shot | _ / _ | |
| ... | | |

## Conclusions

- Tags with accuracy ≥ 70%: usable as-is for MVP
- Tags with accuracy 40–70%: refine prompt phrasing in stage 1
- Tags with accuracy < 40%: defer to Phase β fine-tune; rely on review-driven labels

## Prompt revisions tried

(Document any prompt rewrites you tested.)
```

Fill in based on the run.

- [ ] **Step 6.6: Commit**

```bash
git add scripts/clip_zero_shot_probe.py docs/clip_zero_shot_findings.md
git commit -m "scripts: CLIP zero-shot accuracy probe on carsmeet samples

Measures per-tag accuracy of CLIP ViT-L-14 against the carsmeet
profile tag list. Findings inform whether MVP can rely on CLIP or
needs custom training."
```

- [ ] **Step 6.7: Tear down side venv**

```bash
deactivate
rm -rf .venv-clip
```

---

## Task 7: File System Access API browser smoke test

**Files:**
- Create: `scripts/verify_fs_access_api.html`

- [ ] **Step 7.1: Write `scripts/verify_fs_access_api.html`**

Path: `scripts/verify_fs_access_api.html`
```html
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<title>FS Access API smoke test</title>
<style>
  body { font-family: system-ui, sans-serif; max-width: 700px; margin: 2em auto; padding: 0 1em; }
  button { padding: 0.5em 1em; font-size: 1em; margin: 0.5em 0; }
  pre { background: #f4f4f4; padding: 1em; overflow: auto; }
</style>
</head>
<body>
<h1>File System Access API smoke test</h1>
<p>Run this on the girlfriend's Mac in Chrome / Edge / Brave to confirm browser-based directory write works for the auto-sync feature.</p>

<ol>
  <li>Click <b>Pick directory</b> and choose any folder (later: her CapCut draft folder).</li>
  <li>Click <b>Write file</b>. A file <code>mp-test.txt</code> should appear in that folder.</li>
  <li>Reload the page; click <b>Pick saved directory</b> — should reuse without re-prompting (persistent permission).</li>
  <li>Click <b>Write again</b>. Should succeed without re-prompting.</li>
</ol>

<button id="pick">Pick directory</button>
<button id="write">Write file</button>
<button id="picksaved">Pick saved directory</button>
<button id="writeagain">Write again</button>
<pre id="log"></pre>

<script>
const log = (msg) => { document.getElementById('log').textContent += msg + '\n'; };
const dbName = 'fs-access-smoke';
let dirHandle = null;

async function saveHandle(handle) {
  const db = await new Promise((res, rej) => {
    const r = indexedDB.open(dbName, 1);
    r.onupgradeneeded = () => r.result.createObjectStore('handles');
    r.onsuccess = () => res(r.result);
    r.onerror = () => rej(r.error);
  });
  const tx = db.transaction('handles', 'readwrite');
  tx.objectStore('handles').put(handle, 'capcut');
  await new Promise((res) => (tx.oncomplete = res));
}

async function loadHandle() {
  const db = await new Promise((res, rej) => {
    const r = indexedDB.open(dbName, 1);
    r.onupgradeneeded = () => r.result.createObjectStore('handles');
    r.onsuccess = () => res(r.result);
    r.onerror = () => rej(r.error);
  });
  return new Promise((res) => {
    const r = db.transaction('handles').objectStore('handles').get('capcut');
    r.onsuccess = () => res(r.result);
    r.onerror = () => res(null);
  });
}

document.getElementById('pick').onclick = async () => {
  try {
    dirHandle = await window.showDirectoryPicker({ mode: 'readwrite' });
    await saveHandle(dirHandle);
    log('Picked: ' + dirHandle.name);
  } catch (e) { log('Pick failed: ' + e.message); }
};

document.getElementById('write').onclick = async () => {
  try {
    if (!dirHandle) { log('No dir handle. Pick first.'); return; }
    const fileHandle = await dirHandle.getFileHandle('mp-test.txt', { create: true });
    const w = await fileHandle.createWritable();
    await w.write(`smoke test ${new Date().toISOString()}`);
    await w.close();
    log('Wrote mp-test.txt');
  } catch (e) { log('Write failed: ' + e.message); }
};

document.getElementById('picksaved').onclick = async () => {
  try {
    const stored = await loadHandle();
    if (!stored) { log('No saved handle.'); return; }
    const perm = await stored.queryPermission({ mode: 'readwrite' });
    if (perm === 'granted') { dirHandle = stored; log('Reused saved handle: ' + stored.name); return; }
    const req = await stored.requestPermission({ mode: 'readwrite' });
    if (req === 'granted') { dirHandle = stored; log('Re-granted saved handle: ' + stored.name); }
    else { log('Permission denied.'); }
  } catch (e) { log('Pick saved failed: ' + e.message); }
};

document.getElementById('writeagain').onclick = async () => {
  try {
    if (!dirHandle) { log('No dir handle. Pick saved first.'); return; }
    const fileHandle = await dirHandle.getFileHandle('mp-test-2.txt', { create: true });
    const w = await fileHandle.createWritable();
    await w.write(`re-write ${new Date().toISOString()}`);
    await w.close();
    log('Wrote mp-test-2.txt');
  } catch (e) { log('Write again failed: ' + e.message); }
};
</script>
</body>
</html>
```

- [ ] **Step 7.2: Serve the page locally and have girlfriend run it**

On the developer's Windows host:
```bash
cd scripts && python -m http.server 8765
```
Send the URL `http://<windows-ip>:8765/verify_fs_access_api.html` to her (or open via Tailscale). Walk through all four buttons. Confirm:
- Directory pick works on her Mac Chrome
- File writes succeed
- After page reload, picking saved directory reuses permission without re-prompt

- [ ] **Step 7.3: Document findings**

Append a `## Result` section to `scripts/verify_fs_access_api.html` (as a HTML comment) or write a quick note in `docs/fs_access_api_findings.md`:

Path: `docs/fs_access_api_findings.md`
```markdown
# File System Access API — Findings

| Field | Value |
|-------|-------|
| Date | 2026-__-__ |
| Mac OS | x.x |
| Browser | Chrome ___ / Edge ___ |
| Pick dir | PASS / FAIL |
| Write file | PASS / FAIL |
| Persistent permission | PASS / FAIL |
| Notes | |
```

- [ ] **Step 7.4: Commit**

```bash
git add scripts/verify_fs_access_api.html docs/fs_access_api_findings.md
git commit -m "scripts: FS Access API browser smoke test for auto-sync"
```

---

## Task 8: Docker Compose foundation (Postgres + Redis)

**Files:**
- Create: `docker-compose.yml`
- Create: `docker-compose.override.yml.example`

- [ ] **Step 8.1: Write `docker-compose.yml`**

Path: `docker-compose.yml`
```yaml
services:
  postgres:
    image: postgres:16-alpine
    restart: unless-stopped
    environment:
      POSTGRES_USER: ${POSTGRES_USER}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      POSTGRES_DB: ${POSTGRES_DB}
    volumes:
      - postgres_data:/var/lib/postgresql/data
    ports:
      - "127.0.0.1:5432:5432"
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER} -d ${POSTGRES_DB}"]
      interval: 5s
      timeout: 3s
      retries: 10

  redis:
    image: redis:7-alpine
    restart: unless-stopped
    ports:
      - "127.0.0.1:6379:6379"
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 10

  api:
    build:
      context: .
      dockerfile: docker/api.Dockerfile
    restart: unless-stopped
    env_file: .env
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
    ports:
      - "127.0.0.1:8000:8000"
    volumes:
      - ./src:/app/src:ro
      - ./profiles:/app/profiles:ro

  web:
    build:
      context: ./web
      dockerfile: ../docker/web.Dockerfile
    restart: unless-stopped
    depends_on:
      - api
    ports:
      - "127.0.0.1:8080:80"

volumes:
  postgres_data:
```

- [ ] **Step 8.2: Write `docker-compose.override.yml.example`**

Path: `docker-compose.override.yml.example`
```yaml
# Copy to docker-compose.override.yml for local development overrides.
# E.g., expose Postgres on 0.0.0.0 for dev tools, or attach a worker GPU service.
services:
  postgres:
    ports:
      - "5432:5432"
```

- [ ] **Step 8.3: Verify env file**

```bash
cp .env.example .env
docker compose config 2>&1 | head -50
```

Expected: docker compose validates the YAML. No errors.

- [ ] **Step 8.4: Commit**

```bash
git add docker-compose.yml docker-compose.override.yml.example
git commit -m "infra: docker-compose with postgres + redis + api/web stubs"
```

---

## Task 9: FastAPI hello service with `/health` endpoint

**Files:**
- Create: `src/media_processor/__init__.py`
- Create: `src/media_processor/api/__init__.py`
- Create: `src/media_processor/api/main.py`
- Create: `src/media_processor/api/config.py`
- Create: `src/media_processor/api/routers/__init__.py`
- Create: `src/media_processor/api/routers/health.py`
- Create: `src/media_processor/core/__init__.py`
- Create: `src/media_processor/core/db.py`
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`
- Create: `tests/unit/__init__.py`
- Create: `tests/unit/test_health.py`
- Create: `docker/api.Dockerfile`

- [ ] **Step 9.1: Write the failing health test (TDD)**

Path: `tests/unit/test_health.py`
```python
"""Health endpoint smoke test."""
from fastapi.testclient import TestClient

from media_processor.api.main import app


def test_health_returns_ok() -> None:
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert "version" in body


def test_health_includes_dependency_status() -> None:
    client = TestClient(app)
    response = client.get("/health")
    body = response.json()
    assert "dependencies" in body
    assert "postgres" in body["dependencies"]
    assert "redis" in body["dependencies"]
```

- [ ] **Step 9.2: Write `tests/conftest.py`**

Path: `tests/conftest.py`
```python
"""Pytest fixtures for media_processor tests."""
import os

import pytest


@pytest.fixture(autouse=True)
def _set_test_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set environment variables required for the API to start in tests."""
    monkeypatch.setenv("POSTGRES_USER", "test")
    monkeypatch.setenv("POSTGRES_PASSWORD", "test")
    monkeypatch.setenv("POSTGRES_DB", "test")
    monkeypatch.setenv("POSTGRES_HOST", "localhost")
    monkeypatch.setenv("POSTGRES_PORT", "5432")
    monkeypatch.setenv("REDIS_HOST", "localhost")
    monkeypatch.setenv("REDIS_PORT", "6379")
    monkeypatch.setenv("API_HOST", "0.0.0.0")
    monkeypatch.setenv("API_PORT", "8000")
```

- [ ] **Step 9.3: Add empty `__init__.py` files**

```bash
mkdir -p src/media_processor/api/routers src/media_processor/core tests/unit
touch src/media_processor/__init__.py
touch src/media_processor/api/__init__.py
touch src/media_processor/api/routers/__init__.py
touch src/media_processor/core/__init__.py
touch tests/__init__.py
touch tests/unit/__init__.py
```

- [ ] **Step 9.4: Run test to verify it fails**

Run: `pytest tests/unit/test_health.py -v`

Expected: ImportError on `media_processor.api.main` — module doesn't exist yet.

- [ ] **Step 9.5: Implement `config.py`**

Path: `src/media_processor/api/config.py`
```python
"""Application settings loaded from environment via pydantic-settings."""
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    postgres_user: str = Field(...)
    postgres_password: str = Field(...)
    postgres_db: str = Field(...)
    postgres_host: str = Field(default="postgres")
    postgres_port: int = Field(default=5432)

    redis_host: str = Field(default="redis")
    redis_port: int = Field(default=6379)

    api_host: str = Field(default="0.0.0.0")
    api_port: int = Field(default=8000)

    @property
    def postgres_dsn(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def redis_url(self) -> str:
        return f"redis://{self.redis_host}:{self.redis_port}/0"


settings = Settings()  # type: ignore[call-arg]
```

- [ ] **Step 9.6: Implement `core/db.py`**

Path: `src/media_processor/core/db.py`
```python
"""SQLAlchemy async engine + session factory."""
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from media_processor.api.config import settings

engine = create_async_engine(settings.postgres_dsn, echo=False, pool_pre_ping=True)
async_session_maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def ping_postgres() -> bool:
    """Lightweight check that the database is reachable."""
    from sqlalchemy import text

    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


async def ping_redis() -> bool:
    """Lightweight Redis reachability check."""
    import redis.asyncio as redis_asyncio

    client = redis_asyncio.from_url(settings.redis_url, socket_timeout=2.0)
    try:
        return bool(await client.ping())
    except Exception:
        return False
    finally:
        await client.aclose()
```

- [ ] **Step 9.7: Implement `routers/health.py`**

Path: `src/media_processor/api/routers/health.py`
```python
"""Health endpoint."""
from typing import Any

from fastapi import APIRouter

from media_processor.core.db import ping_postgres, ping_redis

router = APIRouter()

VERSION = "0.1.0"


@router.get("/health")
async def health() -> dict[str, Any]:
    pg_ok = await ping_postgres()
    redis_ok = await ping_redis()
    return {
        "status": "ok" if (pg_ok and redis_ok) else "degraded",
        "version": VERSION,
        "dependencies": {
            "postgres": "up" if pg_ok else "down",
            "redis": "up" if redis_ok else "down",
        },
    }
```

- [ ] **Step 9.8: Implement `api/main.py`**

Path: `src/media_processor/api/main.py`
```python
"""FastAPI application entry point."""
from fastapi import FastAPI

from media_processor.api.routers import health

app = FastAPI(
    title="media-processor API",
    version="0.1.0",
)

app.include_router(health.router)
```

- [ ] **Step 9.9: Run test (still expected to fail because Postgres/Redis aren't up in test env)**

Run: `pytest tests/unit/test_health.py -v`

Expected: `test_health_returns_ok` may PASS with `status: degraded` (since dependencies are unreachable in unit-test env), but the assertion `body["status"] == "ok"` will FAIL.

- [ ] **Step 9.10: Adjust the test to allow `degraded` when dependencies are not running**

Edit `tests/unit/test_health.py`:
```python
def test_health_returns_ok() -> None:
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    # In unit tests Postgres/Redis are not running, so status may be "degraded".
    assert body["status"] in {"ok", "degraded"}
    assert "version" in body
```

- [ ] **Step 9.11: Run tests; expect both to PASS**

Run: `pytest tests/unit/ -v`

Expected: both tests PASS. The health endpoint exists and returns the right shape.

- [ ] **Step 9.12: Write `docker/api.Dockerfile`**

Path: `docker/api.Dockerfile`
```dockerfile
FROM python:3.11-slim

WORKDIR /app

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential libpq-dev curl \
    && rm -rf /var/lib/apt/lists/*

# Python deps
COPY pyproject.toml ./
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir hatchling \
 && pip install --no-cache-dir \
        "fastapi>=0.115.0" \
        "uvicorn[standard]>=0.32.0" \
        "pydantic>=2.9.0" \
        "pydantic-settings>=2.6.0" \
        "sqlalchemy>=2.0.36" \
        "asyncpg>=0.30.0" \
        "redis>=5.2.0" \
        "rq>=2.0.0" \
        "pyyaml>=6.0.2"

COPY src/ ./src/
COPY profiles/ ./profiles/
COPY alembic.ini ./

ENV PYTHONPATH=/app/src

EXPOSE 8000

CMD ["uvicorn", "media_processor.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 9.13: Bring up the stack and curl the health endpoint**

```bash
cp -n .env.example .env || true
docker compose up -d --build postgres redis api
sleep 10
curl http://127.0.0.1:8000/health
```

Expected JSON output:
```json
{"status":"ok","version":"0.1.0","dependencies":{"postgres":"up","redis":"up"}}
```

If `degraded`, check `docker compose logs api` for connection errors.

- [ ] **Step 9.14: Commit**

```bash
git add src/ tests/ docker/api.Dockerfile
git commit -m "feat(api): FastAPI hello with /health endpoint and dep checks"
```

---

## Task 10: Alembic skeleton

**Files:**
- Create: `alembic.ini`
- Create: `alembic/env.py`
- Create: `alembic/script.py.mako`
- Create: `alembic/versions/.gitkeep`

- [ ] **Step 10.1: Initialise Alembic structure**

Run:
```bash
pip install alembic
alembic init alembic
```

This creates `alembic.ini` and the `alembic/` directory with `env.py`, `script.py.mako`, and `versions/`.

- [ ] **Step 10.2: Edit `alembic.ini` to read DSN from env**

Open `alembic.ini`, find the `sqlalchemy.url` line, set:
```ini
sqlalchemy.url = postgresql+psycopg2://%(POSTGRES_USER)s:%(POSTGRES_PASSWORD)s@%(POSTGRES_HOST)s:%(POSTGRES_PORT)s/%(POSTGRES_DB)s
```

- [ ] **Step 10.3: Edit `alembic/env.py` to load env vars**

Replace `alembic/env.py` content with:
```python
"""Alembic environment configuration."""
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Inject env vars into the alembic config so %(VAR)s interpolation works.
for var in (
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "POSTGRES_HOST",
    "POSTGRES_PORT",
    "POSTGRES_DB",
):
    if var in os.environ:
        config.set_main_option(var, os.environ[var])

target_metadata = None  # No models yet — schema added in later plans


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

- [ ] **Step 10.4: Add `.gitkeep` so versions/ is tracked**

```bash
touch alembic/versions/.gitkeep
```

Verify `.gitignore` exception for `.gitkeep` is present (added in Task 1).

- [ ] **Step 10.5: Run `alembic upgrade head` (no migrations yet, should be a no-op)**

Make sure Postgres is up: `docker compose up -d postgres`. Then:
```bash
export POSTGRES_USER=media POSTGRES_PASSWORD=changeme POSTGRES_DB=media_processor POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=5432
alembic upgrade head
```

Expected: `INFO  [alembic.runtime.migration] Will assume transactional DDL.` and exits cleanly.

- [ ] **Step 10.6: Commit**

```bash
git add alembic.ini alembic/
git commit -m "infra: alembic skeleton (no migrations yet)"
```

---

## Task 11: Web UI shell (Vite + React)

**Files:**
- Create: `web/package.json`
- Create: `web/tsconfig.json`
- Create: `web/vite.config.ts`
- Create: `web/index.html`
- Create: `web/src/main.tsx`
- Create: `web/src/App.tsx`
- Create: `web/nginx.conf`
- Create: `docker/web.Dockerfile`

- [ ] **Step 11.1: Bootstrap the Vite project (manual files, no `create-vite` to keep deterministic)**

Path: `web/package.json`
```json
{
  "name": "media-processor-web",
  "private": true,
  "version": "0.1.0",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc -b && vite build",
    "preview": "vite preview"
  },
  "dependencies": {
    "react": "^18.3.1",
    "react-dom": "^18.3.1"
  },
  "devDependencies": {
    "@types/react": "^18.3.12",
    "@types/react-dom": "^18.3.1",
    "@vitejs/plugin-react": "^4.3.3",
    "typescript": "^5.6.3",
    "vite": "^5.4.10"
  }
}
```

Path: `web/tsconfig.json`
```json
{
  "compilerOptions": {
    "target": "ES2022",
    "useDefineForClassFields": true,
    "lib": ["ES2022", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "moduleResolution": "Bundler",
    "strict": true,
    "noEmit": true,
    "jsx": "react-jsx",
    "esModuleInterop": true,
    "skipLibCheck": true,
    "resolveJsonModule": true,
    "isolatedModules": true,
    "allowImportingTsExtensions": true
  },
  "include": ["src"]
}
```

Path: `web/vite.config.ts`
```typescript
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "0.0.0.0",
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
});
```

Path: `web/index.html`
```html
<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Media Processor</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
```

Path: `web/src/main.tsx`
```typescript
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";

const root = document.getElementById("root");
if (!root) throw new Error("Root element not found");
createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>
);
```

Path: `web/src/App.tsx`
```typescript
import { useEffect, useState } from "react";

interface Health {
  status: string;
  version: string;
  dependencies: { postgres: string; redis: string };
}

export default function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch("/api/health")
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then(setHealth)
      .catch((e) => setError(e.message));
  }, []);

  return (
    <main style={{ fontFamily: "system-ui", maxWidth: 600, margin: "2em auto", padding: "0 1em" }}>
      <h1>Media Processor</h1>
      <p>Phase α — Step 0 + M1 infrastructure shell.</p>
      {error && <pre style={{ color: "crimson" }}>API error: {error}</pre>}
      {health && (
        <pre style={{ background: "#f4f4f4", padding: "1em" }}>
          {JSON.stringify(health, null, 2)}
        </pre>
      )}
      {!health && !error && <p>Loading health…</p>}
    </main>
  );
}
```

Path: `web/nginx.conf`
```nginx
server {
  listen 80;
  server_name _;

  root /usr/share/nginx/html;
  index index.html;

  location /api/ {
    proxy_pass http://api:8000/;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
  }

  location / {
    try_files $uri $uri/ /index.html;
  }
}
```

Path: `docker/web.Dockerfile`
```dockerfile
FROM node:20-alpine AS build
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm install
COPY . .
RUN npm run build

FROM nginx:1.27-alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
```

- [ ] **Step 11.2: Install web deps and run dev server**

```bash
cd web
npm install
npm run dev
```

Expected: Vite starts on `http://0.0.0.0:5173`. Open in browser; should show "Media Processor" title and the JSON health response.

- [ ] **Step 11.3: Build and run via Docker Compose**

```bash
cd ..
docker compose up -d --build web
sleep 5
curl -s http://127.0.0.1:8080/ | grep "Media Processor"
curl -s http://127.0.0.1:8080/api/health | head -1
```

Expected: HTML contains "Media Processor"; `/api/health` returns the JSON.

- [ ] **Step 11.4: Commit**

```bash
git add web/ docker/web.Dockerfile
git commit -m "feat(web): vite + react shell that polls /api/health"
```

---

## Task 12: CI / lint / typecheck baseline

**Files:**
- Modify: `Makefile` (already has lint / fmt / typecheck / test from Task 1.4 — verify they all pass now)
- Create: `.github/workflows/ci.yml`

- [ ] **Step 12.1: Run lint, format, typecheck, tests locally; fix anything that fails**

```bash
ruff check src tests
ruff format --check src tests
mypy src
pytest -v
```

If `ruff format --check` complains, run `ruff format src tests`. If `mypy` complains about missing type stubs, install relevant stub packages or add `# type: ignore[reason]` with a real reason. Re-run until all four commands return zero exit codes.

- [ ] **Step 12.2: Write `.github/workflows/ci.yml`**

Path: `.github/workflows/ci.yml`
```yaml
name: CI

on:
  pull_request:
  push:
    branches: [main]

jobs:
  python:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
          cache: pip
      - run: pip install -e ".[dev]"
      - run: ruff check src tests
      - run: ruff format --check src tests
      - run: mypy src
      - run: pytest -v

  web:
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: web
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: "20"
          cache: npm
          cache-dependency-path: web/package-lock.json
      - run: npm ci
      - run: npm run build
```

- [ ] **Step 12.3: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: lint/format/typecheck/test on push and PR"
```

- [ ] **Step 12.4: Push and confirm CI green**

```bash
git push -u origin main
```

Open the GitHub Actions tab; confirm both `python` and `web` jobs pass. If they fail, fix locally and push again.

---

## Task 13: M1 wrap-up — README updates and final smoke test

**Files:**
- Modify: `README.md`

- [ ] **Step 13.1: Run the full local smoke test**

```bash
docker compose down -v       # Reset
docker compose up -d --build
sleep 15
curl -fsS http://127.0.0.1:8000/health | tee health.json
curl -fsS http://127.0.0.1:8080/api/health | tee health-via-web.json
diff health.json health-via-web.json && echo "OK: API responses match through both routes"
rm health.json health-via-web.json
```

Expected: both responses match and have `"status":"ok"`.

- [ ] **Step 13.2: Update `README.md` with M1 status and verification table**

Append a new section to `README.md`:
```markdown
## Step 0 verification status

| Check | Status | Doc |
|-------|--------|-----|
| SMB share Mac ↔ Windows | ✅ / ❌ | `scripts/verify_smb.md` |
| WSL2 + NVIDIA GPU passthrough | ✅ / ❌ | `scripts/verify_gpu.log` |
| CapCut draft schema captured | ✅ / ❌ | `docs/capcut_draft_schema_findings.md` |
| CLIP zero-shot probe | ✅ / ❌ | `docs/clip_zero_shot_findings.md` |
| File System Access API on her Mac | ✅ / ❌ | `docs/fs_access_api_findings.md` |

## M1 acceptance

- `docker compose up -d --build` brings all services up.
- `curl http://127.0.0.1:8000/health` returns `{"status":"ok", ...}`.
- `curl http://127.0.0.1:8080/api/health` returns the same.
- `pytest -v` and `ruff check` and `mypy src` all green.
- CI green on `main`.
```

Replace the ✅/❌ placeholders with the actual results from each verification task.

- [ ] **Step 13.3: Commit**

```bash
git add README.md
git commit -m "docs: M1 acceptance checklist and Step 0 verification status"
```

- [ ] **Step 13.4: Push**

```bash
git push
```

---

## Self-review checklist (run after completing all tasks)

- [ ] All Step 0 evidence lives in `docs/` or `scripts/` and is linked from README
- [ ] `docker compose up -d --build` from a clean clone works
- [ ] `pytest -v` passes
- [ ] `ruff check src tests` passes with no errors
- [ ] `ruff format --check src tests` passes
- [ ] `mypy src` passes
- [ ] CI green on GitHub
- [ ] CapCut draft sample collected, schema documented
- [ ] CLIP zero-shot probe run, accuracy recorded
- [ ] SMB share works in both directions, Mac and Windows
- [ ] WSL2 GPU container can run `nvidia-smi`
- [ ] File System Access API works on girlfriend's Mac Chrome

---

## Hand-off to next plan

When this plan is done, the foundation is in place. Next plan (M2):

- Add 9 entities to Postgres via Alembic migrations
- Implement Ingest Watcher (folder watcher + ffprobe + thumbnail + sha256)
- Implement Stage 0 (Probe) and a stub Stage 1 that does `noop` analysis
- Wire RQ jobs end-to-end so a clip dropped in `assets/` produces an Asset row
- Add basic project list UI

That plan will be written separately under `docs/superpowers/plans/`.
