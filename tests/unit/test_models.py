"""Schema-level tests for the M2 ORM models.

We exercise the migration end-to-end against an on-disk SQLite database so that
both the model declarations and the hand-written `0001_init` migration stay in
lockstep.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, event, inspect
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from media_processor.models import (
    Asset,
    AssetSegment,
    AssetTag,
    Draft,
    DraftSegment,
    Project,
    Review,
)


@event.listens_for(Engine, "connect")
def _enforce_sqlite_fks(dbapi_connection: object, _: object) -> None:
    """SQLite ships with foreign keys disabled by default; enable per-connection."""
    if dbapi_connection.__class__.__module__.startswith("sqlite3"):
        cur = dbapi_connection.cursor()  # type: ignore[attr-defined]
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()


REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture()
def alembic_config(tmp_path: Path) -> Iterator[tuple[Config, str]]:
    """Spin up a fresh SQLite db and run migrations against it."""
    db_path = tmp_path / "m2.sqlite3"
    db_url = f"sqlite:///{db_path.as_posix()}"

    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", db_url)

    yield cfg, db_url


def test_alembic_upgrade_creates_all_tables(alembic_config: tuple[Config, str]) -> None:
    cfg, db_url = alembic_config
    command.upgrade(cfg, "head")

    engine = create_engine(db_url)
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    expected = {
        "projects",
        "assets",
        "asset_tags",
        "asset_segments",
        "drafts",
        "draft_segments",
        "reviews",
        "bgms",
        "profiles",
    }
    assert expected.issubset(tables), f"missing tables: {expected - tables}"


def test_alembic_downgrade_to_base(alembic_config: tuple[Config, str]) -> None:
    cfg, db_url = alembic_config
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")

    engine = create_engine(db_url)
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    app_tables = {
        "projects",
        "assets",
        "asset_tags",
        "asset_segments",
        "drafts",
        "draft_segments",
        "reviews",
        "bgms",
        "profiles",
    }
    leftover = tables & app_tables
    assert leftover == set(), f"unexpected residual tables after downgrade: {leftover}"


@pytest.fixture()
def session(alembic_config: tuple[Config, str]) -> Iterator[Session]:
    cfg, db_url = alembic_config
    command.upgrade(cfg, "head")
    engine = create_engine(db_url)
    with Session(engine) as s:
        yield s


def _make_project(s: Session, name: str = "carsmeet-Phantom-0428") -> Project:
    p = Project(
        name=name,
        client="CarsMeet",
        profile_name="carsmeet-luxury",
        source_dir="/mnt/assets/carsmeet/phantom",
        status="pending",
    )
    s.add(p)
    s.commit()
    s.refresh(p)
    return p


def _make_asset(s: Session, project_id: int, sha: str = "a" * 64) -> Asset:
    a = Asset(
        project_id=project_id,
        file_path="/mnt/assets/foo.mp4",
        duration_ms=5000,
        resolution="3840x2160",
        fps=30.0,
        codec="h264",
        sha256=sha,
        status="pending",
    )
    s.add(a)
    s.commit()
    s.refresh(a)
    return a


def test_project_round_trip(session: Session) -> None:
    p = _make_project(session)
    fetched = session.get(Project, p.id)
    assert fetched is not None
    assert fetched.name == "carsmeet-Phantom-0428"
    assert fetched.profile_name == "carsmeet-luxury"
    assert fetched.created_at is not None


def test_project_status_check_rejects_bogus_value(session: Session) -> None:
    p = Project(
        name="x",
        profile_name="universal",
        source_dir="/tmp/x",
        status="bogus",
    )
    session.add(p)
    with pytest.raises(IntegrityError):
        session.commit()


def test_cascade_delete_assets(session: Session) -> None:
    p = _make_project(session)
    for i in range(5):
        _make_asset(session, p.id, sha=f"{i:0>64}")
    session.delete(p)
    session.commit()
    remaining = session.query(Asset).count()
    assert remaining == 0


def test_asset_tag_uniqueness(session: Session) -> None:
    p = _make_project(session)
    a = _make_asset(session, p.id)
    session.add(
        AssetTag(
            asset_id=a.id,
            tag_type="object",
            tag_name="car",
            confidence=0.9,
            source_model="yolov11",
        )
    )
    session.commit()
    session.add(
        AssetTag(
            asset_id=a.id,
            tag_type="object",
            tag_name="car",
            confidence=0.5,
            source_model="yolov11",
        )
    )
    with pytest.raises(IntegrityError):
        session.commit()


def test_asset_segment_range_check(session: Session) -> None:
    p = _make_project(session)
    a = _make_asset(session, p.id)
    seg = AssetSegment(asset_id=a.id, start_ms=2000, end_ms=1000, score=0.8)
    session.add(seg)
    with pytest.raises(IntegrityError):
        session.commit()


def test_draft_version_uniqueness(session: Session) -> None:
    p = _make_project(session)
    d1 = Draft(project_id=p.id, profile_name="carsmeet-luxury", version=1)
    session.add(d1)
    session.commit()

    d2 = Draft(project_id=p.id, profile_name="carsmeet-luxury", version=1)
    session.add(d2)
    with pytest.raises(IntegrityError):
        session.commit()


def test_draft_segment_order_uniqueness(session: Session) -> None:
    p = _make_project(session)
    a = _make_asset(session, p.id)
    seg = AssetSegment(asset_id=a.id, start_ms=0, end_ms=1000, score=1.0)
    session.add(seg)
    session.commit()
    session.refresh(seg)

    d = Draft(project_id=p.id, profile_name="carsmeet-luxury", version=1)
    session.add(d)
    session.commit()
    session.refresh(d)

    session.add(
        DraftSegment(
            draft_id=d.id,
            order=0,
            asset_segment_id=seg.id,
            on_timeline_start_ms=0,
            on_timeline_end_ms=500,
        )
    )
    session.commit()

    session.add(
        DraftSegment(
            draft_id=d.id,
            order=0,
            asset_segment_id=seg.id,
            on_timeline_start_ms=500,
            on_timeline_end_ms=1000,
        )
    )
    with pytest.raises(IntegrityError):
        session.commit()


def test_review_action_check(session: Session) -> None:
    p = _make_project(session)
    d = Draft(project_id=p.id, profile_name="carsmeet-luxury", version=1)
    session.add(d)
    session.commit()
    session.refresh(d)

    r = Review(draft_id=d.id, action="bogus")
    session.add(r)
    with pytest.raises(IntegrityError):
        session.commit()
