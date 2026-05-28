"""Tests for POST /projects/{id}/edit (M5 auto-edit trigger)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from media_processor.api.deps import get_session
from media_processor.api.main import app as production_app
from media_processor.api.routers import projects as projects_router
from media_processor.models import Asset, Base, Draft, Project

# (project_id, draft_id, force, target_duration_ms, initial_voice_volume, style_preset, edit_mode)
EnqueueCall = tuple[int, int, bool, int | None, float, str, str]


def _make_engine_and_session() -> tuple[Any, async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _seed(session_maker: async_sessionmaker[AsyncSession]) -> None:
    async with session_maker() as s:
        p = Project(
            name="m5-trigger-test",
            client=None,
            profile_name="universal",
            source_dir="/tmp/m5",
            status="ready_for_review",
            target_aspect_ratio="9:16",
        )
        s.add(p)
        await s.flush()
        s.add(
            Asset(
                project_id=p.id,
                file_path="/tmp/a.mp4",
                duration_ms=5_000,
                sha256="0" * 64,
                status="analyzed",
            )
        )
        await s.commit()


async def _seed_with_processing_draft(
    session_maker: async_sessionmaker[AsyncSession],
) -> None:
    await _seed(session_maker)
    async with session_maker() as s:
        s.add(
            Draft(
                project_id=1,
                profile_name="universal",
                version=1,
                status="processing",
                progress_steps_json={"plan": "running"},
            )
        )
        await s.commit()


@pytest.fixture()
def fake_enqueue(
    monkeypatch: pytest.MonkeyPatch,
) -> list[EnqueueCall]:
    calls: list[EnqueueCall] = []

    def _record(
        project_id: int,
        *,
        draft_id: int,
        force: bool = False,
        target_duration_ms: int | None = None,
        stabilize: bool = True,
        initial_voice_volume: float = 1.0,
        style_preset: str = "custom",
        edit_mode: str = "standard",
        **_extra: object,
    ) -> str:
        # ``stabilize`` was added in v0.14.3; tests that don't care about it
        # rely on the default. Extra kwargs are absorbed so future toggles
        # don't break unrelated assertions.
        calls.append(
            (
                project_id,
                draft_id,
                force,
                target_duration_ms,
                initial_voice_volume,
                style_preset,
                edit_mode,
            )
        )
        return f"job-{project_id}"

    monkeypatch.setattr(projects_router, "enqueue_project_edit", _record)
    return calls


@pytest.fixture()
def app(fake_enqueue: list[EnqueueCall]) -> Iterator[FastAPI]:
    engine, session_maker = _make_engine_and_session()

    async def init() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await _seed(session_maker)

    asyncio.run(init())

    async def override_session() -> AsyncIterator[AsyncSession]:
        async with session_maker() as s:
            yield s

    production_app.dependency_overrides[get_session] = override_session
    try:
        yield production_app
    finally:
        production_app.dependency_overrides.clear()
        asyncio.run(engine.dispose())


def test_edit_trigger_enqueues_and_returns_job_id(
    app: FastAPI, fake_enqueue: list[EnqueueCall]
) -> None:
    client = TestClient(app)
    resp = client.post("/projects/1/edit", json={})
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["project_id"] == 1
    assert body["job_id"] == "job-1"
    assert body["status"] == "enqueued"
    # The API now creates the Draft row synchronously so the UI can pick
    # it up immediately. draft_id must be a real id (not the 0 placeholder
    # we returned before this fix), and the row should be in `pending`
    # with all four progress steps initialised.
    assert isinstance(body["draft_id"], int) and body["draft_id"] > 0
    assert fake_enqueue == [(1, body["draft_id"], False, None, 1.0, "custom", "standard")]


def test_edit_trigger_passes_target_duration_seconds(
    app: FastAPI, fake_enqueue: list[EnqueueCall]
) -> None:
    """User-supplied target_duration_seconds is converted to ms and enqueued."""
    client = TestClient(app)
    resp = client.post("/projects/1/edit", json={"target_duration_seconds": 90})
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert fake_enqueue == [(1, body["draft_id"], False, 90_000, 1.0, "custom", "standard")]


def test_edit_trigger_passes_initial_voice_volume(
    app: FastAPI, fake_enqueue: list[EnqueueCall]
) -> None:
    """Fresh renders can mute source audio before DraftSegment rows exist."""
    client = TestClient(app)
    resp = client.post("/projects/1/edit", json={"initial_voice_volume": 0})
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert fake_enqueue == [(1, body["draft_id"], False, None, 0.0, "custom", "standard")]


def test_edit_trigger_persists_and_enqueues_edit_mode(
    app: FastAPI, fake_enqueue: list[EnqueueCall]
) -> None:
    client = TestClient(app)
    resp = client.post(
        "/projects/1/edit",
        json={"style_preset": "commercial", "edit_mode": "luxury_auto"},
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert fake_enqueue == [(1, body["draft_id"], False, None, 1.0, "commercial", "luxury_auto")]

    drafts_resp = client.get("/projects/1/drafts")
    assert drafts_resp.status_code == 200
    draft = drafts_resp.json()[0]
    assert draft["style_preset"] == "commercial"
    assert draft["edit_mode"] == "luxury_auto"


def test_edit_trigger_rejects_out_of_range_duration(
    app: FastAPI, fake_enqueue: list[EnqueueCall]
) -> None:
    """Pydantic clamps the field — values outside [10, 300] should 422."""
    client = TestClient(app)
    too_short = client.post("/projects/1/edit", json={"target_duration_seconds": 5})
    too_long = client.post("/projects/1/edit", json={"target_duration_seconds": 600})
    voice_too_quiet = client.post("/projects/1/edit", json={"initial_voice_volume": -0.1})
    voice_too_loud = client.post("/projects/1/edit", json={"initial_voice_volume": 1.6})
    assert too_short.status_code == 422
    assert too_long.status_code == 422
    assert voice_too_quiet.status_code == 422
    assert voice_too_loud.status_code == 422
    assert fake_enqueue == []


def test_edit_trigger_persists_pending_draft(app: FastAPI, fake_enqueue: list[EnqueueCall]) -> None:
    """The POST creates a pending Draft row before returning 202 so the
    UI's immediate ``GET /projects/{id}/drafts`` finds it (no race with
    the worker creating the row asynchronously)."""
    client = TestClient(app)
    resp = client.post("/projects/1/edit", json={})
    assert resp.status_code == 202, resp.text
    new_draft_id = resp.json()["draft_id"]

    drafts_resp = client.get("/projects/1/drafts")
    assert drafts_resp.status_code == 200
    drafts = drafts_resp.json()
    assert len(drafts) == 1
    assert drafts[0]["id"] == new_draft_id
    assert drafts[0]["status"] == "pending"
    assert drafts[0]["version"] == 1
    # Progress map seeds every EditStep value to "pending" (M6.4 added
    # ``bgm``); the API mirrors EDIT_STEP_VALUES so new stages show up
    # automatically without the test needing to know about them.
    from media_processor.models import EDIT_STEP_VALUES

    assert drafts[0]["progress_steps"] == dict.fromkeys(EDIT_STEP_VALUES, "pending")


def test_edit_trigger_enqueue_failure_marks_draft_failed(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_enqueue(*_args: Any, **_kwargs: Any) -> str:
        raise RuntimeError("redis down")

    monkeypatch.setattr(projects_router, "enqueue_project_edit", fail_enqueue)
    client = TestClient(app)
    resp = client.post("/projects/1/edit", json={})
    assert resp.status_code == 502, resp.text

    drafts_resp = client.get("/projects/1/drafts")
    assert drafts_resp.status_code == 200
    drafts = drafts_resp.json()
    assert len(drafts) == 1
    assert drafts[0]["status"] == "failed"


def test_edit_trigger_409_when_pending_draft_exists(
    fake_enqueue: list[EnqueueCall],
) -> None:
    """A second POST while the first is still ``pending`` (worker hasn't
    started yet) must also return 409 — this is the user-visible bug we
    just fixed: without this, the user clicks twice in the gap between
    enqueue and worker pickup."""
    engine, session_maker = _make_engine_and_session()

    async def init() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await _seed(session_maker)
        async with session_maker() as s:
            s.add(
                Draft(
                    project_id=1,
                    profile_name="universal",
                    version=1,
                    status="pending",
                    progress_steps_json={
                        "plan": "pending",
                        "cut": "pending",
                        "concat": "pending",
                        "subtitles": "pending",
                    },
                )
            )
            await s.commit()

    asyncio.run(init())

    async def override_session() -> AsyncIterator[AsyncSession]:
        async with session_maker() as s:
            yield s

    production_app.dependency_overrides[get_session] = override_session
    try:
        client = TestClient(production_app)
        resp = client.post("/projects/1/edit", json={})
        assert resp.status_code == 409
        assert fake_enqueue == []

        async def _count_drafts() -> int:
            async with session_maker() as s:
                rows = (await s.execute(select(Draft))).scalars().all()
                return len(rows)

        assert asyncio.run(_count_drafts()) == 1
    finally:
        production_app.dependency_overrides.clear()
        asyncio.run(engine.dispose())


def test_edit_trigger_404_on_missing_project(app: FastAPI, fake_enqueue: list[EnqueueCall]) -> None:
    client = TestClient(app)
    resp = client.post("/projects/999/edit", json={})
    assert resp.status_code == 404
    assert fake_enqueue == []


def test_edit_trigger_409_when_draft_processing(
    fake_enqueue: list[EnqueueCall],
) -> None:
    """A second POST while a draft is `processing` returns 409 unless force=true."""
    engine, session_maker = _make_engine_and_session()

    async def init() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await _seed_with_processing_draft(session_maker)

    asyncio.run(init())

    async def override_session() -> AsyncIterator[AsyncSession]:
        async with session_maker() as s:
            yield s

    production_app.dependency_overrides[get_session] = override_session
    try:
        client = TestClient(production_app)
        resp = client.post("/projects/1/edit", json={})
        assert resp.status_code == 409
        # force=true bypasses the conflict and creates a fresh draft row.
        resp_force = client.post("/projects/1/edit", json={"force": True})
        assert resp_force.status_code == 202
        body = resp_force.json()
        assert body["draft_id"] > 1  # version 1 already exists from the seed
        assert fake_enqueue == [(1, body["draft_id"], True, None, 1.0, "custom", "standard")]
    finally:
        production_app.dependency_overrides.clear()
        asyncio.run(engine.dispose())
