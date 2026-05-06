"""SRT subtitle generation for M5 auto-edit.

Given a ``CutPlan`` (the plan order maps onto the timeline) and a per-asset
transcript map, build SRT cues clipped to each cut and remapped from the
asset's local timeline onto the rendered output's timeline.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from media_processor.models import AssetTranscript
from media_processor.services.edit_planner import CutPlan, CutPlanSegment

# Display rules. Constants live here so the renderer doesn't drift from
# the SRT generator.
MAX_LINE_CHARS = 12
MAX_LINES = 2
MIN_DISPLAY_MS = 700

# v0.18 — secondary-language cues use looser per-line caps because
# English is wider per character than zh-Hant; matching the primary
# 12-char cap would force premature line-wraps and overflow vertically.
SECONDARY_MAX_LINE_CHARS = 28
SECONDARY_MAX_LINES = 2

# M6.3 introduced xfade transitions between adjacent cuts: each pair
# overlaps by ``video_renderer.TRANSITION_DURATION_S`` on the rendered
# timeline, so cut N starts at ``sum(d_i for i<N) - N * TRANSITION_MS``
# rather than the simple sum. The subtitle generator must shrink its
# per-cut advance by the same amount or every cut after the first lands
# late on the rendered video. Mirrored from video_renderer to avoid an
# import cycle.
TRANSITION_OVERLAP_MS = 500


@dataclass(frozen=True)
class SubtitleCue:
    """A single SRT cue, timeline-anchored."""

    sequence: int
    timeline_start_ms: int
    timeline_end_ms: int
    text: str


def _format_timecode(ms: int) -> str:
    if ms < 0:
        ms = 0
    total_seconds, millis = divmod(int(ms), 1000)
    minutes, seconds = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def _wrap_text(raw: str) -> str:
    """Clamp to ``MAX_LINES`` × ``MAX_LINE_CHARS`` chars.

    zh-Hant has no word boundaries, so wrap by character count. Lines
    overflow gets ellipsised so the burned-in subtitle never grows the
    font box past its 2-line height.
    """
    text = raw.strip().replace("\n", " ")
    if not text:
        return ""
    lines: list[str] = []
    cursor = 0
    while cursor < len(text) and len(lines) < MAX_LINES:
        lines.append(text[cursor : cursor + MAX_LINE_CHARS])
        cursor += MAX_LINE_CHARS
    if cursor < len(text) and lines:
        # Trim the last line and append an ellipsis.
        lines[-1] = lines[-1][: max(0, MAX_LINE_CHARS - 1)] + "…"
    return "\n".join(lines)


def _wrap_secondary(raw: str) -> str:
    """Wrap an English (secondary) cue at word boundaries.

    Different from :func:`_wrap_text`: English needs word-aware breaks
    or a wrap mid-word looks broken. Falls back to a hard char-count
    break only when a single word exceeds ``SECONDARY_MAX_LINE_CHARS``.
    """
    text = raw.strip().replace("\n", " ")
    if not text:
        return ""
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        if len(lines) >= SECONDARY_MAX_LINES:
            break
        candidate = (current + " " + word).strip() if current else word
        if len(candidate) <= SECONDARY_MAX_LINE_CHARS:
            current = candidate
            continue
        if current:
            lines.append(current)
            current = ""
        if len(lines) >= SECONDARY_MAX_LINES:
            break
        if len(word) > SECONDARY_MAX_LINE_CHARS:
            # Pathological single token (URL, token-glob): hard split.
            current = word[: SECONDARY_MAX_LINE_CHARS - 1] + "…"
            continue
        current = word
    if current and len(lines) < SECONDARY_MAX_LINES:
        lines.append(current)
    if not lines:
        return ""
    # Ellipsise if there were words we couldn't fit.
    consumed_chars = sum(len(line) for line in lines) + len(lines) - 1
    if consumed_chars < len(text) and lines:
        last = lines[-1]
        lines[-1] = last[: max(0, SECONDARY_MAX_LINE_CHARS - 1)].rstrip() + "…"
    return "\n".join(lines)


def _clip_transcript_to_cut(
    transcript_segments: list[dict[str, Any]],
    cut: CutPlanSegment,
) -> list[tuple[int, int, str]]:
    """Return [(local_start_ms, local_end_ms, text), …] inside the cut.

    Local timestamps are relative to ``cut.asset_start_ms`` so they can be
    added to the timeline offset without further math.
    """
    out: list[tuple[int, int, str]] = []
    cut_start, cut_end = cut.asset_start_ms, cut.asset_end_ms
    for seg in transcript_segments:
        try:
            seg_start = int(seg.get("start_ms", 0))
            seg_end = int(seg.get("end_ms", 0))
        except (TypeError, ValueError):
            continue
        if seg_end <= cut_start or seg_start >= cut_end:
            continue
        text = str(seg.get("text", "")).strip()
        if not text:
            continue
        clipped_start = max(seg_start, cut_start) - cut_start
        clipped_end = min(seg_end, cut_end) - cut_start
        if clipped_end <= clipped_start:
            continue
        out.append((clipped_start, clipped_end, text))
    return out


def build_cues(
    plan: CutPlan,
    transcripts: dict[int, AssetTranscript],
) -> list[SubtitleCue]:
    """Produce timeline-anchored SubtitleCue rows for the whole plan.

    Cuts are placed back-to-back on the timeline in plan order. A cut with
    no overlapping transcript text contributes zero cues but still advances
    the timeline cursor.
    """
    cues: list[SubtitleCue] = []
    timeline_cursor = 0
    sequence = 1
    is_first = True
    for cut in sorted(plan.segments, key=lambda s: s.order):
        cut_duration = cut.asset_end_ms - cut.asset_start_ms
        if cut_duration <= 0:
            continue
        # Every cut after the first overlaps the previous one by
        # ``TRANSITION_OVERLAP_MS`` because of xfade — pull the cursor back
        # by that amount before placing this cut's subtitles, mirroring
        # the renderer's cumulative offset arithmetic in
        # ``_build_xfade_filter``.
        if not is_first:
            timeline_cursor = max(0, timeline_cursor - TRANSITION_OVERLAP_MS)
        is_first = False
        tx = transcripts.get(cut.asset_id)
        raw_segments = list(tx.segments_json or []) if tx is not None else []
        clipped = _clip_transcript_to_cut(raw_segments, cut)
        for local_start, local_end, text in clipped:
            tl_start = timeline_cursor + local_start
            tl_end = timeline_cursor + local_end
            if tl_end - tl_start < MIN_DISPLAY_MS:
                tl_end = tl_start + MIN_DISPLAY_MS
            wrapped = _wrap_text(text)
            if not wrapped:
                continue
            cues.append(
                SubtitleCue(
                    sequence=sequence,
                    timeline_start_ms=tl_start,
                    timeline_end_ms=min(tl_end, timeline_cursor + cut_duration),
                    text=wrapped,
                )
            )
            sequence += 1
        timeline_cursor += cut_duration

    cues.sort(key=lambda c: c.timeline_start_ms)
    # Re-sequence after sort so SRT numbering is monotonic.
    return [
        SubtitleCue(
            sequence=i + 1,
            timeline_start_ms=c.timeline_start_ms,
            timeline_end_ms=c.timeline_end_ms,
            text=c.text,
        )
        for i, c in enumerate(cues)
    ]


_SRT_TC_RE = r"(?P<h>\d+):(?P<m>\d{1,2}):(?P<s>\d{1,2})[,.](?P<ms>\d{1,3})"


def _parse_timecode(token: str) -> int:
    """Reverse of ``_format_timecode`` — accept comma OR dot millis separator."""
    import re

    m = re.fullmatch(_SRT_TC_RE, token.strip())
    if m is None:
        raise ValueError(f"bad SRT timecode: {token!r}")
    return int(m["h"]) * 3_600_000 + int(m["m"]) * 60_000 + int(m["s"]) * 1_000 + int(m["ms"])


def parse_srt(text: str) -> list[SubtitleCue]:
    """Parse an SRT document back into ``SubtitleCue`` rows.

    Used by the M7.2 subtitle editor: after the initial subtitles stage
    runs, the orchestrator persists each parsed cue into ``subtitle_cues``
    so the user can edit text inline. ``rebuild-subtitles`` then writes a
    fresh SRT from the edited rows.

    Tolerant: blank-block separators are required, but extra trailing
    whitespace, BOM, and dot-vs-comma millis separators are accepted.
    Cues that fail to parse are skipped rather than raising.
    """
    if not text:
        return []
    raw = text.lstrip("﻿").strip()
    if not raw:
        return []
    blocks = [b.strip() for b in raw.replace("\r\n", "\n").split("\n\n") if b.strip()]
    cues: list[SubtitleCue] = []
    for block in blocks:
        lines = block.split("\n")
        # First line is sequence (digits); second line is "start --> end";
        # remaining lines are body. Some authoring tools omit the sequence —
        # fall back to position-based numbering.
        if len(lines) < 2:
            continue
        if lines[0].strip().isdigit():
            seq = int(lines[0].strip())
            time_line = lines[1].strip() if len(lines) >= 2 else ""
            body_lines = lines[2:]
        else:
            seq = len(cues) + 1
            time_line = lines[0].strip()
            body_lines = lines[1:]
        if "-->" not in time_line:
            continue
        start_token, end_token = (t.strip() for t in time_line.split("-->", 1))
        try:
            start_ms = _parse_timecode(start_token)
            end_ms = _parse_timecode(end_token.split()[0])
        except ValueError:
            continue
        body = "\n".join(body_lines).strip()
        if not body or end_ms <= start_ms:
            continue
        cues.append(
            SubtitleCue(
                sequence=seq,
                timeline_start_ms=start_ms,
                timeline_end_ms=end_ms,
                text=body,
            )
        )
    return cues


def render_srt(cues: list[SubtitleCue]) -> str:
    """Serialise cues to an SRT document. Returns an empty string if none."""
    if not cues:
        return ""
    blocks: list[str] = []
    for cue in cues:
        blocks.append(
            f"{cue.sequence}\n"
            f"{_format_timecode(cue.timeline_start_ms)} --> "
            f"{_format_timecode(cue.timeline_end_ms)}\n"
            f"{cue.text}\n"
        )
    return "\n".join(blocks).rstrip() + "\n"


def build_srt(plan: CutPlan, transcripts: dict[int, AssetTranscript]) -> str:
    """Convenience shortcut: cues + serialise in one call."""
    return render_srt(build_cues(plan, transcripts))


# ---------- v0.18 — secondary-language subtitle support ----------


def build_secondary_cues(
    plan: CutPlan,
    secondary_segments_by_asset: dict[int, list[dict[str, Any]]],
) -> list[SubtitleCue]:
    """Build timeline-anchored cues from per-asset translated segments.

    Mirrors :func:`build_cues` but reads from
    ``Asset.subtitle_secondary_segments_json`` instead of
    ``AssetTranscript.segments_json`` and uses the English-friendly
    word-boundary wrapper. Cuts whose source asset has no translation
    contribute zero secondary cues but still advance the timeline cursor
    (so primary + secondary stay in sync on the rendered timeline).
    """
    cues: list[SubtitleCue] = []
    timeline_cursor = 0
    sequence = 1
    is_first = True
    for cut in sorted(plan.segments, key=lambda s: s.order):
        cut_duration = cut.asset_end_ms - cut.asset_start_ms
        if cut_duration <= 0:
            continue
        if not is_first:
            timeline_cursor = max(0, timeline_cursor - TRANSITION_OVERLAP_MS)
        is_first = False
        raw_segments = list(secondary_segments_by_asset.get(cut.asset_id) or [])
        clipped = _clip_transcript_to_cut(raw_segments, cut)
        for local_start, local_end, text in clipped:
            tl_start = timeline_cursor + local_start
            tl_end = timeline_cursor + local_end
            if tl_end - tl_start < MIN_DISPLAY_MS:
                tl_end = tl_start + MIN_DISPLAY_MS
            wrapped = _wrap_secondary(text)
            if not wrapped:
                continue
            cues.append(
                SubtitleCue(
                    sequence=sequence,
                    timeline_start_ms=tl_start,
                    timeline_end_ms=min(tl_end, timeline_cursor + cut_duration),
                    text=wrapped,
                )
            )
            sequence += 1
        timeline_cursor += cut_duration

    cues.sort(key=lambda c: c.timeline_start_ms)
    return [
        SubtitleCue(
            sequence=i + 1,
            timeline_start_ms=c.timeline_start_ms,
            timeline_end_ms=c.timeline_end_ms,
            text=c.text,
        )
        for i, c in enumerate(cues)
    ]


def secondary_text_for_cut(
    cut: CutPlanSegment,
    asset_secondary_segments: list[dict[str, Any]] | None,
) -> str | None:
    """Join all secondary segments overlapping ``cut`` into a single line.

    Used by the orchestrator to populate
    ``DraftSegment.subtitle_secondary_text`` — a per-cut snapshot the
    SubtitleEditor can show without re-clipping per cue. Returns
    ``None`` when no overlapping segments exist (so the column stays
    NULL and the UI renders nothing).
    """
    if not asset_secondary_segments:
        return None
    clipped = _clip_transcript_to_cut(list(asset_secondary_segments), cut)
    if not clipped:
        return None
    return " ".join(text for _, _, text in clipped if text).strip() or None


__all__ = [
    "MAX_LINE_CHARS",
    "MAX_LINES",
    "MIN_DISPLAY_MS",
    "SECONDARY_MAX_LINES",
    "SECONDARY_MAX_LINE_CHARS",
    "TRANSITION_OVERLAP_MS",
    "SubtitleCue",
    "build_cues",
    "build_secondary_cues",
    "build_srt",
    "parse_srt",
    "render_srt",
    "secondary_text_for_cut",
]
