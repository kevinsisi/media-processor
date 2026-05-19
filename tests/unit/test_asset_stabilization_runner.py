from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from media_processor.models import Asset, Base, Project
from media_processor.services import (
    asset_stabilization_runner,
    asset_variants,
    auto_reframe,
    point_tracking_runner,
)


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
async def test_run_asset_stabilization_skips_auto_tracking_on_low_jitter_source(
    session_maker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "raw.mp4"
    source.write_bytes(b"raw")
    asset_id = await _seed_asset(session_maker, source)
    old_derivative = tmp_path / "old.stab.mp4"
    old_derivative.write_bytes(b"old")
    async with session_maker() as session:
        asset = await session.get(Asset, asset_id)
        assert asset is not None
        asset.tracked_object_index = None
        asset.active_asset_variant = asset_variants.STABILIZED_VARIANT
        asset.stabilized_path = str(old_derivative)
        asset.stabilization_status = asset_variants.STABILIZATION_DONE
        await session.commit()

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
        "stabilize_source_from_tracking",
        lambda *_args, **_kwargs: pytest.fail("low-jitter auto tracking must be skipped"),
    )
    monkeypatch.setattr(
        asset_variants,
        "stabilize_source",
        lambda *_args, **_kwargs: pytest.fail("low-jitter source must not run vidstab"),
    )

    result = await asset_stabilization_runner.run_asset_stabilization(asset_id)

    assert result == {"asset_id": asset_id, "status": "skipped"}
    assert not old_derivative.exists()
    async with session_maker() as session:
        asset = await session.get(Asset, asset_id)
        assert asset is not None
        assert asset.active_asset_variant == asset_variants.RAW_VARIANT
        assert asset.stabilized_path is None
        assert asset.stabilization_status == asset_variants.STABILIZATION_SKIPPED
        assert asset.stabilization_error is not None
        assert "low-jitter source skipped" in asset.stabilization_error


@pytest.mark.asyncio
async def test_run_asset_stabilization_keeps_auto_done_derivative_when_still_needed(
    session_maker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "raw.mp4"
    source.write_bytes(b"raw")
    asset_id = await _seed_asset(session_maker, source)
    old_derivative = tmp_path / "old.stab.mp4"
    old_derivative.write_bytes(b"old")
    async with session_maker() as session:
        asset = await session.get(Asset, asset_id)
        assert asset is not None
        asset.tracked_object_index = None
        asset.active_asset_variant = asset_variants.STABILIZED_VARIANT
        asset.stabilized_path = str(old_derivative)
        asset.stabilization_status = asset_variants.STABILIZATION_DONE
        asset.stabilization_error = "tracking-based stabilization (auto_tracking)"
        await session.commit()

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
            True,
            sampled_frames=300,
            usable_steps=299,
            jitter_rms_px=0.5,
            jitter_p95_px=1.0,
            reason="jitter_rms=0.500px jitter_p95=1.000px usable_steps=299",
        ),
    )
    monkeypatch.setattr(
        asset_variants,
        "stabilize_source_from_tracking",
        lambda *_args, **_kwargs: pytest.fail("valid done derivative must stay idempotent"),
    )
    monkeypatch.setattr(
        asset_variants,
        "stabilize_source",
        lambda *_args, **_kwargs: pytest.fail("valid done derivative must not run vidstab"),
    )

    result = await asset_stabilization_runner.run_asset_stabilization(asset_id)

    assert result == {"asset_id": asset_id, "status": "done"}
    assert old_derivative.exists()
    async with session_maker() as session:
        asset = await session.get(Asset, asset_id)
        assert asset is not None
        assert asset.active_asset_variant == asset_variants.STABILIZED_VARIANT
        assert asset.stabilized_path == str(old_derivative)
        assert asset.stabilization_status == asset_variants.STABILIZATION_DONE
        assert asset.stabilization_error == "tracking-based stabilization (auto_tracking)"


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
    candidate = tmp_path / "tracking.unique.mp4"
    async with session_maker() as session:
        asset = await session.get(Asset, asset_id)
        assert asset is not None
        asset.tracked_object_index = -4
        asset.point_tracking_status = "done"
        await session.commit()

    monkeypatch.setattr(asset_stabilization_runner, "async_session_maker", session_maker)
    monkeypatch.setattr(asset_variants, "stabilized_path_for_asset", lambda _asset: dst)
    monkeypatch.setattr(asset_stabilization_runner, "_candidate_path", lambda _dst: candidate)

    def fake_tracking_derivative(
        _asset: Asset,
        _src: Path,
        output: Path,
        _scratch_dir: Path,
    ) -> asset_variants.TrackingStabilizationResult:
        output.write_bytes(b"tracking-stabilized")
        return asset_variants.TrackingStabilizationResult(
            mode="tracking_point",
            point_count=120,
            crop_w=3556,
            crop_h=2000,
        )

    monkeypatch.setattr(
        asset_variants,
        "stabilize_source_from_tracking",
        fake_tracking_derivative,
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
        assert asset.stabilized_path == str(candidate)
        assert asset.stabilization_status == asset_variants.STABILIZATION_DONE
        assert asset.stabilization_error is None
        assert asset.stabilization_mode == "tracking"
        assert asset.stabilization_metrics_json is not None
        assert "tracking_point" in asset.stabilization_metrics_json["mode"]
    assert candidate.read_bytes() == b"tracking-stabilized"


@pytest.mark.asyncio
async def test_run_asset_stabilization_discards_stale_tracking_derivative(
    session_maker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "raw.mp4"
    source.write_bytes(b"raw")
    asset_id = await _seed_asset(session_maker, source)
    dst = tmp_path / "tracking.stab.mp4"
    candidate = tmp_path / "tracking.stale.mp4"

    async def change_tracking_intent() -> None:
        async with session_maker() as session:
            asset = await session.get(Asset, asset_id)
            assert asset is not None
            asset.tracked_object_index = -3
            asset.stabilization_status = asset_variants.STABILIZATION_PENDING
            asset.stabilization_error = "new tracking target queued"
            await session.commit()

    def fake_tracking_derivative(
        _asset: Asset,
        _src: Path,
        output: Path,
        _scratch_dir: Path,
    ) -> asset_variants.TrackingStabilizationResult:
        output.write_bytes(b"stale-output")
        asyncio.run(change_tracking_intent())
        return asset_variants.TrackingStabilizationResult(
            mode="tracking_point",
            point_count=120,
            crop_w=3556,
            crop_h=2000,
        )

    monkeypatch.setattr(asset_stabilization_runner, "async_session_maker", session_maker)
    monkeypatch.setattr(asset_variants, "stabilized_path_for_asset", lambda _asset: dst)
    monkeypatch.setattr(asset_stabilization_runner, "_candidate_path", lambda _dst: candidate)
    monkeypatch.setattr(asset_variants, "stabilize_source_from_tracking", fake_tracking_derivative)

    result = await asset_stabilization_runner.run_asset_stabilization(asset_id, force=True)

    assert result == {"asset_id": asset_id, "status": "stale_intent"}
    assert not candidate.exists()
    async with session_maker() as session:
        asset = await session.get(Asset, asset_id)
        assert asset is not None
        assert asset.tracked_object_index == -3
        assert asset.stabilized_path == str(dst)
        assert asset.stabilization_status == asset_variants.STABILIZATION_PENDING
        assert asset.stabilization_error == "new tracking target queued"


@pytest.mark.asyncio
async def test_run_asset_stabilization_cleans_replaced_same_intent_derivative(
    session_maker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "raw.mp4"
    source.write_bytes(b"raw")
    asset_id = await _seed_asset(session_maker, source)
    dst = tmp_path / "tracking.stab.mp4"
    candidate = tmp_path / "tracking.latest.mp4"
    earlier_derivative = tmp_path / "tracking.earlier.mp4"
    earlier_derivative.write_bytes(b"earlier")

    async def publish_same_intent_first() -> None:
        async with session_maker() as session:
            asset = await session.get(Asset, asset_id)
            assert asset is not None
            asset.stabilized_path = str(earlier_derivative)
            asset.stabilization_status = asset_variants.STABILIZATION_DONE
            await session.commit()

    def fake_tracking_derivative(
        _asset: Asset,
        _src: Path,
        output: Path,
        _scratch_dir: Path,
    ) -> asset_variants.TrackingStabilizationResult:
        output.write_bytes(b"latest")
        asyncio.run(publish_same_intent_first())
        return asset_variants.TrackingStabilizationResult(
            mode="tracking_point",
            point_count=120,
            crop_w=3556,
            crop_h=2000,
        )

    monkeypatch.setattr(asset_stabilization_runner, "async_session_maker", session_maker)
    monkeypatch.setattr(asset_variants, "stabilized_path_for_asset", lambda _asset: dst)
    monkeypatch.setattr(asset_stabilization_runner, "_candidate_path", lambda _dst: candidate)
    monkeypatch.setattr(asset_variants, "stabilize_source_from_tracking", fake_tracking_derivative)

    result = await asset_stabilization_runner.run_asset_stabilization(asset_id, force=True)

    assert result == {"asset_id": asset_id, "status": "done"}
    assert candidate.exists()
    assert not earlier_derivative.exists()
    async with session_maker() as session:
        asset = await session.get(Asset, asset_id)
        assert asset is not None
        assert asset.stabilized_path == str(candidate)
        assert asset.stabilization_status == asset_variants.STABILIZATION_DONE


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
    candidate = tmp_path / "forced.unique.mp4"
    old_derivative = tmp_path / "old.stab.mp4"
    old_derivative.write_bytes(b"old")
    async with session_maker() as session:
        asset = await session.get(Asset, asset_id)
        assert asset is not None
        asset.active_asset_variant = asset_variants.STABILIZED_VARIANT
        asset.stabilized_path = str(old_derivative)
        asset.stabilization_status = asset_variants.STABILIZATION_DONE
        await session.commit()

    monkeypatch.setattr(asset_stabilization_runner, "async_session_maker", session_maker)
    monkeypatch.setattr(asset_variants, "stabilized_path_for_asset", lambda _asset: dst)
    monkeypatch.setattr(asset_stabilization_runner, "_candidate_path", lambda _dst: candidate)
    monkeypatch.setattr(
        asset_variants,
        "estimate_stabilization_need",
        lambda _src: pytest.fail("force=True must bypass low-jitter preflight"),
    )

    def fake_stabilize_source(_src: Path, output: Path, _scratch_dir: Path) -> None:
        assert _src == source
        output.write_bytes(b"stabilized")

    monkeypatch.setattr(asset_variants, "stabilize_source", fake_stabilize_source)

    result = await asset_stabilization_runner.run_asset_stabilization(asset_id, force=True)

    assert result == {"asset_id": asset_id, "status": "done"}
    async with session_maker() as session:
        asset = await session.get(Asset, asset_id)
        assert asset is not None
        assert asset.stabilized_path == str(candidate)
        assert asset.stabilization_status == asset_variants.STABILIZATION_DONE
    assert candidate.read_bytes() == b"stabilized"
    assert not old_derivative.exists()


def test_asset_stabilization_uses_project11_validated_vidstab_smoothing() -> None:
    assert asset_variants.STABILIZE_SMOOTHING == 30


@pytest.mark.asyncio
async def test_run_asset_stabilization_records_tracking_fallback_reason(
    session_maker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "raw.mp4"
    source.write_bytes(b"raw")
    asset_id = await _seed_asset(session_maker, source)
    dst = tmp_path / "fallback.stab.mp4"
    candidate = tmp_path / "fallback.unique.mp4"

    monkeypatch.setattr(asset_stabilization_runner, "async_session_maker", session_maker)
    monkeypatch.setattr(asset_variants, "stabilized_path_for_asset", lambda _asset: dst)
    monkeypatch.setattr(asset_stabilization_runner, "_candidate_path", lambda _dst: candidate)

    def fail_tracking_derivative(*_args: object, **_kwargs: object) -> None:
        raise asset_variants.AssetStabilizationError("tracking output regressed jitter")

    def fake_stabilize_source(_src: Path, output: Path, _scratch_dir: Path) -> None:
        output.write_bytes(b"vidstab-fallback")

    monkeypatch.setattr(asset_variants, "stabilize_source_from_tracking", fail_tracking_derivative)
    monkeypatch.setattr(
        asset_variants,
        "estimate_stabilization_need",
        lambda _src: asset_variants.StabilizationNeedEstimate(
            True,
            sampled_frames=300,
            usable_steps=299,
            jitter_rms_px=0.5,
            jitter_p95_px=1.0,
            reason="jitter_rms=0.500px jitter_p95=1.000px usable_steps=299",
        ),
    )
    monkeypatch.setattr(asset_variants, "stabilize_source", fake_stabilize_source)

    result = await asset_stabilization_runner.run_asset_stabilization(asset_id, force=True)

    assert result == {"asset_id": asset_id, "status": "done"}
    async with session_maker() as session:
        asset = await session.get(Asset, asset_id)
        assert asset is not None
        assert asset.stabilization_status == asset_variants.STABILIZATION_DONE
        assert asset.stabilization_mode == "vidstab"
        assert asset.stabilization_error is not None
        assert "tracking output regressed jitter" in asset.stabilization_error


@pytest.mark.asyncio
async def test_tracking_rejection_fallback_still_skips_low_jitter_source(
    session_maker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "raw.mp4"
    source.write_bytes(b"raw")
    asset_id = await _seed_asset(session_maker, source)
    old_derivative = tmp_path / "old.stab.mp4"
    old_derivative.write_bytes(b"old")
    async with session_maker() as session:
        asset = await session.get(Asset, asset_id)
        assert asset is not None
        asset.active_asset_variant = asset_variants.STABILIZED_VARIANT
        asset.stabilized_path = str(old_derivative)
        asset.stabilization_status = asset_variants.STABILIZATION_DONE
        await session.commit()

    monkeypatch.setattr(asset_stabilization_runner, "async_session_maker", session_maker)
    monkeypatch.setattr(
        asset_variants,
        "stabilized_path_for_asset",
        lambda asset: tmp_path / f"{asset.id}.stab.mp4",
    )
    monkeypatch.setattr(
        asset_variants,
        "stabilize_source_from_tracking",
        lambda *_args: (_ for _ in ()).throw(
            asset_variants.AssetStabilizationError("tracking output regressed jitter")
        ),
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
        lambda *_args, **_kwargs: pytest.fail("low-jitter fallback must not run vidstab"),
    )

    result = await asset_stabilization_runner.run_asset_stabilization(asset_id, force=True)

    assert result == {"asset_id": asset_id, "status": "skipped"}
    async with session_maker() as session:
        asset = await session.get(Asset, asset_id)
        assert asset is not None
        assert asset.active_asset_variant == asset_variants.RAW_VARIANT
        assert asset.stabilized_path is None
        assert asset.stabilization_status == asset_variants.STABILIZATION_SKIPPED
        assert asset.stabilization_error is not None
        assert "tracking output regressed jitter" in asset.stabilization_error
        assert "low-jitter source skipped" in asset.stabilization_error
    assert not old_derivative.exists()


@pytest.mark.asyncio
async def test_run_asset_stabilization_waits_for_pending_point_tracking(
    session_maker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "raw.mp4"
    source.write_bytes(b"raw")
    asset_id = await _seed_asset(session_maker, source)
    async with session_maker() as session:
        asset = await session.get(Asset, asset_id)
        assert asset is not None
        asset.tracked_object_index = -4
        asset.point_tracking_status = "pending"
        await session.commit()

    monkeypatch.setattr(asset_stabilization_runner, "async_session_maker", session_maker)
    monkeypatch.setattr(
        asset_variants,
        "stabilize_source",
        lambda *_args, **_kwargs: pytest.fail("must wait for point tracking"),
    )

    result = await asset_stabilization_runner.run_asset_stabilization(asset_id)

    assert result == {"asset_id": asset_id, "status": "waiting_point_tracking"}
    async with session_maker() as session:
        asset = await session.get(Asset, asset_id)
        assert asset is not None
        assert asset.stabilization_status == asset_variants.STABILIZATION_NOT_STARTED
        assert asset.stabilization_error == "waiting for point tracking before stabilization"


@pytest.mark.asyncio
async def test_point_tracking_done_enqueues_tracking_stabilization(
    session_maker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "raw.mp4"
    source.write_bytes(b"raw")
    asset_id = await _seed_asset(session_maker, source)
    async with session_maker() as session:
        asset = await session.get(Asset, asset_id)
        assert asset is not None
        asset.tracked_object_index = -4
        asset.point_tracking_status = "pending"
        asset.point_tracking_origin = {"norm_x": 0.5, "norm_y": 0.5, "frame_ms": 0}
        await session.commit()

    job_calls: list[tuple[int, bool]] = []
    monkeypatch.setattr(point_tracking_runner, "async_session_maker", session_maker)
    monkeypatch.setattr(
        asset_variants, "stabilized_path_for_asset", lambda _asset: tmp_path / "point.stab.mp4"
    )
    monkeypatch.setattr(
        point_tracking_runner.point_tracking_svc,
        "track_point",
        lambda *_args, **_kwargs: {
            "src_w": 3840,
            "src_h": 2160,
            "fps": 60.0,
            "init": {"x": 1920, "y": 1080},
            "frames": [{"t_ms": 0, "x": 1920.0, "y": 1080.0, "lost": False}],
            "sampled_frames": 1,
        },
    )
    monkeypatch.setattr(
        point_tracking_runner,
        "enqueue_asset_stabilization",
        lambda asset_id_arg, *, force=False: job_calls.append((asset_id_arg, force)) or "stab-1",
    )

    result = await point_tracking_runner.run_point_tracking(
        asset_id,
        init_norm_x=0.5,
        init_norm_y=0.5,
        init_t_ms=0,
    )

    assert result["status"] == "done"
    assert result["stabilization_job_id"] == "stab-1"
    assert job_calls == [(asset_id, True)]
    async with session_maker() as session:
        asset = await session.get(Asset, asset_id)
        assert asset is not None
        assert asset.point_tracking_status == "done"
        assert asset.stabilization_status == asset_variants.STABILIZATION_PENDING
        assert asset.stabilized_path == str(tmp_path / "point.stab.mp4")


@pytest.mark.asyncio
async def test_tracking_success_auto_switches_to_stabilized_variant(
    session_maker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Tracking-based success must set active_asset_variant=stabilized."""
    source = tmp_path / "raw.mp4"
    source.write_bytes(b"raw")
    asset_id = await _seed_asset(session_maker, source)
    async with session_maker() as session:
        asset = await session.get(Asset, asset_id)
        assert asset is not None
        asset.tracked_object_index = -4
        asset.point_tracking_status = "done"
        await session.commit()

    candidate = tmp_path / "tracking.unique.mp4"
    monkeypatch.setattr(asset_stabilization_runner, "async_session_maker", session_maker)
    monkeypatch.setattr(
        asset_variants, "stabilized_path_for_asset", lambda _asset: tmp_path / "tracking.stab.mp4"
    )
    monkeypatch.setattr(asset_stabilization_runner, "_candidate_path", lambda _dst: candidate)

    def _fake_tracking(
        _asset: Asset, _src: Path, output: Path, _scratch: Path
    ) -> asset_variants.TrackingStabilizationResult:
        output.write_bytes(b"tracking-stabilized")
        return asset_variants.TrackingStabilizationResult(
            mode="tracking_point", point_count=80, crop_w=1080, crop_h=1920
        )

    monkeypatch.setattr(asset_variants, "stabilize_source_from_tracking", _fake_tracking)
    enqueue_calls: list[tuple[int, bool]] = []
    monkeypatch.setattr(
        asset_stabilization_runner,
        "enqueue_asset_analysis",
        lambda asset_id_arg, *, force=False: enqueue_calls.append((asset_id_arg, force)),
    )

    result = await asset_stabilization_runner.run_asset_stabilization(asset_id)

    assert result["status"] == "done"
    async with session_maker() as session:
        asset = await session.get(Asset, asset_id)
        assert asset is not None
        assert asset.active_asset_variant == asset_variants.STABILIZED_VARIANT
    assert enqueue_calls == [(asset_id, False)]


@pytest.mark.asyncio
async def test_vidstab_success_auto_switches_to_stabilized_variant(
    session_maker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """vidstab success must set active_asset_variant=stabilized."""
    source = tmp_path / "raw.mp4"
    source.write_bytes(b"raw")
    asset_id = await _seed_asset(session_maker, source)

    candidate = tmp_path / "vidstab.unique.mp4"
    monkeypatch.setattr(asset_stabilization_runner, "async_session_maker", session_maker)
    monkeypatch.setattr(
        asset_variants, "stabilized_path_for_asset", lambda _asset: tmp_path / "vidstab.stab.mp4"
    )
    monkeypatch.setattr(asset_stabilization_runner, "_candidate_path", lambda _dst: candidate)
    monkeypatch.setattr(
        asset_variants,
        "stabilize_source_from_tracking",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        asset_variants,
        "estimate_stabilization_need",
        lambda _src: asset_variants.StabilizationNeedEstimate(
            True,
            sampled_frames=300,
            usable_steps=299,
            jitter_rms_px=0.5,
            jitter_p95_px=1.0,
            reason="jitter_rms=0.500px",
        ),
    )
    monkeypatch.setattr(
        asset_variants,
        "stabilize_source",
        lambda _src, output, _scratch: output.write_bytes(b"vidstab"),
    )
    enqueue_calls: list[tuple[int, bool]] = []
    monkeypatch.setattr(
        asset_stabilization_runner,
        "enqueue_asset_analysis",
        lambda asset_id_arg, *, force=False: enqueue_calls.append((asset_id_arg, force)),
    )

    result = await asset_stabilization_runner.run_asset_stabilization(asset_id)

    assert result["status"] == "done"
    async with session_maker() as session:
        asset = await session.get(Asset, asset_id)
        assert asset is not None
        assert asset.active_asset_variant == asset_variants.STABILIZED_VARIANT
    assert enqueue_calls == [(asset_id, False)]


@pytest.mark.asyncio
async def test_stabilization_fail_keeps_raw_variant_and_enqueues_analysis(
    session_maker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """vidstab failure must set active_asset_variant=raw and still enqueue analysis."""
    source = tmp_path / "raw.mp4"
    source.write_bytes(b"raw")
    asset_id = await _seed_asset(session_maker, source)

    candidate = tmp_path / "vidstab.unique.mp4"
    monkeypatch.setattr(asset_stabilization_runner, "async_session_maker", session_maker)
    monkeypatch.setattr(
        asset_variants, "stabilized_path_for_asset", lambda _asset: tmp_path / "vidstab.stab.mp4"
    )
    monkeypatch.setattr(asset_stabilization_runner, "_candidate_path", lambda _dst: candidate)
    monkeypatch.setattr(
        asset_variants,
        "stabilize_source_from_tracking",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        asset_variants,
        "estimate_stabilization_need",
        lambda _src: asset_variants.StabilizationNeedEstimate(
            True,
            sampled_frames=300,
            usable_steps=299,
            jitter_rms_px=0.5,
            jitter_p95_px=1.0,
            reason="jitter_rms=0.500px",
        ),
    )
    monkeypatch.setattr(
        asset_variants,
        "stabilize_source",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("ffmpeg error")),
    )
    enqueue_calls: list[tuple[int, bool]] = []
    monkeypatch.setattr(
        asset_stabilization_runner,
        "enqueue_asset_analysis",
        lambda asset_id_arg, *, force=False: enqueue_calls.append((asset_id_arg, force)),
    )

    result = await asset_stabilization_runner.run_asset_stabilization(asset_id)

    assert result["status"] == "failed"
    async with session_maker() as session:
        asset = await session.get(Asset, asset_id)
        assert asset is not None
        assert asset.active_asset_variant == asset_variants.RAW_VARIANT
        assert asset.stabilization_status == asset_variants.STABILIZATION_FAILED
    assert enqueue_calls == [(asset_id, False)]


@pytest.mark.asyncio
async def test_low_jitter_skip_enqueues_analysis_on_raw(
    session_maker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Low-jitter skip must enqueue analysis on raw variant."""
    source = tmp_path / "raw.mp4"
    source.write_bytes(b"raw")
    asset_id = await _seed_asset(session_maker, source)

    monkeypatch.setattr(asset_stabilization_runner, "async_session_maker", session_maker)
    monkeypatch.setattr(
        asset_variants,
        "stabilized_path_for_asset",
        lambda _asset: tmp_path / "skip.stab.mp4",
    )
    monkeypatch.setattr(
        asset_variants,
        "estimate_stabilization_need",
        lambda _src: asset_variants.StabilizationNeedEstimate(
            False,
            sampled_frames=313,
            usable_steps=312,
            jitter_rms_px=0.10,
            jitter_p95_px=0.22,
            reason="jitter_rms=0.100px",
        ),
    )
    enqueue_calls: list[tuple[int, bool]] = []
    monkeypatch.setattr(
        asset_stabilization_runner,
        "enqueue_asset_analysis",
        lambda asset_id_arg, *, force=False: enqueue_calls.append((asset_id_arg, force)),
    )

    result = await asset_stabilization_runner.run_asset_stabilization(asset_id)

    assert result["status"] == "skipped"
    async with session_maker() as session:
        asset = await session.get(Asset, asset_id)
        assert asset is not None
        assert asset.active_asset_variant == asset_variants.RAW_VARIANT
    assert enqueue_calls == [(asset_id, False)]


@pytest.mark.asyncio
async def test_run_asset_stabilization_sets_mode_tracking(
    session_maker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """stabilization_mode must be 'tracking' after a successful tracking-based run."""
    source = tmp_path / "raw.mp4"
    source.write_bytes(b"raw")
    asset_id = await _seed_asset(session_maker, source)
    async with session_maker() as session:
        asset = await session.get(Asset, asset_id)
        assert asset is not None
        asset.tracked_object_index = -4
        asset.point_tracking_status = "done"
        asset.point_tracking_json = {
            "src_w": 1920,
            "src_h": 1080,
            "frames": [{"t_ms": i * 33, "x": 960, "y": 540} for i in range(60)],
        }
        asset.duration_ms = 2000
        await session.commit()

    fake_result = asset_variants.TrackingStabilizationResult(
        mode="tracking_point", point_count=60, crop_w=608, crop_h=1080
    )
    monkeypatch.setattr(asset_stabilization_runner, "async_session_maker", session_maker)
    monkeypatch.setattr(
        asset_variants,
        "stabilized_path_for_asset",
        lambda a: tmp_path / f"{a.id}.stab.mp4",
    )
    monkeypatch.setattr(
        asset_variants,
        "stabilize_source_from_tracking",
        lambda *_args, **_kwargs: fake_result,
    )
    monkeypatch.setattr(
        asset_stabilization_runner,
        "enqueue_asset_analysis",
        lambda *_args, **_kwargs: None,
    )

    result = await asset_stabilization_runner.run_asset_stabilization(asset_id)

    assert result["status"] == "done"
    async with session_maker() as session:
        asset = await session.get(Asset, asset_id)
        assert asset is not None
        assert asset.stabilization_mode == "tracking"
        assert asset.stabilization_metrics_json is not None
        assert asset.stabilization_metrics_json["mode"] == "tracking_point"
        assert asset.stabilization_metrics_json["point_count"] == 60


@pytest.mark.asyncio
async def test_run_asset_stabilization_sets_mode_vidstab(
    session_maker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """stabilization_mode must be 'vidstab' after a vidstab fallback run."""
    source = tmp_path / "raw.mp4"
    source.write_bytes(b"raw")
    asset_id = await _seed_asset(session_maker, source)

    candidate = tmp_path / "vidstab.unique.mp4"
    monkeypatch.setattr(asset_stabilization_runner, "async_session_maker", session_maker)
    monkeypatch.setattr(
        asset_variants,
        "stabilized_path_for_asset",
        lambda a: tmp_path / f"{a.id}.stab.mp4",
    )
    monkeypatch.setattr(asset_stabilization_runner, "_candidate_path", lambda _dst: candidate)
    monkeypatch.setattr(
        asset_variants,
        "estimate_stabilization_need",
        lambda _src: asset_variants.StabilizationNeedEstimate(
            True, 313, 312, 1.2, 2.5, "jitter_rms=1.200px"
        ),
    )
    monkeypatch.setattr(
        asset_variants,
        "stabilize_source_from_tracking",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        asset_variants,
        "stabilize_source",
        lambda _src, output, _scratch: output.write_bytes(b"stabilized"),
    )
    monkeypatch.setattr(
        asset_stabilization_runner,
        "enqueue_asset_analysis",
        lambda *_args, **_kwargs: None,
    )

    result = await asset_stabilization_runner.run_asset_stabilization(asset_id)

    assert result["status"] == "done"
    async with session_maker() as session:
        asset = await session.get(Asset, asset_id)
        assert asset is not None
        assert asset.stabilization_mode == "vidstab"
        assert asset.stabilization_metrics_json is None


# ---------------------------------------------------------------------------
# D4 — _tracking_crop_path_for_asset priority routing
# ---------------------------------------------------------------------------


class _FakeAsset:
    def __init__(self, **kwargs: object) -> None:
        self.duration_ms = kwargs.get("duration_ms", 5000)
        self.resolution = kwargs.get("resolution", "1920x1080")
        self.tracked_object_index = kwargs.get("tracked_object_index")
        self.point_tracking_json = kwargs.get("point_tracking_json")
        self.point_tracking_status = kwargs.get("point_tracking_status")
        self.custom_roi_json = kwargs.get("custom_roi_json")
        self.tracking_json = kwargs.get("tracking_json")


def _make_fake_crop_path(n: int = 60) -> auto_reframe.CropPath:
    return auto_reframe.CropPath(
        crop_w=608,
        crop_h=1080,
        src_w=1920,
        src_h=1080,
        points=[(i * 0.033, 480, 0) for i in range(n)],
    )


def test_tracking_crop_path_prefers_point_track(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_path = _make_fake_crop_path(60)
    monkeypatch.setattr(
        auto_reframe, "compute_crop_path_from_point_track", lambda *_a, **_kw: fake_path
    )
    monkeypatch.setattr(
        auto_reframe,
        "compute_crop_path_from_custom_roi",
        lambda *_a, **_kw: pytest.fail("must not call custom_roi for tracked_object_index=-4"),
    )

    asset = _FakeAsset(
        tracked_object_index=-4,
        point_tracking_json={"src_w": 1920, "src_h": 1080, "frames": []},
    )
    result = asset_variants._tracking_crop_path_for_asset(asset)

    assert result is not None
    mode, path = result
    assert mode == "tracking_point"
    assert len(path.points) == 60


def test_tracking_crop_path_falls_back_to_custom_roi(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_path = _make_fake_crop_path(60)
    monkeypatch.setattr(
        auto_reframe, "compute_crop_path_from_custom_roi", lambda *_a, **_kw: fake_path
    )
    monkeypatch.setattr(
        auto_reframe,
        "compute_crop_path_from_point_track",
        lambda *_a, **_kw: pytest.fail("must not call point_track for tracked_object_index=-1"),
    )

    asset = _FakeAsset(
        tracked_object_index=-1,
        custom_roi_json={"src_w": 1920, "src_h": 1080},
    )
    result = asset_variants._tracking_crop_path_for_asset(asset)

    assert result is not None
    mode, _ = result
    assert mode == "tracking_custom_roi"


def test_tracking_crop_path_falls_back_to_auto_tracking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_path = _make_fake_crop_path(60)
    monkeypatch.setattr(auto_reframe, "compute_crop_path", lambda *_a, **_kw: fake_path)

    asset = _FakeAsset(
        tracked_object_index=None,
        tracking_json={"src_w": 1920, "src_h": 1080, "tracks": []},
    )
    result = asset_variants._tracking_crop_path_for_asset(asset)

    assert result is not None
    mode, _ = result
    assert mode == "auto_tracking"


def test_tracking_crop_path_returns_none_when_no_tracking_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(auto_reframe, "compute_crop_path", lambda *_a, **_kw: None)

    asset = _FakeAsset(tracked_object_index=None, tracking_json=None)
    result = asset_variants._tracking_crop_path_for_asset(asset)

    assert result is None


def test_tracking_crop_path_returns_none_when_too_few_points(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sparse_path = _make_fake_crop_path(asset_variants.TRACKING_STABILIZE_MIN_POINTS - 1)
    monkeypatch.setattr(
        auto_reframe, "compute_crop_path_from_point_track", lambda *_a, **_kw: sparse_path
    )

    asset = _FakeAsset(
        tracked_object_index=-4,
        point_tracking_json={"src_w": 1920, "src_h": 1080, "frames": []},
    )
    result = asset_variants._tracking_crop_path_for_asset(asset)

    assert result is None
