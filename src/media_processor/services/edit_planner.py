"""Stage M5 — Gemini-backed cut planner.

Given a project's full M4 analysis output (transcripts + scene tags +
motion segments + script coverage), build a single Gemini prompt that
returns an ordered ``CutPlan``. The orchestrator then turns the plan
into ``DraftSegment`` rows.

The planner is *the only* M5 module that calls the Gemini text API.
Every other M5 service operates on the validated ``CutPlan`` dataclass.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field, replace
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

# Per-asset score response schema — separate from the CutPlan output
# schema since they're independent contracts. The planner now fans out
# one call per asset and assembles the CutPlan locally.
# v2 (M8.2 quality fix) — adds the ``summary`` field used by
# ``_assemble_plan`` for transcript-level deduplication so two assets
# that captured the same line/scene don't both land in the cut.
ASSET_SCORE_SCHEMA_VERSION = "m5.asset-score.v2"

# Assembly knobs. Anything below MIN_KEEP_SCORE or position="skip" is
# dropped. Spans wider than MAX_SPAN_MS / narrower than MIN_SPAN_MS get
# clamped before going into the cut plan.
MIN_KEEP_SCORE: int = 30
MIN_SPAN_MS: int = 1500
MAX_SPAN_MS: int = 6000
_VALID_POSITIONS = {"opening", "middle", "closing", "skip"}
_POSITION_ORDER = ("opening", "middle", "closing")

# Motion classification used by the rhythm-aware picker in _assemble_plan.
# Pan/tilt/handheld are camera movement; static is locked-off. The picker
# prefers dynamic at opening and static at closing, and gives an
# alternation bonus so two same-motion cuts don't sit back-to-back.
DYNAMIC_MOTIONS: frozenset[str] = frozenset({"pan", "tilt", "handheld"})
STATIC_MOTIONS: frozenset[str] = frozenset({"static"})
_MOTION_DEFAULT = "static"  # if asset has no motion tags, treat as static
_MOTION_ALTERNATION_BONUS = 10  # boost for differing from prev cut's motion
_MOTION_POSITION_BONUS = 15  # boost for matching opening=dynamic / closing=static

# Phase 8.2 — content-diversity bonuses applied during _assemble_plan.
# A candidate whose top scene tags don't yet appear in the chosen list
# gets a bonus per fresh tag; tags that have already shown up draw a
# penalty proportional to how many times they've appeared so the picker
# stops piling on the same scene (the prod regression where 蚊子館
# appeared 4–5 times in one reel).
_SCENE_DIVERSITY_BONUS: int = 8
_SCENE_REPEAT_PENALTY: int = 12
_SCENE_TAG_TOP_K: int = 3

# Phase 8.2 — transcript-level deduplication. Two cuts whose actual
# spoken text overlaps by more than this Jaccard ratio (3-gram chars)
# are treated as duplicates; the higher-ranked one wins. A second
# threshold falls back to the Gemini-supplied one-sentence summary so
# we still catch repeats when the transcript is sparse.
TRANSCRIPT_DEDUP_THRESHOLD: float = 0.5
SUMMARY_DEDUP_THRESHOLD: float = 0.6
_NGRAM_SIZE: int = 3

# Phase 8.1 — emotion-aware bonuses. The planner cares about two things:
# (1) tell the renderer the dominant emotion so it can apply zoompan, and
# (2) when a cut sits next to one with a *different* emotion, escalate
# the transition to the punchy ``circlecrop`` variant.
EMOTION_DEFAULT: str = "neutral"
DYNAMIC_EMOTIONS: frozenset[str] = frozenset({"happy", "surprised"})
STATIC_EMOTIONS: frozenset[str] = frozenset({"serious", "neutral"})
_EMOTION_SHIFT_TRANSITION: str = "circlecrop"

# xfade transition whitelist — must match ffmpeg xfade filter values.
# Any other suggestion from Gemini gets coerced to TRANSITION_DEFAULT so a
# typo / hallucination doesn't crash the render stage. Phase 8.1 added
# ``circlecrop`` as the strong-transition variant the renderer applies on
# emotion shifts.
VALID_TRANSITIONS: frozenset[str] = frozenset(
    {"fade", "dissolve", "wipeleft", "slideright", "circlecrop"}
)
TRANSITION_DEFAULT: str = "dissolve"
TRANSITION_DURATION_S: float = 0.5

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
    # xfade transition into the NEXT cut. The last cut's value is unused
    # (no next). Defaults are safe so older serialised plans without this
    # field stay loadable.
    transition_to_next: str = "dissolve"
    # Phase 8.1 — dominant face emotion across this cut's best span.
    # Read by ``video_renderer`` to decide whether to apply zoompan
    # (DYNAMIC_EMOTIONS get a slow zoom-in; STATIC stays locked off).
    # Default keeps older serialised plans loadable.
    dominant_emotion: str = EMOTION_DEFAULT


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


def _format_emotion(asset: Asset) -> str:
    """Render emotion tags + dominant verdict for the per-asset prompt.

    Returns a single line summarising the asset's dominant face emotion
    plus per-class time ranges, or a placeholder if the emotion stage
    didn't run (or saw no faces). The dominant verdict lives in the
    ``tag_name="dominant"`` row whose ``time_ranges_ms`` actually stores
    the dominant class string — see ``analysis._run_emotion``.
    """
    dominant = EMOTION_DEFAULT
    chunks: list[str] = []
    for tag in asset.tags:
        if tag.tag_type != "emotion":
            continue
        if tag.tag_name == "dominant":
            stash = list(tag.time_ranges_ms or [])
            if stash and isinstance(stash[0], str):
                dominant = stash[0]
            continue
        ranges = list(tag.time_ranges_ms or [])
        if not ranges:
            continue
        for r in ranges[:4]:
            if isinstance(r, list | tuple) and len(r) == 2:
                chunks.append(f"{tag.tag_name}[{int(r[0])}-{int(r[1])}]")
    if not chunks and dominant == EMOTION_DEFAULT:
        return "（無情緒分析）"
    body = ", ".join(chunks) if chunks else "（無時間段）"
    return f"主要情緒={dominant}; 分布: {body}"


def _dominant_emotion_for_asset(asset: Asset) -> str:
    """Pull the dominant emotion class from the ``dominant`` tag row.

    Returns ``EMOTION_DEFAULT`` for assets that never went through the
    emotion stage so downstream code (planner / renderer) can keep its
    branches simple.
    """
    for tag in asset.tags:
        if tag.tag_type == "emotion" and tag.tag_name == "dominant":
            stash = list(tag.time_ranges_ms or [])
            if stash and isinstance(stash[0], str):
                return stash[0]
    return EMOTION_DEFAULT


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
        f"情緒：{_format_emotion(asset)}\n"
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


_ASSET_SCORE_PROMPT = (
    "你是影片剪輯助手，正在評估「一段」素材是否適合放進最終剪輯。\n"
    "你只需要看這段素材本身——其他素材會由其他助手獨立評估，最後由系統合併。\n\n"
    "整支片要傳達的腳本：\n{script_body}\n\n"
    "這段素材：\n"
    "- asset_id: {asset_id}\n"
    "- 時長: {duration_s:.1f} 秒\n"
    "- 場景標籤: {scene_tags}\n"
    "- 運鏡: {motion}\n"
    "- 情緒: {emotion}\n"
    "- 逐字稿:\n{transcript}\n"
    "- 腳本對應: {coverage}\n\n"
    "請評估：\n"
    " 1. score (0-100)：這段對最終剪輯的相關度與品質\n"
    " 2. position：這段適合放在 opening / middle / closing；"
    "若品質太低或與腳本完全無關回 skip\n"
    " 3. best_span_ms：這段「最值得用」的 1.5–6 秒時間範圍 "
    "[start_ms, end_ms]，必須在 [0, {duration_ms}] 之內\n"
    " 4. source_kind：scripted（照腳本講的部分）或 improv（自然發揮 / 情緒亮點）\n"
    " 5. transition_to_next：這段播完後若銜接「下一段」適合的轉場效果，"
    "從 fade / dissolve / wipeleft / slideright / circlecrop 擇一。\n"
    "    指引：情緒延續或同場景用 dissolve；情緒平緩接同類用 fade；"
    "    場景大跳（室內↔戶外、人物↔產品）用 wipeleft 或 slideright；"
    "    情緒大跳（平靜↔激動 / 嚴肅↔驚喜）用 circlecrop；"
    "    避免整支片只用一種。\n"
    " 6. summary：用「一句話 (≤25 字繁中)」描述 best_span 內這段在講什麼"
    "（含主題與動作 / 主詞）。系統會用此欄位做去重，避免同樣的內容被多支"
    "素材重複塞進剪輯。請寫具體名詞，不要寫『介紹某事』『解釋某物』之類"
    "的空話。\n\n"
    "嚴格輸出 JSON：\n"
    "{{\n"
    f'  "schema_version": "{ASSET_SCORE_SCHEMA_VERSION}",\n'
    '  "score": <0-100>,\n'
    '  "position": "opening" | "middle" | "closing" | "skip",\n'
    '  "best_span_ms": [<start_ms>, <end_ms>],\n'
    '  "source_kind": "scripted" | "improv",\n'
    '  "transition_to_next": "fade" | "dissolve" | "wipeleft" | "slideright" | "circlecrop",\n'
    '  "summary": "<一句話 ≤25 字>",\n'
    '  "reason": "<一句話原因>"\n'
    "}}\n"
)


def _build_asset_prompt(
    asset: Asset,
    transcript: AssetTranscript | None,
    coverage: ScriptCoverage | None,
    script_body: str,
) -> str:
    return _ASSET_SCORE_PROMPT.format(
        script_body=script_body.strip() or "（無腳本）",
        asset_id=asset.id,
        duration_s=asset.duration_ms / 1000,
        duration_ms=int(asset.duration_ms),
        scene_tags=_format_scene_tags(asset),
        motion=_format_motion(asset),
        emotion=_format_emotion(asset),
        transcript=_format_transcript(transcript),
        coverage=_format_coverage(coverage),
    )


# ---------- Response parsing ----------


def _strip_fence(text: str) -> str:
    match = _FENCE_RE.match(text)
    if match:
        return match.group(1)
    return text


@dataclass(frozen=True)
class _AssetScore:
    """One asset's per-asset Gemini verdict before local assembly.

    ``dominant_motion`` is filled in by :func:`_score_one_asset` after the
    Gemini parse — it's the motion tag (pan / tilt / handheld / static)
    whose time_ranges_ms most overlap the picked ``best_span_ms``. The
    assembler uses it for rhythm-aware ordering (no two same-motion cuts
    in a row, dynamic at opening, static at closing).

    ``summary`` / ``span_transcript`` / ``scene_tags_top`` are the
    dedup-and-diversity signals consumed only by :func:`_assemble_plan`.
    They have safe empty defaults so older _AssetScore call sites
    (tests, fallbacks) keep working — an assembler that gets empty
    strings simply skips the dedup check for that pair.
    """

    asset_id: int
    score: int
    position: str  # "opening" | "middle" | "closing" | "skip"
    best_span_ms: tuple[int, int]
    source_kind: str
    reason: str
    dominant_motion: str = _MOTION_DEFAULT
    transition_to_next: str = TRANSITION_DEFAULT  # xfade filter type
    # Phase 8.1 — copied from the asset's ``dominant`` emotion tag row
    # before assembly. Carried through to ``CutPlanSegment.dominant_emotion``
    # so the renderer can act on it without re-querying tags.
    dominant_emotion: str = EMOTION_DEFAULT
    # Phase 8.2 — content-dedup + diversity signals. ``summary`` is the
    # one-sentence Gemini description of best_span; ``span_transcript``
    # is the raw whisper text inside best_span; ``scene_tags_top`` is
    # the top-K scene tags by confidence. Empty defaults make these
    # opt-in for the heuristic / test path.
    summary: str = ""
    span_transcript: str = ""
    scene_tags_top: tuple[str, ...] = ()


def _dominant_motion_for_span(asset: Asset, span_ms: tuple[int, int]) -> str:
    """Pick the motion tag whose time_ranges_ms most overlap ``span_ms``.

    Falls back to ``_MOTION_DEFAULT`` if the asset has no motion tags or
    none of them overlap. Used to attach motion context to an
    ``_AssetScore`` for downstream rhythm-aware ordering.
    """
    span_start, span_end = span_ms
    if span_end <= span_start:
        return _MOTION_DEFAULT
    best_overlap_ms = 0
    best_tag = _MOTION_DEFAULT
    for tag in asset.tags:
        if tag.tag_type != "motion":
            continue
        for r in tag.time_ranges_ms or []:
            if not isinstance(r, list | tuple) or len(r) != 2:
                continue
            try:
                r_start = int(r[0])
                r_end = int(r[1])
            except (TypeError, ValueError):
                continue
            overlap = max(0, min(r_end, span_end) - max(r_start, span_start))
            if overlap > best_overlap_ms:
                best_overlap_ms = overlap
                best_tag = tag.tag_name
    return best_tag


def _parse_asset_score(
    payload: dict[str, Any],
    *,
    asset_id: int,
    asset_duration_ms: int,
) -> _AssetScore:
    """Validate one per-asset Gemini response. Raises EditPlanInvalidError."""
    candidates = payload.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise EditPlanInvalidError(f"asset {asset_id}: response missing candidates")
    parts = candidates[0].get("content", {}).get("parts", [])
    if not isinstance(parts, list) or not parts:
        raise EditPlanInvalidError(f"asset {asset_id}: missing content.parts")
    text = parts[0].get("text", "")
    if not isinstance(text, str) or not text.strip():
        raise EditPlanInvalidError(f"asset {asset_id}: candidate text empty")
    try:
        data = json.loads(_strip_fence(text))
    except json.JSONDecodeError as exc:
        raise EditPlanInvalidError(
            f"asset {asset_id}: JSON parse failed: {exc}; text={text[:200]}"
        ) from exc
    if not isinstance(data, dict):
        raise EditPlanInvalidError(f"asset {asset_id}: top-level JSON not object")
    if data.get("schema_version") != ASSET_SCORE_SCHEMA_VERSION:
        raise EditPlanInvalidError(
            f"asset {asset_id}: schema_version mismatch: {data.get('schema_version')!r}"
        )

    try:
        score = int(data["score"])
    except (KeyError, TypeError, ValueError) as exc:
        raise EditPlanInvalidError(f"asset {asset_id}: score missing/invalid") from exc
    score = max(0, min(100, score))

    position = str(data.get("position", "")).strip().lower()
    if position not in _VALID_POSITIONS:
        raise EditPlanInvalidError(
            f"asset {asset_id}: position must be one of {_VALID_POSITIONS}, got {position!r}"
        )

    span_raw = data.get("best_span_ms")
    if not isinstance(span_raw, list | tuple) or len(span_raw) != 2:
        raise EditPlanInvalidError(f"asset {asset_id}: best_span_ms must be [start, end]")
    try:
        start_ms = int(span_raw[0])
        end_ms = int(span_raw[1])
    except (TypeError, ValueError) as exc:
        raise EditPlanInvalidError(f"asset {asset_id}: span values not int") from exc
    # Clamp into [0, duration] and shrink to MAX_SPAN_MS keeping the start.
    start_ms = max(0, min(start_ms, asset_duration_ms - 1))
    end_ms = max(start_ms + MIN_SPAN_MS, min(end_ms, asset_duration_ms))
    if end_ms - start_ms > MAX_SPAN_MS:
        end_ms = start_ms + MAX_SPAN_MS
    if end_ms > asset_duration_ms:
        # Asset shorter than MIN_SPAN_MS — skip downstream by force-position=skip
        # would be cleaner, but for now return the whole asset and let assembly
        # decide.
        end_ms = asset_duration_ms
        start_ms = max(0, end_ms - MIN_SPAN_MS)

    kind = str(data.get("source_kind", "")).strip().lower()
    if kind not in _VALID_SOURCE_KINDS:
        raise EditPlanInvalidError(
            f"asset {asset_id}: source_kind must be in {_VALID_SOURCE_KINDS}, got {kind!r}"
        )

    # Coerce unknown / missing transition to the safe default rather than
    # rejecting the whole response — a typo here doesn't ruin the cut plan.
    transition = str(data.get("transition_to_next", "")).strip().lower()
    if transition not in VALID_TRANSITIONS:
        transition = TRANSITION_DEFAULT

    reason = str(data.get("reason", "")).strip() or "(no reason)"
    # ``summary`` is best-effort — older ``m5.asset-score.v1`` responses
    # never contained it. Treat missing as empty so the dedup pass simply
    # falls back to the transcript-only signal for that candidate.
    summary = str(data.get("summary", "")).strip()
    return _AssetScore(
        asset_id=asset_id,
        score=score,
        position=position,
        best_span_ms=(start_ms, end_ms),
        source_kind=kind,
        reason=reason,
        transition_to_next=transition,
        summary=summary,
    )


def _char_ngrams(text: str, n: int = _NGRAM_SIZE) -> set[str]:
    """Character n-grams for Chinese-friendly similarity. Whitespace and
    common punctuation are stripped first so reordered sentences with the
    same nouns still cluster."""
    if not text:
        return set()
    norm = "".join(c for c in text if c.isalnum())
    if len(norm) < n:
        return {norm} if norm else set()
    return {norm[i : i + n] for i in range(len(norm) - n + 1)}


def _jaccard(a: str, b: str, n: int = _NGRAM_SIZE) -> float:
    """Jaccard similarity over character n-grams. 0.0 when either side
    has no extractable n-grams (treated as "can't tell" → not duplicate)."""
    sa = _char_ngrams(a, n)
    sb = _char_ngrams(b, n)
    if not sa or not sb:
        return 0.0
    inter = len(sa & sb)
    union = len(sa | sb)
    return inter / union if union else 0.0


def _is_content_duplicate(cand: _AssetScore, chosen: list[_AssetScore]) -> bool:
    """True when ``cand`` says the same thing as something already chosen.

    Three-signal check: (1) same ``asset_id`` — per-asset fanout gives
    one score per asset, so seeing the same id again means the leftover
    pool wasn't filtered; (2) raw transcript text inside best_span —
    strongest semantic signal, since identical lines really did get
    spoken twice; (3) the Gemini one-sentence summary — weaker fallback
    for sparse-transcript cuts. Any one breaching its threshold counts
    as a dup; that bias matches the prod regression where a single
    "蚊子館" topic landed 4–5 times.
    """
    for c in chosen:
        if cand.asset_id == c.asset_id:
            return True
        if cand.span_transcript and c.span_transcript:
            if _jaccard(cand.span_transcript, c.span_transcript) >= TRANSCRIPT_DEDUP_THRESHOLD:
                return True
        if cand.summary and c.summary:
            if _jaccard(cand.summary, c.summary) >= SUMMARY_DEDUP_THRESHOLD:
                return True
    return False


def _scene_counts(chosen: list[_AssetScore]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for c in chosen:
        for tag in c.scene_tags_top[:_SCENE_TAG_TOP_K]:
            counts[tag] = counts.get(tag, 0) + 1
    return counts


def _diversity_score(cand: _AssetScore, chosen_counts: dict[str, int]) -> int:
    """Bonus for fresh scene tags, penalty scaling with how many times a
    tag has already been picked. Stays bounded by clamping each tag's
    contribution so a single repeated tag can't make a candidate
    unusable on its own."""
    if not cand.scene_tags_top:
        return 0
    delta = 0
    for tag in cand.scene_tags_top[:_SCENE_TAG_TOP_K]:
        seen = chosen_counts.get(tag, 0)
        if seen == 0:
            delta += _SCENE_DIVERSITY_BONUS
        else:
            delta -= _SCENE_REPEAT_PENALTY * min(seen, 3)
    return delta


def _transcript_text_in_span(
    transcript: AssetTranscript | None,
    span_ms: tuple[int, int],
) -> str:
    """Whisper text whose segments overlap ``span_ms``. Joined with spaces
    so character n-grams stay positionally meaningful."""
    if transcript is None:
        return ""
    span_start, span_end = span_ms
    if span_end <= span_start:
        return ""
    parts: list[str] = []
    for seg in transcript.segments_json or []:
        try:
            s_start = int(seg.get("start_ms", 0))
            s_end = int(seg.get("end_ms", 0))
        except (TypeError, ValueError):
            continue
        if s_end <= span_start or s_start >= span_end:
            continue
        text = str(seg.get("text", "")).strip()
        if text:
            parts.append(text)
    return " ".join(parts)


def _top_scene_tag_names(asset: Asset, k: int = _SCENE_TAG_TOP_K) -> tuple[str, ...]:
    """Top-K scene tags by confidence. Used by the diversity bonus so
    the picker knows which thematic buckets are already over-represented."""
    pairs = sorted(
        ((t.tag_name, float(t.confidence)) for t in asset.tags if t.tag_type == "scene"),
        key=lambda p: -p[1],
    )
    return tuple(name for name, _ in pairs[:k])


def _bucket_motion_preference(bucket: str) -> str | None:
    """Per-bucket preferred motion class for the rhythm-aware picker.

    Opening favours dynamic shots so the reel doesn't open flat; closing
    favours a settled static frame. Middle is neutral so the picker is
    free to optimise alternation only.
    """
    if bucket == "opening":
        return "dynamic"
    if bucket == "closing":
        return "static"
    return None


def _rhythm_score(
    candidate: _AssetScore,
    *,
    prev_motion: str | None,
    position_preference: str | None,
) -> int:
    """Effective score after motion-alternation and position bonuses.

    Soft constraints — bonuses just shift ranking. If only same-motion
    candidates remain, the picker still returns one (no hard rejection).
    """
    score = candidate.score
    if prev_motion is not None and candidate.dominant_motion != prev_motion:
        score += _MOTION_ALTERNATION_BONUS
    if position_preference == "dynamic" and candidate.dominant_motion in DYNAMIC_MOTIONS:
        score += _MOTION_POSITION_BONUS
    elif position_preference == "static" and candidate.dominant_motion in STATIC_MOTIONS:
        score += _MOTION_POSITION_BONUS
    return score


def _argmax_pick(
    candidates: list[_AssetScore],
    *,
    chosen: list[_AssetScore],
    position_pref: str | None,
) -> _AssetScore | None:
    """Pop and return the best candidate from ``candidates`` that is not a
    content-duplicate of any cut already in ``chosen``. Skipped duplicates
    are dropped from the list. Returns ``None`` when the list is empty
    after dedup.

    The ranking blends three soft signals so no single one can dominate:
    base Gemini score, rhythm bonus (motion alternation + position
    preference), and Phase 8.2 scene-diversity bonus (fresh tags reward,
    repeats penalised). Hard duplicate rejection happens *after* ranking
    — picking the best then discarding it on a dup match is what lets
    the rhythm/diversity signals still push us toward different scenes,
    rather than letting transcript-overlap silently keep the dup in
    play.
    """
    chosen_counts = _scene_counts(chosen)
    while candidates:
        prev_motion = chosen[-1].dominant_motion if chosen else None
        best_idx = max(
            range(len(candidates)),
            key=lambda i: (
                _rhythm_score(
                    candidates[i],
                    prev_motion=prev_motion,
                    position_preference=position_pref,
                )
                + _diversity_score(candidates[i], chosen_counts),
                candidates[i].score,
            ),
        )
        cand = candidates.pop(best_idx)
        if _is_content_duplicate(cand, chosen):
            continue
        return cand
    return None


def _assemble_plan(
    scores: list[_AssetScore],
    target_duration_ms: int,
) -> list[CutPlanSegment]:
    """Local cut-plan assembly with rhythm-aware ordering, content dedup
    and a top-up pass that hits ``target_duration_ms``.

    Pipeline:

    1. Drop ``position=="skip"`` and anything below ``MIN_KEEP_SCORE``.
    2. Walk opening → middle → closing buckets. Each pick is the highest
       rhythm- + diversity-adjusted candidate that isn't a transcript /
       summary duplicate of an already-chosen cut.
    3. If the bucket pass under-shoots the target (Phase 8.2 prod fix —
       a 60 s ask had been resolving at ~30 s because each bucket bailed
       as soon as one cut was placed), top up from the rest of the
       usable pool — same dedup + diversity rules — until we hit the
       target or run out of candidates.

    Returns the chosen ``_AssetScore``s in pick order, with transition
    escalation applied (Phase 8.1: emotion-bucket shifts get circlecrop).
    """
    usable = [s for s in scores if s.position != "skip" and s.score >= MIN_KEEP_SCORE]
    if not usable:
        # Loosen: if everything got skipped or scored low, take the best 4
        # non-skip ones so we still produce a draft (orchestrator can re-roll).
        non_skip = sorted(
            (s for s in scores if s.position != "skip"),
            key=lambda x: -x.score,
        )
        usable = non_skip[:4]
    if not usable:
        return []

    by_pos: dict[str, list[_AssetScore]] = {p: [] for p in _POSITION_ORDER}
    for s in usable:
        by_pos[s.position].append(s)

    chosen: list[_AssetScore] = []
    accumulated = 0
    max_target = int(target_duration_ms * 1.2)

    def _try_consume(cand: _AssetScore) -> bool:
        """Append ``cand`` if it wouldn't blow past ``max_target``.
        Returns True iff the candidate was added."""
        nonlocal accumulated
        span_dur = cand.best_span_ms[1] - cand.best_span_ms[0]
        if accumulated + span_dur > max_target and chosen:
            return False
        chosen.append(cand)
        accumulated += span_dur
        return True

    # Pass 1 — bucket-driven selection. Stop early if we already hit
    # target so opening / closing don't get filled past the budget.
    for bucket in _POSITION_ORDER:
        candidates = list(by_pos[bucket])
        position_pref = _bucket_motion_preference(bucket)
        while candidates and accumulated < target_duration_ms:
            cand = _argmax_pick(candidates, chosen=chosen, position_pref=position_pref)
            if cand is None:
                break
            _try_consume(cand)
        if accumulated >= target_duration_ms:
            break

    # Pass 2 — duration top-up. The bucket walk above used to stop with
    # whatever it had after one bucket-per-cut walk, which is how a 60 s
    # request would render at ~30 s. Now we keep pulling from any
    # remaining candidates (regardless of bucket) until we either reach
    # the target or genuinely have nothing left that isn't a dup.
    if accumulated < target_duration_ms:
        leftovers: list[_AssetScore] = []
        for bucket in _POSITION_ORDER:
            leftovers.extend(by_pos[bucket])
        while leftovers and accumulated < target_duration_ms:
            cand = _argmax_pick(leftovers, chosen=chosen, position_pref=None)
            if cand is None:
                break
            _try_consume(cand)

    # Phase 8.1 — escalate transition to ``circlecrop`` whenever the
    # following cut's dominant emotion is a different bucket (dynamic
    # vs static), so the visual jolt mirrors the emotional jolt. The
    # last cut's transition is unused by the renderer so we leave it.
    out: list[CutPlanSegment] = []
    for i, s in enumerate(chosen):
        next_emotion = chosen[i + 1].dominant_emotion if i + 1 < len(chosen) else None
        transition = s.transition_to_next
        if next_emotion is not None and _is_emotion_shift(s.dominant_emotion, next_emotion):
            transition = _EMOTION_SHIFT_TRANSITION
        out.append(
            CutPlanSegment(
                order=i,
                asset_id=s.asset_id,
                asset_start_ms=s.best_span_ms[0],
                asset_end_ms=s.best_span_ms[1],
                source_kind=s.source_kind,
                reason=s.reason,
                transition_to_next=transition,
                dominant_emotion=s.dominant_emotion,
            )
        )
    return out


def _is_emotion_shift(prev: str, nxt: str) -> bool:
    """True when prev/next sit in different emotion buckets.

    Treats {happy, surprised} as the dynamic bucket and {serious,
    neutral} as static so quick same-bucket sequels (happy→surprised)
    don't escalate every transition into a circlecrop.
    """
    prev_dyn = prev in DYNAMIC_EMOTIONS
    nxt_dyn = nxt in DYNAMIC_EMOTIONS
    return prev_dyn != nxt_dyn


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


async def _score_one_asset(
    asset: Asset,
    transcript: AssetTranscript | None,
    coverage: ScriptCoverage | None,
    script_body: str,
    *,
    api_keys: tuple[str, ...],
    key_offset: int,
    model: str,
    base_url: str,
    timeout_s: float,
    client: httpx.AsyncClient,
) -> _AssetScore:
    """Single-asset Gemini call with key rotation on 429 / 5xx / transport.

    Walks the key pool starting from ``key_offset`` so concurrent fanout
    calls naturally start with different keys (and rotate through the
    whole pool on retry). Raises ``EditPlanQuotaError`` if every key
    exhausts; raises ``EditPlanInvalidError`` if a 200 came back with an
    unparseable body.
    """
    prompt = _build_asset_prompt(asset, transcript, coverage, script_body)
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.3,
            "responseMimeType": "application/json",
        },
    }
    last_status = 0
    last_invalid: EditPlanInvalidError | None = None
    for i in range(len(api_keys)):
        key = api_keys[(key_offset + i) % len(api_keys)]
        url = f"{base_url}/models/{model}:generateContent?key={key}"
        try:
            response = await client.post(url, json=body)
        except httpx.HTTPError as exc:
            logger.warning(
                "edit-planner asset=%d transport error; rotating key: %r",
                asset.id,
                exc,
            )
            continue
        last_status = response.status_code
        if response.status_code == 429 or 500 <= response.status_code < 600:
            logger.warning(
                "edit-planner asset=%d status=%d; rotating key",
                asset.id,
                response.status_code,
            )
            continue
        if response.status_code >= 400:
            raise EditPlanError(
                f"edit-planner asset {asset.id} call failed: "
                f"status={response.status_code} body={response.text[:200]}"
            )
        try:
            parsed = _parse_asset_score(
                response.json(),
                asset_id=asset.id,
                asset_duration_ms=int(asset.duration_ms),
            )
            # Attach motion + emotion context for rhythm-aware assembly
            # and renderer-side zoompan / transition decisions. We do
            # this server-side rather than asking Gemini to echo back
            # the tags so the model can't accidentally rewrite them.
            # Phase 8.2 also attaches the actual transcript inside
            # best_span and the asset's top scene tags so _assemble_plan
            # can dedup by content and reward scene diversity without
            # needing another DB round-trip.
            return replace(
                parsed,
                dominant_motion=_dominant_motion_for_span(asset, parsed.best_span_ms),
                dominant_emotion=_dominant_emotion_for_asset(asset),
                span_transcript=_transcript_text_in_span(transcript, parsed.best_span_ms),
                scene_tags_top=_top_scene_tag_names(asset),
            )
        except EditPlanInvalidError as exc:
            last_invalid = exc
            logger.warning(
                "edit-planner asset=%d JSON invalid (%s); rotating key",
                asset.id,
                exc,
            )
            continue
    if last_invalid is not None:
        raise last_invalid
    raise EditPlanQuotaError(
        f"asset {asset.id}: all {len(api_keys)} keys exhausted; last_status={last_status}"
    )


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
    """Build a CutPlan via per-asset parallel Gemini calls + local assembly.

    Sends one small prompt per asset (transcript + script + tags + coverage
    → score / position / best span / source_kind), fanned out concurrently
    over httpx.AsyncClient with key rotation. Each call is independent, so
    one slow / failed asset does not poison the batch — it just gets
    excluded from the assembled plan. The caller falls back to
    :func:`heuristic_fallback` if every asset call fails.
    """
    if not api_keys:
        raise EditPlanError("no API keys configured for edit planner")

    ctx = await _load_project_context(session, project_id)
    if not ctx.assets:
        raise EditPlanEmptyError("no assets to score")

    async with httpx.AsyncClient(timeout=timeout_s) as client:
        tasks = [
            _score_one_asset(
                asset,
                ctx.transcripts.get(asset.id),
                ctx.coverage.get(asset.id),
                ctx.script_body,
                api_keys=api_keys,
                key_offset=i,  # stagger so concurrent calls hit different keys
                model=model,
                base_url=base_url,
                timeout_s=timeout_s,
                client=client,
            )
            for i, asset in enumerate(ctx.assets)
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    scores: list[_AssetScore] = []
    quota_failures = 0
    invalid_failures = 0
    other_failures = 0
    last_invalid: EditPlanInvalidError | None = None
    for r in results:
        if isinstance(r, _AssetScore):
            scores.append(r)
        elif isinstance(r, EditPlanQuotaError):
            quota_failures += 1
        elif isinstance(r, EditPlanInvalidError):
            invalid_failures += 1
            last_invalid = r
        elif isinstance(r, Exception):
            other_failures += 1
            logger.warning("edit-planner per-asset task crashed: %r", r)

    if not scores:
        # No asset got scored at all — surface the dominant failure mode so
        # the orchestrator's fallback log makes sense.
        if quota_failures and quota_failures >= invalid_failures:
            raise EditPlanQuotaError(
                f"all {len(results)} per-asset calls quota-exhausted across "
                f"{len(api_keys)} keys"
            )
        if last_invalid is not None:
            raise last_invalid
        raise EditPlanError(
            f"all {len(results)} per-asset calls failed "
            f"(quota={quota_failures}, invalid={invalid_failures}, other={other_failures})"
        )

    cut_segments = _assemble_plan(scores, target_duration_ms)
    if not cut_segments:
        raise EditPlanInvalidError(
            f"assembly produced no segments from {len(scores)} scored assets "
            f"(all skipped or below threshold)"
        )

    notes = (
        f"per-asset fanout: {len(scores)}/{len(results)} assets scored "
        f"(quota_fails={quota_failures}, invalid={invalid_failures}); "
        f"chose {len(cut_segments)} cuts totalling "
        f"{sum(s.asset_end_ms - s.asset_start_ms for s in cut_segments)}ms"
    )
    logger.info("edit-planner: %s", notes)

    return CutPlan(
        schema_version=SCHEMA_VERSION,
        target_duration_ms=target_duration_ms,
        target_aspect_ratio=ctx.project.target_aspect_ratio,
        profile_name=ctx.project.profile_name,
        segments=tuple(cut_segments),
        notes=notes,
        used_fallback=False,
        fallback_reason=None,
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
                        dominant_emotion=_dominant_emotion_for_asset(asset),
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
                        dominant_emotion=_dominant_emotion_for_asset(asset),
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
                "transition_to_next": s.transition_to_next,
                "dominant_emotion": s.dominant_emotion,
            }
            for s in plan_obj.segments
        ],
    }


def deserialise_plan(blob: dict[str, Any]) -> CutPlan:
    """Inverse of :func:`serialise_plan`. Used by the M7 skip-plan path so a
    reordered plan can be reloaded from ``Draft.cut_plan_json`` without
    re-calling Gemini.
    """
    raw_segments = blob.get("segments") or []
    segments: list[CutPlanSegment] = []
    for seg in raw_segments:
        if not isinstance(seg, dict):
            continue
        segments.append(
            CutPlanSegment(
                order=int(seg["order"]),
                asset_id=int(seg["asset_id"]),
                asset_start_ms=int(seg["asset_start_ms"]),
                asset_end_ms=int(seg["asset_end_ms"]),
                source_kind=str(seg["source_kind"]),
                reason=str(seg.get("reason", "")),
                transition_to_next=str(seg.get("transition_to_next", "dissolve")),
                dominant_emotion=str(seg.get("dominant_emotion", EMOTION_DEFAULT)),
            )
        )
    segments.sort(key=lambda s: s.order)
    return CutPlan(
        schema_version=str(blob.get("schema_version", SCHEMA_VERSION)),
        target_duration_ms=int(blob.get("target_duration_ms", 0)),
        target_aspect_ratio=str(blob.get("target_aspect_ratio", "")),
        profile_name=str(blob.get("profile_name", "")),
        notes=str(blob.get("notes", "")),
        used_fallback=bool(blob.get("used_fallback", False)),
        fallback_reason=blob.get("fallback_reason"),
        segments=tuple(segments),
    )


__all__ = [
    "ASSET_SCORE_SCHEMA_VERSION",
    "DEFAULT_TARGET_DURATION_MS",
    "SCHEMA_VERSION",
    "CutPlan",
    "CutPlanSegment",
    "EditPlanEmptyError",
    "EditPlanError",
    "EditPlanInvalidError",
    "EditPlanQuotaError",
    "deserialise_plan",
    "heuristic_fallback",
    "plan",
    "serialise_plan",
]
