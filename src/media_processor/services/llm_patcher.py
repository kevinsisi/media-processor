"""Stage 4.5 — LLM Patcher: turn user prompt feedback into a profile patch.

Spec: design doc §6.5. Given the current profile, draft segments, and a free-text
user feedback string, the LLM returns a JSON patch limited to ``tag_weights``
deltas and ``required_segments`` overrides. The patch is applied to a copy of
``ProfileSpec`` in memory; the on-disk YAML is never modified.

We do NOT take a hard dependency on the Anthropic SDK or google-generativeai.
Instead this module talks to Google's Gemini ``generativelanguage`` REST API
directly via ``httpx`` (already a project dependency) and rotates across a pool
of API keys on 429 / 5xx — the same key-pool concept used by ai-core.
"""

from __future__ import annotations

import json
import logging
import re
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import httpx

from media_processor.profile.loader import EditingRules, ProfileSpec, RequiredSegments

if TYPE_CHECKING:
    from media_processor.services.opencode_client import OpenCodeConfig

logger = logging.getLogger(__name__)


class LLMPatchError(RuntimeError):
    """Raised when the LLM patch call cannot produce a usable result."""


class LLMQuotaExhaustedError(LLMPatchError):
    """All keys in the pool returned 429 / quota errors."""


class LLMResponseInvalidError(LLMPatchError):
    """The model returned content we could not parse into a ProfilePatch."""


@dataclass(frozen=True)
class DraftSegmentSummary:
    """Compact view of a draft segment for the LLM prompt."""

    order: int
    primary_tag: str
    score: float
    on_timeline_start_ms: int
    on_timeline_end_ms: int


@dataclass(frozen=True)
class ProfilePatch:
    """The patch produced by Stage 4.5.

    ``tag_weight_deltas`` are additive — applied via ``new = max(0, old + delta)``.
    ``required_segments_overrides`` may set any subset of opening_hero /
    closing_hero / hero_tag; missing keys leave the existing value unchanged.
    """

    tag_weight_deltas: dict[str, float] = field(default_factory=dict)
    required_segments_overrides: dict[str, Any] = field(default_factory=dict)
    raw_response: str = ""


@dataclass(frozen=True)
class GeminiKeyPoolConfig:
    """Configuration for the Gemini REST client + multi-key pool.

    ``api_keys`` is rotated on 429 / 5xx — first success wins. An empty pool
    raises ``LLMPatchError`` immediately so callers can fall back to a non-LLM
    recut path (see spec §6.5 fallback).
    """

    api_keys: tuple[str, ...]
    model: str = "gemini-2.5-flash"
    timeout_s: float = 30.0
    base_url: str = "https://generativelanguage.googleapis.com/v1beta"


_SYSTEM_PROMPT = (
    "你是影片剪輯助手。使用者會提供當前 profile 摘要、目前草稿的片段清單，"
    "以及一段自然語言反饋。請輸出一段嚴格的 JSON（不要 Markdown code fence、"
    "不要解釋文字），形狀為：\n"
    "{\n"
    '  "tag_weight_deltas": { "<tag>": <float, 可正可負>, ... },\n'
    '  "required_segments_overrides": {\n'
    '    "opening_hero": <bool, optional>,\n'
    '    "closing_hero": <bool, optional>,\n'
    '    "hero_tag": <string, optional>\n'
    "  }\n"
    "}\n"
    "規則：\n"
    "- 只能調整 tag_weights 與 required_segments；不要回傳其他欄位。\n"
    "- 若無調整需求，對應欄位請給空物件 {}。\n"
    "- delta 通常落在 [-1.0, 1.0] 區間，避免大幅震盪。\n"
)


class LLMPatcher:
    """Drives a single Stage 4.5 patch request — OpenCode primary, Gemini fallback."""

    def __init__(
        self,
        config: GeminiKeyPoolConfig,
        *,
        opencode_config: "OpenCodeConfig | None" = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not config.api_keys and opencode_config is None:
            raise LLMPatchError("no API keys or OpenCode servers configured for LLM patcher")
        self._config = config
        self._opencode_config = opencode_config
        self._client = client

    async def request_patch(
        self,
        *,
        profile: ProfileSpec,
        segments: list[DraftSegmentSummary],
        user_feedback: str,
    ) -> ProfilePatch:
        prompt = _build_user_prompt(profile, segments, user_feedback)

        # OpenCode primary
        if self._opencode_config is not None:
            from media_processor.services.opencode_client import call_opencode_text

            for server_url in self._opencode_config.servers:
                text = await call_opencode_text(
                    prompt=prompt,
                    system_prompt=_SYSTEM_PROMPT,
                    server_url=server_url,
                    password=self._opencode_config.password,
                    model=self._opencode_config.model,
                    variant=self._opencode_config.variant,
                    timeout_s=self._opencode_config.timeout_s,
                )
                if text:
                    try:
                        return _parse_patch_from_text(text)
                    except LLMResponseInvalidError as exc:
                        logger.warning("opencode patch response invalid; trying next: %s", exc)
            logger.warning("all OpenCode servers failed for patch; falling back to Gemini")

        # Gemini fallback
        if not self._config.api_keys:
            raise LLMQuotaExhaustedError("no Gemini keys and all OpenCode servers failed")

        body = {
            "system_instruction": {"parts": [{"text": _SYSTEM_PROMPT}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.2,
                "responseMimeType": "application/json",
            },
        }

        last_exc: Exception | None = None
        async with self._http_client() as client:
            for key in self._config.api_keys:
                url = (
                    f"{self._config.base_url}/models/{self._config.model}:generateContent?key={key}"
                )
                try:
                    response = await client.post(url, json=body)
                except httpx.HTTPError as exc:
                    last_exc = exc
                    logger.warning("LLM key transport error; rotating key: %s", exc)
                    continue

                if response.status_code == 429 or 500 <= response.status_code < 600:
                    logger.warning(
                        "LLM key returned %s; rotating to next key in pool",
                        response.status_code,
                    )
                    last_exc = LLMQuotaExhaustedError(
                        f"status={response.status_code} body={response.text[:200]}"
                    )
                    continue

                if response.status_code >= 400:
                    raise LLMPatchError(
                        f"LLM call failed: status={response.status_code} body={response.text[:200]}"
                    )

                return _parse_response(response.json())

        if isinstance(last_exc, LLMQuotaExhaustedError):
            raise last_exc
        raise LLMQuotaExhaustedError(
            f"all {len(self._config.api_keys)} keys exhausted; last_error={last_exc!r}"
        )

    def _http_client(self) -> AbstractAsyncContextManager[httpx.AsyncClient]:
        if self._client is not None:
            return _NonClosingClient(self._client)
        return httpx.AsyncClient(timeout=self._config.timeout_s)


class _NonClosingClient(AbstractAsyncContextManager[httpx.AsyncClient]):
    """Adapter so caller-owned ``AsyncClient`` instances are not closed by us.

    ``async with`` on the adapter is a no-op for close — the test owns the
    transport and may want to re-use the client across multiple calls.
    """

    def __init__(self, inner: httpx.AsyncClient) -> None:
        self._inner = inner

    async def __aenter__(self) -> httpx.AsyncClient:
        return self._inner

    async def __aexit__(self, *_: object) -> None:
        return None


def _build_user_prompt(
    profile: ProfileSpec,
    segments: list[DraftSegmentSummary],
    user_feedback: str,
) -> str:
    rs = profile.editing_rules.required_segments
    profile_summary = {
        "name": profile.name,
        "tag_weights": profile.tag_weights,
        "editing_rules": {
            "target_duration_ms": profile.editing_rules.target_duration_ms,
            "min_cuts": profile.editing_rules.min_cuts,
            "max_cuts": profile.editing_rules.max_cuts,
            "required_segments": {
                "opening_hero": rs.opening_hero,
                "closing_hero": rs.closing_hero,
                "hero_tag": rs.hero_tag,
            },
        },
    }
    segments_summary = [
        {
            "order": s.order,
            "primary_tag": s.primary_tag,
            "score": round(s.score, 3),
            "duration_ms": s.on_timeline_end_ms - s.on_timeline_start_ms,
        }
        for s in segments
    ]
    return (
        "當前 profile 摘要 (JSON):\n"
        + json.dumps(profile_summary, ensure_ascii=False, indent=2)
        + "\n\n當前草稿片段 (JSON):\n"
        + json.dumps(segments_summary, ensure_ascii=False, indent=2)
        + f"\n\n使用者反饋：{user_feedback.strip()}\n"
    )


def _parse_patch_from_text(text: str) -> ProfilePatch:
    cleaned = _strip_code_fence(text)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise LLMResponseInvalidError(
            f"model output is not JSON: {exc}; text={text[:200]}"
        ) from exc

    if not isinstance(data, dict):
        raise LLMResponseInvalidError(
            f"model output JSON must be an object, got {type(data).__name__}"
        )

    deltas_raw = data.get("tag_weight_deltas", {}) or {}
    if not isinstance(deltas_raw, dict):
        raise LLMResponseInvalidError("tag_weight_deltas must be an object")
    deltas: dict[str, float] = {}
    for tag, value in deltas_raw.items():
        if not isinstance(tag, str):
            raise LLMResponseInvalidError("tag_weight_deltas keys must be strings")
        if not isinstance(value, int | float) or isinstance(value, bool):
            raise LLMResponseInvalidError(f"tag_weight_deltas['{tag}'] must be a number")
        deltas[tag] = float(value)

    overrides_raw = data.get("required_segments_overrides", {}) or {}
    if not isinstance(overrides_raw, dict):
        raise LLMResponseInvalidError("required_segments_overrides must be an object")
    overrides: dict[str, Any] = {}
    for key in ("opening_hero", "closing_hero"):
        if key in overrides_raw:
            v = overrides_raw[key]
            if not isinstance(v, bool):
                raise LLMResponseInvalidError(f"required_segments_overrides.{key} must be bool")
            overrides[key] = v
    if "hero_tag" in overrides_raw:
        v = overrides_raw["hero_tag"]
        if not isinstance(v, str) or not v:
            raise LLMResponseInvalidError(
                "required_segments_overrides.hero_tag must be a non-empty string"
            )
        overrides["hero_tag"] = v

    return ProfilePatch(
        tag_weight_deltas=deltas,
        required_segments_overrides=overrides,
        raw_response=text,
    )


def _parse_response(payload: dict[str, Any]) -> ProfilePatch:
    text = _extract_text(payload)
    return _parse_patch_from_text(text)


def _extract_text(payload: dict[str, Any]) -> str:
    candidates = payload.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise LLMResponseInvalidError("LLM payload missing 'candidates'")
    parts = candidates[0].get("content", {}).get("parts", [])
    if not isinstance(parts, list) or not parts:
        raise LLMResponseInvalidError("LLM payload candidate missing content.parts")
    text = parts[0].get("text")
    if not isinstance(text, str) or not text.strip():
        raise LLMResponseInvalidError("LLM payload candidate.text is empty")
    return text


_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)


def _strip_code_fence(text: str) -> str:
    match = _FENCE_RE.match(text)
    if match:
        return match.group(1)
    return text


def apply_patch(profile: ProfileSpec, patch: ProfilePatch) -> ProfileSpec:
    """Return a new ``ProfileSpec`` with the patch applied (in-memory only).

    Negative effective weights are clamped to 0. Unknown tags in the patch are
    accepted — they extend ``tag_weights`` so a future re-cut can pick them up.
    """
    new_weights = dict(profile.tag_weights)
    for tag, delta in patch.tag_weight_deltas.items():
        merged = new_weights.get(tag, 0.0) + delta
        new_weights[tag] = max(0.0, merged)

    rs = profile.editing_rules.required_segments
    overrides = patch.required_segments_overrides
    new_required = RequiredSegments(
        opening_hero=bool(overrides.get("opening_hero", rs.opening_hero)),
        closing_hero=bool(overrides.get("closing_hero", rs.closing_hero)),
        hero_tag=str(overrides.get("hero_tag", rs.hero_tag)),
    )

    new_editing = EditingRules(
        target_duration_ms=profile.editing_rules.target_duration_ms,
        min_cuts=profile.editing_rules.min_cuts,
        max_cuts=profile.editing_rules.max_cuts,
        diversity_penalty_same_tag=profile.editing_rules.diversity_penalty_same_tag,
        required_segments=new_required,
    )

    return ProfileSpec(
        name=profile.name,
        description=profile.description,
        tag_weights=new_weights,
        filters=profile.filters,
        editing_rules=new_editing,
        reframe=profile.reframe,
        captions=profile.captions,
        face_blur=profile.face_blur,
        raw_yaml=profile.raw_yaml,
    )
