"""Unit tests for services.edit_planner — Gemini cut-plan generation."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from media_processor.models import (
    Asset,
    AssetTranscript,
    Base,
    Project,
    Script,
)
from media_processor.services import edit_planner
from media_processor.services.edit_planner import (
    ASSET_SCORE_SCHEMA_VERSION,
    SCHEMA_VERSION,
    EditPlanInvalidError,
    EditPlanQuotaError,
)

_BASE_URL = "https://example.test/v1"
_MODEL = "gemini-2.5-flash"


def _build_response(payload: dict[str, Any]) -> httpx.Response:
    body = {"candidates": [{"content": {"parts": [{"text": json.dumps(payload)}]}}]}
    return httpx.Response(200, json=body)


@pytest.fixture()
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        # Seed a project with one asset + one transcript + a script body.
        project = Project(
            name="t",
            client=None,
            profile_name="universal",
            source_dir=str(Path("assets")),
            target_aspect_ratio="9:16",
        )
        s.add(project)
        await s.flush()
        asset = Asset(
            project_id=project.id,
            file_path=str(Path("/tmp/a.mp4")),
            duration_ms=10_000,
            sha256="0" * 64,
        )
        s.add(asset)
        await s.flush()
        s.add(
            AssetTranscript(
                asset_id=asset.id,
                language="zh-Hant",
                model="whisper-medium",
                transcript_text="片段一 片段二",
                segments_json=[
                    {"idx": 0, "start_ms": 0, "end_ms": 4_000, "text": "片段一"},
                    {"idx": 1, "start_ms": 4_000, "end_ms": 8_000, "text": "片段二"},
                ],
                edited=False,
            )
        )
        s.add(Script(project_id=project.id, body="腳本內容", source_filename=None))
        await s.commit()
        yield s
    await engine.dispose()


def _mock_transport(handler):  # type: ignore[no-untyped-def]
    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_plan_happy_path(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    # New shape: one Gemini call per asset, each returns a per-asset score
    # plus an M6.3 transition_to_next field.
    asset_score_payload = {
        "schema_version": ASSET_SCORE_SCHEMA_VERSION,
        "score": 80,
        "position": "opening",
        "best_span_ms": [0, 4000],
        "source_kind": "scripted",
        "transition_to_next": "fade",
        "reason": "matches line 1",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return _build_response(asset_score_payload)

    transport = _mock_transport(handler)

    real_async_client = httpx.AsyncClient

    def patched_async_client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = transport
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(edit_planner.httpx, "AsyncClient", patched_async_client)

    plan = await edit_planner.plan(
        project_id=1,
        session=session,
        api_keys=("k1",),
        model=_MODEL,
        base_url=_BASE_URL,
        timeout_s=5.0,
        target_duration_ms=20_000,
    )
    assert plan.schema_version == SCHEMA_VERSION
    assert len(plan.segments) == 1  # fixture seeds one asset → one cut
    seg = plan.segments[0]
    assert seg.asset_id == 1
    assert seg.asset_start_ms == 0
    assert seg.asset_end_ms == 4000
    assert seg.source_kind == "scripted"
    assert seg.transition_to_next == "fade"
    # Phase 8.1: with no emotion tags on the asset, dominant_emotion
    # falls back to the canonical default.
    assert seg.dominant_emotion == edit_planner.EMOTION_DEFAULT
    assert plan.target_duration_ms == 20_000
    # Notes are now synthesised locally summarising the fanout.
    assert "per-asset fanout" in plan.notes


def test_emotion_shift_escalates_transition_to_circlecrop() -> None:
    """Adjacent cuts whose dominant emotion buckets differ should burn a circlecrop."""
    from media_processor.services.edit_planner import (
        TRANSITION_DEFAULT,
        VALID_TRANSITIONS,
        _AssetScore,
        _assemble_plan,
    )

    assert "circlecrop" in VALID_TRANSITIONS

    scores = [
        _AssetScore(
            asset_id=1,
            score=90,
            position="opening",
            best_span_ms=(0, 2_000),
            source_kind="improv",
            reason="",
            dominant_motion="static",
            transition_to_next=TRANSITION_DEFAULT,
            dominant_emotion="happy",
        ),
        _AssetScore(
            asset_id=2,
            score=85,
            position="middle",
            best_span_ms=(0, 2_500),
            source_kind="improv",
            reason="",
            dominant_motion="static",
            transition_to_next="dissolve",
            dominant_emotion="serious",
        ),
    ]
    cuts = _assemble_plan(scores, target_duration_ms=10_000)
    assert len(cuts) == 2
    # First cut transitions across an emotion-bucket boundary → circlecrop.
    assert cuts[0].transition_to_next == "circlecrop"
    # Last cut's transition is unused; left as Gemini's suggestion.
    assert cuts[1].transition_to_next == "dissolve"


def test_serialise_round_trip_preserves_dominant_emotion() -> None:
    """Phase 8.1 — dominant_emotion survives JSON round-trip via cut_plan_json."""
    from media_processor.services.edit_planner import (
        CutPlan,
        CutPlanSegment,
        deserialise_plan,
        serialise_plan,
    )

    plan = CutPlan(
        schema_version=SCHEMA_VERSION,
        target_duration_ms=10_000,
        target_aspect_ratio="9:16",
        profile_name="universal",
        segments=(
            CutPlanSegment(
                order=0,
                asset_id=1,
                asset_start_ms=0,
                asset_end_ms=2_000,
                source_kind="improv",
                reason="",
                transition_to_next="circlecrop",
                dominant_emotion="surprised",
            ),
        ),
    )
    blob = serialise_plan(plan)
    restored = deserialise_plan(blob)
    assert restored.segments[0].dominant_emotion == "surprised"
    assert restored.segments[0].transition_to_next == "circlecrop"


@pytest.mark.asyncio
async def test_plan_invalid_schema_raises(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    bad_payload = {
        "schema_version": "wrong",
        "score": 50,
        "position": "middle",
        "best_span_ms": [0, 1000],
        "source_kind": "improv",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return _build_response(bad_payload)

    transport = _mock_transport(handler)
    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(
        edit_planner.httpx,
        "AsyncClient",
        lambda *a, **kw: real_async_client(*a, **{**kw, "transport": transport}),
    )

    with pytest.raises(EditPlanInvalidError):
        await edit_planner.plan(
            project_id=1,
            session=session,
            api_keys=("k1",),
            model=_MODEL,
            base_url=_BASE_URL,
            timeout_s=5.0,
        )


@pytest.mark.asyncio
async def test_plan_quota_exhausted(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text="rate-limited")

    transport = _mock_transport(handler)
    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(
        edit_planner.httpx,
        "AsyncClient",
        lambda *a, **kw: real_async_client(*a, **{**kw, "transport": transport}),
    )

    with pytest.raises(EditPlanQuotaError):
        await edit_planner.plan(
            project_id=1,
            session=session,
            api_keys=("k1", "k2"),
            model=_MODEL,
            base_url=_BASE_URL,
            timeout_s=5.0,
        )


@pytest.mark.asyncio
async def test_heuristic_fallback_uses_transcript(session: AsyncSession) -> None:
    plan = await edit_planner.heuristic_fallback(
        project_id=1,
        session=session,
        target_duration_ms=10_000,
        fallback_reason="test",
    )
    assert plan.used_fallback is True
    assert plan.fallback_reason == "test"
    assert len(plan.segments) >= 1
    assert all(s.source_kind == "improv" for s in plan.segments)


def test_serialise_plan_roundtrip() -> None:
    plan = edit_planner.CutPlan(
        schema_version=SCHEMA_VERSION,
        target_duration_ms=5_000,
        target_aspect_ratio="1:1",
        profile_name="universal",
        segments=(
            edit_planner.CutPlanSegment(
                order=0,
                asset_id=42,
                asset_start_ms=100,
                asset_end_ms=600,
                source_kind="improv",
                reason="x",
            ),
        ),
        notes="n",
    )
    data = edit_planner.serialise_plan(plan)
    assert data["schema_version"] == SCHEMA_VERSION
    assert data["segments"][0]["asset_id"] == 42
