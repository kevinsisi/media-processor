## 1. ORM models and base

- [ ] 1.1 Create `src/media_processor/models/__init__.py` exposing `Base` and the 9 entities
- [ ] 1.2 Define `Project` with status enum (`pending`, `processing`, `degraded`, `ready_for_review`, `approved`, `failed`)
- [ ] 1.3 Define `Asset` with FK to Project and cascade delete
- [ ] 1.4 Define `AssetTag` with composite uniqueness (`asset_id`, `tag_type`, `tag_name`, `source_model`)
- [ ] 1.5 Define `AssetSegment` with `start_ms < end_ms` check constraint
- [ ] 1.6 Define `Draft` with `(project_id, version)` unique key
- [ ] 1.7 Define `DraftSegment` with `(draft_id, order)` unique key
- [ ] 1.8 Define `Review` with action enum (`approve`, `reject`, `repatch`, `download`)
- [ ] 1.9 Define `BGM` with JSON beat grid
- [ ] 1.10 Define `Profile` with unique `name`

## 2. Alembic migration

- [ ] 2.1 Wire `target_metadata = Base.metadata` in `alembic/env.py`
- [ ] 2.2 Hand-write `alembic/versions/0001_init.py` covering all 9 tables, FKs, indexes, check constraints
- [ ] 2.3 Implement `downgrade()` in reverse FK order
- [ ] 2.4 Add unit test that runs `alembic upgrade head` on SQLite and introspects tables
- [ ] 2.5 Add unit test that downgrades back to base

## 3. Profile loader

- [ ] 3.1 Create `src/media_processor/profile/__init__.py`
- [ ] 3.2 Implement `load_profile(path) -> Profile` with typed dataclasses
- [ ] 3.3 Add `ProfileValidationError` and validation rules from spec §5
- [ ] 3.4 Add unit test loading both bundled profiles
- [ ] 3.5 Add unit tests for missing-key and bad-value cases

## 4. Cut planner

- [ ] 4.1 Create `src/media_processor/services/__init__.py`
- [ ] 4.2 Define typed input dataclasses (`SegmentInput`, `EditingRules`, `PlannedSegment`)
- [ ] 4.3 Implement `plan_cuts` greedy + diversity penalty
- [ ] 4.4 Implement required-segments pin for `opening_hero` / `closing_hero`
- [ ] 4.5 Add cut-count clamping (`min_cuts ≤ len ≤ max_cuts`, ≤ `len(beats)`)
- [ ] 4.6 Add per-segment uniqueness guard (no double-use)
- [ ] 4.7 Add unit tests covering determinism, diversity, hero pinning, fallback warning, beat-aligned timeline windows

## 5. CapCut writer

- [ ] 5.1 Create `src/media_processor/services/capcut_writer.py`
- [ ] 5.2 Implement `CapCutDraftWriter.write(draft, segments, output_path)` producing a zip
- [ ] 5.3 Emit `draft_content.json` with `version=SCHEMA_VERSION`, deterministic key order
- [ ] 5.4 Emit `draft_meta_info.json`
- [ ] 5.5 Build `tracks` array with video / audio / text per spec
- [ ] 5.6 Add unit test for zip layout, version marker, video-only and full-draft cases, determinism

## 6. API routers

- [ ] 6.1 Add Pydantic response schemas in `src/media_processor/api/schemas.py`
- [ ] 6.2 Add async DB session dependency
- [ ] 6.3 Implement `routers/projects.py` (`GET /projects`, `GET /projects/{id}`, `GET /projects/{id}/drafts`)
- [ ] 6.4 Implement `routers/drafts.py` (`GET /drafts/{id}`)
- [ ] 6.5 Implement `routers/assets.py` (`GET /assets/{id}` with tags)
- [ ] 6.6 Implement `routers/reviews.py` (`POST /reviews`)
- [ ] 6.7 Wire routers in `api/main.py`
- [ ] 6.8 Add unit tests using FastAPI `TestClient` + SQLite test DB fixture

## 7. Web API client

- [ ] 7.1 Add `web/src/api/types.ts` mirroring response schemas
- [ ] 7.2 Add `web/src/api/client.ts` with `fetchProjects`, `fetchProject`, `fetchProjectDrafts`, `fetchDraft`, `fetchAsset`, `postReview`
- [ ] 7.3 Keep `ProjectList` / `Review` mockup screens on mock data; wiring deferred to M3

## 8. Verification and release

- [ ] 8.1 Run `pytest -q`
- [ ] 8.2 Run `ruff check .`
- [ ] 8.3 Run `mypy --strict src tests`
- [ ] 8.4 Bump `pyproject.toml` and `routers/health.py` `VERSION` from `0.2.0` to `0.3.0`
- [ ] 8.5 Update `docs/superpowers/specs/2026-04-30-media-processor-design.md` §17 changelog
- [ ] 8.6 Commit (Kevin identity) and push to `main`
