from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from media_processor.models import Asset, Base, Project
from media_processor.services import asset_stabilization_runner, asset_variants


async def _seed_asset(session_maker: async_sessionmaker[AsyncSession], source: Path) -> int:
    async with session_maker() as session:
        project = Project(
            name="stab-test",
            profile_name="default",
            source_dir=str(source.parent),
        )
        session.add(project)
        await session.flush()
        asset = Asset(
            project_id=project.id,
            file_path=str(source),
            duration_ms=5000,
            resolution="3840x2160",
            fps=60.0,
            codec="h264",
            sha256="a" * 64,
            status="analyzed",
        )
        session.add(asset)
        await session.commit()
        return asset.id


@pytest.fixture()
async def session_maker() -> async_sessionmaker[AsyncSession]:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_run_asset_stabilization_skips_low_jitter_source(
    session_maker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "raw.mp4"
    source.write_bytes(b"raw")
    asset_id = await _seed_asset(session_maker, source)

    monkeypatch.setattr(asset_stabilization_runner, "async_session_maker", session_maker)
    monkeypatch.setattr(
        asset_variants,
        "stabilized_path_for_asset",
        lambda asset: tmp_path / f"{asset.id}.stab.mp4",
    )
    monkeypatch.setattr(
        asset_variants,
        "estimate_stabilization_need",
        lambda _src: asset_variants.StabilizationNeedEstimate(
            False,
            sampled_frames=313,
            usable_steps=312,
            jitter_rms_px=0.114,
            jitter_p95_px=0.241,
            reason="jitter_rms=0.114px jitter_p95=0.241px usable_steps=312",
        ),
    )
    monkeypatch.setattr(
        asset_variants,
        "stabilize_source",
        lambda *_args, **_kwargs: pytest.fail("low-jitter source must not run vidstab"),
    )

    result = await asset_stabilization_runner.run_asset_stabilization(asset_id)

    assert result == {"asset_id": asset_id, "status": "skipped"}
    async with session_maker() as session:
        asset = await session.get(Asset, asset_id)
        assert asset is not None
        assert asset.stabilized_path is None
        assert asset.stabilization_status == asset_variants.STABILIZATION_SKIPPED
        assert asset.stabilization_error is not None
        assert "low-jitter source skipped" in asset.stabilization_error
        assert "jitter_rms=0.114px" in asset.stabilization_error


@pytest.mark.asyncio
async def test_run_asset_stabilization_prefers_tracking_derivative(
    session_maker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "raw.mp4"
    source.write_bytes(b"raw")
    asset_id = await _seed_asset(session_maker, source)
    dst = tmp_path / "tracking.stab.mp4"

    monkeypatch.setattr(asset_stabilization_runner, "async_session_maker", session_maker)
    monkeypatch.setattr(asset_variants, "stabilized_path_for_asset", lambda _asset: dst)
    monkeypatch.setattr(
        asset_variants,
        "stabilize_source_from_tracking",
        lambda *_args: asset_variants.TrackingStabilizationResult(
            mode="tracking_point",
            point_count=120,
            crop_w=3556,
            crop_h=2000,
        ),
    )
    monkeypatch.setattr(
        asset_variants,
        "estimate_stabilization_need",
        lambda _src: pytest.fail("tracking derivative should skip vidstab preflight"),
    )
    monkeypatch.setattr(
        asset_variants,
        "stabilize_source",
        lambda *_args, **_kwargs: pytest.fail("tracking derivative should skip vidstab"),
    )

    result = await asset_stabilization_runner.run_asset_stabilization(asset_id)

    assert result == {"asset_id": asset_id, "status": "done"}
    async with session_maker() as session:
        asset = await session.get(Asset, asset_id)
        assert asset is not None
        assert asset.stabilized_path == str(dst)
        assert asset.stabilization_status == asset_variants.STABILIZATION_DONE
        assert asset.stabilization_error is not None
        assert "tracking-based stabilization (tracking_point)" in asset.stabilization_error


@pytest.mark.asyncio
async def test_run_asset_stabilization_force_bypasses_low_jitter_preflight(
    session_maker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "raw.mp4"
    source.write_bytes(b"raw")
    asset_id = await _seed_asset(session_maker, source)
    dst = tmp_path / "forced.stab.mp4"

    monkeypatch.setattr(asset_stabilization_runner, "async_session_maker", session_maker)
    monkeypatch.setattr(asset_variants, "stabilized_path_for_asset", lambda _asset: dst)
    monkeypatch.setattr(
        asset_variants,
        "estimate_stabilization_need",
        lambda _src: pytest.fail("force=True must bypass low-jitter preflight"),
    )

    def fake_stabilize_source(_src: Path, output: Path, _scratch_dir: Path) -> None:
        output.write_bytes(b"stabilized")

    monkeypatch.setattr(asset_variants, "stabilize_source", fake_stabilize_source)

    result = await asset_stabilization_runner.run_asset_stabilization(asset_id, force=True)

    assert result == {"asset_id": asset_id, "status": "done"}
    async with session_maker() as session:
        asset = await session.get(Asset, asset_id)
        assert asset is not None
        assert asset.stabilized_path == str(dst)
        assert asset.stabilization_status == asset_variants.STABILIZATION_DONE
