"""NarratoAI documentary frame analysis pipeline.

Extracts keyframes from an asset video at a fixed interval, then calls
Gemini Vision in batches to produce per-frame observations and a per-batch
activity summary. Results are cached on disk keyed by (file sha256 + mtime
+ interval) and persisted to Asset.frame_analysis_json.

Ported from NarratoAI DocumentaryFrameAnalysisService, adapted to use the
media-processor ffmpeg + Gemini key-pool pattern.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Any

import httpx

from media_processor.api.config import settings
from media_processor.services.opencode_client import OpenCodeConfig, call_opencode_vision

logger = logging.getLogger(__name__)

_GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
_DEFAULT_INTERVAL_S = 3.0
_DEFAULT_BATCH_SIZE = 10
_DEFAULT_CONCURRENCY = 2
_MAX_FRAMES = 500
_VISION_TIMEOUT_S = 90.0

_VISION_PROMPT = """我提供了 {frame_count} 張視頻幀，它們按時間順序排列，代表一個連續的視頻片段。
首先，請詳細描述每一幀的關鍵視覺資訊（包含：主要內容、人物、動作和場景）。
然後，基於所有幀的分析，請用簡潔的語言總結整個視頻片段中發生的主要活動或事件流程。
請務必使用 JSON 格式輸出。
JSON 必須包含以下鍵：
- frame_observations: 陣列，且長度必須為 {frame_count}
- overall_activity_summary: 字串，描述整個批次主要活動
示例結構：
{{
  "frame_observations": [
    {{"timestamp": "00:00:00,000", "observation": "畫面描述"}}
  ],
  "overall_activity_summary": "本批次主要活動總結"
}}
請務必不要遺漏視頻幀，frame_observations 必須包含 {frame_count} 個元素。
只返回 JSON 字串，不要附加解釋文字。"""


# ---------- Keyframe extraction ----------


def _cache_key(file_path: str, mtime: float, interval_s: float) -> str:
    raw = f"{file_path}|{mtime:.3f}|{interval_s:.2f}"
    return hashlib.sha256(raw.encode()).hexdigest()[:20]


def extract_keyframes(
    file_path: str,
    interval_s: float = _DEFAULT_INTERVAL_S,
) -> list[Path]:
    """Extract JPEG frames at *interval_s* seconds. Cached on disk."""
    cache_root = Path(settings.frame_cache_dir)
    mtime = os.path.getmtime(file_path)
    key = _cache_key(file_path, mtime, interval_s)
    cache_dir = cache_root / key
    cache_dir.mkdir(parents=True, exist_ok=True)

    existing = sorted(cache_dir.glob("frame_*.jpg"))
    if existing:
        logger.debug("frame cache hit: %s (%d frames)", cache_dir, len(existing))
        return existing[:_MAX_FRAMES]

    # Extract frames with ffmpeg: 1 frame per interval_s, max long-edge 960px
    fps_expr = f"1/{interval_s:.3f}"
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        file_path,
        "-vf",
        f"fps={fps_expr},scale='if(gt(iw,ih),960,-2)':'if(gt(iw,ih),-2,960)'",
        "-q:v",
        "4",
        str(cache_dir / "frame_%05d.jpg"),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=300)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            f"ffmpeg keyframe extraction failed: {exc.stderr.decode()[:400]}"
        ) from exc

    frames = sorted(cache_dir.glob("frame_*.jpg"))
    logger.info("extracted %d keyframes → %s", len(frames), cache_dir)
    return frames[:_MAX_FRAMES]


def _ms_from_frame_index(index: int, interval_s: float) -> int:
    return int(index * interval_s * 1000)


def _ms_to_srt_time(ms: int) -> str:
    h = ms // 3_600_000
    m = (ms % 3_600_000) // 60_000
    s = (ms % 60_000) // 1000
    rest = ms % 1000
    return f"{h:02d}:{m:02d}:{s:02d},{rest:03d}"


# ---------- Vision LLM call ----------


def _encode_image(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode()


async def _call_gemini_vision(
    frames: list[Path],
    *,
    api_key: str,
    batch_start_ms: int,
    interval_s: float,
) -> dict[str, Any] | None:
    """Call Gemini Vision with a batch of frames. Returns parsed JSON or None."""
    frame_count = len(frames)
    prompt = _VISION_PROMPT.format(frame_count=frame_count)

    parts: list[dict[str, Any]] = []
    for frame in frames:
        parts.append({"inlineData": {"mimeType": "image/jpeg", "data": _encode_image(frame)}})
    parts.append({"text": prompt})

    body = {
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {
            "temperature": 0.2,
            "responseMimeType": "application/json",
        },
    }

    url = f"{_GEMINI_BASE_URL}/models/{settings.llm_model}:generateContent?key={api_key}"
    async with httpx.AsyncClient(timeout=_VISION_TIMEOUT_S) as client:
        try:
            resp = await client.post(url, json=body)
        except httpx.HTTPError as exc:
            logger.warning("frame analysis Vision transport error: %s", exc)
            return None

        if resp.status_code == 429 or 500 <= resp.status_code < 600:
            logger.warning("frame analysis Vision transient error %d", resp.status_code)
            return None
        if resp.status_code >= 400:
            logger.error("frame analysis Vision error %d: %s", resp.status_code, resp.text[:200])
            return None

        payload = resp.json()
        parts_out = payload.get("candidates", [{}])[0].get("content", {}).get("parts", [])
        text = next((p.get("text") for p in parts_out if isinstance(p, dict)), None)
        if not isinstance(text, str) or not text.strip():
            return None

    return _parse_vision_json(text, batch_start_ms=batch_start_ms, interval_s=interval_s)


async def _call_opencode_vision(
    frames: list[Path],
    *,
    opencode_config: OpenCodeConfig,
    batch_start_ms: int,
    interval_s: float,
) -> dict[str, Any] | None:
    frame_count = len(frames)
    prompt = _VISION_PROMPT.format(frame_count=frame_count)
    images = [("image/jpeg", _encode_image(frame)) for frame in frames]
    for server_url in opencode_config.servers:
        text = await call_opencode_vision(
            prompt=prompt,
            images=images,
            system_prompt="你是專業影片幀分析器。只輸出 JSON。",
            server_url=server_url,
            password=opencode_config.password,
            model=opencode_config.model,
            variant=opencode_config.variant,
            timeout_s=max(_VISION_TIMEOUT_S, opencode_config.timeout_s),
        )
        if text:
            parsed = _parse_vision_json(text, batch_start_ms=batch_start_ms, interval_s=interval_s)
            if parsed is not None:
                return parsed
    return None


def _parse_vision_json(
    text: str,
    *,
    batch_start_ms: int,
    interval_s: float,
) -> dict[str, Any] | None:
    """Parse a Vision model response and attach missing frame timestamps."""
    try:
        data = json.loads(text)
        if not isinstance(data, dict):
            return None
    except Exception:
        # Try to find JSON block
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            try:
                data = json.loads(text[start : end + 1])
                if not isinstance(data, dict):
                    return None
            except Exception:
                return None
        else:
            return None

    # Ensure timestamps are attached
    observations = data.get("frame_observations", [])
    if isinstance(observations, list):
        for idx, obs in enumerate(observations):
            if isinstance(obs, dict) and not obs.get("timestamp"):
                frame_ms = batch_start_ms + int(idx * interval_s * 1000)
                obs["timestamp"] = _ms_to_srt_time(frame_ms)

    return data


async def _analyse_batch(
    frames: list[Path],
    *,
    batch_index: int,
    start_ms: int,
    end_ms: int,
    interval_s: float,
    api_keys: tuple[str, ...],
    opencode_config: OpenCodeConfig | None = None,
) -> dict[str, Any]:
    """Analyse one batch with retry across key pool. Returns a batch result dict."""
    time_range = f"{_ms_to_srt_time(start_ms)}-{_ms_to_srt_time(end_ms)}"

    if opencode_config is not None:
        result = await _call_opencode_vision(
            frames,
            opencode_config=opencode_config,
            batch_start_ms=start_ms,
            interval_s=interval_s,
        )
        if result is not None:
            return {
                "batch_index": batch_index,
                "time_range": time_range,
                "frame_observations": result.get("frame_observations", []),
                "overall_activity_summary": result.get("overall_activity_summary", ""),
            }

    for key in api_keys:
        result = await _call_gemini_vision(
            frames, api_key=key, batch_start_ms=start_ms, interval_s=interval_s
        )
        if result is not None:
            return {
                "batch_index": batch_index,
                "time_range": time_range,
                "frame_observations": result.get("frame_observations", []),
                "overall_activity_summary": result.get("overall_activity_summary", ""),
            }

    raise RuntimeError(f"frame analysis batch {batch_index}: all Vision providers failed")


# ---------- Main pipeline ----------


async def analyse_asset(
    file_path: str,
    *,
    api_keys: tuple[str, ...],
    opencode_config: OpenCodeConfig | None = None,
    interval_s: float = _DEFAULT_INTERVAL_S,
    batch_size: int = _DEFAULT_BATCH_SIZE,
    concurrency: int = _DEFAULT_CONCURRENCY,
) -> dict[str, Any]:
    """Full NarratoAI documentary frame analysis pipeline.

    Returns a dict ready to be stored in Asset.frame_analysis_json.
    Raises RuntimeError if ffmpeg fails or no AI provider is configured.
    """
    if not api_keys and opencode_config is None:
        raise RuntimeError("frame_analysis_service: no Vision AI provider configured")

    frames = await asyncio.to_thread(extract_keyframes, file_path, interval_s)
    if not frames:
        raise RuntimeError(f"no frames extracted from {file_path}")

    # Chunk frames into batches
    batches: list[list[Path]] = []
    for i in range(0, len(frames), batch_size):
        batches.append(frames[i : i + batch_size])

    sem = asyncio.Semaphore(concurrency)

    async def _bounded(batch_idx: int, batch_frames: list[Path]) -> dict[str, Any]:
        async with sem:
            global_frame_start = batch_idx * batch_size
            start_ms = _ms_from_frame_index(global_frame_start, interval_s)
            end_ms = _ms_from_frame_index(global_frame_start + len(batch_frames), interval_s)
            return await _analyse_batch(
                batch_frames,
                batch_index=batch_idx,
                start_ms=start_ms,
                end_ms=end_ms,
                interval_s=interval_s,
                api_keys=api_keys,
                opencode_config=opencode_config,
            )

    tasks = [_bounded(i, b) for i, b in enumerate(batches)]
    batch_results: list[dict[str, Any]] = await asyncio.gather(*tasks)

    return {
        "interval_seconds": interval_s,
        "frame_count": len(frames),
        "batch_count": len(batch_results),
        "batches": sorted(batch_results, key=lambda b: b["batch_index"]),
    }


# ---------- Markdown conversion (NarratoAI parse_frame_analysis_to_markdown) ----------


def analysis_to_markdown(analysis_json: dict[str, Any]) -> str:
    """Convert frame_analysis_json to markdown summary for LLM narration prompt."""
    lines: list[str] = []
    for batch in sorted(analysis_json.get("batches", []), key=lambda b: b.get("batch_index", 0)):
        time_range = batch.get("time_range", "")
        summary = batch.get("overall_activity_summary", "")
        observations = batch.get("frame_observations", [])
        if time_range:
            lines.append(f"## {time_range}")
        if summary:
            lines.append(f"**總結：** {summary}")
        for obs in observations:
            ts = obs.get("timestamp", "")
            text = obs.get("observation", "")
            if ts and text:
                lines.append(f"- [{ts}] {text}")
        lines.append("")
    return "\n".join(lines).strip()
