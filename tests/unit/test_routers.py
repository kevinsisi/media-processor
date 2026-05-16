"""Tests for the M2 read/write API routers.

We swap the production async-Postgres engine for an in-memory async-SQLite
engine via dependency override; the routers themselves are unchanged.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
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

from media_processor.api.deps import get_session
from media_processor.api.main import app as production_app
from media_processor.api.routers import assets as assets_router
from media_processor.api.routers import drafts as drafts_router
from media_processor.api.routers import music as music_router
from media_processor.api.routers import projects as projects_router
from media_processor.models import (
    Asset,
    AssetSegment,
    AssetTag,
    Base,
    Draft,
    DraftSegment,
    Project,
)


@pytest.fixture()
def app(monkeypatch: pytest.MonkeyPatch) -> Iterator[FastAPI]:
    import asyncio

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    session_maker = async_sessionmaker(engine, expire_on_commit=False)

    async def init() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await _seed(session_maker)

    asyncio.run(init())

    def fake_enqueue_draft_export(draft_id: int, **kwargs: Any) -> str:
        return f"fake-export-{draft_id}-{kwargs['export_id']}"

    monkeypatch.setattr(drafts_router, "enqueue_draft_export", fake_enqueue_draft_export)

    async def override_get_session() -> AsyncIterator[AsyncSession]:
        async with session_maker() as session:
            yield session

    production_app.dependency_overrides[get_session] = override_get_session
    production_app.state.test_session_maker = session_maker
    try:
        yield production_app
    finally:
        production_app.dependency_overrides.clear()
        asyncio.run(engine.dispose())


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
            stabilized_path="/mnt/assets/_stabilized/1_foo.stab.mp4",
            stabilization_status="done",
            duration_ms=5000,
            resolution="3840x2160",
            fps=30.0,
            codec="h264",
            sha256="a" * 64,
            status="analyzed",
            analysis_steps_json={"scene": "done", "tracking": "done"},
            tracked_object_index=0,
            tracking_json={
                "src_w": 3840,
                "src_h": 2160,
                "fps": 5.0,
                "sampled_frames": 5,
                "subject_class": "car",
                "confidence": 0.9,
                "frames": [
                    {"t_ms": i * 250, "x": 100, "y": 200, "w": 800, "h": 400, "conf": 0.9}
                    for i in range(5)
                ],
            },
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
                AssetTag(
                    asset_id=a.id,
                    tag_type="scene",
                    tag_name="showroom",
                    confidence=0.1,
                    source_model="gemini",
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
            mp4_preview_path="/mnt/drafts/p1_v1.mp4",
            output_zip_path="/mnt/drafts/p1_v1.zip",
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


def test_get_projects_returns_summary(app: FastAPI) -> None:
    client = TestClient(app)
    resp = client.get("/projects")
    assert resp.status_code == 200
    body: list[dict[str, Any]] = resp.json()
    assert len(body) == 1
    assert body[0]["name"] == "carsmeet-Phantom-0428"
    assert body[0]["asset_count"] == 1
    assert body[0]["latest_draft_version"] == 1


def test_get_project_detail_404(app: FastAPI) -> None:
    client = TestClient(app)
    resp = client.get("/projects/9999")
    assert resp.status_code == 404


def test_get_project_drafts(app: FastAPI) -> None:
    client = TestClient(app)
    resp = client.get("/projects/1/drafts")
    assert resp.status_code == 200
    body: list[dict[str, Any]] = resp.json()
    assert len(body) == 1
    assert body[0]["version"] == 1


def test_get_draft_detail_with_segments(app: FastAPI) -> None:
    client = TestClient(app)
    resp = client.get("/drafts/1")
    assert resp.status_code == 200
    body: dict[str, Any] = resp.json()
    assert body["version"] == 1
    assert len(body["segments"]) == 1
    assert body["segments"][0]["transition"] == "fade"


def test_get_draft_404(app: FastAPI) -> None:
    client = TestClient(app)
    resp = client.get("/drafts/9999")
    assert resp.status_code == 404


def test_export_draft_creates_listable_artifact(app: FastAPI) -> None:
    client = TestClient(app)
    resp = client.post("/drafts/1/export", json={"aspect": "9:16", "height": 1080})
    assert resp.status_code == 200, resp.text
    body: dict[str, Any] = resp.json()
    assert body["export_id"] == 1
    assert body["job_id"] == "fake-export-1-1"
    assert body["status"] == "queued"
    assert body["download_url"] is None

    list_resp = client.get("/drafts/1/exports")
    assert list_resp.status_code == 200, list_resp.text
    exports = list_resp.json()
    assert len(exports) == 1
    assert exports[0]["export_id"] == body["export_id"]
    assert exports[0]["output_filename"] == "v1-9x16-1080p.mp4"


def test_export_enqueue_failure_marks_artifact_failed(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_enqueue(*_args: Any, **_kwargs: Any) -> str:
        raise RuntimeError("redis down")

    monkeypatch.setattr(drafts_router, "enqueue_draft_export", fail_enqueue)
    client = TestClient(app)
    resp = client.post("/drafts/1/export", json={"aspect": "9:16", "height": 1080})
    assert resp.status_code == 502, resp.text

    list_resp = client.get("/drafts/1/exports")
    assert list_resp.status_code == 200, list_resp.text
    exports = list_resp.json()
    assert exports[0]["status"] == "failed"
    assert "enqueue failed" in exports[0]["error"]


def test_point_tracking_enqueue_failure_reaches_terminal_state(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_enqueue(*_args: Any, **_kwargs: Any) -> str:
        raise RuntimeError("redis down")

    monkeypatch.setattr(assets_router, "enqueue_point_tracking", fail_enqueue)
    client = TestClient(app)
    resp = client.patch(
        "/assets/1/tracking-target",
        json={"mode": "point", "point": {"norm_x": 0.5, "norm_y": 0.5, "frame_ms": 0}},
    )
    assert resp.status_code == 502, resp.text

    detail = client.get("/assets/1/tracking")
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["point_tracking_status"] == "failed"
    assert "enqueue failed" in body["point_tracking_error"]


def test_point_tracking_request_invalidates_stabilized_variant(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_enqueue(*_args: Any, **_kwargs: Any) -> str:
        return "point-track-1"

    monkeypatch.setattr(assets_router, "enqueue_point_tracking", fake_enqueue)
    client = TestClient(app)

    resp = client.patch(
        "/assets/1/tracking-target",
        json={"mode": "point", "point": {"norm_x": 0.5, "norm_y": 0.5, "frame_ms": 0}},
    )

    assert resp.status_code == 202, resp.text
    detail = client.get("/assets/1")
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["active_asset_variant"] == "raw"
    assert body["stabilized_path"] is None
    assert body["stabilization_status"] == "not_started"
    assert "tracking target changed" in body["stabilization_error"]


def test_tracking_detail_returns_custom_roi_origin(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_track_custom_roi(*_args: Any, **kwargs: Any) -> dict[str, Any]:
        return {
            "src_w": 3840,
            "src_h": 2160,
            "fps": 5.0,
            "init_t_ms": int(kwargs["init_t_ms"]),
            "init": {
                "x": int(kwargs["init_x"]),
                "y": int(kwargs["init_y"]),
                "w": int(kwargs["init_w"]),
                "h": int(kwargs["init_h"]),
            },
            "frames": [
                {
                    "t_ms": int(kwargs["init_t_ms"]),
                    "x": int(kwargs["init_x"]),
                    "y": int(kwargs["init_y"]),
                    "w": int(kwargs["init_w"]),
                    "h": int(kwargs["init_h"]),
                    "conf": 1.0,
                }
            ],
            "sampled_frames": 1,
        }

    monkeypatch.setattr(assets_router.object_tracking, "track_custom_roi", fake_track_custom_roi)
    monkeypatch.setattr(
        assets_router,
        "enqueue_asset_stabilization",
        lambda *_args, **_kwargs: "tracking-stabilize-1",
    )
    client = TestClient(app)

    resp = client.patch(
        "/assets/1/tracking-target",
        json={"mode": "custom", "custom_roi": {"x": 120, "y": 240, "w": 640, "h": 360}},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["custom_roi_origin"] == {
        "x": 120,
        "y": 240,
        "w": 640,
        "h": 360,
        "source_t_ms": 0,
    }

    detail = client.get("/assets/1/tracking")
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["tracked_object_index"] == -1
    assert body["has_custom_roi"] is True
    assert body["custom_roi_origin"] == resp.json()["custom_roi_origin"]

    asset_detail = client.get("/assets/1")
    assert asset_detail.status_code == 200, asset_detail.text
    asset_body = asset_detail.json()
    assert asset_body["active_asset_variant"] == "raw"
    assert asset_body["stabilization_status"] == "pending"
    assert asset_body["stabilized_path"].endswith("1_foo.stab.mp4")


def test_tracking_target_stabilization_enqueue_failure_marks_failed(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_enqueue(*_args: Any, **_kwargs: Any) -> str:
        raise RuntimeError("redis down")

    monkeypatch.setattr(assets_router, "enqueue_asset_stabilization", fail_enqueue)
    client = TestClient(app)

    resp = client.patch(
        "/assets/1/tracking-target",
        json={"mode": "object", "object_index": 0},
    )

    assert resp.status_code == 200, resp.text
    detail = client.get("/assets/1")
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["active_asset_variant"] == "raw"
    assert body["stabilization_status"] == "failed"
    assert "redis down" in body["stabilization_error"]


def test_custom_tracking_target_uses_raw_after_invalidating_stabilized_variant(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import asyncio

    old_derivative = tmp_path / "old.stab.mp4"
    old_derivative.write_bytes(b"old stabilized")

    async def set_stabilized_active() -> None:
        async with app.state.test_session_maker() as session:
            asset = await session.get(Asset, 1)
            assert asset is not None
            asset.active_asset_variant = "stabilized"
            asset.stabilized_path = str(old_derivative)
            asset.stabilization_status = "done"
            await session.commit()

    asyncio.run(set_stabilized_active())
    tracked_sources: list[Path] = []

    def fake_track_custom_roi(media_path: Path, *_args: Any, **kwargs: Any) -> dict[str, Any]:
        tracked_sources.append(media_path)
        return {
            "src_w": 3840,
            "src_h": 2160,
            "fps": 5.0,
            "init_t_ms": int(kwargs["init_t_ms"]),
            "init": {
                "x": int(kwargs["init_x"]),
                "y": int(kwargs["init_y"]),
                "w": int(kwargs["init_w"]),
                "h": int(kwargs["init_h"]),
            },
            "frames": [
                {
                    "t_ms": int(kwargs["init_t_ms"]),
                    "x": int(kwargs["init_x"]),
                    "y": int(kwargs["init_y"]),
                    "w": int(kwargs["init_w"]),
                    "h": int(kwargs["init_h"]),
                    "conf": 1.0,
                }
            ],
            "sampled_frames": 1,
        }

    monkeypatch.setattr(assets_router.object_tracking, "track_custom_roi", fake_track_custom_roi)
    monkeypatch.setattr(
        assets_router,
        "enqueue_asset_stabilization",
        lambda *_args, **_kwargs: "tracking-stabilize-1",
    )
    client = TestClient(app)

    resp = client.patch(
        "/assets/1/tracking-target",
        json={"mode": "custom", "custom_roi": {"x": 120, "y": 240, "w": 640, "h": 360}},
    )

    assert resp.status_code == 200, resp.text
    assert tracked_sources == [Path("/mnt/assets/foo.mp4")]
    assert not old_derivative.exists()


def test_custom_tracking_failure_keeps_previous_stabilized_file(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import asyncio

    old_derivative = tmp_path / "old.stab.mp4"
    old_derivative.write_bytes(b"old stabilized")

    async def set_stabilized_active() -> None:
        async with app.state.test_session_maker() as session:
            asset = await session.get(Asset, 1)
            assert asset is not None
            asset.active_asset_variant = "stabilized"
            asset.stabilized_path = str(old_derivative)
            asset.stabilization_status = "done"
            await session.commit()

    asyncio.run(set_stabilized_active())

    def fail_track_custom_roi(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise assets_router.object_tracking.TrackingError("boom")

    monkeypatch.setattr(assets_router.object_tracking, "track_custom_roi", fail_track_custom_roi)
    client = TestClient(app)

    resp = client.patch(
        "/assets/1/tracking-target",
        json={"mode": "custom", "custom_roi": {"x": 120, "y": 240, "w": 640, "h": 360}},
    )

    assert resp.status_code == 500, resp.text
    assert old_derivative.exists()

    async def read_asset_state() -> tuple[str | None, str | None, str | None]:
        async with app.state.test_session_maker() as session:
            asset = await session.get(Asset, 1)
            assert asset is not None
            return asset.active_asset_variant, asset.stabilized_path, asset.stabilization_status

    active_variant, stabilized_path, stabilization_status = asyncio.run(read_asset_state())
    assert active_variant == "stabilized"
    assert stabilized_path == str(old_derivative)
    assert stabilization_status == "done"


def test_bgm_enqueue_failure_marks_job_failed(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_enqueue(*_args: Any, **_kwargs: Any) -> str:
        raise RuntimeError("redis down")

    monkeypatch.setattr(music_router, "enqueue_bgm_generation", fail_enqueue)
    client = TestClient(app)
    resp = client.post("/projects/1/generate-bgm", json={"prompt": "calm luxury beat"})
    assert resp.status_code == 502, resp.text

    status_resp = client.get("/projects/1/bgm-status")
    assert status_resp.status_code == 200, status_resp.text
    body = status_resp.json()
    assert body["status"] == "failed:enqueue"
    assert body["error"] == "redis down"


def test_export_draft_accepts_landscape(app: FastAPI) -> None:
    """v0.29.0 — 16:9 is the new horizontal export aspect; the
    artifact filename uses the slash-replaced ``16x9`` token."""
    client = TestClient(app)
    resp = client.post("/drafts/1/export", json={"aspect": "16:9", "height": 1080})
    assert resp.status_code == 200, resp.text
    body: dict[str, Any] = resp.json()
    assert body["aspect"] == "16:9"
    assert body["output_filename"] == "v1-16x9-1080p.mp4"


def test_export_draft_rejects_dropped_4_5(app: FastAPI) -> None:
    """v0.29.0 — 4:5 was dropped from the literal; pydantic 422s before
    we even hit the endpoint body."""
    client = TestClient(app)
    resp = client.post("/drafts/1/export", json={"aspect": "4:5", "height": 1080})
    assert resp.status_code == 422


def test_patch_project_crop_region_round_trip(app: FastAPI) -> None:
    """v0.29.0 — set + clear the static-crop anchor."""
    client = TestClient(app)
    # Default: no override.
    detail = client.get("/projects/1").json()
    assert detail.get("crop_region") in (None, {"x_norm": 0.5, "y_norm": 0.5})

    # Set a top-anchor (vertical crop, source above-target case).
    set_resp = client.patch(
        "/projects/1/crop-region",
        json={"x_norm": 0.5, "y_norm": 0.0},
    )
    assert set_resp.status_code == 200, set_resp.text
    body = set_resp.json()
    assert body["crop_region"] == {"x_norm": 0.5, "y_norm": 0.0}

    # Clear with both null.
    clear_resp = client.patch(
        "/projects/1/crop-region",
        json={"x_norm": None, "y_norm": None},
    )
    assert clear_resp.status_code == 200, clear_resp.text
    assert clear_resp.json()["crop_region"] is None


def test_patch_project_smart_camera_round_trip(app: FastAPI) -> None:
    """v0.30.0 — flip the persistent AI Smart Camera toggle."""
    client = TestClient(app)
    # Default off — opt-in feature.
    detail = client.get("/projects/1").json()
    assert detail["smart_camera_enabled"] is False

    on_resp = client.patch("/projects/1/smart-camera", json={"enabled": True})
    assert on_resp.status_code == 200, on_resp.text
    assert on_resp.json()["smart_camera_enabled"] is True

    off_resp = client.patch("/projects/1/smart-camera", json={"enabled": False})
    assert off_resp.status_code == 200, off_resp.text
    assert off_resp.json()["smart_camera_enabled"] is False


def test_patch_project_crop_region_rejects_partial_payload(app: FastAPI) -> None:
    """v0.29.0 — mixed null + value must 400; storing a half-anchor
    would leave the renderer guessing."""
    client = TestClient(app)
    resp = client.patch(
        "/projects/1/crop-region",
        json={"x_norm": 0.5, "y_norm": None},
    )
    assert resp.status_code == 400


def test_get_asset_with_tags_sorted(app: FastAPI) -> None:
    client = TestClient(app)
    resp = client.get("/assets/1")
    assert resp.status_code == 200
    body: dict[str, Any] = resp.json()
    confidences = [t["confidence"] for t in body["tags"]]
    assert confidences == sorted(confidences, reverse=True)
    assert confidences[0] == 0.95


def test_post_asset_stabilize_marks_pending_and_enqueues(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_enqueue(asset_id: int, *, force: bool = False) -> str:
        assert asset_id == 1
        assert force is True
        return "fake-stabilize-1"

    monkeypatch.setattr(assets_router, "enqueue_asset_stabilization", fake_enqueue)
    client = TestClient(app)

    resp = client.post("/assets/1/stabilize", json={"force": True})

    assert resp.status_code == 202, resp.text
    assert resp.json() == {
        "asset_id": 1,
        "job_id": "fake-stabilize-1",
        "stabilization_status": "pending",
    }
    detail = client.get("/assets/1")
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["active_asset_variant"] == "raw"
    assert body["stabilization_status"] == "pending"
    assert body["stabilized_path"].endswith("1_foo.stab.mp4")


def test_batch_stabilize_assets_skips_done_by_default(app: FastAPI) -> None:
    client = TestClient(app)

    resp = client.post("/projects/1/assets/stabilize", json={})

    assert resp.status_code == 202, resp.text
    assert resp.json() == {
        "project_id": 1,
        "enqueued_count": 0,
        "skipped_count": 1,
        "failed_count": 0,
        "results": [
            {
                "asset_id": 1,
                "status": "skipped",
                "job_id": None,
                "reason": "done",
            }
        ],
    }


def test_batch_stabilize_assets_force_enqueues_done(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[int, bool]] = []

    def fake_enqueue(asset_id: int, *, force: bool = False) -> str:
        calls.append((asset_id, force))
        return f"batch-stabilize-{asset_id}"

    monkeypatch.setattr(projects_router, "enqueue_asset_stabilization", fake_enqueue)
    client = TestClient(app)

    resp = client.post("/projects/1/assets/stabilize", json={"force": True})

    assert resp.status_code == 202, resp.text
    assert calls == [(1, True)]
    assert resp.json()["enqueued_count"] == 1
    assert resp.json()["results"] == [
        {
            "asset_id": 1,
            "status": "enqueued",
            "job_id": "batch-stabilize-1",
            "reason": None,
        }
    ]


def test_batch_stabilize_assets_marks_enqueue_failure_terminal(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_enqueue(asset_id: int, *, force: bool = False) -> str:
        assert asset_id == 1
        assert force is True
        raise RuntimeError("redis down")

    monkeypatch.setattr(projects_router, "enqueue_asset_stabilization", fail_enqueue)
    client = TestClient(app)

    resp = client.post("/projects/1/assets/stabilize", json={"force": True})

    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["enqueued_count"] == 0
    assert body["failed_count"] == 1
    assert body["results"][0]["status"] == "failed"
    assert "redis down" in body["results"][0]["reason"]
    detail = client.get("/assets/1")
    assert detail.status_code == 200, detail.text
    assert detail.json()["stabilization_status"] == "failed"
    assert "redis down" in detail.json()["stabilization_error"]


def test_batch_stabilize_assets_404_for_missing_project(app: FastAPI) -> None:
    client = TestClient(app)

    resp = client.post("/projects/999/assets/stabilize", json={})

    assert resp.status_code == 404


def test_patch_asset_variant_clears_variant_dependent_state(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_enqueue(asset_id: int, *, force: bool = False) -> str:
        assert asset_id == 1
        assert force is True
        return "fake-analysis-1"

    monkeypatch.setattr(assets_router.Path, "is_file", lambda _path: True)
    monkeypatch.setattr(assets_router, "enqueue_asset_analysis", fake_enqueue)
    client = TestClient(app)

    resp = client.patch("/assets/1/variant", json={"variant": "stabilized"})

    assert resp.status_code == 200, resp.text
    assert resp.json()["analysis_job_id"] == "fake-analysis-1"
    assert resp.json()["active_asset_variant"] == "stabilized"

    detail = client.get("/assets/1")
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["active_asset_variant"] == "stabilized"
    assert body["status"] == "pending"
    assert body["analysis_steps"] is None
    assert [tag["tag_type"] for tag in body["tags"]] == ["object", "visual"]

    tracking = client.get("/assets/1/tracking")
    assert tracking.status_code == 404


def test_patch_asset_variant_restores_saved_analysis_without_enqueue(
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[int, bool]] = []

    def fake_enqueue(asset_id: int, *, force: bool = False) -> str:
        calls.append((asset_id, force))
        return f"fake-analysis-{len(calls)}"

    monkeypatch.setattr(assets_router.Path, "is_file", lambda _path: True)
    monkeypatch.setattr(assets_router, "enqueue_asset_analysis", fake_enqueue)
    client = TestClient(app)

    first = client.patch("/assets/1/variant", json={"variant": "stabilized"})
    assert first.status_code == 200, first.text
    assert first.json()["restored_from_snapshot"] is False
    assert first.json()["analysis_job_id"] == "fake-analysis-1"

    second = client.patch("/assets/1/variant", json={"variant": "raw"})
    assert second.status_code == 200, second.text
    assert second.json()["restored_from_snapshot"] is True
    assert second.json()["analysis_job_id"] is None
    assert calls == [(1, True)]

    detail = client.get("/assets/1")
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["active_asset_variant"] == "raw"
    assert body["status"] == "analyzed"
    assert body["analysis_steps"] == {"scene": "done", "tracking": "done"}
    assert [tag["tag_name"] for tag in body["tags"]] == ["car", "logo_close_up", "showroom"]

    tracking = client.get("/assets/1/tracking")
    assert tracking.status_code == 200, tracking.text


def test_post_review_approve(app: FastAPI) -> None:
    client = TestClient(app)
    resp = client.post("/reviews", json={"draft_id": 1, "action": "approve"})
    assert resp.status_code == 201, resp.text
    body: dict[str, Any] = resp.json()
    assert body["action"] == "approve"
    assert body["reviewer"] == "alice"


def test_post_review_invalid_action(app: FastAPI) -> None:
    client = TestClient(app)
    resp = client.post("/reviews", json={"draft_id": 1, "action": "bogus"})
    assert resp.status_code == 422


def test_post_review_unknown_draft(app: FastAPI) -> None:
    client = TestClient(app)
    resp = client.post("/reviews", json={"draft_id": 9999, "action": "approve"})
    assert resp.status_code == 404
