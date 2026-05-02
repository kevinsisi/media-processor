"""v0.15 — text-to-music generator backed by MusicGen-small.

Wraps ``transformers.MusicgenForConditionalGeneration`` so the worker
can render a 30-second wav from a free-form Chinese prompt. Heavy
imports (torch, transformers) live inside the lazy ``_load_pipeline``
helper so importing this module is cheap on the api side.

Test seam: ``MUSICGEN_FAKE=1`` short-circuits to a 30-second silent
wav. CI / non-GPU dev boxes can drive the rest of the pipeline
(prompt fanout, status polling, UI) without paying the model download
or inference cost.
"""

from __future__ import annotations

import logging
import os
import struct
import wave
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


# Defaults tuned for ~30 s output on the small MusicGen variant.
# ``facebook/musicgen-small`` writes 32 kHz mono int16. Token rate is
# 50 tokens/s of audio, so 30 s ≈ 1500 new tokens.
DEFAULT_MODEL: str = os.environ.get("MUSICGEN_MODEL", "facebook/musicgen-small")
DEFAULT_DURATION_S: int = int(os.environ.get("MUSICGEN_DURATION_S", "30"))
DEFAULT_SAMPLE_RATE: int = 32_000
DEFAULT_TOKENS_PER_SECOND: int = 50
GUIDANCE_SCALE: float = 3.0  # higher = stricter prompt adherence
TEMPERATURE: float = 1.0
GENERATION_TIMEOUT_S: float = 600.0  # outer cap; rq job lives longer


class MusicGenError(RuntimeError):
    """Generic generation failure (model load, inference, IO)."""


class MusicGenUnavailableError(MusicGenError):
    """transformers / torch missing or model failed to load."""


@dataclass(frozen=True)
class MusicGenResult:
    output_path: Path
    duration_s: float
    sample_rate: int
    model: str


def _is_fake() -> bool:
    """``MUSICGEN_FAKE=1`` swaps the engine for a deterministic silent
    wav so the rest of the pipeline (RQ job, DB row updates, UI polling)
    is testable without the model. Mirrors the ``WHISPER_FAKE`` /
    ``EMOTION_FAKE`` patterns elsewhere in the codebase.
    """
    return os.environ.get("MUSICGEN_FAKE", "0") == "1"


def _write_silent_wav(path: Path, duration_s: int, sample_rate: int) -> None:
    """Write a deterministic mono 16-bit silent wav for the FAKE path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    n_frames = duration_s * sample_rate
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(struct.pack("<" + "h" * n_frames, *([0] * n_frames)))


# Module-level cache so a single worker process only pays the model
# load cost once across many generation jobs. Loaded inside a thread
# (services run under ``asyncio.to_thread``); the dict access is
# trivially safe even without a lock for our single-worker setup.
_PIPELINE_CACHE: dict[str, object] = {}


def _load_pipeline(model_id: str) -> tuple[object, object]:
    """Return ``(processor, model)`` for ``model_id``, lazily loaded.

    Caches inside ``_PIPELINE_CACHE`` so repeat generations skip the
    multi-second torch + transformers warmup. Raises
    ``MusicGenUnavailableError`` when the deps aren't installed (so the
    worker boots even when the heavy extras failed to install) or when
    the model download fails.
    """
    cached = _PIPELINE_CACHE.get(model_id)
    if cached is not None:
        return cached  # type: ignore[return-value]

    try:
        import torch  # type: ignore[import-not-found]
        from transformers import (  # type: ignore[import-not-found]
            AutoProcessor,
            MusicgenForConditionalGeneration,
        )
    except ImportError as exc:  # pragma: no cover — install-time guard
        raise MusicGenUnavailableError(
            f"transformers / torch not installed: {exc}"
        ) from exc

    try:
        processor = AutoProcessor.from_pretrained(model_id)
        # Pin fp32 from the load. The default fp32 weights match the
        # numerical stability the multinomial sampler needs; fp16 /
        # bf16 routes overflow into NaN on small consumer GPUs.
        model = MusicgenForConditionalGeneration.from_pretrained(
            model_id, torch_dtype=torch.float32
        )
    except Exception as exc:  # noqa: BLE001 — surface as unavailable.
        raise MusicGenUnavailableError(
            f"failed to load MusicGen model {model_id!r}: {exc}"
        ) from exc

    device = "cuda" if torch.cuda.is_available() else "cpu"
    if device == "cuda":
        try:
            model = model.to(device)
        except Exception as exc:  # noqa: BLE001 — fall back to CPU.
            logger.warning(
                "MusicGen model.to(cuda) failed (%s); falling back to CPU",
                exc,
            )
            device = "cpu"
    logger.info("MusicGen pipeline loaded: model=%s device=%s", model_id, device)

    _PIPELINE_CACHE[model_id] = (processor, model)
    return processor, model


def generate(
    prompt: str,
    output_path: Path,
    *,
    duration_s: int = DEFAULT_DURATION_S,
    model_id: str = DEFAULT_MODEL,
) -> MusicGenResult:
    """Synchronous text → 30 s wav. Designed to run inside an RQ worker.

    Caller passes the ABSOLUTE output path (typically
    ``${BGM_DIR}/{project_id}/generated_{timestamp}.wav``). This
    function creates parent dirs, runs the model, writes 16-bit mono
    PCM, and returns a :class:`MusicGenResult`. Failures raise
    ``MusicGenError`` subclasses; the worker job catches and writes
    ``BgmGenerationJob.status = failed:…``.

    ``prompt`` is passed through verbatim — MusicGen handles short
    English style descriptors best, but Chinese prompts work via the
    underlying T5 text encoder, so we don't translate.
    """
    output_path = Path(output_path)

    if _is_fake():
        _write_silent_wav(output_path, duration_s, DEFAULT_SAMPLE_RATE)
        logger.info(
            "MusicGen FAKE: wrote %d s of silence to %s",
            duration_s,
            output_path,
        )
        return MusicGenResult(
            output_path=output_path,
            duration_s=float(duration_s),
            sample_rate=DEFAULT_SAMPLE_RATE,
            model=f"{model_id} (FAKE)",
        )

    import numpy as np  # type: ignore[import-not-found]
    import torch  # type: ignore[import-not-found]

    processor, model = _load_pipeline(model_id)
    # Force fp32 for the language-model + audio decoder. MusicGen-small
    # is small enough that fp32 fits in 2 GB of VRAM, and fp16 / autocast
    # paths produce ``probability tensor contains either inf, nan``
    # during the multinomial sampling step on Ampere / Turing cards
    # (RTX 2070 hits this consistently). fp32 sidesteps it entirely.
    try:
        model = model.to(torch.float32)  # type: ignore[union-attr]
    except Exception:  # noqa: BLE001
        pass
    device = next(model.parameters()).device  # type: ignore[union-attr]

    # padding=True yields the attention_mask the generate loop needs
    # to ignore PAD tokens during cross-attention. Skipping it makes
    # MusicGen attend to PAD positions, which is what feeds NaN logits
    # into multinomial sampling for prompts shorter than the EOS token.
    inputs = processor(  # type: ignore[operator]
        text=[prompt],
        padding=True,
        return_tensors="pt",
    )
    # Move every tensor entry to the model's device explicitly.
    inputs = {k: v.to(device) for k, v in inputs.items() if hasattr(v, "to")}

    max_new_tokens = duration_s * DEFAULT_TOKENS_PER_SECOND

    # v0.15.2 — three-attempt sampling chain. The previous greedy
    # fallback was a foot-gun: ``do_sample=False`` produced byte-
    # identical degenerate output across every prompt (MD5-identical
    # wavs verified in seed run; sounded like a sustained tone, not
    # music). Greedy is REMOVED.
    #
    # The NaN-on-sampling failure mode is a classifier-free-guidance
    # numerical issue on consumer Turing cards (RTX 2070): with
    # ``guidance_scale=3.0`` the unguided forward pass occasionally
    # produces inf/nan logits that multinomial sampling rejects.
    # Lowering ``guidance_scale`` toward 1.0 gradually stabilises the
    # math at the cost of looser prompt adherence — still musical, just
    # less faithful. We try strongest guidance first and step down on
    # NaN, ending at ``guidance_scale=1.0`` (CFG fully off) which is
    # numerically rock-solid.
    SAMPLING_ATTEMPTS = [
        # Strict prompt adherence; usually works on second-or-later
        # generations after the model warms up.
        dict(do_sample=True, guidance_scale=3.0, temperature=1.0, top_k=250),
        # Loose prompt adherence; combats NaN by halving the unguided
        # contribution.
        dict(do_sample=True, guidance_scale=1.5, temperature=1.0, top_k=100, top_p=0.95),
        # No CFG — sampler sees only the guided forward pass, which
        # never NaN'd in our soak. Bonus: slightly faster (one forward
        # per token instead of two).
        dict(do_sample=True, guidance_scale=1.0, temperature=1.0, top_k=50, top_p=0.95),
    ]

    audio_values: object | None = None
    last_exc: Exception | None = None
    for attempt_idx, params in enumerate(SAMPLING_ATTEMPTS, start=1):
        try:
            with torch.no_grad():
                audio_values = model.generate(  # type: ignore[union-attr]
                    **inputs,
                    max_new_tokens=max_new_tokens,
                    **params,
                )
            if attempt_idx > 1:
                logger.info(
                    "MusicGen succeeded on attempt %d/%d with %r",
                    attempt_idx,
                    len(SAMPLING_ATTEMPTS),
                    params,
                )
            break
        except RuntimeError as exc:
            msg = str(exc).lower()
            if "inf" in msg or "nan" in msg or "probability" in msg:
                logger.warning(
                    "MusicGen attempt %d/%d hit NaN/Inf with %r; trying next",
                    attempt_idx,
                    len(SAMPLING_ATTEMPTS),
                    params,
                )
                last_exc = exc
                continue
            raise

    if audio_values is None:
        # Every CFG level NaN'd — that's a genuine failure (model
        # corruption, broken CUDA, etc.). Don't fall back to greedy —
        # it just produces identical garbage across prompts. Surface
        # the error so the BGM gen job is marked failed.
        raise MusicGenError(
            f"MusicGen inference failed after {len(SAMPLING_ATTEMPTS)} sampling "
            f"attempts at descending guidance levels: {last_exc}"
        )

    sample_rate = int(getattr(model.config, "audio_encoder", None) and  # type: ignore[union-attr]
                      model.config.audio_encoder.sampling_rate
                      or DEFAULT_SAMPLE_RATE)

    # Take the first sample, mono channel. Tensor shape is typically
    # (batch, channels, samples). Detach + move to CPU before numpy.
    audio_tensor = audio_values[0]
    if audio_tensor.dim() == 2:
        audio_tensor = audio_tensor[0]
    audio_np = audio_tensor.detach().cpu().to(torch.float32).numpy()
    # Clamp + scale to int16 PCM.
    audio_np = np.clip(audio_np, -1.0, 1.0)
    pcm = (audio_np * 32767.0).astype(np.int16)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(output_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm.tobytes())

    logger.info(
        "MusicGen wrote %d frames @ %d Hz to %s",
        len(pcm),
        sample_rate,
        output_path,
    )
    return MusicGenResult(
        output_path=output_path,
        duration_s=len(pcm) / float(sample_rate),
        sample_rate=sample_rate,
        model=model_id,
    )


__all__ = [
    "DEFAULT_DURATION_S",
    "DEFAULT_MODEL",
    "DEFAULT_SAMPLE_RATE",
    "MusicGenError",
    "MusicGenResult",
    "MusicGenUnavailableError",
    "generate",
]
