"""Tests for POST /projects/{id}/fork."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload
from sqlalchemy.pool import StaticPool

from media_processor.api.config import settings
from media_processor.api.deps import get_session
from media_processor.api.main import app as production_app
from media_processor.models import (
    Asset,
    AssetSegment,
    AssetTag,
    AssetTranscript,
    Base,
    Draft,
    DraftSegment,
    Project,
    Script,
    ScriptCoverage,
)


def _make_engine_and_session() -> tuple[Any, async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _seed(session_maker: async_sessionmaker[AsyncSession], paths: dict[str, Path]) -> None:
    async with session_maker() as s:
        p = Project(
            name="launch-cut",
            client="CarsMeet",
            profile_name="carsmeet-luxury",
            source_dir="",
            status="ready_for_review",
            target_aspect_ratio="16:9",
            bgm_fade_out_sec=0.0,
            watermark_position="top-left",
            watermark_scale=0.25,
            watermark_opacity=0.5,
            subtitle_color="#ffeeaa",
            subject_class="car",
            crop_region_json={"x_norm": 0.25, "y_norm": 0.75},
            smart_camera_enabled=True,
        )
        s.add(p)
        await s.flush()
        p.source_dir = str(paths["assets"] / str(p.id))

        source_dir = paths["assets"] / str(p.id)
        source_dir.mkdir(parents=True)
        raw_path = source_dir / "source.mp4"
        raw_path.write_bytes(b"raw-video")
        stabilized_dir = source_dir / "_stabilized"
        stabilized_dir.mkdir()
        stabilized_path = stabilized_dir / "1_source.stab.mp4"
        stabilized_path.write_bytes(b"stabilized-video")

        bgm_path = paths["bgm"] / f"{p.id}.mp3"
        bgm_path.write_bytes(b"bgm")
        watermark_path = paths["watermarks"] / f"{p.id}.png"
        watermark_path.write_bytes(b"png")
        p.bgm_path = str(bgm_path)
        p.watermark_path = str(watermark_path)

        script = Script(project_id=p.id, body="Script body", source_filename="brief.txt")
        s.add(script)
        await s.flush()

        asset = Asset(
            project_id=p.id,
            file_path=str(raw_path),
            stabilized_path=str(stabilized_path),
            stabilization_status="done",
            active_asset_variant="stabilized",
            duration_ms=12_000,
            resolution="3840x2160",
            fps=59.94,
            codec="h264",
            sha256="a" * 64,
            status="analyzed",
            analysis_steps_json={"scene": "done", "tracking": "done"},
            tracking_json={"tracks": [{"object_index": 0, "cls_name": "car"}]},
            tracked_object_index=0,
            custom_roi_json={"frames": [{"t_ms": 0, "x": 1, "y": 2, "w": 3, "h": 4}]},
            point_tracking_json={"frames": [{"t_ms": 0, "x": 10, "y": 20}]},
            point_tracking_origin={"norm_x": 0.1, "norm_y": 0.2},
            point_tracking_status="done",
            subtitle_secondary_lang="en",
            subtitle_secondary_segments_json=[{"idx": 1, "text": "hello"}],
        )
        s.add(asset)
        await s.flush()

        thumb_dir = paths["thumbnails"] / str(asset.id)
        thumb_dir.mkdir(parents=True)
        (thumb_dir / "frame_0.jpg").write_bytes(b"thumb")

        tag = AssetTag(
            asset_id=asset.id,
            tag_type="scene",
            tag_name="showroom",
            confidence=0.9,
            source_model="gemini",
            time_ranges_ms=[[0, 1000]],
        )
        segment = AssetSegment(
            asset_id=asset.id,
            start_ms=0,
            end_ms=2000,
            score=0.8,
            used_in_draft=True,
        )
        transcript = AssetTranscript(
            asset_id=asset.id,
            language="zh-Hant",
            model="whisper",
            transcript_text="hello transcript",
            segments_json=[{"idx": 1, "start_ms": 0, "end_ms": 1000, "text": "hello"}],
            edited=True,
        )
        coverage = ScriptCoverage(
            asset_id=asset.id,
            script_id=script.id,
            model="gemini",
            scripted_segment_count=1,
            total_segment_count=2,
            coverage_ratio_by_count=0.5,
            coverage_ratio_by_duration_ms=0.25,
            match_details_json=[{"segment": 1}],
        )
        s.add_all([tag, segment, transcript, coverage])
        await s.flush()

        draft = Draft(
            project_id=p.id,
            profile_name=p.profile_name,
            version=1,
            status="ready_for_review",
            mp4_preview_path="/tmp/rendered.mp4",
        )
        s.add(draft)
        await s.flush()
        s.add(
            DraftSegment(
                draft_id=draft.id,
                order=0,
                asset_id=asset.id,
                asset_segment_id=segment.id,
                asset_start_ms=0,
                asset_end_ms=2000,
                on_timeline_start_ms=0,
                on_timeline_end_ms=2000,
            )
        )
        await s.commit()


@pytest.fixture()
def app_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[FastAPI, async_sessionmaker[AsyncSession], dict[str, Path]]]:
    paths = {
        "assets": tmp_path / "assets",
        "bgm": tmp_path / "bgm",
        "watermarks": tmp_path / "watermarks",
        "thumbnails": tmp_path / "thumbnails",
    }
    for path in paths.values():
        path.mkdir(parents=True)

    monkeypatch.setattr(settings, "assets_dir", str(paths["assets"]))
    monkeypatch.setattr(settings, "bgm_dir", str(paths["bgm"]))
    monkeypatch.setattr(settings, "watermark_dir", str(paths["watermarks"]))
    monkeypatch.setattr(settings, "thumbnails_dir", str(paths["thumbnails"]))

    engine, session_maker = _make_engine_and_session()

    async def init() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await _seed(session_maker, paths)

    asyncio.run(init())

    async def override_session() -> AsyncIterator[AsyncSession]:
        async with session_maker() as s:
            yield s

    production_app.dependency_overrides[get_session] = override_session
    try:
        yield production_app, session_maker, paths
    finally:
        production_app.dependency_overrides.clear()
        asyncio.run(engine.dispose())


async def _load_project(
    session_maker: async_sessionmaker[AsyncSession],
    project_id: int,
) -> Project:
    async with session_maker() as s:
        stmt = (
            select(Project)
            .where(Project.id == project_id)
            .options(
                selectinload(Project.script),
                selectinload(Project.assets).selectinload(Asset.tags),
                selectinload(Project.assets).selectinload(Asset.segments),
                selectinload(Project.assets).selectinload(Asset.transcript),
                selectinload(Project.assets).selectinload(Asset.coverage),
            )
        )
        return (await s.execute(stmt)).scalar_one()


async def _project_count(session_maker: async_sessionmaker[AsyncSession]) -> int:
    async with session_maker() as s:
        return int(await s.scalar(select(func.count(Project.id))) or 0)


def test_project_fork_copies_rows_files_and_excludes_drafts(
    app_context: tuple[FastAPI, async_sessionmaker[AsyncSession], dict[str, Path]],
) -> None:
    app, session_maker, paths = app_context
    client = TestClient(app)

    resp = client.post("/projects/1/fork")

    assert resp.status_code == 201, resp.text
    body = resp.json()
    fork_id = int(body["id"])
    assert fork_id != 1
    assert body["name"] == "launch-cut (copy)"
    assert body["asset_count"] == 1
    assert body["draft_count"] == 0
    assert body["bgm_fade_out_sec"] == 0.0
    assert body["smart_camera_enabled"] is True

    drafts_resp = client.get(f"/projects/{fork_id}/drafts")
    assert drafts_resp.status_code == 200
    assert drafts_resp.json() == []

    source = asyncio.run(_load_project(session_maker, 1))
    fork = asyncio.run(_load_project(session_maker, fork_id))
    source_asset = source.assets[0]
    fork_asset = fork.assets[0]

    assert fork.source_dir == str(paths["assets"] / str(fork_id))
    assert fork.bgm_path != source.bgm_path
    assert Path(fork.bgm_path or "").read_bytes() == b"bgm"
    assert fork.watermark_path != source.watermark_path
    assert Path(fork.watermark_path or "").read_bytes() == b"png"
    assert fork.script is not None
    assert fork.script.body == "Script body"

    assert fork_asset.id != source_asset.id
    assert fork_asset.project_id == fork_id
    assert fork_asset.file_path != source_asset.file_path
    assert Path(fork_asset.file_path).read_bytes() == b"raw-video"
    assert fork_asset.stabilized_path != source_asset.stabilized_path
    assert Path(fork_asset.stabilized_path or "").read_bytes() == b"stabilized-video"
    assert fork_asset.stabilization_status == "done"
    assert fork_asset.active_asset_variant == "stabilized"
    assert fork_asset.analysis_steps_json == {"scene": "done", "tracking": "done"}
    assert fork_asset.tracking_json == {"tracks": [{"object_index": 0, "cls_name": "car"}]}
    assert len(fork_asset.tags) == 1
    assert fork_asset.tags[0].time_ranges_ms == [[0, 1000]]
    assert len(fork_asset.segments) == 1
    assert fork_asset.segments[0].used_in_draft is False
    assert fork_asset.transcript is not None
    assert fork_asset.transcript.transcript_text == "hello transcript"
    assert fork_asset.coverage is not None
    assert fork_asset.coverage.script_id == fork.script.id
    assert (paths["thumbnails"] / str(fork_asset.id) / "frame_0.jpg").read_bytes() == b"thumb"


def test_project_fork_returns_404_for_missing_source(
    app_context: tuple[FastAPI, async_sessionmaker[AsyncSession], dict[str, Path]],
) -> None:
    app, _session_maker, _paths = app_context
    client = TestClient(app)

    resp = client.post("/projects/999/fork")

    assert resp.status_code == 404


def test_project_fork_rolls_back_and_cleans_copied_files_when_source_media_missing(
    app_context: tuple[FastAPI, async_sessionmaker[AsyncSession], dict[str, Path]],
) -> None:
    app, session_maker, paths = app_context
    (paths["assets"] / "1" / "source.mp4").unlink()
    client = TestClient(app)

    resp = client.post("/projects/1/fork")

    assert resp.status_code == 409
    assert "missing" in resp.json()["detail"]
    assert asyncio.run(_project_count(session_maker)) == 1
    assert not (paths["bgm"] / "2.mp3").exists()
    assert not (paths["watermarks"] / "2.png").exists()
