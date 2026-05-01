"""Tests for POST /projects/{id}/edit (M5 auto-edit trigger)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
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
def fake_enqueue(monkeypatch: pytest.MonkeyPatch) -> list[tuple[int, bool]]:
    calls: list[tuple[int, bool]] = []

    def _record(project_id: int, *, force: bool = False) -> str:
        calls.append((project_id, force))
        return f"job-{project_id}"

    monkeypatch.setattr(projects_router, "enqueue_project_edit", _record)
    return calls


@pytest.fixture()
def app(fake_enqueue: list[tuple[int, bool]]) -> Iterator[FastAPI]:
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
    app: FastAPI, fake_enqueue: list[tuple[int, bool]]
) -> None:
    client = TestClient(app)
    resp = client.post("/projects/1/edit", json={})
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["project_id"] == 1
    assert body["job_id"] == "job-1"
    assert body["status"] == "enqueued"
    assert fake_enqueue == [(1, False)]


def test_edit_trigger_404_on_missing_project(
    app: FastAPI, fake_enqueue: list[tuple[int, bool]]
) -> None:
    client = TestClient(app)
    resp = client.post("/projects/999/edit", json={})
    assert resp.status_code == 404
    assert fake_enqueue == []


def test_edit_trigger_409_when_draft_processing(
    fake_enqueue: list[tuple[int, bool]],
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
        # force=true bypasses the conflict.
        resp_force = client.post("/projects/1/edit", json={"force": True})
        assert resp_force.status_code == 202
        assert fake_enqueue == [(1, True)]
    finally:
        production_app.dependency_overrides.clear()
        asyncio.run(engine.dispose())
