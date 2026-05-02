"""Local STT via faster-whisper, post-converted to Traditional Chinese.

The worker container loads the model once on first call and keeps it
resident for the worker's lifetime. ``WHISPER_FAKE=1`` swaps the engine for
a deterministic canned zh-Hant transcript so CI and any non-GPU dev box can
exercise the rest of the pipeline.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # heavy deps only present in the worker image.
    from faster_whisper import WhisperModel

logger = logging.getLogger(__name__)


_INITIAL_PROMPT = "以下是繁體中文影片逐字稿。"
_DEFAULT_LANGUAGE = "zh"
_OUTPUT_LANGUAGE_TAG = "zh-Hant"

# Cue-shaping caps — enforced by :func:`_regroup_words` so each emitted
# TranscriptSegment fits under the drawtext burn-in's 12-CJK-char width
# limit and stays on screen at most 3 s. Whisper's native segments are
# whole-sentence and routinely exceed both, which is why we re-bucket
# from word_timestamps instead of using the segment-level output.
SUBTITLE_MAX_CHARS: int = 12
SUBTITLE_MAX_SECONDS: float = 3.0

# Canned WHISPER_FAKE transcript — already shaped to the SUBTITLE_MAX_*
# caps so tests don't need to re-run the regrouper on synthetic words.
_FAKE_SEGMENTS: tuple[tuple[int, int, str], ...] = (
    (0, 1500, "今天介紹這個產品"),
    (1500, 3000, "設計輕巧好攜帶"),
    (3000, 4800, "適合一個人使用"),
    (4800, 6500, "包裝打開的瞬間"),
    (6500, 8500, "外觀整體質感不錯"),
    (8500, 10500, "重量比想像中輕"),
    (10500, 12500, "接下來實際操作"),
    (12500, 15000, "帶大家看看細節"),
)


class WhisperUnavailableError(RuntimeError):
    """Raised when CUDA initialisation fails or the audio file is missing."""


@dataclass(frozen=True)
class TranscriptSegment:
    idx: int
    start_ms: int
    end_ms: int
    text: str


@dataclass(frozen=True)
class _PseudoWord:
    """Stand-in for faster-whisper's Word when a segment lacks word_timestamps.

    Lets :func:`_regroup_words` treat the orphaned segment text as a single
    over-long token so the splitter can still produce sane cues from it.
    """

    start: float
    end: float
    word: str


@dataclass(frozen=True)
class TranscriptResult:
    language: str
    model: str
    segments: tuple[TranscriptSegment, ...]

    @property
    def transcript_text(self) -> str:
        return "\n".join(s.text for s in self.segments)


# Module-level cache so successive jobs in the same worker process reuse
# the loaded model (medium / int8_float16 takes ~10 s to load on cold start).
_model_cache: dict[tuple[str, str, str], WhisperModel] = {}


def _is_fake() -> bool:
    return str(os.environ.get("WHISPER_FAKE", "0")).strip() not in {"", "0", "false", "False"}


def _load_model(model_name: str, device: str, compute_type: str) -> WhisperModel:
    key = (model_name, device, compute_type)
    cached = _model_cache.get(key)
    if cached is not None:
        return cached
    from faster_whisper import WhisperModel  # local import — heavy

    logger.info(
        "loading faster-whisper model=%s device=%s compute_type=%s",
        model_name,
        device,
        compute_type,
    )
    try:
        model = WhisperModel(model_name, device=device, compute_type=compute_type)
    except Exception as exc:  # noqa: BLE001 — surface as typed error for the orchestrator
        raise WhisperUnavailableError(f"faster-whisper failed to initialise: {exc}") from exc
    _model_cache[key] = model
    return model


def _regroup_words(
    word_iter: list,
    *,
    max_chars: int = SUBTITLE_MAX_CHARS,
    max_seconds: float = SUBTITLE_MAX_SECONDS,
) -> list[tuple[float, float, str]]:
    """Re-bucket faster-whisper Word timestamps into ``(start_s, end_s, text)``.

    Cuts whichever limit hits first — character count or wall-clock duration.
    For Chinese, faster-whisper word_timestamps usually yield per-character
    or per-syllable units, so the count is a fair proxy for visual width.
    A single word that itself exceeds ``max_chars`` (rare) is sub-split with
    proportionally interpolated time spans so we never emit an oversize cue.
    """
    groups: list[tuple[float, float, str]] = []
    cur_start: float | None = None
    cur_end: float = 0.0
    cur_parts: list[str] = []
    cur_chars: int = 0

    def _flush() -> None:
        nonlocal cur_start, cur_end, cur_parts, cur_chars
        if cur_parts and cur_start is not None:
            text = "".join(cur_parts).strip()
            if text:
                groups.append((cur_start, cur_end, text))
        cur_start = None
        cur_end = 0.0
        cur_parts = []
        cur_chars = 0

    for w in word_iter:
        raw = getattr(w, "word", "") or ""
        stripped = raw.strip()
        n_chars = len(stripped)
        if n_chars == 0:
            continue
        w_start = float(getattr(w, "start", 0.0) or 0.0)
        w_end = float(getattr(w, "end", w_start) or w_start)

        if n_chars > max_chars:
            # Pathological single token — slice into max_chars chunks with
            # linearly interpolated timing so the rest of the algorithm
            # never sees an over-cap unit.
            _flush()
            duration = max(w_end - w_start, 0.0)
            for i in range(0, n_chars, max_chars):
                chunk = stripped[i : i + max_chars]
                chunk_start = w_start + duration * (i / n_chars)
                chunk_end = w_start + duration * (min(i + max_chars, n_chars) / n_chars)
                groups.append((chunk_start, chunk_end, chunk))
            continue

        if cur_start is None:
            cur_start = w_start

        new_chars = cur_chars + n_chars
        new_end = w_end
        exceeds = new_chars > max_chars or (new_end - cur_start) > max_seconds
        if cur_parts and exceeds:
            _flush()
            cur_start = w_start
            cur_parts = [raw]
            cur_chars = n_chars
            cur_end = w_end
        else:
            cur_parts.append(raw)
            cur_chars = new_chars
            cur_end = new_end

    _flush()
    return groups


def _convert_to_traditional(text: str) -> str:
    """Run OpenCC s2twp (Simplified → Traditional, Taiwan-region phrasing).

    Imported lazily so the api container (which never instantiates this
    module path beyond the type-only import) does not pay the OpenCC import
    cost just to run unrelated tests.
    """
    from opencc import OpenCC  # local import — heavy

    converter = OpenCC("s2twp.json")
    return str(converter.convert(text))


def transcribe(audio_path: Path | str) -> TranscriptResult:
    """Run STT on the given media file and return zh-Hant SRT-style segments.

    When ``WHISPER_FAKE=1``, returns the canned transcript regardless of
    input — including when the file does not exist (fake mode is for tests
    and dev boxes that do not have audio fixtures).
    """

    model_name = os.environ.get("WHISPER_MODEL", "medium")
    device = os.environ.get("WHISPER_DEVICE", "cuda")
    compute_type = os.environ.get("WHISPER_COMPUTE_TYPE", "int8_float16")
    fake = _is_fake()

    if fake:
        logger.info("WHISPER_FAKE=1 — returning canned zh-Hant transcript")
        segments = tuple(
            TranscriptSegment(idx=i, start_ms=s, end_ms=e, text=t)
            for i, (s, e, t) in enumerate(_FAKE_SEGMENTS)
        )
        return TranscriptResult(
            language=_OUTPUT_LANGUAGE_TAG,
            model="faster-whisper-fake",
            segments=segments,
        )

    audio_path_obj = Path(audio_path)
    if not audio_path_obj.exists():
        raise WhisperUnavailableError(f"audio file missing: {audio_path_obj}")

    model = _load_model(model_name, device, compute_type)
    raw_segments, info = model.transcribe(
        str(audio_path_obj),
        language=_DEFAULT_LANGUAGE,
        initial_prompt=_INITIAL_PROMPT,
        vad_filter=True,
        word_timestamps=True,
    )
    # Flatten word_timestamps from every segment into one stream, then
    # re-bucket. faster-whisper exposes words as ``segment.words`` (an
    # iterable of Word(start, end, word, probability)). If any segment
    # comes back without word-level data, fall back to that segment as a
    # single pseudo-word so we don't silently lose its text.
    all_words: list = []
    raw_segment_count = 0
    for seg in raw_segments:
        raw_segment_count += 1
        seg_words = list(getattr(seg, "words", None) or [])
        if seg_words:
            all_words.extend(seg_words)
        else:
            seg_text = (seg.text or "").strip()
            if seg_text:
                all_words.append(
                    _PseudoWord(
                        start=float(seg.start or 0.0),
                        end=float(seg.end or seg.start or 0.0),
                        word=seg_text,
                    )
                )

    grouped = _regroup_words(all_words)
    out: list[TranscriptSegment] = []
    for i, (start_s, end_s, text) in enumerate(grouped):
        traditional = _convert_to_traditional(text)
        if not traditional:
            continue
        out.append(
            TranscriptSegment(
                idx=i,
                start_ms=int(round(start_s * 1000)),
                end_ms=int(round(end_s * 1000)),
                text=traditional,
            )
        )

    logger.info(
        "transcribed %d whisper-segments → %d regrouped cues "
        "(max_chars=%d max_seconds=%.1f), detected_language=%r duration=%.1fs",
        raw_segment_count,
        len(out),
        SUBTITLE_MAX_CHARS,
        SUBTITLE_MAX_SECONDS,
        getattr(info, "language", None),
        getattr(info, "duration", 0.0),
    )

    return TranscriptResult(
        language=_OUTPUT_LANGUAGE_TAG,
        model=f"faster-whisper-{model_name}",
        segments=tuple(out),
    )


__all__ = [
    "TranscriptResult",
    "TranscriptSegment",
    "WhisperUnavailableError",
    "transcribe",
]
