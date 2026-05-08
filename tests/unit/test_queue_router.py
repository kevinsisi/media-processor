from __future__ import annotations

import asyncio
from datetime import datetime

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from media_processor.api.routers import queue as queue_router
from media_processor.models import Base, BgmGenerationJob, Project


class _FakeJob:
    def __init__(self, func_name: str, *, args: tuple[object, ...] = ()) -> None:
        self.id = "rq-bgm-12345678"
        self.func_name = func_name
        self.args = args
        self.kwargs: dict[str, object] = {}
        self.enqueued_at: datetime | None = None
        self.started_at: datetime | None = None


def test_queue_status_resolves_bgm_job_project_context() -> None:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    session_maker = async_sessionmaker(engine, expire_on_commit=False)

    async def run() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with session_maker() as session:
            project = Project(
                name="BGM context project",
                client=None,
                profile_name="universal",
                source_dir="/tmp/bgm-context",
                status="processing",
            )
            session.add(project)
            await session.flush()
            bgm = BgmGenerationJob(
                project_id=project.id,
                prompt="upbeat corporate pop",
                status="running",
            )
            session.add(bgm)
            await session.flush()

            item = queue_router._job_to_item(
                _FakeJob(
                    "media_processor.workers.bgm_jobs.generate_bgm",
                    args=(bgm.id,),
                ),
                "bgm",
                "queued",
                position=0,
            )
            await queue_router._resolve_project_links(session, [item])

            assert item.bgm_job_id == bgm.id
            assert item.project_id == project.id
            assert item.project_name == "BGM context project"

    try:
        asyncio.run(run())
    finally:
        asyncio.run(engine.dispose())
