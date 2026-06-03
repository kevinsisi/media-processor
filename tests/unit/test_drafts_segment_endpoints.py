"""v0.20 — tests for the timeline-editor segment-level endpoints.

Covers:
  - POST   /drafts/{id}/segments/{seg_id}/split
  - PATCH  /drafts/{id}/segments/{seg_id}
  - DELETE /drafts/{id}/segments/{seg_id}

None of the three should auto-enqueue a render — that's the whole
point of decoupling them from the M7.1 reorder endpoint, so the
operator can iterate on trims/splits without firing a worker job
each time. We stub out ``enqueue_project_edit`` to assert it's never
called from these handlers; the existing PATCH /drafts/{id}/order
keeps its enqueue and is exercised separately to confirm the refactor
to the shared reflow helper didn't regress.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from media_processor.api.deps import get_session
from media_processor.api.main import app as production_app
from media_processor.api.routers import drafts as drafts_router
from media_processor.models import (
    Asset,
    Base,
    Draft,
    DraftSegment,
    Project,
)


def test_draft_url_includes_file_mtime_cache_buster(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(drafts_router.settings, "drafts_dir", str(tmp_path))
    draft_dir = tmp_path / "9"
    draft_dir.mkdir()
    video_path = draft_dir / "v1.mp4"
    video_path.write_bytes(b"fake mp4")

    url = drafts_router._draft_url(9, 1, "mp4")

    assert url.startswith("/api/media/drafts/9/v1.mp4?v=")
    assert url.endswith(str(video_path.stat().st_mtime_ns))


def test_draft_url_without_existing_file_omits_cache_buster(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(drafts_router.settings, "drafts_dir", str(tmp_path))

    assert drafts_router._draft_url(9, 1, "mp4") == "/api/media/drafts/9/v1.mp4"


def _make_engine_and_session() -> tuple[Any, async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _seed(session_maker: async_sessionmaker[AsyncSession]) -> None:
    """Seed: 1 project, 1 asset (10 s long), 1 draft with 3 contiguous
    segments of 2 s each on the timeline."""
    async with session_maker() as s:
        p = Project(
            name="timeline-test",
            client="test",
            profile_name="carsmeet-luxury",
            source_dir="/tmp/assets",
            status="ready_for_review",
        )
        s.add(p)
        await s.flush()

        a = Asset(
            project_id=p.id,
            file_path="/tmp/assets/foo.mp4",
            duration_ms=10_000,
            resolution="1920x1080",
            fps=30.0,
            codec="h264",
            sha256="a" * 64,
            status="analyzed",
        )
        s.add(a)
        await s.flush()

        d = Draft(
            project_id=p.id,
            profile_name="carsmeet-luxury",
            version=1,
            status="ready_for_review",
            ai_score=7.5,
            cut_plan_json={
                "segments": [
                    {
                        "order": 0,
                        "asset_id": a.id,
                        "asset_start_ms": 0,
                        "asset_end_ms": 2000,
                        "transition_to_next": "wipeleft",
                        "source_kind": "scripted",
                        "reason": "",
                    },
                    {
                        "order": 1,
                        "asset_id": a.id,
                        "asset_start_ms": 4000,
                        "asset_end_ms": 6000,
                        "transition_to_next": "fade",
                        "source_kind": "scripted",
                        "reason": "",
                    },
                    {
                        "order": 2,
                        "asset_id": a.id,
                        "asset_start_ms": 7000,
                        "asset_end_ms": 9000,
                        "transition_to_next": "dissolve",
                        "source_kind": "scripted",
                        "reason": "",
                    },
                ],
            },
        )
        s.add(d)
        await s.flush()

        s.add_all(
            [
                DraftSegment(
                    draft_id=d.id,
                    order=0,
                    asset_id=a.id,
                    asset_start_ms=0,
                    asset_end_ms=2000,
                    on_timeline_start_ms=0,
                    on_timeline_end_ms=2000,
                    transition="wipeleft",
                    source_kind="scripted",
                ),
                DraftSegment(
                    draft_id=d.id,
                    order=1,
                    asset_id=a.id,
                    asset_start_ms=4000,
                    asset_end_ms=6000,
                    on_timeline_start_ms=2000,
                    on_timeline_end_ms=4000,
                    transition="fade",
                    source_kind="scripted",
                ),
                DraftSegment(
                    draft_id=d.id,
                    order=2,
                    asset_id=a.id,
                    asset_start_ms=7000,
                    asset_end_ms=9000,
                    on_timeline_start_ms=4000,
                    on_timeline_end_ms=6000,
                    transition="dissolve",
                    source_kind="scripted",
                ),
            ]
        )
        await s.commit()


class _FakeQueue:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def __call__(self, project_id: int, **kwargs: Any) -> str:
        self.calls.append({"project_id": project_id, **kwargs})
        return "fake-job-id"


@pytest.fixture()
def app(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[FastAPI, _FakeQueue]]:
    import asyncio

    engine, session_maker = _make_engine_and_session()

    async def init() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await _seed(session_maker)

    asyncio.run(init())

    fake_q = _FakeQueue()
    monkeypatch.setattr(drafts_router, "enqueue_project_edit", fake_q)

    async def override_session() -> AsyncIterator[AsyncSession]:
        async with session_maker() as session:
            yield session

    production_app.dependency_overrides[get_session] = override_session
    try:
        yield production_app, fake_q
    finally:
        production_app.dependency_overrides.clear()
        asyncio.run(engine.dispose())


# ---------- split ----------


def test_split_at_midpoint_creates_two_halves(app: tuple[FastAPI, _FakeQueue]) -> None:
    fastapi_app, fake_q = app
    client = TestClient(fastapi_app)
    # Get current segments.
    seg_id = client.get("/drafts/1").json()["segments"][1]["id"]
    # Segment 1 spans on-timeline 2000..4000 → split at 3000.
    resp = client.post(f"/drafts/1/segments/{seg_id}/split", json={"at_ms": 3000})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    segs = body["segments"]
    assert len(segs) == 4

    # Halves of the original segment 1.
    left = next(s for s in segs if s["asset_start_ms"] == 4000 and s["asset_end_ms"] == 5000)
    right = next(s for s in segs if s["asset_start_ms"] == 5000 and s["asset_end_ms"] == 6000)
    assert left["on_timeline_start_ms"] == 2000
    assert left["on_timeline_end_ms"] == 3000
    assert right["on_timeline_start_ms"] == 3000
    assert right["on_timeline_end_ms"] == 4000

    # Both halves keep the original transition (Phase 1 design — see
    # proposal: hard-cut semantic deferred).
    assert left["transition"] == "fade"
    assert right["transition"] == "fade"

    # Orders are 0..3 contiguous; new row sits where order==2.
    orders = sorted(s["order"] for s in segs)
    assert orders == [0, 1, 2, 3]

    # No render was enqueued.
    assert fake_q.calls == []


def test_split_at_edge_rejected(app: tuple[FastAPI, _FakeQueue]) -> None:
    fastapi_app, _ = app
    client = TestClient(fastapi_app)
    seg_id = client.get("/drafts/1").json()["segments"][0]["id"]
    # Segment 0 spans on-timeline 0..2000 — splitting at 0 OR 2000
    # would make a zero-length half.
    for at in (0, 2000):
        resp = client.post(f"/drafts/1/segments/{seg_id}/split", json={"at_ms": at})
        assert resp.status_code == 400, f"at={at}: {resp.text}"


def test_split_unknown_segment_404(app: tuple[FastAPI, _FakeQueue]) -> None:
    fastapi_app, _ = app
    client = TestClient(fastapi_app)
    resp = client.post("/drafts/1/segments/9999/split", json={"at_ms": 100})
    assert resp.status_code == 404


# ---------- patch ----------


def test_patch_trim_end_reflows_subsequent(app: tuple[FastAPI, _FakeQueue]) -> None:
    fastapi_app, fake_q = app
    client = TestClient(fastapi_app)
    detail = client.get("/drafts/1").json()
    seg = detail["segments"][0]  # spans 0..2000 on timeline, asset 0..2000.
    # Trim end from 2000 → 1500 ms in asset-time.
    resp = client.patch(
        f"/drafts/1/segments/{seg['id']}",
        json={"asset_end_ms": 1500},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    by_order = sorted(body["segments"], key=lambda s: s["order"])
    # Segment 0 is now 1500 ms long → on-timeline 0..1500.
    assert by_order[0]["asset_end_ms"] == 1500
    assert by_order[0]["on_timeline_end_ms"] == 1500
    # Subsequent segments shifted left by 500 ms.
    assert by_order[1]["on_timeline_start_ms"] == 1500
    assert by_order[1]["on_timeline_end_ms"] == 3500
    assert by_order[2]["on_timeline_start_ms"] == 3500
    # No render enqueued.
    assert fake_q.calls == []


def test_patch_asset_end_past_duration_rejected(app: tuple[FastAPI, _FakeQueue]) -> None:
    fastapi_app, _ = app
    client = TestClient(fastapi_app)
    detail = client.get("/drafts/1").json()
    seg_id = detail["segments"][0]["id"]
    # Asset is 10_000 ms long; ask for 11_000.
    resp = client.patch(
        f"/drafts/1/segments/{seg_id}",
        json={"asset_end_ms": 11_000},
    )
    assert resp.status_code == 400


def test_patch_voice_volume_clamped(app: tuple[FastAPI, _FakeQueue]) -> None:
    fastapi_app, _ = app
    client = TestClient(fastapi_app)
    seg_id = client.get("/drafts/1").json()["segments"][0]["id"]
    # Pydantic schema clamps to ≤ 1.5 — anything higher is 422.
    resp = client.patch(
        f"/drafts/1/segments/{seg_id}",
        json={"voice_volume": 2.5},
    )
    assert resp.status_code == 422


def test_patch_unknown_transition_rejected(app: tuple[FastAPI, _FakeQueue]) -> None:
    fastapi_app, _ = app
    client = TestClient(fastapi_app)
    seg_id = client.get("/drafts/1").json()["segments"][0]["id"]
    resp = client.patch(
        f"/drafts/1/segments/{seg_id}",
        json={"transition": "nonexistent"},
    )
    assert resp.status_code == 400


# ---------- delete ----------


def test_delete_segment_reflows(app: tuple[FastAPI, _FakeQueue]) -> None:
    fastapi_app, fake_q = app
    client = TestClient(fastapi_app)
    detail = client.get("/drafts/1").json()
    seg_id = detail["segments"][1]["id"]  # the middle one (4000..6000 asset)
    resp = client.delete(f"/drafts/1/segments/{seg_id}")
    assert resp.status_code == 204, resp.text

    # Refetch — should now have 2 segments, contiguous from 0.
    after = client.get("/drafts/1").json()
    assert len(after["segments"]) == 2
    by_order = sorted(after["segments"], key=lambda s: s["order"])
    assert by_order[0]["on_timeline_start_ms"] == 0
    assert by_order[0]["on_timeline_end_ms"] == 2000
    assert by_order[1]["on_timeline_start_ms"] == 2000
    assert by_order[1]["on_timeline_end_ms"] == 4000
    assert by_order[1]["asset_start_ms"] == 7000

    # No render enqueued.
    assert fake_q.calls == []


def test_delete_last_segment_409(app: tuple[FastAPI, _FakeQueue]) -> None:
    fastapi_app, _ = app
    client = TestClient(fastapi_app)
    # Delete the first two so only one remains, then try to delete that one.
    detail = client.get("/drafts/1").json()
    s0 = detail["segments"][0]["id"]
    s1 = detail["segments"][1]["id"]
    assert client.delete(f"/drafts/1/segments/{s0}").status_code == 204
    assert client.delete(f"/drafts/1/segments/{s1}").status_code == 204

    after = client.get("/drafts/1").json()
    assert len(after["segments"]) == 1
    last = after["segments"][0]["id"]
    resp = client.delete(f"/drafts/1/segments/{last}")
    assert resp.status_code == 409


# ---------- reorder regression (post-refactor) ----------


def test_reorder_still_enqueues_render(app: tuple[FastAPI, _FakeQueue]) -> None:
    fastapi_app, fake_q = app
    client = TestClient(fastapi_app)
    detail = client.get("/drafts/1").json()
    ids = [s["id"] for s in sorted(detail["segments"], key=lambda s: s["order"])]
    # Reverse them.
    resp = client.patch("/drafts/1/order", json={"orders": list(reversed(ids))})
    assert resp.status_code == 200, resp.text

    # The reorder endpoint MUST still call enqueue_project_edit (existing
    # behaviour). The new segment-level endpoints must NOT.
    assert len(fake_q.calls) == 1
    assert fake_q.calls[0]["draft_id"] == 1
    assert fake_q.calls[0]["skip_plan"] is True


# ---------- v0.21.1 — render-flag preservation across skip-plan re-renders ----------


def test_reorder_preserves_render_flags_from_draft(
    app: tuple[FastAPI, _FakeQueue],
) -> None:
    """v0.21.1 regression — previously the reorder endpoint dropped the
    operator's render-flag choices on the floor (every flag silently
    defaulted to True), so toggling transitions off and then re-ordering
    the timeline brought dissolves back. The enqueue call must now
    carry the values stored on ``Draft.render_flags_json``.
    """
    import asyncio

    fastapi_app, fake_q = app
    # Stamp the seeded draft with an explicit "transitions off" snapshot
    # the way the trigger endpoint does for fresh v0.21.1 drafts. Direct
    # SQL update so we don't depend on the trigger-endpoint plumbing.
    from sqlalchemy import update

    overrides = fastapi_app.dependency_overrides[get_session]

    async def _stamp() -> None:
        async for s in overrides():
            await s.execute(
                update(Draft)
                .where(Draft.id == 1)
                .values(
                    render_flags_json={
                        "transitions": False,
                        "stabilize": False,
                        "subtitles": True,
                        "auto_reframe": True,
                    }
                )
            )
            await s.commit()
            return

    asyncio.run(_stamp())

    client = TestClient(fastapi_app)
    detail = client.get("/drafts/1").json()
    ids = [s["id"] for s in sorted(detail["segments"], key=lambda s: s["order"])]
    resp = client.patch("/drafts/1/order", json={"orders": list(reversed(ids))})
    assert resp.status_code == 200, resp.text

    assert len(fake_q.calls) == 1
    call = fake_q.calls[0]
    assert call["transitions"] is False
    assert call["stabilize"] is False
    assert call["subtitles"] is True
    assert call["auto_reframe"] is True


def test_reorder_uses_current_legacy_defaults_for_legacy_drafts(
    app: tuple[FastAPI, _FakeQueue],
) -> None:
    """Legacy drafts (``render_flags_json IS NULL``) use the current
    per-flag defaults. v0.24 changed transitions to default-off so a
    legacy re-render now matches what fresh projects show in the UI."""
    fastapi_app, fake_q = app
    # Seeded draft has render_flags_json = NULL (default).
    client = TestClient(fastapi_app)
    detail = client.get("/drafts/1").json()
    ids = [s["id"] for s in sorted(detail["segments"], key=lambda s: s["order"])]
    resp = client.patch("/drafts/1/order", json={"orders": list(reversed(ids))})
    assert resp.status_code == 200, resp.text

    call = fake_q.calls[0]
    assert call["transitions"] is False
    assert call["stabilize"] is True
    assert call["subtitles"] is True
    assert call["auto_reframe"] is True


def test_reorder_body_override_beats_legacy_null_snapshot(
    app: tuple[FastAPI, _FakeQueue],
) -> None:
    """v0.21.3 regression — when the FE sends a render_flags override
    on the reorder request, the resolved flags must come from the
    body rather than the (NULL) Draft snapshot. Without this, legacy
    drafts re-rendered after the operator turned transitions off
    silently re-enable them."""
    import asyncio

    fastapi_app, fake_q = app
    client = TestClient(fastapi_app)
    detail = client.get("/drafts/1").json()
    ids = [s["id"] for s in sorted(detail["segments"], key=lambda s: s["order"])]

    resp = client.patch(
        "/drafts/1/order",
        json={
            "orders": list(reversed(ids)),
            "render_flags": {
                "transitions": False,
                "stabilize": False,
            },
        },
    )
    assert resp.status_code == 200, resp.text

    call = fake_q.calls[0]
    # Override applied for the two fields the FE sent.
    assert call["transitions"] is False
    assert call["stabilize"] is False
    # Untouched fields fall back to current per-flag defaults since this
    # draft had no snapshot.
    assert call["subtitles"] is True
    assert call["auto_reframe"] is True

    # And the resolved flags are now backfilled onto the Draft so a
    # follow-up re-render without an override stays consistent.
    overrides = fastapi_app.dependency_overrides[get_session]

    async def _read_back() -> dict:
        async for s in overrides():
            d = (await s.execute(select(Draft).where(Draft.id == 1))).scalar_one()
            return dict(d.render_flags_json or {})
        raise AssertionError("session generator did not yield")

    flags = asyncio.run(_read_back())
    assert flags == {
        "transitions": False,
        "stabilize": False,
        "subtitles": True,
        "auto_reframe": True,
        # v0.30.0 — smart camera defaults False on legacy rows.
        "smart_camera": False,
        # v0.43.5 — legacy drafts settle onto the backward-compatible
        # default edit mode the first time we backfill render flags.
        "edit_mode": "standard",
        "story_narration": False,
        "story_narration_fallback": True,
    }


def test_rebuild_subtitles_no_body_keeps_legacy_compat(
    app: tuple[FastAPI, _FakeQueue],
) -> None:
    """Older clients post to /rebuild-subtitles with no body. The
    endpoint must still parse + run with current per-flag defaults,
    not 422 on missing body."""
    fastapi_app, fake_q = app
    # Seed a cut_plan_json on the draft so rebuild-subtitles doesn't
    # hit the "no plan" 409.
    client = TestClient(fastapi_app)
    resp = client.post("/drafts/1/rebuild-subtitles")
    assert resp.status_code == 200, resp.text
    call = fake_q.calls[0]
    assert call["subtitles_from_db"] is True
    assert call["transitions"] is False  # current legacy fallback
    assert call["subtitles"] is True


def test_rebuild_subtitles_body_override_beats_snapshot(
    app: tuple[FastAPI, _FakeQueue],
) -> None:
    """rebuild-subtitles accepts the same render_flags override as
    reorder so a subtitle re-burn after the operator turned
    transitions off doesn't quietly re-enable them on a legacy
    draft."""
    fastapi_app, fake_q = app
    client = TestClient(fastapi_app)
    resp = client.post(
        "/drafts/1/rebuild-subtitles",
        json={
            "render_flags": {
                "transitions": False,
                "subtitles": False,
            },
        },
    )
    assert resp.status_code == 200, resp.text
    call = fake_q.calls[0]
    assert call["transitions"] is False
    assert call["subtitles"] is False
    assert call["stabilize"] is True
    assert call["auto_reframe"] is True


def test_re_render_clears_stale_bgm_snapshot(
    app: tuple[FastAPI, _FakeQueue],
) -> None:
    """The settings re-render path must use the current Project.bgm_path.

    Draft.bgm_path is a render-time snapshot. If it is left populated,
    the worker intentionally keeps the old soundtrack and the operator's
    newly generated / selected BGM appears to do nothing.
    """
    import asyncio

    fastapi_app, fake_q = app
    overrides = fastapi_app.dependency_overrides[get_session]

    async def _stamp_bgm() -> None:
        async for s in overrides():
            draft = (await s.execute(select(Draft).where(Draft.id == 1))).scalar_one()
            project = (await s.execute(select(Project).where(Project.id == 1))).scalar_one()
            draft.bgm_path = "/app/media/bgm/old.wav"
            project.bgm_path = "/app/media/bgm/new.wav"
            await s.commit()
            return

    async def _read_draft_bgm() -> str | None:
        async for s in overrides():
            draft = (await s.execute(select(Draft).where(Draft.id == 1))).scalar_one()
            return draft.bgm_path
        raise AssertionError("session generator did not yield")

    asyncio.run(_stamp_bgm())

    client = TestClient(fastapi_app)
    resp = client.post("/drafts/1/re-render")
    assert resp.status_code == 200, resp.text
    assert fake_q.calls[0]["skip_plan"] is True
    assert asyncio.run(_read_draft_bgm()) is None


def test_re_render_rejects_unfinished_point_tracking(
    app: tuple[FastAPI, _FakeQueue],
) -> None:
    """Do not silently render static crop when the selected point track is not ready."""
    import asyncio

    fastapi_app, fake_q = app
    overrides = fastapi_app.dependency_overrides[get_session]

    async def _mark_point_pending() -> None:
        async for s in overrides():
            asset = (await s.execute(select(Asset).where(Asset.id == 1))).scalar_one()
            asset.tracked_object_index = -4
            asset.point_tracking_status = "pending"
            asset.point_tracking_json = None
            await s.commit()
            return

    asyncio.run(_mark_point_pending())

    client = TestClient(fastapi_app)
    resp = client.post("/drafts/1/re-render")
    assert resp.status_code == 409
    assert "point tracking pending" in resp.text
    assert fake_q.calls == []
