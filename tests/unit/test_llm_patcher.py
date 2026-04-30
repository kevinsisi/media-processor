"""Unit tests for the Stage 4.5 LLM patcher.

Tests use ``httpx.MockTransport`` to stub the Gemini REST endpoint — no real
network calls and no API keys required.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from media_processor.profile import load_profile
from media_processor.profile.loader import ProfileSpec
from media_processor.services.llm_patcher import (
    DraftSegmentSummary,
    GeminiKeyPoolConfig,
    LLMPatcher,
    LLMPatchError,
    LLMQuotaExhaustedError,
    LLMResponseInvalidError,
    ProfilePatch,
    apply_patch,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
CARSMEET = REPO_ROOT / "profiles" / "carsmeet-luxury.yaml"


@pytest.fixture()
def profile() -> ProfileSpec:
    return load_profile(CARSMEET)


@pytest.fixture()
def segments() -> list[DraftSegmentSummary]:
    return [
        DraftSegmentSummary(
            order=0,
            primary_tag="integral_hero_shot",
            score=0.92,
            on_timeline_start_ms=0,
            on_timeline_end_ms=2000,
        ),
        DraftSegmentSummary(
            order=1,
            primary_tag="logo_close_up",
            score=0.78,
            on_timeline_start_ms=2000,
            on_timeline_end_ms=3500,
        ),
    ]


def _gemini_response(text: str) -> dict[str, object]:
    return {
        "candidates": [
            {"content": {"parts": [{"text": text}]}},
        ]
    }


def _make_client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_request_patch_parses_strict_json(
    profile: ProfileSpec, segments: list[DraftSegmentSummary]
) -> None:
    response_json = json.dumps(
        {
            "tag_weight_deltas": {"integral_hero_shot": 0.5, "logo_close_up": -0.2},
            "required_segments_overrides": {"opening_hero": True, "hero_tag": "wheel_spin"},
        }
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert "key=alpha" in str(request.url)
        body = json.loads(request.content)
        # The system prompt and user prompt are both set
        assert "system_instruction" in body
        assert "tag_weights" in body["contents"][0]["parts"][0]["text"]
        return httpx.Response(200, json=_gemini_response(response_json))

    config = GeminiKeyPoolConfig(api_keys=("alpha",))
    async with _make_client(handler) as client:
        patcher = LLMPatcher(config, client=client)
        patch = await patcher.request_patch(
            profile=profile, segments=segments, user_feedback="多用車身特寫，開頭要 Hero shot"
        )

    assert patch.tag_weight_deltas == {"integral_hero_shot": 0.5, "logo_close_up": -0.2}
    assert patch.required_segments_overrides == {
        "opening_hero": True,
        "hero_tag": "wheel_spin",
    }


@pytest.mark.asyncio
async def test_request_patch_strips_markdown_fence(
    profile: ProfileSpec, segments: list[DraftSegmentSummary]
) -> None:
    fenced = "```json\n" + json.dumps({"tag_weight_deltas": {"car": 0.3}}) + "\n```"

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_gemini_response(fenced))

    config = GeminiKeyPoolConfig(api_keys=("k1",))
    async with _make_client(handler) as client:
        patch = await LLMPatcher(config, client=client).request_patch(
            profile=profile, segments=segments, user_feedback="x"
        )

    assert patch.tag_weight_deltas == {"car": 0.3}
    assert patch.required_segments_overrides == {}


@pytest.mark.asyncio
async def test_request_patch_rotates_on_quota_then_succeeds(
    profile: ProfileSpec, segments: list[DraftSegmentSummary]
) -> None:
    seen_keys: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "key=k1" in url:
            seen_keys.append("k1")
            return httpx.Response(429, text="quota exceeded")
        if "key=k2" in url:
            seen_keys.append("k2")
            return httpx.Response(
                200,
                json=_gemini_response(json.dumps({"tag_weight_deltas": {"car": 0.1}})),
            )
        raise AssertionError(f"unexpected url: {url}")

    config = GeminiKeyPoolConfig(api_keys=("k1", "k2"))
    async with _make_client(handler) as client:
        patch = await LLMPatcher(config, client=client).request_patch(
            profile=profile, segments=segments, user_feedback="x"
        )

    assert seen_keys == ["k1", "k2"]
    assert patch.tag_weight_deltas == {"car": 0.1}


@pytest.mark.asyncio
async def test_request_patch_all_keys_quota_exhausted(
    profile: ProfileSpec, segments: list[DraftSegmentSummary]
) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text="quota exceeded")

    config = GeminiKeyPoolConfig(api_keys=("k1", "k2"))
    async with _make_client(handler) as client:
        patcher = LLMPatcher(config, client=client)
        with pytest.raises(LLMQuotaExhaustedError):
            await patcher.request_patch(profile=profile, segments=segments, user_feedback="x")


@pytest.mark.asyncio
async def test_request_patch_invalid_json_raises(
    profile: ProfileSpec, segments: list[DraftSegmentSummary]
) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_gemini_response("not-json"))

    config = GeminiKeyPoolConfig(api_keys=("k1",))
    async with _make_client(handler) as client:
        with pytest.raises(LLMResponseInvalidError):
            await LLMPatcher(config, client=client).request_patch(
                profile=profile, segments=segments, user_feedback="x"
            )


@pytest.mark.asyncio
async def test_request_patch_rejects_non_object_overrides(
    profile: ProfileSpec, segments: list[DraftSegmentSummary]
) -> None:
    bad = json.dumps({"tag_weight_deltas": {}, "required_segments_overrides": "nope"})

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_gemini_response(bad))

    config = GeminiKeyPoolConfig(api_keys=("k1",))
    async with _make_client(handler) as client:
        with pytest.raises(LLMResponseInvalidError):
            await LLMPatcher(config, client=client).request_patch(
                profile=profile, segments=segments, user_feedback="x"
            )


@pytest.mark.asyncio
async def test_request_patch_rejects_non_bool_opening_hero(
    profile: ProfileSpec, segments: list[DraftSegmentSummary]
) -> None:
    bad = json.dumps({"required_segments_overrides": {"opening_hero": "yes"}})

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_gemini_response(bad))

    config = GeminiKeyPoolConfig(api_keys=("k1",))
    async with _make_client(handler) as client:
        with pytest.raises(LLMResponseInvalidError):
            await LLMPatcher(config, client=client).request_patch(
                profile=profile, segments=segments, user_feedback="x"
            )


@pytest.mark.asyncio
async def test_request_patch_4xx_other_than_quota_raises(
    profile: ProfileSpec, segments: list[DraftSegmentSummary]
) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text="bad request")

    config = GeminiKeyPoolConfig(api_keys=("k1",))
    async with _make_client(handler) as client:
        with pytest.raises(LLMPatchError):
            await LLMPatcher(config, client=client).request_patch(
                profile=profile, segments=segments, user_feedback="x"
            )


def test_empty_key_pool_rejected() -> None:
    with pytest.raises(LLMPatchError):
        LLMPatcher(GeminiKeyPoolConfig(api_keys=()))


def test_apply_patch_adds_deltas_and_clamps_to_zero(profile: ProfileSpec) -> None:
    patch = ProfilePatch(
        tag_weight_deltas={"logo_close_up": 0.5, "interior_leather": -100.0, "new_tag": 0.7},
        required_segments_overrides={"opening_hero": False},
    )
    patched = apply_patch(profile, patch)

    assert patched.tag_weights["logo_close_up"] == pytest.approx(
        profile.tag_weights["logo_close_up"] + 0.5
    )
    assert patched.tag_weights["interior_leather"] == 0.0  # clamped
    assert patched.tag_weights["new_tag"] == pytest.approx(0.7)
    assert patched.editing_rules.required_segments.opening_hero is False
    # Untouched fields preserved
    assert patched.editing_rules.required_segments.closing_hero is (
        profile.editing_rules.required_segments.closing_hero
    )
    assert patched.editing_rules.target_duration_ms == profile.editing_rules.target_duration_ms
    # On-disk YAML reference preserved
    assert patched.raw_yaml == profile.raw_yaml


def test_apply_patch_no_overrides_keeps_required_segments(profile: ProfileSpec) -> None:
    patch = ProfilePatch(tag_weight_deltas={"car": 0.1})
    patched = apply_patch(profile, patch)
    assert patched.editing_rules.required_segments == profile.editing_rules.required_segments
