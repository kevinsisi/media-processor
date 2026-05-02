## Context

M1 left us with a service shell that only knows how to answer `/health`. The Phase α design (`docs/superpowers/specs/2026-04-30-media-processor-design.md` §3–7) lays out a 9-entity Postgres schema, a YAML-based profile system, and an 8.5-stage AI pipeline. M2 is the **deterministic, GPU-free spine** that the GPU stages will hang from in M3+.

Stakeholders:

- The pipeline (M3+ workers) needs ORM models to read/write segments, scores, drafts.
- The Review Inbox UI needs HTTP endpoints to list projects, fetch drafts, post review actions.
- The CapCut writer is the contract that bridges our internal timeline model to剪映/CapCut Pro Mac. The exact JSON schema is still pending Step 0 sample arrival (§11.1), so we ship an adapter seam now and tighten the schema later without breaking callers.

Constraints:

- Python 3.11, SQLAlchemy 2.0 async, Alembic, FastAPI, Pydantic v2 — all already pinned in `pyproject.toml`.
- Models must be pure SQLAlchemy 2.0 declarative (`MappedAsDataclass` not required); migrations must run on Postgres 16 in prod and SQLite in unit tests.
- Cut planning runs pure-Python (no numpy hot path) — 30-cut workload is < 50 ms; correctness > speed.
- All time fields stored as integer milliseconds (consistent with `duration_ms` in spec §4.1).

## Goals / Non-Goals

**Goals:**

1. 9 ORM entities mirroring spec §4.1, with sensible indexes and FK cascades.
2. Initial Alembic migration that creates the full schema in one revision (`0001_init`).
3. Typed profile loader with validation (catches malformed YAML before pipeline runs).
4. Pure-Python cut planner producing a deterministic timeline given (segments, beat grid, profile rules).
5. CapCut writer skeleton that emits a zip containing `draft_content.json` + `draft_meta_info.json`, structured so the JSON schema can be tightened once the Step 0 sample arrives.
6. Core API routers (`/projects`, `/assets`, `/drafts`, `/reviews`) — minimum surface for the Review Inbox UI.
7. Typed Web API client tracking the routes above.

**Non-Goals:**

- Any GPU stage (per-asset analysis, reframe, caption, face blur).
- Anthropic LLM patcher.
- WebSocket notifications.
- Ingest Watcher (folder monitoring) — separate change, M3.
- Authentication / multi-tenancy — Phase β.
- File System Access API auto-sync — separate change, M4.

## Decisions

### D1. SQLAlchemy 2.0 declarative with `Mapped[...]` typing

Use `DeclarativeBase` + `Mapped[T]` annotations rather than the legacy 1.x `Column(...)` pattern. Why: full mypy strict compatibility, future-proof, matches what's already imported in `core/db.py`. Alternative considered: `MappedAsDataclass` — rejected for now because it conflicts with `Mapped[<Optional>]` style and forces `init=False` boilerplate everywhere.

### D2. Single initial migration, not autogenerate

Hand-write `0001_init.py` rather than running `alembic revision --autogenerate`. Why: autogenerate has known issues with partial-index/jsonb defaults and we want the migration deterministic and reviewable. The migration is also exercised under SQLite in unit tests where autogenerate would diverge from Postgres reality. Alternative: autogenerate then hand-edit — rejected, more error-prone for the first migration.

### D3. JSONB for `reframe_keyframes` / `beat_grid_json` / `tag.time_ranges_ms`

Use `JSONB` on Postgres, fall back to `JSON` on SQLite (via SQLAlchemy `JSON` type with PG variant). Why: spec §4.4 explicitly calls out jsonb indexability. Alternative: serialize to text — rejected, loses query power.

### D4. Profile lives in YAML, not in DB

Spec §4.3 + §5 both make this call. The DB `Profile` table stores **only the loaded snapshot** (name, description, raw YAML blob, parsed-at timestamp) so AI workers can fetch it without disk access; the canonical source remains the file. Alternative: skip the table entirely — rejected, makes the worker depend on shared filesystem layout, fights idempotency.

### D5. Cut planner is a pure function, no DB access

Signature: `plan_cuts(segments: list[SegmentInput], beats: list[float], rules: EditingRules) -> list[PlannedSegment]`. The router/service layer is responsible for hydrating segments from DB and persisting the resulting `Draft` + `DraftSegment` rows. Why: testable offline, swappable later (LP solver / DP), no DB mock needed. Alternative: stateful service holding session — rejected, makes unit tests heavy.

### D6. CapCut writer ships with a versioned schema marker

`CapCutDraftWriter.SCHEMA_VERSION = "step0-pending"` in the emitted JSON. When Step 0 sample lands, we bump to a real version and tighten the schema. Why: the data shape is provisional; consumers (the worker) can detect and refuse mismatched versions in M3.

### D7. API surface — read-heavy, minimal writes

For M2 we only need:

- `GET /projects`, `GET /projects/{id}` — list and detail
- `GET /projects/{id}/drafts` — drafts for a project
- `GET /drafts/{id}` — single draft with segments
- `POST /reviews` — create a review action (approve / reject / repatch / download)
- `GET /assets/{id}` — asset detail (used by "AI 判斷理由" popup)

Write endpoints for `Project` / `Asset` / `Draft` are deferred — those rows are produced by the pipeline (M3+), not by HTTP requests. Alternative: full CRUD on every entity — rejected, premature, adds attack surface.

### D8. Reviewer hardcoded to `"alice"` (spec §4.3)

API accepts `reviewer` field but defaults to `"alice"`. No auth in MVP. When Phase β adds multi-tenancy this becomes a real claim from the JWT.

### D9. Web API client is typed but unwired

`web/src/api/client.ts` exposes `fetchProjects()`, `fetchDraft(id)`, `postReview(...)` etc. The existing `ProjectList` and `Review` mockup screens **continue to use mock data** — wiring them to the live API is part of M3 / M4. Why: avoids gating the visual mockup on real data while still producing the typed surface so M3 wiring is mechanical.

## Risks / Trade-offs

- **[CapCut schema is provisional]** → Mitigated by `SCHEMA_VERSION` marker + adapter seam (D6); the unit test in M2 only asserts structural shape (zip layout, top-level keys).
- **[SQLite vs Postgres divergence in tests]** → Use SQLAlchemy-level JSON column abstraction; never rely on Postgres-only operators in app code (no `@>`, no `jsonb_path_exists`); migrations exercised only against SQLite in CI for now, real Postgres validated locally via `docker compose up`.
- **[Alembic `target_metadata` previously `None`]** → switch to `models.Base.metadata`; existing skeleton migration directory has only `.gitkeep`, so no in-flight revision conflict.
- **[Cut planner determinism under tied scores]** → Stable sort by `(score, segment_id)` so two runs with the same input produce the same output; tests pin this.
- **[Time fields as int ms]** → Some libraries prefer float seconds; we centralize conversion in `cut_planner` boundary and keep DB strictly int ms (avoids floating-point drift over 30-cut concatenations).

## Migration Plan

1. Land models package + populate `target_metadata` in `alembic/env.py`.
2. Hand-write `0001_init.py`.
3. Smoke-run migration against SQLite in unit test (`alembic upgrade head` → introspect tables).
4. Local dev: `docker compose up postgres && alembic upgrade head`.
5. Rollback: `alembic downgrade base` (the migration ships a `downgrade()` that drops everything in reverse FK order).

## Open Questions

- Exact CapCut JSON schema (waiting on Step 0 sample). Resolved in M3 after `tools/capcut_schema_parser` runs against the real zip.
- Whether `BGM.beat_grid_json` should normalize to fractional beats or absolute milliseconds. Going with **milliseconds (int list)** for now to match the rest of the time-axis convention; revisit if librosa output proves unwieldy.
