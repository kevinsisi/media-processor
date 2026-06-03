"""Narrato-style StoryScript generation and conversion services."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from media_processor.api.config import settings
from media_processor.models import (
    Asset,
    AssetTranscript,
    Project,
    Script,
    StoryScript,
)
from media_processor.services.edit_planner import (
    TRANSITION_DEFAULT,
    CutPlan,
    CutPlanSegment,
)
from media_processor.services.opencc_converter import to_traditional
from media_processor.services.opencode_client import call_opencode_text
from media_processor.services.settings_store import build_opencode_config, get_llm_api_keys
from media_processor.services.subtitles import SubtitleCue, render_srt

logger = logging.getLogger(__name__)

STORY_SCRIPT_SCHEMA_VERSION = "story-script.v1"
STORY_PLAN_SCHEMA_VERSION = "story.cut-plan.v1"
_GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)
_VALID_AUDIO_INTENTS = {"narration", "original", "narration_with_original"}
_SRT_TIME_RE = re.compile(
    r"(?P<start>\d{1,2}:\d{2}:\d{2}[,.]\d{3})\s*-->\s*(?P<end>\d{1,2}:\d{2}:\d{2}[,.]\d{3})"
)
_AUDIO_INTENT_ALIASES = {
    0: "narration",
    1: "original",
    2: "narration_with_original",
    "0": "narration",
    "1": "original",
    "2": "narration_with_original",
    "ost0": "narration",
    "ost1": "original",
    "ost2": "narration_with_original",
}


class StoryScriptError(RuntimeError):
    """Base class for StoryScript failures."""


class StoryScriptValidationError(StoryScriptError):
    """Raised when StoryScript JSON cannot be validated."""


class StoryScriptInputError(StoryScriptError):
    """Raised when no usable text inputs exist."""


@dataclass(frozen=True)
class StoryScriptItem:
    order: int
    asset_id: int
    source_start_ms: int
    source_end_ms: int
    picture: str
    narration: str
    audio_intent: str
    beat_type: str = "middle"
    hook_type: str | None = None
    reason: str = ""

    @property
    def duration_ms(self) -> int:
        return self.source_end_ms - self.source_start_ms

    def to_json(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "order": self.order,
            "asset_id": self.asset_id,
            "source_start_ms": self.source_start_ms,
            "source_end_ms": self.source_end_ms,
            "picture": self.picture,
            "narration": self.narration,
            "audio_intent": self.audio_intent,
            "beat_type": self.beat_type,
            "reason": self.reason,
        }
        if self.hook_type:
            payload["hook_type"] = self.hook_type
        return payload


@dataclass(frozen=True)
class StoryScriptDocument:
    project_id: int
    items: tuple[StoryScriptItem, ...]
    title: str = ""
    summary: str = ""
    schema_version: str = STORY_SCRIPT_SCHEMA_VERSION
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "project_id": self.project_id,
            "title": self.title,
            "summary": self.summary,
            "items": [item.to_json() for item in self.items],
        }


@dataclass(frozen=True)
class StoryInputSegment:
    asset_id: int
    asset_duration_ms: int
    start_ms: int
    end_ms: int
    text: str


@dataclass(frozen=True)
class StoryInputBundle:
    project_id: int
    project_name: str
    segments: tuple[StoryInputSegment, ...]
    asset_durations: dict[int, int]
    used_transcripts: bool
    used_script_text: bool = False
    used_visual_context: bool = False

    def metadata(self) -> dict[str, Any]:
        return {
            "source": "asset_transcripts" if self.used_transcripts else "project_script",
            "input_segment_count": len(self.segments),
            "asset_count": len(self.asset_durations),
            "used_script_text": self.used_script_text,
            "used_visual_context": self.used_visual_context,
        }


def _apply_traditional_chinese(payload: dict[str, Any]) -> None:
    """Convert all text fields in a raw StoryScript JSON payload to Traditional Chinese."""
    if "title" in payload:
        payload["title"] = to_traditional(str(payload.get("title") or ""))
    if "summary" in payload:
        payload["summary"] = to_traditional(str(payload.get("summary") or ""))
    for item in payload.get("items", []):
        if isinstance(item, dict):
            if "narration" in item:
                item["narration"] = to_traditional(str(item.get("narration") or ""))
            if "picture" in item:
                item["picture"] = to_traditional(str(item.get("picture") or ""))
            if "reason" in item:
                item["reason"] = to_traditional(str(item.get("reason") or ""))


def _strip_fence(text: str) -> str:
    cleaned = (text or "").strip()
    match = _FENCE_RE.match(cleaned)
    return match.group(1).strip() if match else cleaned


def _load_json_candidate(text: str) -> dict[str, Any] | None:
    try:
        data = json.loads(text)
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _repair_json_payload(text: str) -> dict[str, Any]:
    cleaned = _strip_fence(text)
    candidates = [cleaned]
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start >= 0 and end > start:
        candidates.append(cleaned[start : end + 1])
    for candidate in candidates:
        data = _load_json_candidate(candidate)
        if data is not None:
            return data
    raise StoryScriptValidationError("StoryScript JSON parse failed")


def _coerce_audio_intent(value: Any) -> str:
    if value in _AUDIO_INTENT_ALIASES:
        return _AUDIO_INTENT_ALIASES[value]
    if isinstance(value, str):
        key = value.strip().lower()
        if key in _AUDIO_INTENT_ALIASES:
            return _AUDIO_INTENT_ALIASES[key]
        if key in _VALID_AUDIO_INTENTS:
            return key
    raise StoryScriptValidationError(f"invalid audio_intent: {value!r}")


def _int_field(item: dict[str, Any], name: str) -> int:
    try:
        value = int(item[name])
    except Exception as exc:
        raise StoryScriptValidationError(f"missing/invalid {name}") from exc
    return value


def _parse_srt_time_ms(value: str) -> int:
    hh, mm, rest = value.replace(",", ".").split(":")
    ss, mmm = rest.split(".")
    return ((int(hh) * 60 + int(mm)) * 60 + int(ss)) * 1000 + int(mmm)


def _script_segments(
    script_body: str, *, asset_id: int, asset_duration_ms: int
) -> list[StoryInputSegment]:
    """Parse uploaded subtitle-like project text into story input segments."""
    body = script_body.strip()
    if not body:
        return []
    segments: list[StoryInputSegment] = []
    matches = list(_SRT_TIME_RE.finditer(body))
    for index, match in enumerate(matches):
        text_start = match.end()
        text_end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        text = " ".join(
            line.strip()
            for line in body[text_start:text_end].splitlines()
            if line.strip() and not line.strip().isdigit()
        )
        if not text:
            continue
        start_ms = max(0, min(_parse_srt_time_ms(match.group("start")), asset_duration_ms))
        end_ms = max(0, min(_parse_srt_time_ms(match.group("end")), asset_duration_ms))
        if end_ms > start_ms:
            segments.append(
                StoryInputSegment(
                    asset_id=asset_id,
                    asset_duration_ms=asset_duration_ms,
                    start_ms=start_ms,
                    end_ms=end_ms,
                    text=text,
                )
            )
    if segments:
        return segments

    lines = [line.strip() for line in body.splitlines() if line.strip()]
    if not lines:
        return []
    chunk_ms = max(1000, asset_duration_ms // max(1, len(lines)))
    for index, text in enumerate(lines):
        start_ms = min(asset_duration_ms, index * chunk_ms)
        end_ms = min(asset_duration_ms, start_ms + chunk_ms)
        if end_ms > start_ms:
            segments.append(
                StoryInputSegment(
                    asset_id=asset_id,
                    asset_duration_ms=asset_duration_ms,
                    start_ms=start_ms,
                    end_ms=end_ms,
                    text=text,
                )
            )
    return segments


def validate_story_script(
    payload: dict[str, Any],
    *,
    project_id: int,
    asset_durations: dict[int, int],
) -> StoryScriptDocument:
    raw_items = payload.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        raise StoryScriptValidationError("StoryScript must contain non-empty items")

    items: list[StoryScriptItem] = []
    seen_orders: set[int] = set()
    for index, raw in enumerate(raw_items, 1):
        if not isinstance(raw, dict):
            raise StoryScriptValidationError(f"item {index} is not an object")
        order = int(raw.get("order") or raw.get("_id") or index)
        if order in seen_orders:
            raise StoryScriptValidationError(f"duplicate order: {order}")
        seen_orders.add(order)
        asset_id = _int_field(raw, "asset_id")
        if asset_id not in asset_durations:
            raise StoryScriptValidationError(f"unknown asset_id: {asset_id}")
        start_ms = _int_field(raw, "source_start_ms")
        end_ms = _int_field(raw, "source_end_ms")
        asset_duration = int(asset_durations[asset_id])
        start_ms = max(0, min(start_ms, asset_duration))
        end_ms = max(0, min(end_ms, asset_duration))
        if end_ms <= start_ms:
            raise StoryScriptValidationError(f"invalid source range for item {order}")
        picture = str(raw.get("picture") or raw.get("visual") or "").strip()
        narration = str(raw.get("narration") or raw.get("text") or "").strip()
        if not picture:
            picture = "根據逐字稿選出的短影音片段"
        if not narration:
            raise StoryScriptValidationError(f"missing narration for item {order}")
        audio_intent = _coerce_audio_intent(raw.get("audio_intent", raw.get("OST", "narration")))
        items.append(
            StoryScriptItem(
                order=order,
                asset_id=asset_id,
                source_start_ms=start_ms,
                source_end_ms=end_ms,
                picture=picture,
                narration=narration,
                audio_intent=audio_intent,
                beat_type=str(raw.get("beat_type") or "middle").strip() or "middle",
                hook_type=(str(raw.get("hook_type")).strip() if raw.get("hook_type") else None),
                reason=str(raw.get("reason") or "").strip(),
            )
        )

    items.sort(key=lambda item: item.order)
    return StoryScriptDocument(
        project_id=project_id,
        title=str(payload.get("title") or "").strip(),
        summary=str(payload.get("summary") or "").strip(),
        items=tuple(items),
    )


async def gather_story_inputs(session: AsyncSession, project_id: int) -> StoryInputBundle:
    project = await session.get(Project, project_id)
    if project is None:
        raise StoryScriptInputError("project not found")
    assets = (
        (
            await session.execute(
                select(Asset).where(Asset.project_id == project_id).order_by(Asset.id)
            )
        )
        .scalars()
        .all()
    )
    asset_durations = {asset.id: int(asset.duration_ms) for asset in assets}
    if not asset_durations:
        raise StoryScriptInputError("project has no assets")
    tx_rows = (
        (
            await session.execute(
                select(AssetTranscript).where(AssetTranscript.asset_id.in_(asset_durations.keys()))
            )
        )
        .scalars()
        .all()
    )
    segments: list[StoryInputSegment] = []
    for tx in tx_rows:
        asset_duration = asset_durations.get(tx.asset_id, 0)
        for raw in list(tx.segments_json or []):
            try:
                start_ms = max(0, int(raw.get("start_ms", 0)))
                end_ms = min(asset_duration, int(raw.get("end_ms", 0)))
            except Exception:
                continue
            text = str(raw.get("text") or "").strip()
            if text and end_ms > start_ms:
                segments.append(
                    StoryInputSegment(
                        asset_id=tx.asset_id,
                        asset_duration_ms=asset_duration,
                        start_ms=start_ms,
                        end_ms=end_ms,
                        text=text,
                    )
                )
    segments.sort(key=lambda seg: (seg.asset_id, seg.start_ms))
    used_transcripts = bool(segments)
    used_script_text = False
    if not segments:
        script = (
            await session.execute(select(Script).where(Script.project_id == project_id).limit(1))
        ).scalar_one_or_none()
        first_asset = assets[0] if assets else None
        if script is not None and first_asset is not None:
            segments.extend(
                _script_segments(
                    script.body,
                    asset_id=first_asset.id,
                    asset_duration_ms=int(first_asset.duration_ms),
                )
            )
            used_script_text = bool(segments)
    if not segments:
        raise StoryScriptInputError("Story/Narrato mode needs transcript or subtitle text first")
    return StoryInputBundle(
        project_id=project_id,
        project_name=project.name,
        segments=tuple(segments),
        asset_durations=asset_durations,
        used_transcripts=used_transcripts,
        used_script_text=used_script_text,
    )


def build_story_prompt(bundle: StoryInputBundle, *, target_items: int = 8) -> str:
    lines = []
    for idx, seg in enumerate(bundle.segments[:120], 1):
        lines.append(f"{idx}. asset_id={seg.asset_id} [{seg.start_ms}-{seg.end_ms}ms] {seg.text}")
    transcript_block = "\n".join(lines)
    return f"""
你是一位頂級短影音解說編劇與剪輯企劃。請根據逐字稿產生 NarratoAI 風格的短影音 StoryScript。

目標：
- 產生 {target_items} 段以內的短影音腳本。
- 開頭 3 秒要有鉤子、衝突、疑問或反差。
- 每段都要有明確的畫面描述 picture 與繁體中文 narration。
- 保留重要原聲片段時使用 audio_intent="original"。
- 解說片段使用 audio_intent="narration"。
- 若需要旁白和原聲共存，使用 audio_intent="narration_with_original"。
- 每個 source_start_ms / source_end_ms 必須落在該 asset 的逐字稿時間範圍附近，不可重疊同一 asset 的同一段太多。

專案名稱：{bundle.project_name}

逐字稿片段：
{transcript_block}

只輸出嚴格 JSON，不要 markdown，不要解釋：
{{
  "schema_version": "{STORY_SCRIPT_SCHEMA_VERSION}",
  "title": "短影音標題",
  "summary": "一句話說明這支短影音主軸",
  "items": [
    {{
      "order": 1,
      "asset_id": <int>,
      "source_start_ms": <int>,
      "source_end_ms": <int>,
      "picture": "畫面描述",
      "narration": "繁體中文旁白或原聲說明",
      "audio_intent": "narration" | "original" | "narration_with_original",
      "beat_type": "hook" | "setup" | "conflict" | "payoff" | "closing",
      "hook_type": "conflict" | "question" | "contrast" | null,
      "reason": "為什麼這段適合短影音"
    }}
  ]
}}
""".strip()


def _heuristic_document(bundle: StoryInputBundle, *, target_items: int = 8) -> StoryScriptDocument:
    picked = list(bundle.segments[:target_items])
    items = []
    for idx, seg in enumerate(picked, 1):
        beat = "hook" if idx == 1 else "closing" if idx == len(picked) else "middle"
        audio_intent = "original" if idx % 4 == 0 else "narration"
        items.append(
            StoryScriptItem(
                order=idx,
                asset_id=seg.asset_id,
                source_start_ms=seg.start_ms,
                source_end_ms=seg.end_ms,
                picture="根據逐字稿挑選的關鍵短影音畫面",
                narration=seg.text,
                audio_intent=audio_intent,
                beat_type=beat,
                hook_type="question" if idx == 1 else None,
                reason="AI provider unavailable; used transcript-based fallback",
            )
        )
    return StoryScriptDocument(
        project_id=bundle.project_id,
        title=f"{bundle.project_name} 短影音腳本",
        summary="根據逐字稿自動整理的短影音 StoryScript。",
        items=tuple(items),
        metadata={**bundle.metadata(), "used_fallback": True},
    )


async def _call_gemini(prompt: str, *, api_keys: tuple[str, ...]) -> tuple[str | None, str | None]:
    async with httpx.AsyncClient(timeout=settings.llm_timeout_s) as client:
        for key in api_keys:
            url = f"{_GEMINI_BASE_URL}/models/{settings.llm_model}:generateContent?key={key}"
            body = {
                "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": 0.4, "responseMimeType": "application/json"},
            }
            try:
                resp = await client.post(url, json=body)
            except httpx.HTTPError as exc:
                logger.warning("story Gemini transport error: %s", exc)
                continue
            if resp.status_code == 429 or 500 <= resp.status_code < 600:
                continue
            if resp.status_code >= 400:
                raise StoryScriptError(
                    f"Story Gemini call failed: {resp.status_code} {resp.text[:200]}"
                )
            payload = resp.json()
            parts = payload.get("candidates", [{}])[0].get("content", {}).get("parts", [])
            text = next((p.get("text") for p in parts if isinstance(p, dict)), None)
            if isinstance(text, str) and text.strip():
                return text, settings.llm_model
    return None, None


async def generate_story_script(
    session: AsyncSession,
    project_id: int,
    *,
    target_items: int = 8,
) -> StoryScriptDocument:
    bundle = await gather_story_inputs(session, project_id)
    prompt = build_story_prompt(bundle, target_items=target_items)
    provider = "fallback"
    model: str | None = None
    raw_text: str | None = None

    opencode_config = await build_opencode_config(session)
    if opencode_config is not None:
        for server_url in opencode_config.servers:
            raw_text = await call_opencode_text(
                prompt=prompt,
                system_prompt="你是專業短影音腳本生成器。只輸出 JSON。",
                server_url=server_url,
                password=opencode_config.password,
                model=opencode_config.model,
                variant=opencode_config.variant,
                timeout_s=opencode_config.timeout_s,
            )
            if raw_text:
                provider = "opencode"
                model = opencode_config.model
                break

    if raw_text is None:
        api_keys = await get_llm_api_keys(session)
        if api_keys:
            raw_text, model = await _call_gemini(prompt, api_keys=api_keys)
            if raw_text:
                provider = "gemini"

    if raw_text:
        payload = _repair_json_payload(raw_text)
        _apply_traditional_chinese(payload)
        document = validate_story_script(
            payload, project_id=project_id, asset_durations=bundle.asset_durations
        )
        return StoryScriptDocument(
            project_id=document.project_id,
            title=to_traditional(document.title),
            summary=to_traditional(document.summary),
            items=document.items,
            metadata={
                **bundle.metadata(),
                "provider": provider,
                "model": model,
                "used_fallback": False,
            },
        )

    document = _heuristic_document(bundle, target_items=target_items)
    return StoryScriptDocument(
        project_id=document.project_id,
        title=to_traditional(document.title),
        summary=to_traditional(document.summary),
        items=document.items,
        metadata={**document.metadata, "provider": provider, "model": model},
    )


async def save_story_script(
    session: AsyncSession,
    document: StoryScriptDocument,
    *,
    draft_id: int | None = None,
) -> StoryScript:
    row = StoryScript(
        project_id=document.project_id,
        draft_id=draft_id,
        schema_version=document.schema_version,
        status="ready",
        provider=document.metadata.get("provider"),
        model=document.metadata.get("model"),
        script_json=document.to_json(),
        metadata_json=document.metadata,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def latest_story_script(session: AsyncSession, project_id: int) -> StoryScript | None:
    return (
        await session.execute(
            select(StoryScript)
            .where(StoryScript.project_id == project_id)
            .order_by(StoryScript.created_at.desc(), StoryScript.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


def document_from_row(row: StoryScript, *, asset_durations: dict[int, int]) -> StoryScriptDocument:
    return validate_story_script(
        dict(row.script_json), project_id=row.project_id, asset_durations=asset_durations
    )


async def document_from_latest(session: AsyncSession, project_id: int) -> StoryScriptDocument:
    row = await latest_story_script(session, project_id)
    if row is None:
        raise StoryScriptInputError("no StoryScript has been generated for this project")
    assets = (
        (await session.execute(select(Asset).where(Asset.project_id == project_id))).scalars().all()
    )
    asset_durations = {asset.id: int(asset.duration_ms) for asset in assets}
    return document_from_row(row, asset_durations=asset_durations)


def story_document_to_cut_plan(
    document: StoryScriptDocument,
    *,
    target_aspect_ratio: str,
    profile_name: str,
    narration_durations_ms: dict[int, int] | None = None,
    narration_audio_paths: dict[int, str] | None = None,
) -> CutPlan:
    segments = []
    for item in document.items:
        timeline_duration = max(item.duration_ms, (narration_durations_ms or {}).get(item.order, 0))
        reason_parts = [item.reason or item.beat_type]
        if item.audio_intent != "original":
            reason_parts.append(f"旁白: {item.narration}")
        reason_parts.append(f"audio_intent={item.audio_intent}")
        if timeline_duration > item.duration_ms:
            reason_parts.append(f"narration_extended_ms={timeline_duration}")
        segments.append(
            CutPlanSegment(
                order=item.order,
                asset_id=item.asset_id,
                asset_start_ms=item.source_start_ms,
                asset_end_ms=item.source_end_ms,
                source_kind="scripted" if item.audio_intent != "original" else "improv",
                reason=" | ".join(part for part in reason_parts if part),
                transition_to_next=TRANSITION_DEFAULT,
                audio_intent=item.audio_intent,
                timeline_duration_ms=timeline_duration,
                narration_audio_path=(narration_audio_paths or {}).get(item.order),
            )
        )
    return CutPlan(
        schema_version=STORY_PLAN_SCHEMA_VERSION,
        target_duration_ms=sum(max(1, segment.timeline_duration_ms or 0) for segment in segments),
        target_aspect_ratio=target_aspect_ratio,
        profile_name=profile_name,
        segments=tuple(segments),
        notes=document.summary or "Story/Narrato mode plan",
    )


def story_document_to_srt(
    document: StoryScriptDocument,
    *,
    narration_durations_ms: dict[int, int] | None = None,
) -> str:
    """Build timeline subtitles from StoryScript narration text."""
    cues: list[SubtitleCue] = []
    cursor = 0
    for item in document.items:
        duration = max(700, item.duration_ms, (narration_durations_ms or {}).get(item.order, 0))
        text = item.narration.strip()
        if text:
            cues.append(
                SubtitleCue(
                    sequence=len(cues) + 1,
                    timeline_start_ms=cursor,
                    timeline_end_ms=cursor + duration,
                    text=text,
                )
            )
        cursor += duration
    return render_srt(cues)
