"""Tests for POST /drafts/{id}/patch (Stage 4.5).

We override:
- ``get_session`` → in-memory SQLite, seeded with one draft + one tagged asset
- ``get_llm_patcher`` → stub patcher returning a fixed ``ProfilePatch``
- ``get_profile_loader`` → loads the real ``profiles/carsmeet-luxury.yaml``
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Iterator
from pathlib import Path
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

from media_processor.api.deps import get_llm_patcher, get_profile_loader, get_session
from media_processor.api.main import app as production_app
from media_processor.models import (
    Asset,
    AssetSegment,
    AssetTag,
    Base,
    Draft,
    DraftSegment,
    Project,
)
from media_processor.profile import load_profile
from media_processor.profile.loader import ProfileSpec
from media_processor.services.llm_patcher import (
    DraftSegmentSummary,
    LLMPatcher,
    LLMPatchError,
    ProfilePatch,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
CARSMEET_PROFILE = REPO_ROOT / "profiles" / "carsmeet-luxury.yaml"


class _StubPatcher:
    """Stub that records its call and returns a fixed patch (or raises)."""

    def __init__(self, patch: ProfilePatch | None = None, exc: Exception | None = None) -> None:
        self._patch = patch
        self._exc = exc
        self.calls: list[dict[str, Any]] = []

    async def request_patch(
        self,
        *,
        profile: ProfileSpec,
        segments: list[DraftSegmentSummary],
        user_feedback: str,
    ) -> ProfilePatch:
        self.calls.append(
            {
                "profile_name": profile.name,
                "segments": list(segments),
                "user_feedback": user_feedback,
            }
        )
        if self._exc is not None:
            raise self._exc
        assert self._patch is not None
        return self._patch


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
            name="carsmeet-Phantom-0428",
            client="CarsMeet",
            profile_name="carsmeet-luxury",
            source_dir="/mnt/assets/carsmeet/phantom",
            status="ready_for_review",
        )
        s.add(p)
        await s.flush()

        a = Asset(
            project_id=p.id,
            file_path="/mnt/assets/foo.mp4",
            duration_ms=5000,
            resolution="3840x2160",
            fps=30.0,
            codec="h264",
            sha256="a" * 64,
            status="ready",
        )
        s.add(a)
        await s.flush()

        s.add_all(
            [
                AssetTag(
                    asset_id=a.id,
                    tag_type="object",
                    tag_name="car",
                    confidence=0.95,
                    source_model="yolov11",
                ),
                AssetTag(
                    asset_id=a.id,
                    tag_type="visual",
                    tag_name="logo_close_up",
                    confidence=0.6,
                    source_model="clip",
                ),
            ]
        )

        seg = AssetSegment(asset_id=a.id, start_ms=0, end_ms=1000, score=0.9)
        s.add(seg)
        await s.flush()

        d = Draft(
            project_id=p.id,
            profile_name="carsmeet-luxury",
            version=1,
            status="ready_for_review",
            ai_score=8.4,
        )
        s.add(d)
        await s.flush()

        s.add(
            DraftSegment(
                draft_id=d.id,
                order=0,
                asset_segment_id=seg.id,
                on_timeline_start_ms=0,
                on_timeline_end_ms=1000,
                transition="fade",
            )
        )
        await s.commit()


@pytest.fixture()
def stub_patcher() -> _StubPatcher:
    return _StubPatcher(
        patch=ProfilePatch(
            tag_weight_deltas={"logo_close_up": 0.5, "car": -0.1},
            required_segments_overrides={"opening_hero": True, "hero_tag": "wheel_spin"},
        )
    )


@pytest.fixture()
def app(stub_patcher: _StubPatcher) -> Iterator[FastAPI]:
    import asyncio

    engine, session_maker = _make_engine_and_session()

    async def init() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await _seed(session_maker)

    asyncio.run(init())

    async def override_session() -> AsyncIterator[AsyncSession]:
        async with session_maker() as session:
            yield session

    def override_patcher() -> LLMPatcher:
        # FastAPI doesn't care about the static type — duck-typed via Depends.
        return stub_patcher  # type: ignore[return-value]

    def override_profile_loader() -> Callable[[str], ProfileSpec]:
        def _load(name: str) -> ProfileSpec:
            assert name == "carsmeet-luxury"
            return load_profile(CARSMEET_PROFILE)

        return _load

    production_app.dependency_overrides[get_session] = override_session
    production_app.dependency_overrides[get_llm_patcher] = override_patcher
    production_app.dependency_overrides[get_profile_loader] = override_profile_loader
    try:
        yield production_app
    finally:
        production_app.dependency_overrides.clear()
        asyncio.run(engine.dispose())


def test_patch_draft_returns_patched_profile(app: FastAPI, stub_patcher: _StubPatcher) -> None:
    client = TestClient(app)
    resp = client.post(
        "/drafts/1/patch",
        json={"user_feedback": "多用車身特寫，開頭要 Hero shot"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["draft_id"] == 1
    assert body["profile_name"] == "carsmeet-luxury"
    assert body["tag_weight_deltas"] == {"logo_close_up": 0.5, "car": -0.1}
    assert body["required_segments_overrides"] == {
        "opening_hero": True,
        "hero_tag": "wheel_spin",
    }
    # Profile's logo_close_up is 1.5; +0.5 → 2.0
    assert body["patched_tag_weights"]["logo_close_up"] == pytest.approx(2.0)
    assert body["patched_required_segments"]["hero_tag"] == "wheel_spin"
    assert body["patched_required_segments"]["opening_hero"] is True

    # Stub recorded the call with the actual draft segment + primary tag.
    assert len(stub_patcher.calls) == 1
    call = stub_patcher.calls[0]
    assert call["user_feedback"].startswith("多用")
    assert call["profile_name"] == "carsmeet-luxury"
    assert call["segments"][0].primary_tag == "car"  # higher-confidence tag wins


def test_patch_draft_persists_prompt_feedback(app: FastAPI) -> None:
    client = TestClient(app)
    resp = client.post("/drafts/1/patch", json={"user_feedback": "remember this please"})
    assert resp.status_code == 200

    detail_resp = client.get("/drafts/1")
    assert detail_resp.status_code == 200
    # prompt_feedback isn't part of DraftDetail today — verify via a direct
    # ORM read instead so the test doesn't depend on schema additions.
    import asyncio

    from sqlalchemy import select

    async def _read() -> str | None:
        sess_factory = app.dependency_overrides[get_session]
        async for sess in sess_factory():
            row = (await sess.execute(select(Draft).where(Draft.id == 1))).scalar_one()
            return row.prompt_feedback
        return None

    assert asyncio.run(_read()) == "remember this please"


def test_patch_draft_unknown_id_404(app: FastAPI) -> None:
    client = TestClient(app)
    resp = client.post("/drafts/9999/patch", json={"user_feedback": "x"})
    assert resp.status_code == 404


def test_patch_draft_empty_feedback_422(app: FastAPI) -> None:
    client = TestClient(app)
    resp = client.post("/drafts/1/patch", json={"user_feedback": ""})
    assert resp.status_code == 422


def test_patch_draft_llm_failure_returns_502() -> None:
    """When the LLM patcher raises, the endpoint should return 502 so the client
    knows to fall back to a non-LLM recut (spec §6.5 fallback)."""
    import asyncio

    engine, session_maker = _make_engine_and_session()

    async def init() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await _seed(session_maker)

    asyncio.run(init())

    async def override_session() -> AsyncIterator[AsyncSession]:
        async with session_maker() as session:
            yield session

    failing = _StubPatcher(exc=LLMPatchError("all keys exhausted"))

    def override_patcher() -> LLMPatcher:
        return failing  # type: ignore[return-value]

    def override_profile_loader() -> Callable[[str], ProfileSpec]:
        return lambda _: load_profile(CARSMEET_PROFILE)

    production_app.dependency_overrides[get_session] = override_session
    production_app.dependency_overrides[get_llm_patcher] = override_patcher
    production_app.dependency_overrides[get_profile_loader] = override_profile_loader
    try:
        client = TestClient(production_app)
        resp = client.post("/drafts/1/patch", json={"user_feedback": "x"})
        assert resp.status_code == 502
        assert "all keys exhausted" in resp.json()["detail"]
    finally:
        production_app.dependency_overrides.clear()
        asyncio.run(engine.dispose())


def test_patch_draft_no_keys_returns_503() -> None:
    """With no override and no LLM_API_KEYS env, the dep should raise 503."""
    import asyncio

    engine, session_maker = _make_engine_and_session()

    async def init() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await _seed(session_maker)

    asyncio.run(init())

    async def override_session() -> AsyncIterator[AsyncSession]:
        async with session_maker() as session:
            yield session

    # Note: no override for get_llm_patcher → real dep runs and sees empty keys.
    production_app.dependency_overrides[get_session] = override_session
    try:
        client = TestClient(production_app)
        resp = client.post("/drafts/1/patch", json={"user_feedback": "x"})
        assert resp.status_code == 503
    finally:
        production_app.dependency_overrides.clear()
        asyncio.run(engine.dispose())
