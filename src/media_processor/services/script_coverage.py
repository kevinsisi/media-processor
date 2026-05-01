"""Semantic script-vs-transcript comparison via Gemini.

A single text-generation call per asset produces per-segment
``scripted | improvised`` classifications; the server computes coverage
from the validated matches (so a misbehaving model can't return inflated
totals).
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)


_PROMPT_TEMPLATE = (
    "你是影片剪輯助手。下面是「腳本」與「逐字稿片段」。請判斷每個逐字稿片段"
    "是否與腳本任一段落語意接近（不需逐字相同；若主旨、訴求、講述順序大致"
    "相符即視為「照稿」）。\n\n"
    "腳本：\n{script_body}\n\n"
    "逐字稿片段（idx, [start_ms - end_ms] text）：\n{numbered_segments}\n\n"
    "請輸出嚴格 JSON：\n"
    "{{\n"
    '  "matches": [\n'
    "    {{\n"
    '      "transcript_idx": <int>,\n'
    '      "classification": "scripted" | "improvised",\n'
    '      "confidence": <float 0..1>,\n'
    '      "matched_script_excerpt": <string>\n'
    "    }}\n"
    "  ]\n"
    "}}\n"
)

_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)
_VALID_CLASSIFICATIONS = {"scripted", "improvised"}


class ScriptCoverageError(RuntimeError):
    pass


class ScriptCoverageQuotaError(ScriptCoverageError):
    pass


class ScriptCoverageMissingScriptError(ScriptCoverageError):
    """Caller has no project script — orchestrator maps to failed:missing-script."""


@dataclass(frozen=True)
class TranscriptSegmentInput:
    idx: int
    start_ms: int
    end_ms: int
    text: str


@dataclass(frozen=True)
class CoverageMatch:
    transcript_idx: int
    classification: str  # "scripted" | "improvised"
    confidence: float
    matched_script_excerpt: str


@dataclass(frozen=True)
class CoverageResult:
    model: str
    scripted_segment_count: int
    total_segment_count: int
    coverage_ratio_by_count: float
    coverage_ratio_by_duration_ms: float
    matches: tuple[CoverageMatch, ...] = field(default_factory=tuple)


def _strip_fence(text: str) -> str:
    match = _FENCE_RE.match(text)
    if match:
        return match.group(1)
    return text


def _format_segments(segments: list[TranscriptSegmentInput]) -> str:
    return "\n".join(f"{seg.idx}, [{seg.start_ms} - {seg.end_ms}] {seg.text}" for seg in segments)


def _validate_response(
    payload: dict[str, Any],
    valid_idxs: set[int],
) -> list[CoverageMatch]:
    candidates = payload.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise ScriptCoverageError("Coverage payload missing candidates")
    parts = candidates[0].get("content", {}).get("parts", [])
    if not isinstance(parts, list) or not parts:
        raise ScriptCoverageError("Coverage candidate missing content.parts")
    text = parts[0].get("text", "")
    if not isinstance(text, str) or not text.strip():
        raise ScriptCoverageError("Coverage candidate text empty")
    cleaned = _strip_fence(text)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ScriptCoverageError(f"Coverage JSON parse failed: {exc}; text={text[:200]}") from exc
    matches_raw = data.get("matches") if isinstance(data, dict) else None
    if not isinstance(matches_raw, list):
        return []
    out: list[CoverageMatch] = []
    seen: set[int] = set()
    for entry in matches_raw:
        if not isinstance(entry, dict):
            continue
        idx = entry.get("transcript_idx")
        cls = entry.get("classification")
        conf = entry.get("confidence")
        excerpt = entry.get("matched_script_excerpt") or ""
        if not isinstance(idx, int) or idx not in valid_idxs or idx in seen:
            continue
        if cls not in _VALID_CLASSIFICATIONS:
            raise ScriptCoverageError(f"bad classification: {cls!r}")
        if not isinstance(conf, int | float) or isinstance(conf, bool):
            continue
        if not isinstance(excerpt, str):
            excerpt = str(excerpt)
        out.append(
            CoverageMatch(
                transcript_idx=idx,
                classification=str(cls),
                confidence=max(0.0, min(1.0, float(conf))),
                matched_script_excerpt=excerpt,
            )
        )
        seen.add(idx)
    return out


def _compute_coverage(
    segments: list[TranscriptSegmentInput],
    matches: list[CoverageMatch],
) -> tuple[int, int, float, float]:
    """Return (scripted_count, total_count, ratio_by_count, ratio_by_duration_ms)."""
    if not segments:
        return (0, 0, 0.0, 0.0)
    by_idx: dict[int, CoverageMatch] = {m.transcript_idx: m for m in matches}
    total_count = len(segments)
    total_duration = sum(max(0, s.end_ms - s.start_ms) for s in segments) or 1
    scripted_count = 0
    scripted_duration = 0
    for s in segments:
        m = by_idx.get(s.idx)
        if m is not None and m.classification == "scripted":
            scripted_count += 1
            scripted_duration += max(0, s.end_ms - s.start_ms)
    return (
        scripted_count,
        total_count,
        round(scripted_count / total_count, 4),
        round(scripted_duration / total_duration, 4),
    )


async def compare(
    *,
    script_body: str,
    segments: list[TranscriptSegmentInput],
    api_keys: tuple[str, ...],
    model: str,
    base_url: str,
    timeout_s: float,
) -> CoverageResult:
    """Run the semantic compare and return a fully validated CoverageResult."""
    if not script_body.strip():
        raise ScriptCoverageMissingScriptError("project script is empty")
    if not segments:
        raise ScriptCoverageError("transcript has no segments")
    if not api_keys:
        raise ScriptCoverageError("no API keys configured for coverage")

    prompt = _PROMPT_TEMPLATE.format(
        script_body=script_body.strip(),
        numbered_segments=_format_segments(segments),
    )
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.2,
            "responseMimeType": "application/json",
        },
    }

    valid_idxs = {s.idx for s in segments}
    last_status = 0
    async with httpx.AsyncClient(timeout=timeout_s) as client:
        for key in api_keys:
            url = f"{base_url}/models/{model}:generateContent?key={key}"
            try:
                response = await client.post(url, json=body)
            except httpx.HTTPError as exc:
                logger.warning("Coverage transport error; rotating key: %s", exc)
                continue
            last_status = response.status_code
            if response.status_code == 429 or 500 <= response.status_code < 600:
                logger.warning("Coverage %d; rotating to next key", response.status_code)
                continue
            if response.status_code >= 400:
                raise ScriptCoverageError(
                    f"Coverage call failed: status={response.status_code} body={response.text[:200]}"
                )
            matches = _validate_response(response.json(), valid_idxs)
            scripted_count, total_count, ratio_count, ratio_ms = _compute_coverage(
                segments, matches
            )
            return CoverageResult(
                model=model,
                scripted_segment_count=scripted_count,
                total_segment_count=total_count,
                coverage_ratio_by_count=ratio_count,
                coverage_ratio_by_duration_ms=ratio_ms,
                matches=tuple(matches),
            )

    raise ScriptCoverageQuotaError(
        f"all {len(api_keys)} coverage keys exhausted; last_status={last_status}"
    )


__all__ = [
    "CoverageMatch",
    "CoverageResult",
    "ScriptCoverageError",
    "ScriptCoverageMissingScriptError",
    "ScriptCoverageQuotaError",
    "TranscriptSegmentInput",
    "compare",
]
