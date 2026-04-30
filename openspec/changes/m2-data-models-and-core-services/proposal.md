## Why

M1 stood up the bare service shell (FastAPI `/health`, Postgres + Redis, Alembic skeleton, Web mockup). To reach M2 — "拖 3 素材 → AI 產 mp4 預覽" — we need persistent data structures and the offline core services that the rest of the AI pipeline will plug into. Without ORM models, migrations, profile parsing, cut planning, and a CapCut draft writer, every later stage (Ingest, Per-asset Analysis, Reframe, Caption, Face Blur, Draft Assembly) has nowhere to write its output and no contract to honour.

This change lands the deterministic, GPU-free foundation so the GPU stages (M3+) can be built on top.

## What Changes

- Add 9 SQLAlchemy ORM entities (`Project`, `Asset`, `AssetTag`, `AssetSegment`, `Draft`, `DraftSegment`, `Review`, `BGM`, `Profile`) per design §4.
- Wire `target_metadata` in `alembic/env.py` and ship initial migration `alembic/versions/0001_init.py`.
- Add `media_processor.profile.loader` — typed parser for `profiles/*.yaml` with validation.
- Add `media_processor.services.cut_planner` — pure-Python greedy + diversity penalty + required-segments algorithm (§6.3).
- Add `media_processor.services.capcut_writer` — adapter that takes the internal timeline model and emits a CapCut `draft_content.json` zip (skeleton schema, refined once Step 0 sample arrives).
- Add API routers: `/projects`, `/assets`, `/drafts`, `/reviews` — read endpoints + minimal write endpoints needed for the Review Inbox UI.
- Add Web API client (`web/src/api/client.ts`) typed against the new endpoints; the existing mockup screens keep their mock-data path until M3, but the client surface is in place.
- Bump version `0.2.0 → 0.3.0`.

Out of scope (deferred): GPU stages 1/5/6/7.5, LLM patcher, Ingest Watcher, WebSocket notify, File System Access auto-sync.

## Capabilities

### New Capabilities

- `data-models`: 9 ORM entities + initial Alembic migration for the project/asset/draft/review domain.
- `profile-loading`: Typed loader and validator for `profiles/*.yaml`.
- `cut-planning`: Greedy + diversity-penalty + required-segments algorithm that turns scored segments + beat grid into a `Draft` segment list.
- `capcut-writer`: Adapter from internal timeline model to CapCut draft zip (`draft_content.json` + `draft_meta_info.json`).
- `core-api-routers`: HTTP endpoints for Projects, Assets, Drafts, Reviews backing the Review Inbox UI.

### Modified Capabilities

None — M1 only stood up the shell; no existing spec-level requirements change.

## Impact

- **Code**: new packages `src/media_processor/models/`, `src/media_processor/profile/`, `src/media_processor/services/`; expanded `src/media_processor/api/routers/`; new `web/src/api/`.
- **DB**: first real migration; running `alembic upgrade head` becomes mandatory before the API can serve writes.
- **Dependencies**: no new runtime deps (all already in pyproject — sqlalchemy, alembic, pyyaml, fastapi).
- **Tests**: new unit tests for models, profile loader, cut planner, capcut writer. Integration test for migration up/down on SQLite (lightweight CI).
- **Docs**: §17 changelog gains an M2 entry; OpenSpec `specs/` populated.
- **Risk**: CapCut writer is provisional until the Step 0 sample lands; we ship a schema marker + adapter seam now and refine without breaking callers.
