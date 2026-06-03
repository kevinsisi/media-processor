"""Simplified → Traditional Chinese conversion via OpenCC.

Used by all NarratoAI narration/script generation services to ensure
output is always in Traditional Chinese regardless of LLM language bias.

OpenCC is optional (installed in the `analysis` extra). Falls back to
returning the original text unchanged when not available so the API
container (which skips analysis deps) is unaffected.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_converter: Any | None = None
_unavailable: bool = False


def to_traditional(text: str) -> str:
    """Convert Simplified Chinese text to Traditional Chinese (zh-TW).

    No-op when OpenCC is not installed or conversion fails.
    Idempotent — already-traditional text passes through unchanged.
    """
    global _converter, _unavailable

    if not text or _unavailable:
        return text

    try:
        if _converter is None:
            from opencc import OpenCC

            for config in ("s2twp", "s2tw", "s2t"):
                try:
                    _converter = OpenCC(config)
                    logger.debug("OpenCC initialised with config=%s", config)
                    break
                except Exception:
                    continue

            if _converter is None:
                _unavailable = True
                logger.warning("OpenCC: no working config found; zh-TW conversion disabled")
                return text

        return str(_converter.convert(text))
    except ImportError:
        _unavailable = True
        logger.debug("OpenCC not installed; zh-TW conversion disabled")
        return text
    except Exception as exc:
        logger.warning("OpenCC conversion error: %s", exc)
        return text


def convert_script_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """In-place Traditional Chinese conversion for StoryScript items list.

    Converts 'narration' and 'picture' fields. Returns the same list
    (mutates in place for efficiency with large scripts).
    """
    for item in items:
        if "narration" in item and isinstance(item["narration"], str):
            item["narration"] = to_traditional(item["narration"])
        if "picture" in item and isinstance(item["picture"], str):
            item["picture"] = to_traditional(item["picture"])
        if "reason" in item and isinstance(item["reason"], str):
            item["reason"] = to_traditional(item["reason"])
    return items
