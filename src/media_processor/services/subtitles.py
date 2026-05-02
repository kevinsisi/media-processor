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
    for cut in sorted(plan.segments, key=lambda s: s.order):
        cut_duration = cut.asset_end_ms - cut.asset_start_ms
        if cut_duration <= 0:
            continue
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


__all__ = [
    "MAX_LINE_CHARS",
    "MAX_LINES",
    "MIN_DISPLAY_MS",
    "SubtitleCue",
    "build_cues",
    "build_srt",
    "render_srt",
]
