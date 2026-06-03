"""Tests for Narrato-style StoryScript services."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from media_processor.models import (
    Asset,
    AssetTranscript,
    Base,
    Draft,
    DraftSegment,
    Project,
    Script,
    StoryScript,
)
from media_processor.services import edit_orchestrator
from media_processor.services.story_script import (
    StoryScriptValidationError,
    gather_story_inputs,
    story_document_to_cut_plan,
    story_document_to_srt,
    validate_story_script,
)


def _valid_payload() -> dict[str, Any]:
    return {
        "schema_version": "story-script.v1",
        "title": "展示鉤子",
        "summary": "用三段素材說完主軸。",
        "items": [
            {
                "order": 1,
                "asset_id": 10,
                "source_start_ms": 0,
                "source_end_ms": 2500,
                "picture": "車頭特寫",
                "narration": "這不是一般的展示車。",
                "audio_intent": "narration",
                "beat_type": "hook",
                "hook_type": "contrast",
                "reason": "開頭反差清楚",
            },
            {
                "order": 2,
                "asset_id": 10,
                "source_start_ms": 3000,
                "source_end_ms": 5000,
                "picture": "原聲介紹內裝",
                "narration": "保留現場說明聲。",
                "audio_intent": "original",
                "beat_type": "proof",
                "reason": "原聲資訊密度高",
            },
        ],
    }


def test_validate_story_script_rejects_duplicate_order() -> None:
    payload = _valid_payload()
    payload["items"][1]["order"] = 1

    with pytest.raises(StoryScriptValidationError, match="duplicate order"):
        validate_story_script(payload, project_id=1, asset_durations={10: 8_000})


def test_validate_story_script_rejects_unknown_audio_intent() -> None:
    payload = _valid_payload()
    payload["items"][0]["audio_intent"] = "voiceover"

    with pytest.raises(StoryScriptValidationError, match="invalid audio_intent"):
        validate_story_script(payload, project_id=1, asset_durations={10: 8_000})


def test_validate_story_script_accepts_narrato_ost_alias() -> None:
    payload = _valid_payload()
    del payload["items"][0]["audio_intent"]
    payload["items"][0]["OST"] = 2

    document = validate_story_script(payload, project_id=1, asset_durations={10: 8_000})

    assert document.items[0].audio_intent == "narration_with_original"


def test_story_document_to_cut_plan_maps_audio_intents() -> None:
    document = validate_story_script(_valid_payload(), project_id=1, asset_durations={10: 8_000})

    plan = story_document_to_cut_plan(
        document,
        target_aspect_ratio="9:16",
        profile_name="universal",
    )

    assert plan.schema_version == "story.cut-plan.v1"
    assert plan.target_duration_ms == 4_500
    assert [seg.source_kind for seg in plan.segments] == ["scripted", "improv"]
    assert "audio_intent=narration" in plan.segments[0].reason
    assert "audio_intent=original" in plan.segments[1].reason


def test_story_document_to_srt_uses_narration_timeline() -> None:
    document = validate_story_script(_valid_payload(), project_id=1, asset_durations={10: 8_000})

    srt = story_document_to_srt(document)

    assert "00:00:00,000 --> 00:00:02,500" in srt
    assert "這不是一般的展示車。" in srt
    assert "00:00:02,500 --> 00:00:04,500" in srt


def test_story_document_to_srt_paginates_long_narration() -> None:
    payload = _valid_payload()
    payload["items"] = [
        {
            **payload["items"][0],
            "source_end_ms": 9_000,
            "narration": "這是一段很長很長的旁白字幕如果整句直接丟給drawtext就會超出畫面被裁切",
        }
    ]
    document = validate_story_script(payload, project_id=1, asset_durations={10: 10_000})

    srt = story_document_to_srt(document)

    assert "00:00:00,000 --> 00:00:04,500" in srt
    assert "00:00:04,500 --> 00:00:09,000" in srt
    assert "這是一段很長很長的旁白" in srt
    text_lines = [
        line for line in srt.splitlines() if line and "-->" not in line and not line.isdigit()
    ]
    assert all(len(line) <= 12 for line in text_lines)


def test_story_document_to_srt_prefers_punctuation_pages() -> None:
    payload = _valid_payload()
    payload["items"] = [
        {
            **payload["items"][0],
            "source_end_ms": 6_000,
            "narration": "先看第一個反差。接著第二個畫面更誇張！最後收在疑問？",
        }
    ]
    document = validate_story_script(payload, project_id=1, asset_durations={10: 10_000})

    srt = story_document_to_srt(document)

    assert "先看第一個反差。" in srt
    assert "接著第二個畫面更誇張！" in srt
    assert "最後收在疑問？" in srt


def test_gather_story_inputs_uses_transcripts_without_gpu_analysis() -> None:
    async def run() -> None:
        engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
            future=True,
        )
        session_maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with session_maker() as session:
            project = Project(
                name="story-test",
                profile_name="universal",
                source_dir="/tmp/story-test",
                status="pending",
            )
            session.add(project)
            await session.flush()
            asset = Asset(
                project_id=project.id,
                file_path="/tmp/story-test/a.mp4",
                duration_ms=6_000,
                sha256="1" * 64,
                status="pending",
                analysis_steps_json={},
            )
            session.add(asset)
            await session.flush()
            session.add(
                AssetTranscript(
                    asset_id=asset.id,
                    language="zh-Hant",
                    model="manual",
                    transcript_text="第一句\n第二句",
                    segments_json=[
                        {"idx": 1, "start_ms": 0, "end_ms": 2000, "text": "第一句"},
                        {"idx": 2, "start_ms": 2200, "end_ms": 4300, "text": "第二句"},
                    ],
                )
            )
            await session.commit()

            bundle = await gather_story_inputs(session, project.id)

        await engine.dispose()
        assert bundle.used_transcripts is True
        assert bundle.used_visual_context is False
        assert len(bundle.segments) == 2
        assert bundle.asset_durations[asset.id] == 6_000

    asyncio.run(run())


def test_gather_story_inputs_uses_uploaded_script_text_when_transcript_missing() -> None:
    async def run() -> None:
        engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
            future=True,
        )
        session_maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with session_maker() as session:
            project = Project(
                name="script-fallback-test",
                profile_name="universal",
                source_dir="/tmp/script-fallback-test",
                status="pending",
            )
            session.add(project)
            await session.flush()
            asset = Asset(
                project_id=project.id,
                file_path="/tmp/script-fallback-test/a.mp4",
                duration_ms=6_000,
                sha256="4" * 64,
                status="pending",
            )
            session.add(asset)
            session.add(
                Script(
                    project_id=project.id,
                    body="1\n00:00:00,000 --> 00:00:02,000\n第一句字幕\n\n2\n00:00:02,500 --> 00:00:04,000\n第二句字幕",
                    source_filename="upload.srt",
                )
            )
            await session.commit()

            bundle = await gather_story_inputs(session, project.id)

        await engine.dispose()
        assert bundle.used_transcripts is False
        assert bundle.used_script_text is True
        assert [seg.text for seg in bundle.segments] == ["第一句字幕", "第二句字幕"]
        assert bundle.segments[0].asset_id == asset.id

    asyncio.run(run())


def test_story_plan_persists_as_draft_segments(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
            future=True,
        )
        session_maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        monkeypatch.setattr(edit_orchestrator, "async_session_maker", session_maker)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with session_maker() as session:
            project = Project(
                name="story-persist-test",
                profile_name="universal",
                source_dir="/tmp/story-persist-test",
                status="pending",
            )
            session.add(project)
            await session.flush()
            asset = Asset(
                project_id=project.id,
                file_path="/tmp/story-persist-test/a.mp4",
                duration_ms=8_000,
                sha256="3" * 64,
                status="analyzed",
            )
            session.add(asset)
            draft = Draft(
                project_id=project.id,
                profile_name="universal",
                version=1,
                status="processing",
            )
            session.add(draft)
            await session.commit()
            await session.refresh(draft)

            payload = _valid_payload()
            for item in payload["items"]:
                item["asset_id"] = asset.id
            document = validate_story_script(
                payload, project_id=project.id, asset_durations={asset.id: 8_000}
            )
            plan = story_document_to_cut_plan(
                document,
                target_aspect_ratio="9:16",
                profile_name="universal",
            )
            handle = edit_orchestrator._DraftHandle(
                draft_id=draft.id,
                profile_name="universal",
                target_aspect="9:16",
                version=1,
            )

            await edit_orchestrator._persist_plan(handle, plan, initial_voice_volume=0.25)

            rows = (
                (
                    await session.execute(
                        select(DraftSegment)
                        .where(DraftSegment.draft_id == draft.id)
                        .order_by(DraftSegment.order)
                    )
                )
                .scalars()
                .all()
            )
            await session.refresh(draft)

        await engine.dispose()
        assert len(rows) == 2
        assert rows[0].asset_id == asset.id
        assert rows[0].on_timeline_start_ms == 0
        assert rows[0].on_timeline_end_ms == 2500
        assert rows[0].voice_volume == 0.0
        assert rows[1].source_kind == "improv"
        assert draft.cut_plan_json["schema_version"] == "story.cut-plan.v1"

    asyncio.run(run())


def test_story_plan_stage_regenerates_stale_story_script(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
            future=True,
        )
        session_maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        monkeypatch.setattr(edit_orchestrator, "async_session_maker", session_maker)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with session_maker() as session:
            project = Project(
                name="stale-story-test",
                profile_name="universal",
                source_dir="/tmp/stale-story-test",
                status="pending",
            )
            session.add(project)
            await session.flush()
            asset = Asset(
                project_id=project.id,
                file_path="/tmp/stale-story-test/a.mp4",
                duration_ms=7_000,
                sha256="5" * 64,
                status="analyzed",
            )
            session.add(asset)
            session.add(
                StoryScript(
                    project_id=project.id,
                    schema_version="story-script.v1",
                    status="ready",
                    script_json={
                        "schema_version": "story-script.v1",
                        "project_id": project.id,
                        "items": [
                            {
                                "order": 1,
                                "asset_id": 999,
                                "source_start_ms": 0,
                                "source_end_ms": 1000,
                                "picture": "stale",
                                "narration": "stale",
                                "audio_intent": "narration",
                            }
                        ],
                    },
                    metadata_json={},
                )
            )
            await session.commit()

            async def fake_generate(_session: AsyncSession, project_id: int, **_kwargs: object):
                return validate_story_script(
                    {
                        "items": [
                            {
                                "order": 1,
                                "asset_id": asset.id,
                                "source_start_ms": 0,
                                "source_end_ms": 1200,
                                "picture": "fresh",
                                "narration": "fresh",
                                "audio_intent": "narration",
                            }
                        ]
                    },
                    project_id=project_id,
                    asset_durations={asset.id: asset.duration_ms},
                )

            monkeypatch.setattr(
                edit_orchestrator.story_script, "generate_story_script", fake_generate
            )
            plan = await edit_orchestrator._story_plan_stage(
                project.id,
                target_aspect="9:16",
                profile_name="universal",
            )

        await engine.dispose()
        assert len(plan.segments) == 1
        assert plan.segments[0].asset_id == asset.id
        assert plan.segments[0].reason.startswith("middle")

    asyncio.run(run())
