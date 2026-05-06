from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from media_processor.core import db as core_db
from media_processor.models import (
    Asset,
    Base,
    BgmGenerationJob,
    Draft,
    DraftExport,
    Project,
    SubtitleCueRow,
)
from media_processor.services import edit_orchestrator, music_gen, point_tracking_runner
from media_processor.workers import bgm_jobs, edit_jobs


def _session_maker() -> tuple[object, async_sessionmaker]:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    return engine, async_sessionmaker(engine, expire_on_commit=False)


def test_stale_render_job_cannot_adopt_ready_draft(monkeypatch: pytest.MonkeyPatch) -> None:
    engine, session_maker = _session_maker()

    async def seed() -> tuple[Project, int]:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with session_maker() as session:
            project = Project(
                name="stale-render",
                client=None,
                profile_name="universal",
                source_dir="/tmp/stale",
                status="ready_for_review",
            )
            session.add(project)
            await session.flush()
            draft = Draft(
                project_id=project.id,
                profile_name="universal",
                version=1,
                status="ready_for_review",
            )
            session.add(draft)
            await session.commit()
            return project, draft.id

    async def read_draft(draft_id: int) -> Draft:
        async with session_maker() as session:
            return (await session.execute(select(Draft).where(Draft.id == draft_id))).scalar_one()

    monkeypatch.setattr(edit_orchestrator, "async_session_maker", session_maker)
    try:
        project, draft_id = asyncio.run(seed())
        with pytest.raises(RuntimeError, match="stale render job ignored"):
            asyncio.run(edit_orchestrator._adopt_draft_row(project, draft_id))
        draft = asyncio.run(read_draft(draft_id))
    finally:
        asyncio.run(engine.dispose())

    assert draft.status == "ready_for_review"


def test_stale_render_job_cannot_replace_terminal_subtitle_cues(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, session_maker = _session_maker()

    async def seed() -> int:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with session_maker() as session:
            project = Project(
                name="terminal-subtitles",
                client=None,
                profile_name="universal",
                source_dir="/tmp/terminal-subtitles",
                status="ready_for_review",
            )
            session.add(project)
            await session.flush()
            draft = Draft(
                project_id=project.id,
                profile_name="universal",
                version=1,
                status="failed",
            )
            session.add(draft)
            await session.flush()
            session.add(
                SubtitleCueRow(
                    draft_id=draft.id,
                    idx=1,
                    start_ms=0,
                    end_ms=1000,
                    text="keep this user edit",
                )
            )
            await session.commit()
            return draft.id

    async def read_cues(draft_id: int) -> list[SubtitleCueRow]:
        async with session_maker() as session:
            return (
                (
                    await session.execute(
                        select(SubtitleCueRow)
                        .where(SubtitleCueRow.draft_id == draft_id)
                        .order_by(SubtitleCueRow.idx)
                    )
                )
                .scalars()
                .all()
            )

    monkeypatch.setattr(edit_orchestrator, "async_session_maker", session_maker)
    try:
        draft_id = asyncio.run(seed())
        asyncio.run(
            edit_orchestrator._persist_subtitle_cues(
                draft_id,
                "1\n00:00:00,000 --> 00:00:01,000\nstale worker output\n",
            )
        )
        cues = asyncio.run(read_cues(draft_id))
    finally:
        asyncio.run(engine.dispose())

    assert [cue.text for cue in cues] == ["keep this user edit"]


def test_export_worker_rejects_mismatched_artifact(monkeypatch: pytest.MonkeyPatch) -> None:
    engine, session_maker = _session_maker()

    async def seed() -> int:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with session_maker() as session:
            project = Project(
                name="export-mismatch",
                client=None,
                profile_name="universal",
                source_dir="/tmp/export",
                status="ready_for_review",
            )
            session.add(project)
            await session.flush()
            draft = Draft(
                project_id=project.id,
                profile_name="universal",
                version=1,
                status="ready_for_review",
            )
            session.add(draft)
            await session.flush()
            artifact = DraftExport(
                draft_id=draft.id,
                aspect="1:1",
                height=1080,
                status="queued",
                output_filename="v1-1x1-1080p.mp4",
            )
            session.add(artifact)
            await session.commit()
            return artifact.id

    async def read_export(export_id: int) -> DraftExport:
        async with session_maker() as session:
            return (
                await session.execute(select(DraftExport).where(DraftExport.id == export_id))
            ).scalar_one()

    monkeypatch.setattr(core_db, "async_session_maker", session_maker)
    try:
        export_id = asyncio.run(seed())
        result = edit_jobs.export_draft(1, export_id=export_id, aspect="9:16", height=1080)
        artifact = asyncio.run(read_export(export_id))
    finally:
        asyncio.run(engine.dispose())

    assert result["status"] == "skipped"
    assert artifact.status == "failed"
    assert artifact.error == "export intent mismatch; job ignored"


def test_point_tracking_worker_skips_superseded_intent(monkeypatch: pytest.MonkeyPatch) -> None:
    engine, session_maker = _session_maker()

    async def seed() -> int:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with session_maker() as session:
            project = Project(
                name="point-superseded",
                client=None,
                profile_name="universal",
                source_dir="/tmp/point",
                status="ready_for_review",
            )
            session.add(project)
            await session.flush()
            asset = Asset(
                project_id=project.id,
                file_path="/tmp/a.mp4",
                duration_ms=5_000,
                sha256="3" * 64,
                status="analyzed",
                tracked_object_index=-4,
                point_tracking_status="pending",
                point_tracking_origin={"norm_x": 0.8, "norm_y": 0.8, "frame_ms": 1000},
            )
            session.add(asset)
            await session.commit()
            return asset.id

    async def read_asset(asset_id: int) -> Asset:
        async with session_maker() as session:
            return (await session.execute(select(Asset).where(Asset.id == asset_id))).scalar_one()

    def fail_if_called(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("stale point-tracking job should not run LK")

    monkeypatch.setattr(point_tracking_runner, "async_session_maker", session_maker)
    monkeypatch.setattr(point_tracking_runner.point_tracking_svc, "track_point", fail_if_called)
    try:
        asset_id = asyncio.run(seed())
        result = asyncio.run(
            point_tracking_runner.run_point_tracking(
                asset_id,
                init_norm_x=0.2,
                init_norm_y=0.2,
                init_t_ms=0,
            )
        )
        asset = asyncio.run(read_asset(asset_id))
    finally:
        asyncio.run(engine.dispose())

    assert result["status"] == "skipped"
    assert asset.point_tracking_status == "pending"
    assert asset.point_tracking_json is None
    assert asset.point_tracking_origin == {"norm_x": 0.8, "norm_y": 0.8, "frame_ms": 1000}


def test_point_tracking_worker_skips_when_user_changed_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, session_maker = _session_maker()

    async def seed() -> int:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with session_maker() as session:
            project = Project(
                name="point-mode-switch",
                client=None,
                profile_name="universal",
                source_dir="/tmp/point-mode",
                status="ready_for_review",
            )
            session.add(project)
            await session.flush()
            asset = Asset(
                project_id=project.id,
                file_path="/tmp/a.mp4",
                duration_ms=5_000,
                sha256="4" * 64,
                status="analyzed",
                tracked_object_index=-2,
                point_tracking_status="pending",
                point_tracking_origin={"norm_x": 0.2, "norm_y": 0.2, "frame_ms": 0},
            )
            session.add(asset)
            await session.commit()
            return asset.id

    def fail_if_called(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("stale point-tracking job should not run after mode switch")

    monkeypatch.setattr(point_tracking_runner, "async_session_maker", session_maker)
    monkeypatch.setattr(point_tracking_runner.point_tracking_svc, "track_point", fail_if_called)
    try:
        asset_id = asyncio.run(seed())
        result = asyncio.run(
            point_tracking_runner.run_point_tracking(
                asset_id,
                init_norm_x=0.2,
                init_norm_y=0.2,
                init_t_ms=0,
            )
        )
    finally:
        asyncio.run(engine.dispose())

    assert result["status"] == "skipped"


def test_bgm_late_failure_does_not_overwrite_cancelled_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, session_maker = _session_maker()

    async def seed() -> int:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with session_maker() as session:
            project = Project(
                name="bgm-cancelled",
                client=None,
                profile_name="universal",
                source_dir="/tmp/bgm",
                status="ready_for_review",
            )
            session.add(project)
            await session.flush()
            row = BgmGenerationJob(
                project_id=project.id,
                status="pending",
                prompt="calm",
                rq_job_id="running-bgm",
            )
            session.add(row)
            await session.commit()
            return row.id

    async def cancel_after_claim(job_id: int) -> None:
        async with session_maker() as session:
            row = await session.get(BgmGenerationJob, job_id)
            assert row is not None
            row.status = "failed:cancelled"
            row.error = "cancelled by user"
            await session.commit()

    async def read_job(job_id: int) -> BgmGenerationJob:
        async with session_maker() as session:
            row = await session.get(BgmGenerationJob, job_id)
            assert row is not None
            return row

    def fail_generate(*_args: object, **_kwargs: object) -> object:
        asyncio.run(cancel_after_claim(job_id))
        raise RuntimeError("late model failure")

    monkeypatch.setattr(core_db, "async_session_maker", session_maker)
    monkeypatch.setattr(music_gen, "generate", fail_generate)
    try:
        job_id = asyncio.run(seed())
        result = bgm_jobs.generate_bgm(job_id)
        row = asyncio.run(read_job(job_id))
    finally:
        asyncio.run(engine.dispose())

    assert result["status"] == "failed:RuntimeError"
    assert row.status == "failed:cancelled"
    assert row.error == "cancelled by user"
