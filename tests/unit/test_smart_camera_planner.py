"""Unit tests for services.smart_camera_planner — directive derivation
+ serialise round-trip. The Gemini call itself is HTTP and gets
exercised in the integration test rig; here we just lock the rule
set so it can't drift silently.
"""

from __future__ import annotations

from media_processor.services import smart_camera_planner as scp
from media_processor.services.edit_planner import (
    CutPlan,
    CutPlanSegment,
    deserialise_plan,
    serialise_plan,
)


def _r(t: float, x: float, y: float, w: float, h: float) -> scp.FocusRegion:
    return scp.FocusRegion(t_norm=t, x_norm=x, y_norm=y, w_norm=w, h_norm=h)


def test_derive_zoom_in_for_small_central_region() -> None:
    """A small, single-cluster focus → zoom_in toward the focus centre."""
    regions = [
        _r(0.0, 0.45, 0.45, 0.10, 0.10),
        _r(0.5, 0.46, 0.46, 0.10, 0.10),
        _r(1.0, 0.47, 0.47, 0.10, 0.10),
    ]
    directive = scp._derive_directive(regions, dominant_motion="static")
    assert directive is not None
    assert directive.kind == "zoom_in"
    # to_rect is the zoomed-in window — its size should be smaller than 1.
    assert directive.to_rect[2] < 1.0
    assert directive.to_rect[3] < 1.0


def test_derive_zoom_out_for_large_central_region() -> None:
    """A region covering most of the frame → zoom_out (start tight, end wide)."""
    regions = [
        _r(0.0, 0.10, 0.10, 0.80, 0.80),
        _r(1.0, 0.11, 0.11, 0.80, 0.80),
    ]
    directive = scp._derive_directive(regions, dominant_motion="static")
    assert directive is not None
    assert directive.kind == "zoom_out"
    # to_rect is the full source for zoom_out.
    assert directive.to_rect == (0.0, 0.0, 1.0, 1.0)


def test_derive_pan_for_two_disjoint_clusters() -> None:
    """Two clusters with IoU < 0.10 → pan from first → last (chronological)."""
    regions = [
        _r(0.0, 0.05, 0.45, 0.10, 0.10),  # left cluster, t early
        _r(0.0, 0.06, 0.46, 0.10, 0.10),
        _r(1.0, 0.85, 0.45, 0.10, 0.10),  # right cluster, t late
        _r(1.0, 0.84, 0.46, 0.10, 0.10),
    ]
    directive = scp._derive_directive(regions, dominant_motion="static")
    assert directive is not None
    assert directive.kind == "pan"
    # x of from_rect should be smaller than x of to_rect (left → right pan).
    assert directive.from_rect[0] < directive.to_rect[0]


def test_derive_does_not_pan_for_simultaneous_clusters() -> None:
    """Simultaneous saliency boxes are composition, not a camera move."""
    regions = [
        _r(0.0, 0.25, 0.25, 0.50, 0.50),
        _r(0.0, 0.43, 0.60, 0.14, 0.05),
        _r(0.33, 0.25, 0.25, 0.50, 0.50),
        _r(0.33, 0.43, 0.60, 0.14, 0.05),
        _r(0.66, 0.25, 0.25, 0.50, 0.50),
        _r(0.66, 0.43, 0.60, 0.14, 0.05),
        _r(1.0, 0.25, 0.25, 0.50, 0.50),
        _r(1.0, 0.43, 0.60, 0.14, 0.05),
    ]

    assert scp._derive_directive(regions, dominant_motion="static") is None


def test_derive_returns_none_when_mid_band_area() -> None:
    """A single cluster with mean_area between 0.25 and 0.60 → None."""
    regions = [_r(0.0, 0.25, 0.25, 0.50, 0.50)]
    assert scp._derive_directive(regions, dominant_motion="static") is None


def test_fallback_directives_cover_every_cut() -> None:
    """When Smart Camera is enabled but Vision has no move, every cut still moves."""
    plan = CutPlan(
        schema_version="m5.cut-plan.v1",
        target_duration_ms=3000,
        target_aspect_ratio="9:16",
        profile_name="universal",
        segments=(
            CutPlanSegment(0, 1, 0, 1000, "scripted", "", dominant_motion="static"),
            CutPlanSegment(1, 1, 1000, 2000, "scripted", "", dominant_motion="pan"),
            CutPlanSegment(2, 1, 2000, 3000, "scripted", "", dominant_motion="tilt"),
        ),
    )

    directives = scp.build_fallback_directives(plan, reason="unit test")

    assert sorted(directives) == [0, 1, 2]
    assert {directives[i]["kind"] for i in directives} <= {"zoom_in", "zoom_out", "pan"}
    assert all("fallback" in directives[i]["notes"] for i in directives)


def test_derive_returns_none_for_empty_regions() -> None:
    assert scp._derive_directive([], dominant_motion="static") is None


def test_derive_uses_exp_ease_for_dynamic_motion() -> None:
    """``pan`` / ``tilt`` / ``handheld`` motion gets the exp ease curve."""
    regions = [_r(0.0, 0.45, 0.45, 0.10, 0.10)]
    linear = scp._derive_directive(regions, dominant_motion="static")
    expy = scp._derive_directive(regions, dominant_motion="pan")
    assert linear is not None and linear.ease == "linear"
    assert expy is not None and expy.ease == "exp"


def test_serialise_directive_round_trip() -> None:
    """A directive should round-trip through the JSON shape used on
    ``CutPlanSegment.smart_camera_json`` without losing fields."""
    regions = [_r(0.0, 0.10, 0.10, 0.10, 0.10)]
    directive = scp._derive_directive(regions, dominant_motion="static")
    assert directive is not None
    blob = scp.serialise_directive(directive, focus_regions=regions)
    assert isinstance(blob, dict)
    assert blob["kind"] == "zoom_in"
    assert blob["schema_version"] == scp.SMART_CAMERA_SCHEMA_VERSION
    rebuilt = scp.deserialise_directive(blob)
    assert rebuilt is not None
    assert rebuilt.kind == directive.kind
    # Tolerate float-rounding from the serialiser.
    for a, b in zip(rebuilt.from_rect, directive.from_rect, strict=True):
        assert abs(a - b) < 1e-3


def test_serialise_directive_none_returns_none() -> None:
    assert scp.serialise_directive(None) is None


def test_deserialise_rejects_malformed_blob() -> None:
    """Bad inputs return None instead of raising — keeps the renderer
    fall-through to the static crop branch resilient against corrupt
    persisted plans."""
    assert scp.deserialise_directive(None) is None
    assert scp.deserialise_directive({}) is None
    assert scp.deserialise_directive({"kind": "wibble"}) is None
    assert scp.deserialise_directive({"kind": "zoom_in", "from_rect": [0, 0, 1]}) is None


def test_apply_smart_camera_to_plan_decorates_only_matching_segments() -> None:
    """Segments missing from the directives dict keep their existing
    ``smart_camera_json`` (which is None on a fresh plan)."""
    plan = CutPlan(
        schema_version="m5.cut-plan.v1",
        target_duration_ms=2000,
        target_aspect_ratio="9:16",
        profile_name="universal",
        segments=(
            CutPlanSegment(0, 1, 0, 1000, "scripted", ""),
            CutPlanSegment(1, 1, 1000, 2000, "improv", ""),
        ),
    )
    directive = {"kind": "zoom_in", "from_rect": [0, 0, 1, 1], "to_rect": [0.3, 0.3, 0.4, 0.4]}
    out = scp.apply_smart_camera_to_plan(plan, {0: directive})
    assert out.segments[0].smart_camera_json == directive
    assert out.segments[1].smart_camera_json is None


def test_cutplan_serialise_round_trip_preserves_smart_camera() -> None:
    """``serialise_plan`` / ``deserialise_plan`` on edit_planner must
    carry the v0.30.0 ``smart_camera_json`` field through unchanged."""
    blob = {
        "kind": "pan",
        "from_rect": [0.05, 0.05, 0.30, 0.30],
        "to_rect": [0.65, 0.65, 0.30, 0.30],
        "ease": "linear",
    }
    plan = CutPlan(
        schema_version="m5.cut-plan.v1",
        target_duration_ms=1000,
        target_aspect_ratio="9:16",
        profile_name="universal",
        segments=(
            CutPlanSegment(
                0,
                1,
                0,
                1000,
                "scripted",
                "",
                smart_camera_json=blob,
            ),
        ),
    )
    serial = serialise_plan(plan)
    rebuilt = deserialise_plan(serial)
    assert rebuilt.segments[0].smart_camera_json == blob


def test_legacy_plan_without_smart_camera_key_round_trips_to_none() -> None:
    """A plan blob with no ``smart_camera_json`` key (legacy stored
    plan) should deserialise with ``smart_camera_json=None`` so the
    renderer falls through to the historic static-aspect path."""
    legacy_blob = {
        "schema_version": "m5.cut-plan.v1",
        "target_duration_ms": 1000,
        "target_aspect_ratio": "9:16",
        "profile_name": "universal",
        "notes": "",
        "used_fallback": False,
        "fallback_reason": None,
        "segments": [
            {
                "order": 0,
                "asset_id": 1,
                "asset_start_ms": 0,
                "asset_end_ms": 1000,
                "source_kind": "scripted",
                "reason": "",
                "transition_to_next": "wipeleft",
                "dominant_emotion": "neutral",
                "dominant_motion": "static",
                "has_face": False,
            },
        ],
    }
    rebuilt = deserialise_plan(legacy_blob)
    assert rebuilt.segments[0].smart_camera_json is None
