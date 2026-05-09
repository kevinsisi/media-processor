"""Unit tests for edit orchestrator control-flow helpers."""

from __future__ import annotations

from media_processor.services.edit_orchestrator import _should_run_smart_camera_stage
from media_processor.services.edit_planner import CutPlan, CutPlanSegment


def _plan(*segments: CutPlanSegment) -> CutPlan:
    return CutPlan(
        schema_version="m5.cut-plan.v1",
        target_duration_ms=1_000,
        target_aspect_ratio="9:16",
        profile_name="universal",
        segments=segments,
    )


def _segment(*, smart_camera_json: dict[str, object] | None = None) -> CutPlanSegment:
    return CutPlanSegment(
        0,
        1,
        0,
        1_000,
        "improv",
        "",
        smart_camera_json=smart_camera_json,
    )


def test_smart_camera_runs_for_skip_plan_without_directives() -> None:
    plan = _plan(_segment())

    assert (
        _should_run_smart_camera_stage(
            smart_camera_active=True,
            skip_plan=True,
            plan=plan,
        )
        is True
    )


def test_smart_camera_skip_plan_reuses_existing_directives() -> None:
    plan = _plan(
        _segment(
            smart_camera_json={
                "kind": "zoom_in",
                "from_rect": [0.0, 0.0, 1.0, 1.0],
                "to_rect": [0.2, 0.2, 0.6, 0.6],
            }
        )
    )

    assert (
        _should_run_smart_camera_stage(
            smart_camera_active=True,
            skip_plan=True,
            plan=plan,
        )
        is False
    )


def test_smart_camera_inactive_never_runs() -> None:
    plan = _plan(_segment())

    assert (
        _should_run_smart_camera_stage(
            smart_camera_active=False,
            skip_plan=False,
            plan=plan,
        )
        is False
    )
