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

# Canned WHISPER_FAKE transcript — 5 segments × ~3 s each, total ~15 s.
# Pure ASCII timing; text is stable Traditional Chinese so tests can assert
# on it byte-for-byte.
_FAKE_SEGMENTS: tuple[tuple[int, int, str], ...] = (
    (0, 3000, "今天我們來介紹這個產品。"),
    (3000, 6500, "它的設計非常輕巧，適合一個人使用。"),
    (6500, 9500, "在打開包裝之後，第一眼看到的就是它的外觀。"),
    (9500, 12500, "整體質感很不錯，重量也比想像中輕。"),
    (12500, 15000, "接下來我會帶大家實際操作一次。"),
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
    )
    out: list[TranscriptSegment] = []
    for i, seg in enumerate(raw_segments):
        text = _convert_to_traditional((seg.text or "").strip())
        if not text:
            continue
        out.append(
            TranscriptSegment(
                idx=i,
                start_ms=int(round((seg.start or 0.0) * 1000)),
                end_ms=int(round((seg.end or 0.0) * 1000)),
                text=text,
            )
        )

    logger.info(
        "transcribed %d segments, detected_language=%r duration=%.1fs",
        len(out),
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
