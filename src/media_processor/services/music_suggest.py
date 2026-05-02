"""v0.15 — Gemini-driven music description for AI BGM generation.

Reads the project's analysed assets (emotion + scene + motion tags +
script body) and asks Gemini for a 50–100 character zh-Hant prompt
that the operator can hand-tweak before feeding to MusicGen. The prompt
covers style, mood, instrumentation, tempo so MusicGen has enough
specificity to produce something usable.

Pure-Python — only depends on httpx + the project's existing key pool.
Returns a string; UI displays it in a textarea so the user can edit
before clicking 生成配樂.
"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from media_processor.models import Asset, Project, Script

logger = logging.getLogger(__name__)


_GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"

# How many tags of each kind to surface in the prompt — enough to give
# Gemini a flavour without bloating the request.
TAG_TOP_K: int = 6
SCRIPT_EXCERPT_CHARS: int = 400
PROMPT_TIMEOUT_S: float = 20.0
FALLBACK_DESCRIPTION: str = "輕快、溫暖的 lo-fi 配樂，鋼琴搭配電子節拍，60-80 BPM，適合一般生活短片。"


class MusicSuggestError(RuntimeError):
    """Generic suggestion failure (no keys, all 429, malformed JSON)."""


class MusicSuggestQuotaError(MusicSuggestError):
    """All API keys returned 429 / 5xx."""


def _summarise_tags(assets: list[Asset], tag_type: str) -> str:
    """Top-K tag names by frequency, formatted ``name×count``.

    For ``emotion`` we skip the ``dominant`` sentinel row (it stashes
    the verdict string in ``time_ranges_ms``, not a real tag) and
    instead count occurrences of the per-class spans.
    """
    counter: Counter[str] = Counter()
    for asset in assets:
        for tag in asset.tags:
            if tag.tag_type != tag_type:
                continue
            if tag_type == "emotion" and tag.tag_name == "dominant":
                continue
            counter[tag.tag_name] += 1
    if not counter:
        return "（無）"
    pairs = counter.most_common(TAG_TOP_K)
    return ", ".join(f"{name}×{count}" for name, count in pairs)


def _summarise_emotions(assets: list[Asset]) -> str:
    """Tally the per-asset dominant emotion verdict."""
    counter: Counter[str] = Counter()
    for asset in assets:
        for tag in asset.tags:
            if tag.tag_type == "emotion" and tag.tag_name == "dominant":
                stash = list(tag.time_ranges_ms or [])
                if stash and isinstance(stash[0], str):
                    counter[stash[0]] += 1
    if not counter:
        return "（無情緒分析）"
    pairs = counter.most_common(TAG_TOP_K)
    return ", ".join(f"{name}×{count}" for name, count in pairs)


def _excerpt_script(body: str) -> str:
    body = (body or "").strip()
    if not body:
        return "（無腳本）"
    if len(body) <= SCRIPT_EXCERPT_CHARS:
        return body
    return body[:SCRIPT_EXCERPT_CHARS] + "…"


_PROMPT_TEMPLATE = (
    "你是影片配樂顧問。根據以下素材分析結果，建議一段適合做為背景音樂的「風格描述」，"
    "讓 AI 音樂生成器（MusicGen）能據此生成 30 秒的配樂。\n\n"
    "整支片的腳本：\n{script_excerpt}\n\n"
    "畫面場景（出現次數）：{scene_summary}\n"
    "鏡頭運鏡：{motion_summary}\n"
    "人物情緒（按片段）：{emotion_summary}\n\n"
    "請輸出 50–100 字的繁體中文配樂描述，包含：\n"
    " 1. 整體曲風（lo-fi / 電影感 / 輕快流行 / 民謠 / 環境音 / 節奏感強烈 等）\n"
    " 2. 情緒氛圍（溫暖 / 緊張 / 懷舊 / 興奮 等）\n"
    " 3. 主要樂器（鋼琴 / 木吉他 / 電子合成 / 弦樂 等）\n"
    " 4. 大致 BPM 範圍\n\n"
    "嚴格輸出 JSON：\n"
    "{{\n"
    '  "description": "<50–100 字的繁體中文配樂描述>"\n'
    "}}"
)

_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)


def _strip_fence(text: str) -> str:
    m = _FENCE_RE.match(text)
    return m.group(1) if m else text


async def suggest(
    project_id: int,
    session: AsyncSession,
    *,
    api_keys: tuple[str, ...],
    model: str,
    timeout_s: float = PROMPT_TIMEOUT_S,
) -> str:
    """Compose a 50–100 char zh-Hant music description for ``project_id``.

    Reads project + assets + script in one async pass, summarises the
    tags, asks Gemini, parses ``{"description": ...}``. Returns the
    string; UI displays it pre-filled in a textarea so the user can
    tweak before generation. Raises ``MusicSuggestError`` on hard
    failures (no keys, all keys quota-exhausted, malformed JSON) — the
    api endpoint converts to a 503 / 502 with the canned
    ``FALLBACK_DESCRIPTION`` so the UI never sees an empty textarea.
    """
    if not api_keys:
        raise MusicSuggestError("no API keys configured for music suggestion")

    project = await session.get(Project, project_id)
    if project is None:
        raise MusicSuggestError(f"project {project_id} not found")

    assets = list(
        (
            await session.execute(
                select(Asset)
                .where(Asset.project_id == project_id)
                .options(selectinload(Asset.tags))
            )
        )
        .scalars()
        .all()
    )

    script_row = (
        await session.execute(select(Script).where(Script.project_id == project_id))
    ).scalar_one_or_none()
    script_body = (script_row.body if script_row else "") or ""

    prompt = _PROMPT_TEMPLATE.format(
        script_excerpt=_excerpt_script(script_body),
        scene_summary=_summarise_tags(assets, "scene"),
        motion_summary=_summarise_tags(assets, "motion"),
        emotion_summary=_summarise_emotions(assets),
    )

    body: dict[str, Any] = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.6,
            "responseMimeType": "application/json",
        },
    }

    last_status = 0
    last_invalid: str | None = None
    async with httpx.AsyncClient(timeout=timeout_s) as client:
        for i, key in enumerate(api_keys):
            url = f"{_GEMINI_BASE_URL}/models/{model}:generateContent?key={key}"
            try:
                response = await client.post(url, json=body)
            except httpx.HTTPError as exc:
                logger.warning("music-suggest transport error on key %d: %r", i, exc)
                continue
            last_status = response.status_code
            if response.status_code == 429 or 500 <= response.status_code < 600:
                logger.warning(
                    "music-suggest status=%d on key %d; rotating",
                    response.status_code,
                    i,
                )
                continue
            if response.status_code >= 400:
                raise MusicSuggestError(
                    f"music-suggest call failed: status={response.status_code} "
                    f"body={response.text[:200]}"
                )
            try:
                payload = response.json()
                text = (
                    payload.get("candidates", [{}])[0]
                    .get("content", {})
                    .get("parts", [{}])[0]
                    .get("text", "")
                )
                data = json.loads(_strip_fence(text))
                description = str(data.get("description", "")).strip()
                if not description:
                    raise MusicSuggestError("empty description in response")
                return description
            except (json.JSONDecodeError, KeyError, IndexError) as exc:
                last_invalid = f"{exc}; text={text[:200]}"  # type: ignore[possibly-undefined]
                logger.warning("music-suggest JSON parse failed: %s", last_invalid)
                continue

    if last_invalid is not None:
        raise MusicSuggestError(f"all keys returned malformed JSON: {last_invalid}")
    raise MusicSuggestQuotaError(
        f"all {len(api_keys)} keys exhausted; last_status={last_status}"
    )


__all__ = [
    "FALLBACK_DESCRIPTION",
    "MusicSuggestError",
    "MusicSuggestQuotaError",
    "suggest",
]
