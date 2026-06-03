"""API tests for project StoryScript endpoints."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from media_processor.api.deps import get_session
from media_processor.api.main import app as production_app
from media_processor.api.routers import projects as projects_router
from media_processor.models import Asset, Base, Project
from media_processor.services import story_script as story_scripts


def _make_engine_and_session() -> tuple[Any, async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _seed(session_maker: async_sessionmaker[AsyncSession]) -> None:
    async with session_maker() as session:
        project = Project(
            name="story-api-test",
            profile_name="universal",
            source_dir="/tmp/story-api-test",
            status="pending",
            target_aspect_ratio="9:16",
        )
        session.add(project)
        await session.flush()
        session.add(
            Asset(
                project_id=project.id,
                file_path="/tmp/story-api-test/a.mp4",
                duration_ms=8_000,
                sha256="2" * 64,
                status="pending",
            )
        )
        await session.commit()


@pytest.fixture()
def app() -> Iterator[FastAPI]:
    engine, session_maker = _make_engine_and_session()

    async def init() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await _seed(session_maker)

    asyncio.run(init())

    async def override_session() -> AsyncIterator[AsyncSession]:
        async with session_maker() as session:
            yield session

    production_app.dependency_overrides[get_session] = override_session
    try:
        yield production_app
    finally:
        production_app.dependency_overrides.clear()
        asyncio.run(engine.dispose())


def _story_payload() -> dict[str, Any]:
    return {
        "title": "可發布短影音腳本",
        "summary": "用故事模式產生剪輯計畫。",
        "items": [
            {
                "order": 1,
                "asset_id": 1,
                "source_start_ms": 0,
                "source_end_ms": 2500,
                "picture": "開場特寫",
                "narration": "第一秒先讓觀眾知道亮點。",
                "audio_intent": "narration",
                "beat_type": "hook",
                "hook_type": "question",
                "reason": "適合開頭",
            }
        ],
    }


def test_story_script_save_then_fetch(app: FastAPI) -> None:
    client = TestClient(app)

    save_resp = client.put("/projects/1/story-script", json=_story_payload())
    assert save_resp.status_code == 200, save_resp.text
    saved = save_resp.json()
    assert saved["schema_version"] == "story-script.v1"
    assert saved["title"] == "可發布短影音腳本"
    assert saved["items"][0]["audio_intent"] == "narration"

    fetch_resp = client.get("/projects/1/story-script")
    assert fetch_resp.status_code == 200, fetch_resp.text
    fetched = fetch_resp.json()
    assert fetched["id"] == saved["id"]
    assert fetched["metadata"]["source"] == "manual_edit"


def test_story_script_generate_endpoint_persists_document(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_generate(
        _session: AsyncSession,
        project_id: int,
        *,
        target_items: int = 8,
    ) -> story_scripts.StoryScriptDocument:
        assert project_id == 1
        assert target_items == 3
        return story_scripts.StoryScriptDocument(
            project_id=project_id,
            title="AI 故事腳本",
            summary="測試生成 endpoint。",
            items=(
                story_scripts.StoryScriptItem(
                    order=1,
                    asset_id=1,
                    source_start_ms=0,
                    source_end_ms=1800,
                    picture="畫面",
                    narration="旁白",
                    audio_intent="narration_with_original",
                    beat_type="hook",
                    reason="測試",
                ),
            ),
            metadata={"provider": "test", "model": "fake", "used_visual_context": False},
        )

    monkeypatch.setattr(projects_router.story_scripts, "generate_story_script", fake_generate)
    client = TestClient(app)

    resp = client.post("/projects/1/story-script/generate", json={"target_items": 3})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["title"] == "AI 故事腳本"
    assert body["provider"] == "test"
    assert body["model"] == "fake"
    assert body["items"][0]["audio_intent"] == "narration_with_original"


def test_story_script_save_rejects_invalid_range(app: FastAPI) -> None:
    payload = _story_payload()
    payload["items"][0]["source_end_ms"] = 0
    client = TestClient(app)

    resp = client.put("/projects/1/story-script", json=payload)

    assert resp.status_code == 422
