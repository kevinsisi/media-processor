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
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from media_processor.services.opencode_client import OpenCodeConfig
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from media_processor.models import (
    Asset,
    AssetTranscript,
    Draft,
    DraftComment,
    DraftStatus,
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
ASSET_SCORE_SCHEMA_VERSION = "m5.asset-score.v1"

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
# typo / hallucination doesn't crash the render stage. v0.14.3 dropped
# ``fade`` and ``dissolve`` after operator feedback that every reel
# looked the same; only the assertive variants survive (wipe / slide /
# circlecrop). Old serialised plans that still carry ``dissolve`` get
# coerced to TRANSITION_DEFAULT on load — see ``_safe_transition``.
VALID_TRANSITIONS: frozenset[str] = frozenset(
    {
        # default ("custom") preset transitions
        "wipeleft",
        "slideright",
        "circlecrop",
        # v0.18 — additional transitions enabled for slow / artistic /
        # commercial presets. Renderer accepts the same set.
        "fade",
        "dissolve",
        "fadeblack",
        "fadewhite",
    }
)
TRANSITION_DEFAULT: str = "wipeleft"
TRANSITION_DURATION_S: float = 0.5


# v0.18 — clip-style preset parameter bundle. Each preset biases the
# planner's span bounds, the per-asset Gemini prompt, the transition
# allowlist, and a one-line BGM hint surfaced by the music-suggestion
# endpoint. ``custom`` keeps legacy behaviour (no preset applied).
@dataclass(frozen=True)
class StylePresetParams:
    name: str
    min_span_ms: int
    max_span_ms: int
    transition_allowlist: frozenset[str]
    default_transition: str
    bgm_hint: str  # injected into the music-suggestion prompt
    prompt_hint: str  # injected into the per-asset score prompt
    irregular_lengths: bool = False  # artistic preset: keep span variation


STYLE_PRESET_FAST = StylePresetParams(
    name="fast",
    min_span_ms=3000,
    max_span_ms=5000,
    transition_allowlist=frozenset({"wipeleft", "slideright", "circlecrop"}),
    default_transition="wipeleft",
    bgm_hint=("高能量、快節奏、強勁節拍 (130-150 BPM)，電子或搖滾，鼓點密集"),
    prompt_hint=(
        "【剪輯風格 = 快節奏】每段請挑選 3-5 秒短而有力的段落，"
        "轉場限定 wipeleft / slideright / circlecrop，避免柔和淡出。"
    ),
)

STYLE_PRESET_SLOW = StylePresetParams(
    name="slow",
    min_span_ms=8000,
    max_span_ms=15000,
    transition_allowlist=frozenset({"dissolve", "fade", "fadeblack"}),
    default_transition="dissolve",
    bgm_hint=("柔和、緩慢、放鬆的氛圍音樂 (60-80 BPM)，環境音、鋼琴、弦樂"),
    prompt_hint=(
        "【剪輯風格 = 慢節奏】每段請挑選 8-15 秒較長段落，留白與情緒沉澱優先，"
        "轉場限定 dissolve / fade / fadeblack。"
    ),
)

STYLE_PRESET_COMMERCIAL = StylePresetParams(
    name="commercial",
    min_span_ms=5000,
    max_span_ms=8000,
    transition_allowlist=frozenset({"slideright", "wipeleft", "fadeblack"}),
    default_transition="slideright",
    bgm_hint=("專業、潔淨、商業感的 corporate 配樂 (90-110 BPM)，現代電子合成或乾淨吉他"),
    prompt_hint=(
        "【剪輯風格 = 商業感】每段請挑選 5-8 秒、表達清楚有重點的段落，"
        "轉場限定 slideright / wipeleft / fadeblack，俐落不花俏。"
    ),
)

STYLE_PRESET_ARTISTIC = StylePresetParams(
    name="artistic",
    min_span_ms=3000,
    max_span_ms=12000,
    transition_allowlist=frozenset({"fade", "fadewhite", "fadeblack"}),
    default_transition="fade",
    bgm_hint=("acoustic / indie 木吉他、民謠、文青風 (80-100 BPM)，溫暖人聲或環境氛圍"),
    prompt_hint=(
        "【剪輯風格 = 文青風】每段長度可在 3-12 秒之間自由變化，"
        "刻意製造不規則節奏與停頓感，轉場限定 fade / fadewhite / fadeblack。"
    ),
    irregular_lengths=True,
)

STYLE_PRESET_CUSTOM = StylePresetParams(
    name="custom",
    min_span_ms=1500,
    max_span_ms=6000,
    transition_allowlist=frozenset({"wipeleft", "slideright", "circlecrop"}),
    default_transition="wipeleft",
    bgm_hint="",
    prompt_hint="",
)


STYLE_PRESETS: dict[str, StylePresetParams] = {
    "fast": STYLE_PRESET_FAST,
    "slow": STYLE_PRESET_SLOW,
    "commercial": STYLE_PRESET_COMMERCIAL,
    "artistic": STYLE_PRESET_ARTISTIC,
    "custom": STYLE_PRESET_CUSTOM,
}


def resolve_style_preset(name: str | None) -> StylePresetParams:
    """Map a preset string (or None) to its parameter bundle.

    Unknown / missing values fall back to ``custom`` so a typo in a
    legacy stored draft can't crash the planner.
    """
    if not name:
        return STYLE_PRESET_CUSTOM
    return STYLE_PRESETS.get(name.strip().lower(), STYLE_PRESET_CUSTOM)


def _coerce_legacy_transition(name: str) -> str:
    """Map legacy ``fade`` / ``dissolve`` to the v0.14.3 default.

    Drafts rendered before the dissolve / fade removal still have those
    values stored in ``Draft.cut_plan_json``. Coerce on load so the M7.1
    skip-plan re-render uses the new whitelist without forcing a
    backfill migration.
    """
    if name in VALID_TRANSITIONS:
        return name
    return TRANSITION_DEFAULT


# Each xfade between adjacent cuts shortens the timeline by this much, so
# the planner aims a touch higher than the raw target so the rendered
# reel actually lands at target_duration_ms. Mirrors
# ``video_renderer.TRANSITION_DURATION_S`` and the M8.1 subtitle anchor.
_TRANSITION_OVERLAP_MS: int = 500

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
    # (no next). Default is the post-v0.14.3 ``TRANSITION_DEFAULT``;
    # legacy plans that stored ``"dissolve"`` get coerced through
    # ``_safe_transition`` at render time.
    transition_to_next: str = TRANSITION_DEFAULT
    # Phase 8.1 — dominant face emotion across this cut's best span.
    # Read by ``video_renderer`` to decide whether to apply zoompan
    # (DYNAMIC_EMOTIONS get a slow zoom-in; STATIC stays locked off).
    # Default keeps older serialised plans loadable.
    dominant_emotion: str = EMOTION_DEFAULT
    # M8.1 follow-up — motion class of the chosen span (pan / tilt /
    # handheld / static). The renderer uses this together with
    # ``has_face`` to gate zoompan: a static, faceless clip with
    # zoompan reads as a frozen frame, so we only zoom when the source
    # has actual movement OR a face was detected during the span.
    dominant_motion: str = _MOTION_DEFAULT
    # True when at least one emotion-tag time-range overlapped the
    # chosen ``[asset_start_ms, asset_end_ms)`` window — i.e. a face
    # was actually visible during the cut, not just somewhere in the
    # asset. Read alongside ``dominant_motion`` by the renderer.
    has_face: bool = False
    # v0.30.0 — opt-in AI Smart Camera directive. ``None`` = no camera
    # move (the renderer falls through to the historical static aspect
    # crop / emotion zoompan path). When the orchestrator's smart-camera
    # stage runs and Gemini Vision returns usable focus_regions, this
    # holds a serialised dict produced by
    # ``services.smart_camera_planner._serialise_directive`` — see that
    # module for the schema. Stored as a free-form ``dict`` so the
    # renderer can read it without the planner module having to import
    # the smart-camera dataclass (keeps the module-import graph
    # one-way).
    smart_camera_json: dict[str, Any] | None = None


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
    "{style_preset_block}"
    "{prior_feedback_block}"
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
    " 3. best_span_ms：這段「最值得用」的 {span_min_s}–{span_max_s} 秒時間範圍 "
    "[start_ms, end_ms]，必須在 [0, {duration_ms}] 之內\n"
    " 4. source_kind：scripted（照腳本講的部分）或 improv（自然發揮 / 情緒亮點）\n"
    " 5. transition_to_next：這段播完後若銜接「下一段」適合的轉場效果，"
    "從 {transition_choices} 擇一。\n"
    "    指引：場景大跳（室內↔戶外、人物↔產品、不同地點）用拉開差距的銳利轉場；"
    "    情緒大跳（平靜↔激動 / 嚴肅↔驚喜 / 開頭↔結尾）用視覺較強烈的轉場；"
    "    每兩三段之間適度切換以避免整支片連續用同一種。\n\n"
    "嚴格輸出 JSON：\n"
    "{{\n"
    f'  "schema_version": "{ASSET_SCORE_SCHEMA_VERSION}",\n'
    '  "score": <0-100>,\n'
    '  "position": "opening" | "middle" | "closing" | "skip",\n'
    '  "best_span_ms": [<start_ms>, <end_ms>],\n'
    '  "source_kind": "scripted" | "improv",\n'
    '  "transition_to_next": <{transition_choices} 之一>,\n'
    '  "reason": "<一句話原因>"\n'
    "}}\n"
)


def _format_style_preset_block(style: StylePresetParams) -> str:
    """Render the optional style-preset banner above the script body.

    Empty string when style is ``custom`` so the legacy prompt shape
    stays exactly the same; the four named presets push their hint up
    front so the model treats span-length and transition choice as
    constraints, not suggestions.
    """
    if not style.prompt_hint:
        return ""
    return f"{style.prompt_hint}\n\n"


def _format_prior_feedback_block(prior_feedback: str) -> str:
    """Render the optional ``上一版回饋`` section of the per-asset prompt.

    Returns an empty string when there's no prior feedback, otherwise a
    block the model can read and weigh — e.g. "蚊子館那段太多" should
    push the score for similar transcripts down.
    """
    body = (prior_feedback or "").strip()
    if not body:
        return ""
    return f"【上一版使用者回饋（請參考並改進；不要重複同樣的問題）】\n{body}\n\n"


def _build_asset_prompt(
    asset: Asset,
    transcript: AssetTranscript | None,
    coverage: ScriptCoverage | None,
    script_body: str,
    *,
    prior_feedback: str = "",
    style: StylePresetParams = STYLE_PRESET_CUSTOM,
) -> str:
    transition_choices = " / ".join(sorted(style.transition_allowlist))
    return _ASSET_SCORE_PROMPT.format(
        style_preset_block=_format_style_preset_block(style),
        prior_feedback_block=_format_prior_feedback_block(prior_feedback),
        script_body=script_body.strip() or "（無腳本）",
        asset_id=asset.id,
        duration_s=asset.duration_ms / 1000,
        duration_ms=int(asset.duration_ms),
        scene_tags=_format_scene_tags(asset),
        motion=_format_motion(asset),
        emotion=_format_emotion(asset),
        transcript=_format_transcript(transcript),
        coverage=_format_coverage(coverage),
        span_min_s=f"{style.min_span_ms / 1000:.1f}",
        span_max_s=f"{style.max_span_ms / 1000:.1f}",
        transition_choices=transition_choices,
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
    # Source asset's full duration; carried so the assembler can extend
    # ``best_span_ms`` up to the actual asset bound during the
    # duration-fill pass without crossing past the source.
    asset_duration_ms: int = 0
    # True when an emotion-tag range (excluding the ``dominant``
    # sentinel row) overlapped the chosen span — i.e. a face was
    # visible during this exact window. Used by the renderer to gate
    # zoompan so static clips without faces don't get a frozen-frame
    # zoom.
    has_face: bool = False


def _has_face_in_span(asset: Asset, span_ms: tuple[int, int]) -> bool:
    """True if any emotion range (face detection) overlaps ``span_ms``.

    The ``dominant`` sentinel row stores the asset-wide verdict in
    ``time_ranges_ms`` (as ``[class]``) so we skip it — only per-class
    rows carry real ``[start_ms, end_ms]`` windows. Used to gate the
    renderer's zoompan: an asset whose dominant emotion is ``happy``
    but whose chosen span has no face overlap should NOT get zoompan,
    because the source is effectively a static frame with the zoom
    layered on top, which reads as a frozen photo.
    """
    span_start, span_end = span_ms
    if span_end <= span_start:
        return False
    for tag in asset.tags:
        if tag.tag_type != "emotion" or tag.tag_name == "dominant":
            continue
        for r in tag.time_ranges_ms or []:
            if not isinstance(r, list | tuple) or len(r) != 2:
                continue
            try:
                r_start = int(r[0])
                r_end = int(r[1])
            except (TypeError, ValueError):
                continue
            if min(r_end, span_end) > max(r_start, span_start):
                return True
    return False


# v0.21 — subject-class filter padding. Each side of the asset's
# subject-present window is widened by this amount before clamping the
# planner's chosen span, so a cut doesn't begin / end exactly on the
# first / last YOLO detection (those edge frames are typically lower-
# confidence with the subject only partially in frame).
SUBJECT_PADDING_MS: int = 500
# v0.21.5 — gap between consecutive detections that's bigger than this
# splits a continuous run into two separate "presence windows". YOLO
# samples at TRACKING_SAMPLE_FPS = 5 Hz (~200 ms per sample), so a
# 1500 ms gap allows ~7 missed frames before we declare the subject has
# left the scene. Without the split, an asset where the dog appears at
# t=1 s and t=9 s would have a "presence range" of [0.5, 9.5] s and
# clamping the LLM's span into [3, 7] s would produce 4 seconds of
# floor-only footage — which is exactly the bug report.
SUBJECT_GAP_TOLERANCE_MS: int = 1500
# v0.21.5 — drop windows shorter than this so a single-frame YOLO
# flicker doesn't become a 200 ms cut. 1500 ms ≈ the planner's
# minimum span across all style presets.
SUBJECT_MIN_WINDOW_MS: int = 1500
# v0.42.3 — do not let YOLO noise satisfy a project subject filter.
# Object tracking already hides tracks shorter than 5 sampled frames from
# user-facing pickers; the planner should apply the same floor before it
# trusts a class-specific presence window for one-click auto edits.
SUBJECT_MIN_TRACK_FRAMES: int = 5
# A 0.30 detection can be useful for manual review, but auto planning
# should not treat a barely-there class as proof that the requested
# subject was visible. Keep this below the real-car examples around
# 0.44 while rejecting the 0.35-ish noise clips seen in production.
SUBJECT_MIN_TRACK_CONFIDENCE: float = 0.40

# If the model picks the very start of an asset and that first beat is
# handheld setup movement, begin after that setup whenever enough clip
# remains. This avoids opening a cut on the operator lifting/reframing
# the camera while preserving deliberate pan/tilt motion later in the cut.
UNSTABLE_OPENING_MOTION_SKIP_MS: int = 2_000


def _subject_presence_windows_ms(asset: Asset, subject_class: str) -> list[tuple[int, int]]:
    """v0.21.5 — return the list of contiguous time windows in this
    asset where ``subject_class`` is detected.

    A "window" is a run of detections with no gap larger than
    ``SUBJECT_GAP_TOLERANCE_MS``. Each window is then padded
    ±``SUBJECT_PADDING_MS`` and clamped to the asset's duration;
    windows shorter than ``SUBJECT_MIN_WINDOW_MS`` after padding are
    discarded so we don't ship sub-second flicker cuts.

    Reads the v0.17 ``tracks`` array; falls back to the legacy top-level
    ``frames`` field iff its ``subject_class`` matches (pre-v0.17
    assets without a per-class tracks list still work).

    Returns ``[]`` when the class never appears, tracking never ran,
    or every candidate window is below the minimum length.
    """
    tracking = asset.tracking_json
    if not isinstance(tracking, dict):
        return []
    matched: list[int] = []
    tracks = tracking.get("tracks")
    if isinstance(tracks, list):
        for t in tracks:
            if not isinstance(t, dict):
                continue
            if t.get("cls_name") != subject_class:
                continue
            frames = t.get("frames", []) or []
            if not _is_reliable_subject_track(t, frames):
                continue
            for f in frames:
                if not isinstance(f, dict):
                    continue
                t_ms = f.get("t_ms")
                if isinstance(t_ms, int):
                    matched.append(t_ms)
    if not matched and tracking.get("subject_class") == subject_class:
        frames = tracking.get("frames", []) or []
        if not _is_reliable_subject_track(tracking, frames):
            return []
        for f in frames:
            if not isinstance(f, dict):
                continue
            t_ms = f.get("t_ms")
            if isinstance(t_ms, int):
                matched.append(t_ms)
    if not matched:
        return []
    matched.sort()
    asset_end = max(0, int(asset.duration_ms))
    raw_runs: list[tuple[int, int]] = []
    cur_start = matched[0]
    cur_end = matched[0]
    for ts in matched[1:]:
        if ts - cur_end > SUBJECT_GAP_TOLERANCE_MS:
            raw_runs.append((cur_start, cur_end))
            cur_start = ts
        cur_end = ts
    raw_runs.append((cur_start, cur_end))

    out: list[tuple[int, int]] = []
    for run_start, run_end in raw_runs:
        s = max(0, run_start - SUBJECT_PADDING_MS)
        e = min(asset_end, run_end + SUBJECT_PADDING_MS)
        if e - s >= SUBJECT_MIN_WINDOW_MS:
            out.append((s, e))
    return out


def _is_reliable_subject_track(track: dict[str, Any], frames: Any) -> bool:
    """Return True when a tracking row is strong enough for auto planning.

    Missing ``confidence`` is treated as legacy data and allowed; present
    confidence below the auto floor is treated as YOLO noise.
    """
    if not isinstance(frames, list) or len(frames) < SUBJECT_MIN_TRACK_FRAMES:
        return False
    conf = track.get("confidence")
    if conf is None:
        return True
    try:
        return float(conf) >= SUBJECT_MIN_TRACK_CONFIDENCE
    except (TypeError, ValueError):
        return False


def _avoid_unstable_opening_span(
    asset: Asset,
    span_ms: tuple[int, int],
) -> tuple[int, int]:
    """Move a cut start past initial handheld setup movement when possible."""
    span_start, span_end = span_ms
    if span_start < 0 or span_end <= span_start:
        return span_ms
    new_start = span_start
    for tag in asset.tags:
        if tag.tag_type != "motion" or tag.tag_name != "handheld":
            continue
        for r in tag.time_ranges_ms or []:
            if not isinstance(r, list | tuple) or len(r) != 2:
                continue
            try:
                motion_start = int(r[0])
                motion_end = int(r[1])
            except (TypeError, ValueError):
                continue
            if motion_start > 0 or motion_end > UNSTABLE_OPENING_MOTION_SKIP_MS:
                continue
            if span_start < motion_end and span_end - motion_end >= MIN_SPAN_MS:
                new_start = max(new_start, motion_end)
    if new_start != span_start:
        logger.info(
            "unstable-opening-filter: asset=%d span=(%d,%d) -> (%d,%d)",
            asset.id,
            span_start,
            span_end,
            new_start,
            span_end,
        )
    return new_start, span_end


def _subject_presence_range_ms(asset: Asset, subject_class: str) -> tuple[int, int] | None:
    """Backwards-compat wrapper used by ``heuristic_fallback`` —
    returns the LONGEST contiguous window or ``None`` when the
    subject never appears (or every window is below the minimum
    length). Pre-v0.21.5 callers got a single ``min..max`` range; the
    longest-window choice is the closest 1-tuple approximation that
    still drops floor-only stretches between sparse appearances.
    """
    windows = _subject_presence_windows_ms(asset, subject_class)
    if not windows:
        return None
    return max(windows, key=lambda w: w[1] - w[0])


def _apply_subject_filter(
    scores: list[_AssetScore],
    *,
    assets: tuple[Asset, ...],
    subject_class: str | None,
) -> list[_AssetScore]:
    """v0.21.5 — drop assets where the subject is never visible long
    enough; clamp survivors' ``best_span_ms`` into the contiguous
    window with the most overlap with the LLM's pick.

    Algorithm:
      1. Compute contiguous presence windows for the asset.
      2. If none qualify (missing tracking or all windows shorter
         than ``SUBJECT_MIN_WINDOW_MS``) → drop the asset.
      3. Pick the window with the largest overlap with
         ``best_span_ms``. Clamp the span into that window
         (intersection).
      4. If no window overlaps the LLM's pick at all, snap to the
         LONGEST window (preserves the asset rather than dropping
         it for a missed LLM pick).

    ``None`` for ``subject_class`` is the no-op. Logs every per-asset
    decision so production traces of "auto-trim isn't working" are
    cheap to diagnose.
    """
    if not subject_class:
        return scores
    by_id = {a.id: a for a in assets}
    out: list[_AssetScore] = []
    drop_count = 0
    for s in scores:
        asset = by_id.get(s.asset_id)
        if asset is None:
            continue
        windows = _subject_presence_windows_ms(asset, subject_class)
        if not windows:
            logger.info(
                "subject-filter: drop asset=%d (subject_class=%r not present long enough)",
                s.asset_id,
                subject_class,
            )
            drop_count += 1
            continue
        span_start, span_end = s.best_span_ms
        best_overlap = -1
        chosen = windows[0]
        for ws, we in windows:
            overlap = max(0, min(we, span_end) - max(ws, span_start))
            if overlap > best_overlap:
                best_overlap = overlap
                chosen = (ws, we)
        if best_overlap > 0:
            ws, we = chosen
            new_start = max(span_start, ws)
            new_end = min(span_end, we)
            action = "clamp"
        else:
            longest = max(windows, key=lambda w: w[1] - w[0])
            new_start, new_end = longest
            action = "snap-longest"
        new_start, new_end = _avoid_unstable_opening_span(asset, (new_start, new_end))
        logger.info(
            "subject-filter: %s asset=%d windows=%s llm_span=(%d,%d) -> (%d,%d)",
            action,
            s.asset_id,
            windows,
            span_start,
            span_end,
            new_start,
            new_end,
        )
        out.append(replace(s, best_span_ms=(new_start, new_end)))
    logger.info(
        "subject-filter: subject_class=%r kept=%d dropped=%d",
        subject_class,
        len(out),
        drop_count,
    )
    return out


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


def _parse_asset_score_from_text(
    text: str,
    *,
    asset_id: int,
    asset_duration_ms: int,
    style: StylePresetParams = STYLE_PRESET_CUSTOM,
) -> _AssetScore:
    """Validate one per-asset score response from text. Raises EditPlanInvalidError."""
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
    # Clamp into [0, duration] using the style's span bounds (or the
    # legacy MIN_SPAN_MS / MAX_SPAN_MS for the ``custom`` preset).
    min_span = style.min_span_ms
    max_span = style.max_span_ms
    start_ms = max(0, min(start_ms, asset_duration_ms - 1))
    end_ms = max(start_ms + min_span, min(end_ms, asset_duration_ms))
    if end_ms - start_ms > max_span:
        end_ms = start_ms + max_span
    if end_ms > asset_duration_ms:
        # Asset shorter than min_span — return the whole asset and let
        # assembly decide whether to keep it.
        end_ms = asset_duration_ms
        start_ms = max(0, end_ms - min_span)

    kind = str(data.get("source_kind", "")).strip().lower()
    if kind not in _VALID_SOURCE_KINDS:
        raise EditPlanInvalidError(
            f"asset {asset_id}: source_kind must be in {_VALID_SOURCE_KINDS}, got {kind!r}"
        )

    # Coerce unknown / missing transition to the style's default rather
    # than rejecting the whole response. A model that picks a transition
    # outside the style allowlist gets snapped to the preset default.
    transition = str(data.get("transition_to_next", "")).strip().lower()
    if transition not in style.transition_allowlist:
        transition = style.default_transition

    reason = str(data.get("reason", "")).strip() or "(no reason)"
    return _AssetScore(
        asset_id=asset_id,
        score=score,
        position=position,
        best_span_ms=(start_ms, end_ms),
        source_kind=kind,
        reason=reason,
        transition_to_next=transition,
    )


def _parse_asset_score(
    payload: dict[str, Any],
    *,
    asset_id: int,
    asset_duration_ms: int,
    style: StylePresetParams = STYLE_PRESET_CUSTOM,
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
    return _parse_asset_score_from_text(
        text,
        asset_id=asset_id,
        asset_duration_ms=asset_duration_ms,
        style=style,
    )


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
    if (
        position_preference == "dynamic"
        and candidate.dominant_motion in DYNAMIC_MOTIONS
        or position_preference == "static"
        and candidate.dominant_motion in STATIC_MOTIONS
    ):
        score += _MOTION_POSITION_BONUS
    return score


def _dedup_by_asset(scores: list[_AssetScore]) -> list[_AssetScore]:
    """Collapse multiple ``_AssetScore`` rows for the same asset.

    Per-asset fanout produces one score per asset, but a defensive
    pass keeps the assembler safe against future shapes (e.g. multi-
    span scoring) and against malformed serialised plans fed back
    through this path. When duplicates exist, keep the highest-score
    row so we don't downgrade a known-good pick.
    """
    by_id: dict[int, _AssetScore] = {}
    for s in scores:
        existing = by_id.get(s.asset_id)
        if existing is None or s.score > existing.score:
            by_id[s.asset_id] = s
    # Preserve original ordering for determinism on ties; iterate the
    # input once more and keep only the winners selected above.
    seen: set[int] = set()
    out: list[_AssetScore] = []
    for s in scores:
        winner = by_id.get(s.asset_id)
        if winner is None or s.asset_id in seen:
            continue
        out.append(winner)
        seen.add(s.asset_id)
    return out


def _effective_target_ms(target_duration_ms: int, num_chosen: int) -> int:
    """Bias the stop-threshold up by total xfade overlap.

    Each xfade between adjacent cuts shortens the rendered timeline by
    ``_TRANSITION_OVERLAP_MS``; the planner's accumulated tally is the
    raw span sum, so without this bias an N-cut plan rendered with
    xfade lands ~N*500ms short of the target.
    """
    return target_duration_ms + max(0, num_chosen) * _TRANSITION_OVERLAP_MS


def _extended_span(
    score: _AssetScore,
    extra_ms: int,
    *,
    max_span_ms: int = MAX_SPAN_MS,
) -> tuple[int, int]:
    """Stretch ``best_span_ms`` by up to ``extra_ms`` without exceeding
    ``max_span_ms`` (style-aware) or running past the asset's actual
    duration.

    Used by the duration-fill pass after every candidate is exhausted
    but the accumulated total is still under target. Grows the span
    forwards first (more natural for talking-head footage where the
    sentence continues), then backwards if the asset still allows it.
    """
    start, end = score.best_span_ms
    if extra_ms <= 0:
        return start, end
    asset_end = max(end, score.asset_duration_ms)
    span_cap = min(max_span_ms, asset_end - 0)  # never exceed asset
    cur = end - start
    room_ahead = max(0, asset_end - end)
    grow_ahead = min(extra_ms, room_ahead, max(0, span_cap - cur))
    end += grow_ahead
    cur += grow_ahead
    remaining = extra_ms - grow_ahead
    if remaining > 0:
        room_back = max(0, start)
        grow_back = min(remaining, room_back, max(0, span_cap - cur))
        start -= grow_back
    return max(0, start), end


def _assemble_plan(
    scores: list[_AssetScore],
    target_duration_ms: int,
    *,
    style: StylePresetParams = STYLE_PRESET_CUSTOM,
) -> list[CutPlanSegment]:
    """Local cut-plan assembly with rhythm-aware motion ordering.

    Five passes:
      1. **Dedup** by ``asset_id`` (defensive — keep highest score).
      2. **Primary** bucketed walk (opening → middle → closing) using
         the rhythm-adjusted score.
      3. **Duration-fill** — when the primary pass left the timeline
         short of target (sparse buckets, low scores, or simply too
         few clips), pull from the dropped pool sorted by raw score.
      4. **Span-extend** — if still short after fill, stretch chosen
         spans up to ``MAX_SPAN_MS`` proportionally.
      5. **Materialise** ``CutPlanSegment`` rows, escalating the
         transition to ``circlecrop`` across emotion-bucket boundaries.

    The stop threshold accounts for the renderer's xfade overlap so
    the rendered timeline lands at ``target_duration_ms`` rather than
    ``target_duration_ms - (N-1) * 500ms``.
    """
    scores = _dedup_by_asset(scores)
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
    chosen_ids: set[int] = set()
    accumulated = 0
    # Soft over-budget cap during the primary pass so we don't blow
    # past target with one giant span. The fill / extend passes use
    # the same cap; raising it during fill would just produce a too-
    # long reel that still feels under-curated.
    max_target = int(_effective_target_ms(target_duration_ms, num_chosen=8) * 1.2)

    # ---- Primary pass: bucketed rhythm-aware picker.
    for bucket in _POSITION_ORDER:
        candidates = list(by_pos[bucket])
        position_pref = _bucket_motion_preference(bucket)
        while candidates:
            prev_motion = chosen[-1].dominant_motion if chosen else None
            best_idx = max(
                range(len(candidates)),
                key=lambda i: (
                    _rhythm_score(
                        candidates[i],
                        prev_motion=prev_motion,
                        position_preference=position_pref,
                    ),
                    candidates[i].score,
                ),
            )
            s = candidates.pop(best_idx)
            if s.asset_id in chosen_ids:
                continue
            span_dur = s.best_span_ms[1] - s.best_span_ms[0]
            if accumulated + span_dur > max_target and chosen:
                continue
            chosen.append(s)
            chosen_ids.add(s.asset_id)
            accumulated += span_dur
            stop_at = _effective_target_ms(target_duration_ms, num_chosen=len(chosen))
            if accumulated >= stop_at:
                break
        stop_at = _effective_target_ms(target_duration_ms, num_chosen=len(chosen))
        if accumulated >= stop_at:
            break

    # ---- Duration-fill pass: pull more cuts from the dropped pool when
    # the primary pass under-shot. Includes both below-threshold scores
    # and ``position=="skip"`` rows as a last resort — better to use a
    # mediocre clip than ship a 12-second reel for a 60-second target.
    if accumulated < _effective_target_ms(target_duration_ms, num_chosen=len(chosen)):
        leftovers = [s for s in scores if s.asset_id not in chosen_ids]
        # Below-threshold non-skip first (sorted by score); skip-marked
        # last so we only touch them when nothing else fits.
        below = sorted(
            (s for s in leftovers if s.position != "skip" and s.score < MIN_KEEP_SCORE),
            key=lambda x: -x.score,
        )
        skips = sorted(
            (s for s in leftovers if s.position == "skip"),
            key=lambda x: -x.score,
        )
        for s in [*below, *skips]:
            span_dur = s.best_span_ms[1] - s.best_span_ms[0]
            if accumulated + span_dur > max_target and chosen:
                continue
            chosen.append(s)
            chosen_ids.add(s.asset_id)
            accumulated += span_dur
            stop_at = _effective_target_ms(target_duration_ms, num_chosen=len(chosen))
            if accumulated >= stop_at:
                break

    # ---- Span-extend pass: still short after fill → stretch chosen
    # spans up to MAX_SPAN_MS / asset bounds. Distributes the deficit
    # roughly evenly so we don't blow one cut into a 6-second monolog.
    extended_spans: dict[int, tuple[int, int]] = {i: c.best_span_ms for i, c in enumerate(chosen)}
    stop_at = _effective_target_ms(target_duration_ms, num_chosen=len(chosen))
    deficit = stop_at - accumulated
    if deficit > 0 and chosen:
        per_cut_extra = (deficit + len(chosen) - 1) // len(chosen)
        for i, c in enumerate(chosen):
            new_span = _extended_span(c, per_cut_extra, max_span_ms=style.max_span_ms)
            cur_span = extended_spans[i]
            cur_dur = cur_span[1] - cur_span[0]
            new_dur = new_span[1] - new_span[0]
            gain = new_dur - cur_dur
            if gain <= 0:
                continue
            extended_spans[i] = new_span
            accumulated += gain
            if accumulated >= stop_at:
                break

    # ---- Materialise. Escalate transition to ``circlecrop`` whenever
    # the next cut's dominant emotion is a different bucket (dynamic
    # vs static), so the visual jolt mirrors the emotional jolt — but
    # only if the style preset's allowlist permits it (slow / artistic
    # presets that ban circlecrop fall back to the style default).
    out: list[CutPlanSegment] = []
    for i, s in enumerate(chosen):
        next_emotion = chosen[i + 1].dominant_emotion if i + 1 < len(chosen) else None
        transition = s.transition_to_next
        if next_emotion is not None and _is_emotion_shift(s.dominant_emotion, next_emotion):
            transition = (
                _EMOTION_SHIFT_TRANSITION
                if _EMOTION_SHIFT_TRANSITION in style.transition_allowlist
                else style.default_transition
            )
        if transition not in style.transition_allowlist:
            transition = style.default_transition
        start, end = extended_spans[i]
        out.append(
            CutPlanSegment(
                order=i,
                asset_id=s.asset_id,
                asset_start_ms=start,
                asset_end_ms=end,
                source_kind=s.source_kind,
                reason=s.reason,
                transition_to_next=transition,
                dominant_emotion=s.dominant_emotion,
                dominant_motion=s.dominant_motion,
                has_face=s.has_face,
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
    # v0.14.4 — concatenated user feedback from prior versions of this
    # project's draft (latest comments + last prompt_feedback). Empty
    # string for first-render projects. Surfaces inside the per-asset
    # Gemini prompt so the model can adjust scoring based on what the
    # operator told it last time.
    prior_feedback: str = ""


async def _load_prior_feedback(session: AsyncSession, project_id: int) -> str:
    """Pull operator feedback from earlier draft versions of this project.

    Two sources, concatenated newest-first:
      * ``DraftComment.body`` rows from the most recent ready / approved
        / failed draft (the version the user actually reacted to).
      * ``Draft.prompt_feedback`` from the same draft (the structured
        rejection note from the patch endpoint).

    Returns an empty string when this is the first render for the
    project so the planner can branch on truthiness without worrying
    about ``None``. Capped at ~2000 chars so a long discussion thread
    can't blow the per-asset prompt context.
    """
    latest = (
        await session.execute(
            select(Draft)
            .where(Draft.project_id == project_id)
            .where(Draft.status != DraftStatus.PENDING.value)
            .where(Draft.status != DraftStatus.PROCESSING.value)
            .order_by(Draft.version.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if latest is None:
        return ""

    parts: list[str] = []
    if latest.prompt_feedback:
        parts.append(f"前一版（v{latest.version}）回饋：{latest.prompt_feedback.strip()}")

    comments = (
        (
            await session.execute(
                select(DraftComment)
                .where(DraftComment.draft_id == latest.id)
                .order_by(DraftComment.created_at.desc())
                .limit(20)
            )
        )
        .scalars()
        .all()
    )
    for c in comments:
        body = (c.body or "").strip()
        if not body:
            continue
        parts.append(f"留言（{c.author}）：{body}")

    joined = "\n".join(parts)
    if len(joined) > 2000:
        joined = joined[:2000] + "…(已截斷)"
    return joined


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

    prior_feedback = await _load_prior_feedback(session, project_id)

    return _ProjectContext(
        project=project,
        script_body=script_body,
        assets=assets,
        transcripts=transcripts,
        coverage=coverage,
        asset_bounds={a.id: int(a.duration_ms) for a in assets},
        prior_feedback=prior_feedback,
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
    prior_feedback: str = "",
    style: StylePresetParams = STYLE_PRESET_CUSTOM,
    opencode_config: OpenCodeConfig | None = None,
) -> _AssetScore:
    """Single-asset score call — OpenCode primary, Gemini fallback."""
    prompt = _build_asset_prompt(
        asset,
        transcript,
        coverage,
        script_body,
        prior_feedback=prior_feedback,
        style=style,
    )

    # OpenCode primary
    if opencode_config is not None:
        from media_processor.services.opencode_client import call_opencode_text

        for server_url in opencode_config.servers:
            text = await call_opencode_text(
                prompt=prompt,
                server_url=server_url,
                password=opencode_config.password,
                model=opencode_config.model,
                variant=opencode_config.variant,
                timeout_s=opencode_config.timeout_s,
            )
            if text:
                try:
                    parsed = _parse_asset_score_from_text(
                        text,
                        asset_id=asset.id,
                        asset_duration_ms=int(asset.duration_ms),
                        style=style,
                    )
                    best_span_ms = _avoid_unstable_opening_span(asset, parsed.best_span_ms)
                    return replace(
                        parsed,
                        best_span_ms=best_span_ms,
                        dominant_motion=_dominant_motion_for_span(asset, best_span_ms),
                        dominant_emotion=_dominant_emotion_for_asset(asset),
                        asset_duration_ms=int(asset.duration_ms),
                        has_face=_has_face_in_span(asset, best_span_ms),
                    )
                except EditPlanInvalidError as exc:
                    logger.warning(
                        "opencode score asset=%d invalid; trying next: %s",
                        asset.id,
                        exc,
                    )
        logger.warning(
            "opencode score asset=%d: all servers failed; falling back to Gemini",
            asset.id,
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
                style=style,
            )
            best_span_ms = _avoid_unstable_opening_span(asset, parsed.best_span_ms)
            # Attach motion + emotion context for rhythm-aware assembly
            # and renderer-side zoompan / transition decisions. We do
            # this server-side rather than asking Gemini to echo back
            # the tags so the model can't accidentally rewrite them.
            return replace(
                parsed,
                best_span_ms=best_span_ms,
                dominant_motion=_dominant_motion_for_span(asset, best_span_ms),
                dominant_emotion=_dominant_emotion_for_asset(asset),
                asset_duration_ms=int(asset.duration_ms),
                has_face=_has_face_in_span(asset, best_span_ms),
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
    style_preset: str = "custom",
    opencode_config: OpenCodeConfig | None = None,
) -> CutPlan:
    """Build a CutPlan via per-asset parallel calls + local assembly.

    Tries OpenCode-primary per asset when configured; falls back to Gemini
    key-pool rotation. Fanned out concurrently so one failed asset does not
    poison the batch. The caller falls back to heuristic_fallback if every
    asset call fails.
    """
    if not api_keys and opencode_config is None:
        raise EditPlanError("no API keys or OpenCode servers configured for edit planner")

    style = resolve_style_preset(style_preset)
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
                key_offset=i,
                model=model,
                base_url=base_url,
                timeout_s=timeout_s,
                client=client,
                prior_feedback=ctx.prior_feedback,
                style=style,
                opencode_config=opencode_config,
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
                f"all {len(results)} per-asset calls quota-exhausted across {len(api_keys)} keys"
            )
        if last_invalid is not None:
            raise last_invalid
        raise EditPlanError(
            f"all {len(results)} per-asset calls failed "
            f"(quota={quota_failures}, invalid={invalid_failures}, other={other_failures})"
        )

    pre_filter_count = len(scores)
    scores = _apply_subject_filter(
        scores,
        assets=ctx.assets,
        subject_class=ctx.project.subject_class,
    )
    if not scores:
        raise EditPlanInvalidError(
            f"subject_class={ctx.project.subject_class!r} never appeared in any "
            f"of {pre_filter_count} scored assets"
        )

    cut_segments = _assemble_plan(scores, target_duration_ms, style=style)
    if not cut_segments:
        raise EditPlanInvalidError(
            f"assembly produced no segments from {len(scores)} scored assets "
            f"(all skipped or below threshold)"
        )

    notes = (
        f"per-asset fanout: {len(scores)}/{len(results)} assets scored "
        f"(quota_fails={quota_failures}, invalid={invalid_failures}); "
        f"chose {len(cut_segments)} cuts totalling "
        f"{sum(s.asset_end_ms - s.asset_start_ms for s in cut_segments)}ms; "
        f"style={style.name}"
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
    subject_class = ctx.project.subject_class

    def _clamp_to_presence(
        start: int, end: int, presence: tuple[int, int] | None
    ) -> tuple[int, int] | None:
        """Intersect ``(start, end)`` with the asset's subject-presence
        window. Returns ``None`` when there's no overlap; the caller
        skips that segment (heuristic fallback prefers many short cuts
        over one long snapped span)."""
        if presence is None:
            return start, end
        new_start = max(start, presence[0])
        new_end = min(end, presence[1])
        if new_end <= new_start:
            return None
        return new_start, new_end

    segments: list[CutPlanSegment] = []
    accumulated_ms = 0
    order = 0
    for asset in ctx.assets:
        if subject_class:
            presence = _subject_presence_range_ms(asset, subject_class)
            if presence is None:
                continue
        else:
            presence = None
        tx = ctx.transcripts.get(asset.id)
        raw = list(tx.segments_json or []) if tx is not None else []
        if not raw:
            # Asset with no transcript: take a single 3-second middle slice
            # so a no-script project still yields *something*.
            mid = max(0, asset.duration_ms // 2 - 1500)
            end = min(asset.duration_ms, mid + 3000)
            if end > mid:
                clamped = _clamp_to_presence(mid, end, presence)
                if clamped is not None:
                    cs, ce = _avoid_unstable_opening_span(asset, clamped)
                    segments.append(
                        CutPlanSegment(
                            order=order,
                            asset_id=asset.id,
                            asset_start_ms=cs,
                            asset_end_ms=ce,
                            source_kind="improv",
                            reason="fallback: middle slice",
                            dominant_emotion=_dominant_emotion_for_asset(asset),
                            dominant_motion=_dominant_motion_for_span(asset, (cs, ce)),
                            has_face=_has_face_in_span(asset, (cs, ce)),
                        )
                    )
                    accumulated_ms += ce - cs
                    order += 1
        else:
            for seg in raw:
                start = int(seg.get("start_ms", 0))
                end = int(seg.get("end_ms", 0))
                if end <= start:
                    continue
                clamped = _clamp_to_presence(start, end, presence)
                if clamped is None:
                    continue
                cs, ce = _avoid_unstable_opening_span(asset, clamped)
                segments.append(
                    CutPlanSegment(
                        order=order,
                        asset_id=asset.id,
                        asset_start_ms=cs,
                        asset_end_ms=ce,
                        source_kind="improv",
                        reason="fallback: transcript segment",
                        dominant_emotion=_dominant_emotion_for_asset(asset),
                        dominant_motion=_dominant_motion_for_span(asset, (cs, ce)),
                        has_face=_has_face_in_span(asset, (cs, ce)),
                    )
                )
                accumulated_ms += ce - cs
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
                "dominant_motion": s.dominant_motion,
                "has_face": s.has_face,
                # v0.30.0 — opt-in smart camera directive. ``None`` for
                # legacy / unscanned segments; the renderer treats
                # ``None`` as "no camera move".
                "smart_camera_json": s.smart_camera_json,
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
                transition_to_next=_coerce_legacy_transition(
                    str(seg.get("transition_to_next", TRANSITION_DEFAULT))
                ),
                dominant_emotion=str(seg.get("dominant_emotion", EMOTION_DEFAULT)),
                dominant_motion=str(seg.get("dominant_motion", _MOTION_DEFAULT)),
                has_face=bool(seg.get("has_face", False)),
                # v0.30.0 — preserve ``None`` distinctly from a missing
                # key so legacy plans (no key) and scanned-but-no-move
                # segments (explicit ``None``) round-trip the same way.
                smart_camera_json=(
                    dict(seg["smart_camera_json"])
                    if isinstance(seg.get("smart_camera_json"), dict)
                    else None
                ),
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
    "STYLE_PRESETS",
    "CutPlan",
    "CutPlanSegment",
    "EditPlanEmptyError",
    "EditPlanError",
    "EditPlanInvalidError",
    "EditPlanQuotaError",
    "StylePresetParams",
    "deserialise_plan",
    "heuristic_fallback",
    "plan",
    "resolve_style_preset",
    "serialise_plan",
]
