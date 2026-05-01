"""Scene tagging via Gemini Vision over ffmpeg-sampled frames.

The fixed enum below is intentionally generic — the system stays
industry-agnostic, so M5 cut planning can build on these labels without
needing per-vertical taxonomies.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)


SCENE_TAGS: tuple[str, ...] = (
    "indoor",
    "outdoor",
    "studio",
    "closeup",
    "medium_shot",
    "wide",
    "dynamic",
    "static",
    "bright",
    "dim",
    "mixed_light",
)
_TAG_SET = set(SCENE_TAGS)

# Aggregation thresholds — see scene-tagging spec REQ-4.
FIRE_RATIO_THRESHOLD = 0.30
HIGH_CONFIDENCE_THRESHOLD = 0.80

# Hard cap on Vision calls per asset — prevents very long clips from
# blowing the daily quota. The interval auto-stretches to keep N ≤ cap.
MAX_FRAMES_PER_ASSET = 60


_VISION_PROMPT = (
    "你會看到一張影片擷取的畫面。請從以下標籤集中挑選 1–4 個最貼切的場景描述，"
    "其餘忽略。只回傳 JSON：\n"
    '{ "tags": [{"name": "<tag>", "confidence": 0..1}, ...] }\n'
    "允許的 tag："
    "indoor, outdoor, studio, closeup, medium_shot, wide, "
    "dynamic, static, bright, dim, mixed_light"
)

_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)


class SceneTaggingError(RuntimeError):
    """Caught by the orchestrator and mapped to failed:{reason}."""


class SceneQuotaExhaustedError(SceneTaggingError):
    pass


@dataclass(frozen=True)
class FrameTagging:
    frame_index: int
    tags: tuple[tuple[str, float], ...]


@dataclass(frozen=True)
class AssetSceneTags:
    """Aggregated, allowed-only tag list for the whole asset."""

    model: str
    tags: tuple[tuple[str, float], ...] = field(default_factory=tuple)


def _sample_frames(
    media_path: Path,
    out_dir: Path,
    duration_ms: int,
    interval_ms: int,
) -> list[Path]:
    """Sample one frame per ``interval_ms`` via ffmpeg, capped to MAX_FRAMES.

    Returns the list of frame paths in time order. The output directory is
    created if missing; any pre-existing JPGs are cleared first so a
    force re-run does not mix old and new frames.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("*.jpg"):
        old.unlink(missing_ok=True)

    if duration_ms <= 0:
        # Unknown duration — sample one frame at the start as a fallback.
        target_count = 1
        effective_interval_s = 1.0
    else:
        # If the natural count exceeds the cap, stretch the interval so we
        # land exactly at MAX_FRAMES_PER_ASSET.
        natural = max(1, int(duration_ms / max(1, interval_ms)))
        if natural > MAX_FRAMES_PER_ASSET:
            target_count = MAX_FRAMES_PER_ASSET
            effective_interval_s = (duration_ms / 1000.0) / target_count
        else:
            target_count = natural
            effective_interval_s = interval_ms / 1000.0

    fps = 1.0 / max(0.5, effective_interval_s)
    pattern = str(out_dir / "frame_%04d.jpg")

    cmd = [
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        "-i",
        str(media_path),
        "-vf",
        f"fps={fps:.4f}",
        "-frames:v",
        str(target_count),
        "-q:v",
        "5",
        pattern,
    ]
    proc = subprocess.run(cmd, capture_output=True, timeout=600, check=False)
    if proc.returncode != 0:
        raise SceneTaggingError(
            f"ffmpeg frame sampling failed (code={proc.returncode}): {proc.stderr.decode(errors='replace')[:300]}"
        )

    frames = sorted(out_dir.glob("frame_*.jpg"))
    if not frames:
        raise SceneTaggingError("ffmpeg produced no frames")
    return frames


def _strip_fence(text: str) -> str:
    match = _FENCE_RE.match(text)
    if match:
        return match.group(1)
    return text


def _parse_vision_json(payload: dict[str, Any]) -> list[tuple[str, float]]:
    candidates = payload.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise SceneTaggingError("Vision payload missing candidates")
    parts = candidates[0].get("content", {}).get("parts", [])
    if not isinstance(parts, list) or not parts:
        raise SceneTaggingError("Vision candidate missing content.parts")
    text = parts[0].get("text", "")
    if not isinstance(text, str) or not text.strip():
        raise SceneTaggingError("Vision candidate text empty")
    cleaned = _strip_fence(text)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise SceneTaggingError(f"Vision JSON parse failed: {exc}; text={text[:200]}") from exc
    raw_tags = data.get("tags", []) if isinstance(data, dict) else []
    if not isinstance(raw_tags, list):
        return []
    out: list[tuple[str, float]] = []
    for entry in raw_tags:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        conf = entry.get("confidence")
        if not isinstance(name, str) or name not in _TAG_SET:
            continue
        if not isinstance(conf, int | float) or isinstance(conf, bool):
            continue
        clamped = max(0.0, min(1.0, float(conf)))
        out.append((name, clamped))
    return out


async def _classify_frame(
    client: httpx.AsyncClient,
    api_keys: tuple[str, ...],
    model: str,
    frame_bytes: bytes,
    base_url: str,
) -> list[tuple[str, float]]:
    """Send one frame to Vision; rotate keys on 429 / 5xx."""
    image_b64 = base64.b64encode(frame_bytes).decode("ascii")
    body = {
        "contents": [
            {
                "role": "user",
                "parts": [
                    {"text": _VISION_PROMPT},
                    {"inline_data": {"mime_type": "image/jpeg", "data": image_b64}},
                ],
            }
        ],
        "generationConfig": {
            "temperature": 0.1,
            "responseMimeType": "application/json",
        },
    }
    last_status = 0
    for key in api_keys:
        url = f"{base_url}/models/{model}:generateContent?key={key}"
        try:
            response = await client.post(url, json=body)
        except httpx.HTTPError as exc:
            logger.warning("Vision transport error; rotating key: %s", exc)
            continue
        last_status = response.status_code
        if response.status_code == 429 or 500 <= response.status_code < 600:
            logger.warning("Vision %d; rotating to next key", response.status_code)
            continue
        if response.status_code >= 400:
            raise SceneTaggingError(
                f"Vision call failed: status={response.status_code} body={response.text[:200]}"
            )
        return _parse_vision_json(response.json())
    raise SceneQuotaExhaustedError(
        f"all {len(api_keys)} Vision keys exhausted; last_status={last_status}"
    )


async def classify_asset(
    media_path: Path,
    duration_ms: int,
    *,
    api_keys: tuple[str, ...],
    model: str,
    base_url: str,
    timeout_s: float,
    interval_ms: int,
    scratch_dir: Path,
) -> AssetSceneTags:
    """Sample frames, classify each, aggregate to per-asset tag list.

    The orchestrator owns DB persistence; this returns the aggregated tags.
    """
    if not api_keys:
        raise SceneTaggingError("no API keys configured for Vision")

    frames_dir = scratch_dir / "frames"
    frame_paths = _sample_frames(media_path, frames_dir, duration_ms, interval_ms)
    n = len(frame_paths)

    occurrences: dict[str, list[float]] = {tag: [] for tag in SCENE_TAGS}
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            for fp in frame_paths:
                frame_bytes = fp.read_bytes()
                tags = await _classify_frame(client, api_keys, model, frame_bytes, base_url)
                for name, conf in tags:
                    occurrences[name].append(conf)
                # Be polite — small inter-frame pause to spread the rate.
                await asyncio.sleep(0.05)
    finally:
        # Always clean up the frame scratch dir.
        shutil.rmtree(frames_dir, ignore_errors=True)

    fired: list[tuple[str, float]] = []
    for tag, confs in occurrences.items():
        if not confs:
            continue
        ratio = len(confs) / n
        max_conf = max(confs)
        if ratio >= FIRE_RATIO_THRESHOLD or max_conf >= HIGH_CONFIDENCE_THRESHOLD:
            mean_conf = sum(confs) / len(confs)
            fired.append((tag, mean_conf))

    fired.sort(key=lambda x: x[1], reverse=True)
    return AssetSceneTags(model=f"gemini-vision-{model}", tags=tuple(fired))


__all__ = [
    "MAX_FRAMES_PER_ASSET",
    "SCENE_TAGS",
    "AssetSceneTags",
    "FrameTagging",
    "SceneQuotaExhaustedError",
    "SceneTaggingError",
    "classify_asset",
]
