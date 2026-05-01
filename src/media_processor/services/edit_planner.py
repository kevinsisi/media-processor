"""Stage M5 — Gemini-backed cut planner.

Given a project's full M4 analysis output (transcripts + scene tags +
motion segments + script coverage), build a single Gemini prompt that
returns an ordered ``CutPlan``. The orchestrator then turns the plan
into ``DraftSegment`` rows.

The planner is *the only* M5 module that calls the Gemini text API.
Every other M5 service operates on the validated ``CutPlan`` dataclass.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from media_processor.models import (
    Asset,
    AssetTranscript,
    Project,
    Script,
    ScriptCoverage,
)

logger = logging.getLogger(__name__)


# JSON schema version handshake — the planner refuses to consume any
# response whose schema_version disagrees so a future change is a
# noisy parse failure rather than silent drift.
SCHEMA_VERSION = "m5.cut-plan.v1"

# Prompt-budget caps so very long shoots don't blow the context window.
MAX_TRANSCRIPT_SEGMENTS_VERBATIM = 60
TRANSCRIPT_BUCKET_SIZE = 8

# Acceptable source_kind values — kept in sync with CutSourceKind enum.
_VALID_SOURCE_KINDS = {"scripted", "improv"}

# Default render targets — overridden by the project profile in callers.
DEFAULT_TARGET_DURATION_MS = 30_000

_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)


class EditPlanError(RuntimeError):
    """Base class for planner failures."""


class EditPlanQuotaError(EditPlanError):
    """All API keys returned 429 / 5xx."""


class EditPlanInvalidError(EditPlanError):
    """Gemini returned malformed or unusable JSON."""


class EditPlanEmptyError(EditPlanError):
    """No assets / no analysed segments at all — nothing to plan."""


@dataclass(frozen=True)
class CutPlanSegment:
    """One ordered slot in the final timeline.

    ``asset_start_ms`` / ``asset_end_ms`` are within the source asset; the
    renderer maps them onto the timeline in plan order.
    """

    order: int
    asset_id: int
    asset_start_ms: int
    asset_end_ms: int
    source_kind: str  # "scripted" | "improv"
    reason: str


@dataclass(frozen=True)
class CutPlan:
    schema_version: str
    target_duration_ms: int
    target_aspect_ratio: str
    profile_name: str
    segments: tuple[CutPlanSegment, ...] = field(default_factory=tuple)
    notes: str = ""
    used_fallback: bool = False
    fallback_reason: str | None = None

    @property
    def total_duration_ms(self) -> int:
        return sum(s.asset_end_ms - s.asset_start_ms for s in self.segments)


# ---------- Prompt assembly ----------


def _format_scene_tags(asset: Asset) -> str:
    pairs = sorted(
        ((t.tag_name, round(float(t.confidence), 2)) for t in asset.tags if t.tag_type == "scene"),
        key=lambda p: p[1],
        reverse=True,
    )
    if not pairs:
        return "（無場景標籤）"
    return ", ".join(f"{name}:{conf}" for name, conf in pairs[:8])


def _format_motion(asset: Asset) -> str:
    chunks: list[str] = []
    for tag in asset.tags:
        if tag.tag_type != "motion":
            continue
        ranges = list(tag.time_ranges_ms or [])
        if not ranges:
            continue
        for r in ranges[:6]:
            if isinstance(r, list | tuple) and len(r) == 2:
                chunks.append(f"{tag.tag_name}[{int(r[0])}-{int(r[1])}]")
    if not chunks:
        return "（無運鏡分段）"
    return ", ".join(chunks)


def _bucket_transcript(
    segments: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Compress >60-segment transcripts into 8-segment buckets."""
    if len(segments) <= MAX_TRANSCRIPT_SEGMENTS_VERBATIM:
        return segments
    out: list[dict[str, Any]] = []
    for start in range(0, len(segments), TRANSCRIPT_BUCKET_SIZE):
        chunk = segments[start : start + TRANSCRIPT_BUCKET_SIZE]
        if not chunk:
            continue
        out.append(
            {
                "idx": chunk[0].get("idx", start),
                "start_ms": int(chunk[0].get("start_ms", 0)),
                "end_ms": int(chunk[-1].get("end_ms", 0)),
                "text": " ".join(str(s.get("text", "")).strip() for s in chunk),
            }
        )
    return out


def _format_transcript(transcript: AssetTranscript | None) -> str:
    if transcript is None:
        return "（無逐字稿）"
    raw = list(transcript.segments_json or [])
    bucketed = _bucket_transcript(raw)
    if not bucketed:
        return "（無逐字稿）"
    lines = []
    for seg in bucketed:
        idx = seg.get("idx", 0)
        start = int(seg.get("start_ms", 0))
        end = int(seg.get("end_ms", 0))
        text = str(seg.get("text", "")).strip().replace("\n", " ")
        lines.append(f"  - [{idx}] {start}-{end}ms：{text}")
    return "\n".join(lines)


def _format_coverage(coverage: ScriptCoverage | None) -> str:
    if coverage is None:
        return "（無 script coverage）"
    matches = list(coverage.match_details_json or [])
    scripted = [m for m in matches if m.get("classification") == "scripted"]
    if not scripted:
        return f"照稿覆蓋率 {coverage.coverage_ratio_by_count:.0%}（無 scripted 段）"
    excerpts = []
    for m in scripted[:8]:
        idx = m.get("transcript_idx")
        excerpt = str(m.get("matched_script_excerpt", "")).strip()
        if excerpt:
            excerpts.append(f"idx={idx} → {excerpt[:40]}")
    body = "; ".join(excerpts) if excerpts else "（略）"
    return f"照稿覆蓋率 {coverage.coverage_ratio_by_count:.0%}；對應段落：{body}"


def _format_asset_block(
    asset: Asset,
    transcript: AssetTranscript | None,
    coverage: ScriptCoverage | None,
) -> str:
    return (
        f"== asset_id={asset.id}（{asset.duration_ms / 1000:.1f}s）==\n"
        f"場景標籤：{_format_scene_tags(asset)}\n"
        f"運鏡：{_format_motion(asset)}\n"
        f"逐字稿：\n{_format_transcript(transcript)}\n"
        f"腳本對應：{_format_coverage(coverage)}\n"
    )


# Coverage targets the planner is instructed to honour. We surface the
# numeric thresholds so the prompt is auditable: when a draft only uses
# 2 / 14 clips like project 3 did, you can compare the rendered plan
# against these constants instead of guessing what the model heard.
MIN_ASSET_COVERAGE_RATIO = 0.5  # use at least half of the available clips
TARGET_IMPROV_SHARE = 0.4  # ~40% of total length should be improv
MIN_SEGMENTS_FALLBACK = 6  # at least this many cuts even on tiny shoots
MIN_SEGMENT_DURATION_S = 1.5
MAX_SEGMENT_DURATION_S = 6.0


_PROMPT_TEMPLATE = (
    "你是一位影片剪輯導演，正在為以下專案挑選最終剪輯使用的片段。\n\n"
    "專案資訊：\n"
    "- 名稱：{project_name}\n"
    "- 風格 profile：{profile_name}\n"
    "- 目標長度：約 {target_duration_ms} ms（容許 -10% / +20%）\n"
    "- 輸出比例：{target_aspect_ratio}\n"
    "- 可用素材數：{asset_count} 段（總時長約 {total_source_ms} ms）\n"
    "- 至少使用 {min_assets_used} 段不同素材（{min_coverage_pct}% 覆蓋率）\n"
    "- 至少產出 {min_segments} 個片段\n\n"
    "腳本（若有）：\n{script_body}\n\n"
    "可用素材（每段含逐字稿、場景、運鏡、腳本覆蓋）：\n\n"
    "{asset_blocks}\n"
    "請挑選並排序成一個剪輯計畫，遵守以下規則：\n"
    "1. 不要過度保守。目標是說一個完整的小故事，不只是抓 2-3 段最像腳本的片段。\n"
    "2. 結構：以「scripted」段組成敘事骨架（依腳本順序），以「improv」段加入"
    "情緒、視覺亮點、運鏡轉場。整支片約 {improv_pct}% 為 improv，"
    "{scripted_pct}% 為 scripted。\n"
    "3. 素材覆蓋：必須使用 ≥ {min_assets_used} 段不同 asset_id（共 {asset_count} 段）；"
    "不要把 80% 的鏡頭都來自同一段素材。\n"
    "4. 場景／運鏡多樣性：避免同一場景標籤或同一運鏡連續超過 2 段；"
    "穿插 pan / tilt / handheld / static 變化讓畫面有節奏。\n"
    "5. 片段長度：每段 {min_seg_s}–{max_seg_s} 秒，總長 ≈ 目標長度。\n"
    "6. 每段須完整落在素材內 (start_ms < end_ms ≤ asset duration)。\n"
    "7. 即使腳本很短或腳本覆蓋率低，也要產出完整剪輯 — 缺腳本就以 improv 為主。\n\n"
    "嚴格輸出 JSON，schema：\n"
    "{{\n"
    f'  "schema_version": "{SCHEMA_VERSION}",\n'
    '  "notes": "<剪輯思路 1–3 句，說明你怎麼分配 scripted vs improv 與覆蓋哪些素材>",\n'
    '  "segments": [\n'
    "    {{\n"
    '      "asset_id": <int>,\n'
    '      "start_ms": <int>,\n'
    '      "end_ms": <int>,\n'
    '      "source_kind": "scripted" | "improv",\n'
    '      "reason": "<為何挑這段>"\n'
    "    }}\n"
    "  ]\n"
    "}}\n"
)


def _build_prompt(
    project: Project,
    script_body: str,
    target_duration_ms: int,
    asset_blocks: list[str],
    *,
    asset_count: int,
    total_source_ms: int,
) -> str:
    min_assets_used = max(1, int(round(asset_count * MIN_ASSET_COVERAGE_RATIO)))
    return _PROMPT_TEMPLATE.format(
        project_name=project.name,
        profile_name=project.profile_name,
        target_duration_ms=target_duration_ms,
        target_aspect_ratio=project.target_aspect_ratio,
        script_body=script_body.strip() or "（無腳本）",
        asset_blocks="\n".join(asset_blocks) or "（無素材）",
        asset_count=asset_count,
        total_source_ms=total_source_ms,
        min_assets_used=min_assets_used,
        min_coverage_pct=int(round(MIN_ASSET_COVERAGE_RATIO * 100)),
        min_segments=MIN_SEGMENTS_FALLBACK,
        improv_pct=int(round(TARGET_IMPROV_SHARE * 100)),
        scripted_pct=int(round((1 - TARGET_IMPROV_SHARE) * 100)),
        min_seg_s=MIN_SEGMENT_DURATION_S,
        max_seg_s=MAX_SEGMENT_DURATION_S,
    )


# ---------- Response parsing ----------


def _strip_fence(text: str) -> str:
    match = _FENCE_RE.match(text)
    if match:
        return match.group(1)
    return text


def _validate_plan(
    payload: dict[str, Any],
    *,
    asset_bounds: dict[int, int],
) -> tuple[list[CutPlanSegment], str]:
    """Validate Gemini's response shape; return (segments, notes)."""
    candidates = payload.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise EditPlanInvalidError("response missing candidates")
    parts = candidates[0].get("content", {}).get("parts", [])
    if not isinstance(parts, list) or not parts:
        raise EditPlanInvalidError("candidate missing content.parts")
    text = parts[0].get("text", "")
    if not isinstance(text, str) or not text.strip():
        raise EditPlanInvalidError("candidate text empty")
    cleaned = _strip_fence(text)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise EditPlanInvalidError(f"JSON parse failed: {exc}; text={text[:200]}") from exc

    if not isinstance(data, dict):
        raise EditPlanInvalidError("top-level JSON is not an object")
    if data.get("schema_version") != SCHEMA_VERSION:
        raise EditPlanInvalidError(f"schema_version mismatch: got {data.get('schema_version')!r}")
    raw_segments = data.get("segments")
    if not isinstance(raw_segments, list) or not raw_segments:
        raise EditPlanInvalidError("segments empty or wrong type")

    out: list[CutPlanSegment] = []
    for order, entry in enumerate(raw_segments):
        if not isinstance(entry, dict):
            continue
        try:
            asset_id = int(entry["asset_id"])
            start_ms = int(entry["start_ms"])
            end_ms = int(entry["end_ms"])
        except (KeyError, TypeError, ValueError):
            continue
        kind = str(entry.get("source_kind", "")).strip()
        reason = str(entry.get("reason", "")).strip()
        if kind not in _VALID_SOURCE_KINDS:
            continue
        if start_ms < 0 or end_ms <= start_ms:
            continue
        bound = asset_bounds.get(asset_id)
        if bound is None or end_ms > bound:
            continue
        out.append(
            CutPlanSegment(
                order=order,
                asset_id=asset_id,
                asset_start_ms=start_ms,
                asset_end_ms=end_ms,
                source_kind=kind,
                reason=reason or "(no reason given)",
            )
        )

    if not out:
        raise EditPlanInvalidError("no valid segments after validation")

    notes = str(data.get("notes", "")).strip()
    return out, notes


# ---------- DB loading ----------


@dataclass(frozen=True)
class _ProjectContext:
    project: Project
    script_body: str
    assets: tuple[Asset, ...]
    transcripts: dict[int, AssetTranscript]
    coverage: dict[int, ScriptCoverage]
    asset_bounds: dict[int, int]


async def _load_project_context(session: AsyncSession, project_id: int) -> _ProjectContext:
    project = await session.get(Project, project_id)
    if project is None:
        raise EditPlanError(f"project {project_id} not found")

    script_row = (
        await session.execute(select(Script).where(Script.project_id == project_id))
    ).scalar_one_or_none()
    script_body = (script_row.body if script_row else "") or ""

    assets = tuple(
        (
            await session.execute(
                select(Asset)
                .where(Asset.project_id == project_id)
                .options(selectinload(Asset.tags))
                .order_by(Asset.id.asc())
            )
        )
        .scalars()
        .all()
    )
    if not assets:
        raise EditPlanEmptyError("project has no assets")

    asset_ids = [a.id for a in assets]
    tx_rows = (
        (
            await session.execute(
                select(AssetTranscript).where(AssetTranscript.asset_id.in_(asset_ids))
            )
        )
        .scalars()
        .all()
    )
    transcripts = {t.asset_id: t for t in tx_rows}

    cov_rows = (
        (
            await session.execute(
                select(ScriptCoverage).where(ScriptCoverage.asset_id.in_(asset_ids))
            )
        )
        .scalars()
        .all()
    )
    coverage = {c.asset_id: c for c in cov_rows}

    return _ProjectContext(
        project=project,
        script_body=script_body,
        assets=assets,
        transcripts=transcripts,
        coverage=coverage,
        asset_bounds={a.id: int(a.duration_ms) for a in assets},
    )


# ---------- Public entry points ----------


async def plan(
    project_id: int,
    session: AsyncSession,
    *,
    api_keys: tuple[str, ...],
    model: str,
    base_url: str,
    timeout_s: float,
    target_duration_ms: int = DEFAULT_TARGET_DURATION_MS,
) -> CutPlan:
    """Build a CutPlan for the project. Raises on quota / invalid / empty."""
    if not api_keys:
        raise EditPlanError("no API keys configured for edit planner")

    ctx = await _load_project_context(session, project_id)
    asset_blocks = [
        _format_asset_block(
            asset,
            ctx.transcripts.get(asset.id),
            ctx.coverage.get(asset.id),
        )
        for asset in ctx.assets
    ]
    total_source_ms = sum(int(a.duration_ms) for a in ctx.assets)
    prompt = _build_prompt(
        ctx.project,
        ctx.script_body,
        target_duration_ms,
        asset_blocks,
        asset_count=len(ctx.assets),
        total_source_ms=total_source_ms,
    )
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.3,
            "responseMimeType": "application/json",
        },
    }

    last_status = 0
    last_invalid: EditPlanInvalidError | None = None
    async with httpx.AsyncClient(timeout=timeout_s) as client:
        for key in api_keys:
            url = f"{base_url}/models/{model}:generateContent?key={key}"
            try:
                response = await client.post(url, json=body)
            except httpx.HTTPError as exc:
                logger.warning("edit-planner transport error; rotating key: %r", exc)
                continue
            last_status = response.status_code
            if response.status_code == 429 or 500 <= response.status_code < 600:
                logger.warning(
                    "edit-planner status=%d; rotating to next key",
                    response.status_code,
                )
                continue
            if response.status_code >= 400:
                raise EditPlanError(
                    "edit-planner call failed: "
                    f"status={response.status_code} body={response.text[:200]}"
                )
            try:
                segments, notes = _validate_plan(response.json(), asset_bounds=ctx.asset_bounds)
            except EditPlanInvalidError as exc:
                last_invalid = exc
                logger.warning("edit-planner JSON invalid (%s); rotating key", exc)
                continue
            return CutPlan(
                schema_version=SCHEMA_VERSION,
                target_duration_ms=target_duration_ms,
                target_aspect_ratio=ctx.project.target_aspect_ratio,
                profile_name=ctx.project.profile_name,
                segments=tuple(segments),
                notes=notes,
                used_fallback=False,
                fallback_reason=None,
            )

    if last_invalid is not None:
        raise last_invalid
    raise EditPlanQuotaError(
        f"all {len(api_keys)} edit-planner keys exhausted; last_status={last_status}"
    )


async def heuristic_fallback(
    project_id: int,
    session: AsyncSession,
    *,
    target_duration_ms: int = DEFAULT_TARGET_DURATION_MS,
    fallback_reason: str = "gemini failed; used heuristic fallback",
) -> CutPlan:
    """Build a CutPlan from existing transcripts without calling Gemini.

    Emits one improv cut per asset transcript segment, capped by duration.
    Used when the Gemini planner fails so the worker can still produce
    a draft the operator can preview and re-roll.
    """
    ctx = await _load_project_context(session, project_id)

    segments: list[CutPlanSegment] = []
    accumulated_ms = 0
    order = 0
    for asset in ctx.assets:
        tx = ctx.transcripts.get(asset.id)
        raw = list(tx.segments_json or []) if tx is not None else []
        if not raw:
            # Asset with no transcript: take a single 3-second middle slice
            # so a no-script project still yields *something*.
            mid = max(0, asset.duration_ms // 2 - 1500)
            end = min(asset.duration_ms, mid + 3000)
            if end > mid:
                segments.append(
                    CutPlanSegment(
                        order=order,
                        asset_id=asset.id,
                        asset_start_ms=mid,
                        asset_end_ms=end,
                        source_kind="improv",
                        reason="fallback: middle slice",
                    )
                )
                accumulated_ms += end - mid
                order += 1
        else:
            for seg in raw:
                start = int(seg.get("start_ms", 0))
                end = int(seg.get("end_ms", 0))
                if end <= start:
                    continue
                segments.append(
                    CutPlanSegment(
                        order=order,
                        asset_id=asset.id,
                        asset_start_ms=start,
                        asset_end_ms=end,
                        source_kind="improv",
                        reason="fallback: transcript segment",
                    )
                )
                accumulated_ms += end - start
                order += 1
                if accumulated_ms >= target_duration_ms:
                    break
        if accumulated_ms >= target_duration_ms:
            break

    if not segments:
        raise EditPlanEmptyError("no usable transcript or assets for fallback plan")

    return CutPlan(
        schema_version=SCHEMA_VERSION,
        target_duration_ms=target_duration_ms,
        target_aspect_ratio=ctx.project.target_aspect_ratio,
        profile_name=ctx.project.profile_name,
        segments=tuple(segments),
        notes="heuristic fallback (no Gemini)",
        used_fallback=True,
        fallback_reason=fallback_reason,
    )


def serialise_plan(plan_obj: CutPlan) -> dict[str, Any]:
    """JSON-friendly dict suitable for storing on Draft.cut_plan_json."""
    return {
        "schema_version": plan_obj.schema_version,
        "target_duration_ms": plan_obj.target_duration_ms,
        "target_aspect_ratio": plan_obj.target_aspect_ratio,
        "profile_name": plan_obj.profile_name,
        "notes": plan_obj.notes,
        "used_fallback": plan_obj.used_fallback,
        "fallback_reason": plan_obj.fallback_reason,
        "segments": [
            {
                "order": s.order,
                "asset_id": s.asset_id,
                "asset_start_ms": s.asset_start_ms,
                "asset_end_ms": s.asset_end_ms,
                "source_kind": s.source_kind,
                "reason": s.reason,
            }
            for s in plan_obj.segments
        ],
    }


__all__ = [
    "DEFAULT_TARGET_DURATION_MS",
    "SCHEMA_VERSION",
    "CutPlan",
    "CutPlanSegment",
    "EditPlanEmptyError",
    "EditPlanError",
    "EditPlanInvalidError",
    "EditPlanQuotaError",
    "heuristic_fallback",
    "plan",
    "serialise_plan",
]
