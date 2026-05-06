from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from media_processor.api import watchdog
from media_processor.api.routers import queue as queue_router
from media_processor.models import Asset, Base, BgmGenerationJob, Draft, DraftExport, Project


class _FakeJob:
    def __init__(
        self,
        func_name: str,
        *,
        job_id: str = "fake-job",
        args: tuple[object, ...] = (),
        kwargs: dict[str, object] | None = None,
    ) -> None:
        self.id = job_id
        self.func_name = func_name
        self.args = args
        self.kwargs = kwargs or {}


def test_watchdog_reconciles_missing_non_render_jobs(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    session_maker = async_sessionmaker(engine, expire_on_commit=False)

    async def seed() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with session_maker() as session:
            project = Project(
                name="watchdog-test",
                client=None,
                profile_name="universal",
                source_dir="/tmp/watchdog",
                status="ready_for_review",
            )
            session.add(project)
            await session.flush()
            asset = Asset(
                project_id=project.id,
                file_path="/tmp/a.mp4",
                duration_ms=5_000,
                sha256="1" * 64,
                status="analyzing",
                analysis_steps_json={"stt": "running", "scene": "done"},
                point_tracking_status="pending",
            )
            session.add(asset)
            draft = Draft(
                project_id=project.id,
                profile_name="universal",
                version=1,
                status="ready_for_review",
            )
            session.add(draft)
            await session.flush()
            session.add(
                DraftExport(
                    draft_id=draft.id,
                    aspect="9:16",
                    height=1080,
                    status="queued",
                    job_id="missing-export",
                    output_filename="v1-9x16-1080p.mp4",
                )
            )
            session.add(
                BgmGenerationJob(
                    project_id=project.id,
                    status="pending",
                    prompt="calm",
                    rq_job_id="missing-bgm",
                )
            )
            await session.commit()

    async def read_rows() -> tuple[Asset, DraftExport, BgmGenerationJob]:
        async with session_maker() as session:
            asset = (await session.execute(select(Asset))).scalar_one()
            export = (await session.execute(select(DraftExport))).scalar_one()
            bgm = (await session.execute(select(BgmGenerationJob))).scalar_one()
            return asset, export, bgm

    asyncio.run(seed())
    monkeypatch.setattr(watchdog, "async_session_maker", session_maker)
    monkeypatch.setattr(watchdog, "has_draft_export_job", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(watchdog, "has_bgm_generation_job", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(watchdog, "has_point_tracking_job", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(watchdog, "has_asset_analysis_job", lambda *_args, **_kwargs: False)

    try:
        asyncio.run(watchdog._sweep_once())
        asset, export, bgm = asyncio.run(read_rows())
    finally:
        asyncio.run(engine.dispose())

    assert export.status == "failed"
    assert "vanished" in (export.error or "")
    assert bgm.status == "failed:orphaned"
    assert "vanished" in (bgm.error or "")
    assert asset.point_tracking_status == "failed"
    assert "vanished" in (asset.point_tracking_error or "")
    assert asset.status == "analysis_failed"
    assert asset.analysis_steps_json["stt"] == "failed:watchdog-orphaned"
    assert asset.analysis_steps_json["scene"] == "done"


def test_watchdog_reconciles_orphaned_analysis_steps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    session_maker = async_sessionmaker(engine, expire_on_commit=False)

    async def seed() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with session_maker() as session:
            project = Project(
                name="analysis-step-watchdog",
                client=None,
                profile_name="universal",
                source_dir="/tmp/analysis-step",
                status="ready_for_review",
            )
            session.add(project)
            await session.flush()
            session.add(
                Asset(
                    project_id=project.id,
                    file_path="/tmp/a.mp4",
                    duration_ms=5_000,
                    sha256="7" * 64,
                    status="analyzing",
                    analysis_steps_json={"stt": "running", "tracking": "running"},
                )
            )
            await session.commit()

    async def read_asset() -> Asset:
        async with session_maker() as session:
            return (await session.execute(select(Asset))).scalar_one()

    def fake_step_exists(_asset_id: int, step: str) -> bool:
        return step == "tracking"

    asyncio.run(seed())
    monkeypatch.setattr(watchdog, "async_session_maker", session_maker)
    monkeypatch.setattr(watchdog, "has_asset_analysis_job", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(watchdog, "has_asset_analysis_step_job", fake_step_exists)

    try:
        asyncio.run(watchdog._sweep_once())
        asset = asyncio.run(read_asset())
    finally:
        asyncio.run(engine.dispose())

    assert asset.status == "analyzing"
    assert asset.analysis_steps_json["stt"] == "failed:watchdog-orphaned"
    assert asset.analysis_steps_json["tracking"] == "running"


def test_watchdog_commits_retry_state_before_enqueue(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "watchdog.db"
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_path.as_posix()}",
        future=True,
    )
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    observed: list[tuple[str, int]] = []

    async def seed() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with session_maker() as session:
            project = Project(
                name="watchdog-commit-before-enqueue",
                client=None,
                profile_name="universal",
                source_dir="/tmp/watchdog-commit",
                status="ready_for_review",
            )
            session.add(project)
            await session.flush()
            session.add(
                Draft(
                    project_id=project.id,
                    profile_name="universal",
                    version=1,
                    status="processing",
                    render_retry_count=0,
                )
            )
            await session.commit()

    async def read_retry_state() -> tuple[str, int]:
        async with session_maker() as session:
            draft = (await session.execute(select(Draft))).scalar_one()
            return draft.status, draft.render_retry_count

    def fake_enqueue(*_args: object, **_kwargs: object) -> None:
        observed.append(asyncio.run(read_retry_state()))

    asyncio.run(seed())
    monkeypatch.setattr(watchdog, "async_session_maker", session_maker)
    monkeypatch.setattr(watchdog, "has_draft_render_job", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(watchdog, "enqueue_project_edit", fake_enqueue)

    try:
        asyncio.run(watchdog._sweep_once())
    finally:
        asyncio.run(engine.dispose())

    assert observed == [("pending", 1)]


def test_queue_cancel_syncs_durable_state(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(queue_router, "has_draft_render_job", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(queue_router, "has_draft_export_job", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(queue_router, "has_bgm_generation_job", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(queue_router, "has_point_tracking_job", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(queue_router, "has_asset_analysis_job", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        queue_router, "has_asset_analysis_step_job", lambda *_args, **_kwargs: False
    )

    async def seed_and_cancel() -> tuple[Draft, DraftExport, BgmGenerationJob, Asset]:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with session_maker() as session:
            project = Project(
                name="cancel-test",
                client=None,
                profile_name="universal",
                source_dir="/tmp/cancel",
                status="ready_for_review",
            )
            session.add(project)
            await session.flush()
            asset = Asset(
                project_id=project.id,
                file_path="/tmp/a.mp4",
                duration_ms=5_000,
                sha256="2" * 64,
                status="analyzing",
                analysis_steps_json={"stt": "running"},
                tracked_object_index=-4,
                point_tracking_status="pending",
                point_tracking_origin={"norm_x": 0.0, "norm_y": 0.0, "frame_ms": 0},
            )
            draft = Draft(
                project_id=project.id,
                profile_name="universal",
                version=1,
                status="pending",
            )
            session.add_all([asset, draft])
            await session.flush()
            export = DraftExport(
                draft_id=draft.id,
                aspect="9:16",
                height=1080,
                status="queued",
                job_id="queued-export",
                output_filename="v1-9x16-1080p.mp4",
            )
            bgm = BgmGenerationJob(
                project_id=project.id,
                status="pending",
                prompt="calm",
                rq_job_id="queued-bgm",
            )
            session.add_all([export, bgm])
            await session.commit()

            await queue_router._sync_cancelled_job(
                session,
                _FakeJob(
                    "media_processor.workers.edit_jobs.render_draft",
                    args=(project.id,),
                    kwargs={"draft_id": draft.id},
                ),
            )
            await queue_router._sync_cancelled_job(
                session,
                _FakeJob(
                    "media_processor.workers.edit_jobs.export_draft",
                    args=(draft.id,),
                    kwargs={"export_id": export.id},
                ),
            )
            await queue_router._sync_cancelled_job(
                session,
                _FakeJob("media_processor.workers.bgm_jobs.generate_bgm", args=(bgm.id,)),
            )
            await queue_router._sync_cancelled_job(
                session,
                _FakeJob(
                    "media_processor.workers.point_tracking_jobs.track_point_job",
                    args=(asset.id,),
                    kwargs={"init_norm_x": 0.0, "init_norm_y": 0.0, "init_t_ms": 0},
                ),
            )
            await queue_router._sync_cancelled_job(
                session,
                _FakeJob("media_processor.workers.analysis_jobs.analyze_asset", args=(asset.id,)),
            )

        async with session_maker() as session:
            return (
                (await session.execute(select(Draft))).scalar_one(),
                (await session.execute(select(DraftExport))).scalar_one(),
                (await session.execute(select(BgmGenerationJob))).scalar_one(),
                (await session.execute(select(Asset))).scalar_one(),
            )

    try:
        draft, export, bgm, asset = asyncio.run(seed_and_cancel())
    finally:
        asyncio.run(engine.dispose())

    assert draft.status == "failed"
    assert draft.prompt_feedback == "已被使用者取消"
    assert export.status == "failed"
    assert export.error == "cancelled by user"
    assert bgm.status == "failed:cancelled"
    assert bgm.error == "cancelled by user"
    assert asset.point_tracking_status == "failed"
    assert asset.point_tracking_error == "cancelled by user"
    assert asset.status == "analysis_failed"
    assert asset.analysis_steps_json["stt"] == "failed:cancelled"


def test_queue_cancel_preserves_state_when_matching_job_still_exists(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    session_maker = async_sessionmaker(engine, expire_on_commit=False)

    def active_duplicate(*_args: object, **kwargs: object) -> bool:
        assert kwargs["exclude_job_id"] == "cancelled-stale"
        return True

    monkeypatch.setattr(queue_router, "has_draft_render_job", active_duplicate)
    monkeypatch.setattr(queue_router, "has_draft_export_job", active_duplicate)
    monkeypatch.setattr(queue_router, "has_bgm_generation_job", active_duplicate)
    monkeypatch.setattr(queue_router, "has_point_tracking_job", active_duplicate)
    monkeypatch.setattr(queue_router, "has_asset_analysis_job", active_duplicate)
    monkeypatch.setattr(queue_router, "has_asset_analysis_step_job", active_duplicate)

    async def seed_and_cancel() -> tuple[Draft, DraftExport, BgmGenerationJob, Asset]:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with session_maker() as session:
            project = Project(
                name="duplicate-cancel-test",
                client=None,
                profile_name="universal",
                source_dir="/tmp/duplicate-cancel",
                status="ready_for_review",
            )
            session.add(project)
            await session.flush()
            asset = Asset(
                project_id=project.id,
                file_path="/tmp/a.mp4",
                duration_ms=5_000,
                sha256="8" * 64,
                status="analyzing",
                analysis_steps_json={"stt": "running"},
                tracked_object_index=-4,
                point_tracking_status="pending",
                point_tracking_origin={"norm_x": 0.0, "norm_y": 0.0, "frame_ms": 0},
            )
            draft = Draft(
                project_id=project.id,
                profile_name="universal",
                version=1,
                status="pending",
            )
            session.add_all([asset, draft])
            await session.flush()
            export = DraftExport(
                draft_id=draft.id,
                aspect="9:16",
                height=1080,
                status="queued",
                job_id="queued-export",
                output_filename="v1-9x16-1080p.mp4",
            )
            bgm = BgmGenerationJob(
                project_id=project.id,
                status="pending",
                prompt="calm",
                rq_job_id="queued-bgm",
            )
            session.add_all([export, bgm])
            await session.commit()

            await queue_router._sync_cancelled_job(
                session,
                _FakeJob(
                    "media_processor.workers.edit_jobs.render_draft",
                    job_id="cancelled-stale",
                    args=(project.id,),
                    kwargs={"draft_id": draft.id},
                ),
            )
            await queue_router._sync_cancelled_job(
                session,
                _FakeJob(
                    "media_processor.workers.edit_jobs.export_draft",
                    job_id="cancelled-stale",
                    args=(draft.id,),
                    kwargs={"export_id": export.id},
                ),
            )
            await queue_router._sync_cancelled_job(
                session,
                _FakeJob(
                    "media_processor.workers.bgm_jobs.generate_bgm",
                    job_id="cancelled-stale",
                    args=(bgm.id,),
                ),
            )
            await queue_router._sync_cancelled_job(
                session,
                _FakeJob(
                    "media_processor.workers.point_tracking_jobs.track_point_job",
                    job_id="cancelled-stale",
                    args=(asset.id,),
                    kwargs={"init_norm_x": 0.0, "init_norm_y": 0.0, "init_t_ms": 0},
                ),
            )
            await queue_router._sync_cancelled_job(
                session,
                _FakeJob(
                    "media_processor.workers.analysis_jobs.analyze_asset",
                    job_id="cancelled-stale",
                    args=(asset.id,),
                    kwargs={"steps": ["stt"]},
                ),
            )

        async with session_maker() as session:
            return (
                (await session.execute(select(Draft))).scalar_one(),
                (await session.execute(select(DraftExport))).scalar_one(),
                (await session.execute(select(BgmGenerationJob))).scalar_one(),
                (await session.execute(select(Asset))).scalar_one(),
            )

    try:
        draft, export, bgm, asset = asyncio.run(seed_and_cancel())
    finally:
        asyncio.run(engine.dispose())

    assert draft.status == "pending"
    assert draft.prompt_feedback is None
    assert export.status == "queued"
    assert export.error is None
    assert export.completed_at is None
    assert bgm.status == "pending"
    assert bgm.error is None
    assert bgm.completed_at is None
    assert asset.point_tracking_status == "pending"
    assert asset.point_tracking_error is None
    assert asset.status == "analyzing"
    assert asset.analysis_steps_json["stt"] == "running"


def test_queue_cancel_full_analysis_only_fails_uncovered_steps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(queue_router, "has_asset_analysis_job", lambda *_args, **_kwargs: True)

    def active_step(_asset_id: int, step: str, **kwargs: object) -> bool:
        assert kwargs["exclude_job_id"] == "cancelled-full-analysis"
        return step == "tracking"

    monkeypatch.setattr(queue_router, "has_asset_analysis_step_job", active_step)

    async def run_case() -> Asset:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with session_maker() as session:
            project = Project(
                name="analysis-full-cancel",
                client=None,
                profile_name="universal",
                source_dir="/tmp/analysis-full-cancel",
                status="ready_for_review",
            )
            session.add(project)
            await session.flush()
            asset = Asset(
                project_id=project.id,
                file_path="/tmp/a.mp4",
                duration_ms=5_000,
                sha256="9" * 64,
                status="analyzing",
                analysis_steps_json={
                    "stt": "running",
                    "scene": "running",
                    "tracking": "running",
                },
            )
            session.add(asset)
            await session.commit()

            await queue_router._sync_cancelled_job(
                session,
                _FakeJob(
                    "media_processor.workers.analysis_jobs.analyze_asset",
                    job_id="cancelled-full-analysis",
                    args=(asset.id,),
                ),
            )

        async with session_maker() as session:
            return (await session.execute(select(Asset))).scalar_one()

    try:
        asset = asyncio.run(run_case())
    finally:
        asyncio.run(engine.dispose())

    assert asset.status == "analyzing"
    assert asset.analysis_steps_json["stt"] == "failed:cancelled"
    assert asset.analysis_steps_json["scene"] == "failed:cancelled"
    assert asset.analysis_steps_json["tracking"] == "running"


def test_queue_cancel_does_not_fail_superseded_point_tracking() -> None:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    session_maker = async_sessionmaker(engine, expire_on_commit=False)

    async def run_case() -> Asset:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with session_maker() as session:
            project = Project(
                name="superseded-cancel",
                client=None,
                profile_name="universal",
                source_dir="/tmp/superseded-cancel",
                status="ready_for_review",
            )
            session.add(project)
            await session.flush()
            asset = Asset(
                project_id=project.id,
                file_path="/tmp/a.mp4",
                duration_ms=5_000,
                sha256="5" * 64,
                status="analyzed",
                tracked_object_index=-4,
                point_tracking_status="pending",
                point_tracking_origin={"norm_x": 0.8, "norm_y": 0.8, "frame_ms": 1000},
            )
            session.add(asset)
            await session.commit()

            await queue_router._sync_cancelled_job(
                session,
                _FakeJob(
                    "media_processor.workers.point_tracking_jobs.track_point_job",
                    args=(asset.id,),
                    kwargs={"init_norm_x": 0.2, "init_norm_y": 0.2, "init_t_ms": 0},
                ),
            )

        async with session_maker() as session:
            return (await session.execute(select(Asset))).scalar_one()

    try:
        asset = asyncio.run(run_case())
    finally:
        asyncio.run(engine.dispose())

    assert asset.point_tracking_status == "pending"
    assert asset.point_tracking_error is None


def test_queue_cancel_only_fails_requested_analysis_steps(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    session_maker = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(queue_router, "has_asset_analysis_job", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        queue_router, "has_asset_analysis_step_job", lambda *_args, **_kwargs: False
    )

    async def run_case() -> Asset:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with session_maker() as session:
            project = Project(
                name="analysis-subset-cancel",
                client=None,
                profile_name="universal",
                source_dir="/tmp/analysis-subset",
                status="ready_for_review",
            )
            session.add(project)
            await session.flush()
            asset = Asset(
                project_id=project.id,
                file_path="/tmp/a.mp4",
                duration_ms=5_000,
                sha256="6" * 64,
                status="analyzing",
                analysis_steps_json={"stt": "running", "scene": "running"},
            )
            session.add(asset)
            await session.commit()

            await queue_router._sync_cancelled_job(
                session,
                _FakeJob(
                    "media_processor.workers.analysis_jobs.analyze_asset",
                    args=(asset.id,),
                    kwargs={"steps": ["stt"]},
                ),
            )

        async with session_maker() as session:
            return (await session.execute(select(Asset))).scalar_one()

    try:
        asset = asyncio.run(run_case())
    finally:
        asyncio.run(engine.dispose())

    assert asset.status == "analyzing"
    assert asset.analysis_steps_json["stt"] == "failed:cancelled"
    assert asset.analysis_steps_json["scene"] == "running"
