"""Story/Narrato narration audio artifact generation."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from media_processor.api.config import settings
from media_processor.models import StoryNarrationAsset, StoryScript
from media_processor.services.edit_planner import CutPlan
from media_processor.services.story_script import StoryScriptDocument, StoryScriptItem
from media_processor.services.subtitles import MAX_LINE_CHARS, MAX_LINES

logger = logging.getLogger(__name__)

NARRATION_STATUS_PENDING = "pending"
NARRATION_STATUS_DONE = "done"
NARRATION_STATUS_FAILED = "failed"
_TTS_SUBTITLE_MAX_CHARS = MAX_LINE_CHARS * MAX_LINES


class StoryTtsError(RuntimeError):
    """TTS narration generation failed."""


@dataclass(frozen=True)
class NarrationSettings:
    provider: str
    voice: str
    model: str | None
    timeout_s: float


@dataclass(frozen=True)
class NarrationClip:
    order: int
    audio_path: Path
    start_ms: int
    duration_ms: int
    audio_intent: str


class TtsProvider(Protocol):
    async def synthesize(
        self, *, text: str, voice: str, output_path: Path, timeout_s: float
    ) -> None:
        """Write generated speech audio to output_path."""


class EdgeTtsProvider:
    """Microsoft Edge TTS via edge-tts package.

    Supports word-boundary subtitle generation: call synthesize_with_srt()
    to get both the audio file and a parallel SRT of word timings.
    """

    async def synthesize(
        self, *, text: str, voice: str, output_path: Path, timeout_s: float
    ) -> None:
        try:
            import edge_tts
        except Exception as exc:  # pragma: no cover
            raise StoryTtsError("Edge TTS provider is not installed") from exc

        async def _run() -> None:
            communicate = edge_tts.Communicate(text, voice)
            await communicate.save(str(output_path))

        await asyncio.wait_for(_run(), timeout=timeout_s)

    async def synthesize_with_srt(
        self, *, text: str, voice: str, output_path: Path, timeout_s: float
    ) -> str:
        """Synthesize audio and return SRT content built from WordBoundary events.

        Writes audio to output_path. Returns SRT string (empty if no boundaries).
        """
        try:
            import edge_tts
        except Exception as exc:  # pragma: no cover
            raise StoryTtsError("Edge TTS provider is not installed") from exc

        word_events: list[dict[str, object]] = []
        audio_chunks: list[bytes] = []

        async def _stream() -> None:
            communicate = edge_tts.Communicate(text, voice)
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    audio_chunks.append(chunk["data"])
                elif chunk["type"] == "WordBoundary":
                    word_events.append(
                        {
                            "offset": chunk.get("offset", 0),  # 100-ns units
                            "duration": chunk.get("duration", 0),
                            "text": chunk.get("text", ""),
                        }
                    )

        await asyncio.wait_for(_stream(), timeout=timeout_s)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"".join(audio_chunks))

        if not word_events:
            return ""

        return _word_events_to_srt(word_events)


def _word_events_to_srt(events: list[dict[str, object]]) -> str:
    """Convert edge-tts WordBoundary events (100-ns offsets) to paced SRT."""
    lines: list[str] = []
    cue_index = 1
    current_text = ""
    current_start_ms: int | None = None
    current_end_ms: int | None = None

    def flush() -> None:
        nonlocal cue_index, current_text, current_start_ms, current_end_ms
        text = current_text.strip()
        if text and current_start_ms is not None and current_end_ms is not None:
            lines.append(str(cue_index))
            lines.append(f"{_ms_to_srt(current_start_ms)} --> {_ms_to_srt(current_end_ms)}")
            lines.append(text)
            lines.append("")
            cue_index += 1
        current_text = ""
        current_start_ms = None
        current_end_ms = None

    for ev in events:
        raw_text = str(ev.get("text") or "").strip()
        if not raw_text:
            continue
        offset = cast(int | str, ev["offset"])
        duration = cast(int | str, ev["duration"])
        start_ms = int(offset) // 10_000  # 100-ns -> ms
        dur_ms = max(120, int(duration) // 10_000)
        end_ms = start_ms + dur_ms
        if current_text and len(current_text + raw_text) > _TTS_SUBTITLE_MAX_CHARS:
            flush()
        if current_start_ms is None:
            current_start_ms = start_ms
        current_text = _append_word_boundary_text(current_text, raw_text)
        current_end_ms = max(end_ms, current_end_ms or end_ms)

    flush()
    return "\n".join(lines)


def _append_word_boundary_text(current: str, token: str) -> str:
    if not current:
        return token
    prev = current[-1]
    first = token[0]
    if prev.isascii() and first.isascii() and prev.isalnum() and first.isalnum():
        return f"{current} {token}"
    return f"{current}{token}"


def _srt_artifact_path(audio_path: Path) -> Path:
    return audio_path.with_suffix(".srt")


def _write_srt_sidecar(audio_path: Path, srt_text: str) -> None:
    srt_path = _srt_artifact_path(audio_path)
    if srt_text.strip():
        srt_path.write_text(srt_text, encoding="utf-8")
    elif srt_path.is_file():
        srt_path.unlink()


def _ms_to_srt(ms: int) -> str:
    h = ms // 3_600_000
    m = (ms % 3_600_000) // 60_000
    s = (ms % 60_000) // 1000
    rest = ms % 1000
    return f"{h:02d}:{m:02d}:{s:02d},{rest:03d}"


class AzureTtsProvider:
    """Azure Cognitive Services TTS via REST API (no SDK required).

    Configure via env vars:
      TTS_AZURE_KEY    — Ocp-Apim-Subscription-Key
      TTS_AZURE_REGION — e.g. "eastasia", "japaneast"
    """

    async def synthesize(
        self, *, text: str, voice: str, output_path: Path, timeout_s: float
    ) -> None:
        import httpx

        from media_processor.api.config import settings as _cfg

        key = _cfg.tts_azure_key.strip()
        region = _cfg.tts_azure_region.strip() or "eastasia"
        if not key:
            raise StoryTtsError("TTS_AZURE_KEY not configured for azure provider")

        ssml = (
            f'<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" xml:lang="zh-TW">'
            f'<voice name="{voice}">{text}</voice></speak>'
        )
        url = f"https://{region}.tts.speech.microsoft.com/cognitiveservices/v1"
        headers = {
            "Ocp-Apim-Subscription-Key": key,
            "Content-Type": "application/ssml+xml",
            "X-Microsoft-OutputFormat": "audio-16khz-128kbitrate-mono-mp3",
        }
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            resp = await client.post(url, content=ssml.encode("utf-8"), headers=headers)
        if resp.status_code != 200:
            raise StoryTtsError(f"Azure TTS error {resp.status_code}: {resp.text[:300]}")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(resp.content)


class TencentTtsProvider:
    """Tencent Cloud TTS via simple REST API.

    Configure via env vars:
      TTS_TENCENT_SECRET_ID
      TTS_TENCENT_SECRET_KEY
      TTS_TENCENT_APPID (optional, falls back to key derivation)
    Voice code examples: 101001 (zh female), 101002 (zh male).
    """

    async def synthesize(
        self, *, text: str, voice: str, output_path: Path, timeout_s: float
    ) -> None:
        import base64
        import hashlib
        import hmac
        import time

        import httpx

        from media_processor.api.config import settings as _cfg

        secret_id = _cfg.tts_tencent_secret_id.strip()
        secret_key = _cfg.tts_tencent_secret_key.strip()
        if not secret_id or not secret_key:
            raise StoryTtsError("TTS_TENCENT_SECRET_ID / SECRET_KEY not configured")

        # voice is expected to be an integer voice_type (e.g. "101001")
        try:
            voice_type = int(voice)
        except ValueError:
            voice_type = 101001  # default zh-TW female

        timestamp = int(time.time())
        payload = {
            "Action": "TextToVoice",
            "Nonce": timestamp & 0xFFFFFF,
            "Region": "ap-guangzhou",
            "SecretId": secret_id,
            "SignatureMethod": "HmacSHA256",
            "Text": text,
            "Timestamp": timestamp,
            "VoiceType": voice_type,
            "Codec": "mp3",
            "SampleRate": 16000,
            "Volume": 0,
            "Speed": 0,
            "SessionId": hashlib.md5(text.encode()).hexdigest()[:16],
        }
        # Build signature string
        sorted_params = "&".join(f"{k}={v}" for k, v in sorted(payload.items()))
        sign_str = f"POSTtts.tencentcloudapi.com/?{sorted_params}"
        sig = base64.b64encode(
            hmac.new(secret_key.encode(), sign_str.encode(), hashlib.sha256).digest()
        ).decode()
        payload["Signature"] = sig

        async with httpx.AsyncClient(timeout=timeout_s) as client:
            resp = await client.post(
                "https://tts.tencentcloudapi.com",
                data=payload,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )

        data = resp.json()
        if "Error" in data.get("Response", {}):
            err = data["Response"]["Error"]
            raise StoryTtsError(f"Tencent TTS error {err.get('Code')}: {err.get('Message')}")

        audio_b64 = data.get("Response", {}).get("Audio", "")
        if not audio_b64:
            raise StoryTtsError("Tencent TTS: empty audio in response")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(base64.b64decode(audio_b64))


class SilentTestProvider:
    """Deterministic provider for tests and disabled-runtime smoke checks."""

    async def synthesize(
        self, *, text: str, voice: str, output_path: Path, timeout_s: float
    ) -> None:
        duration_s = max(0.7, min(15.0, len(text) / 8.0))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if os.environ.get("FFMPEG_FAKE", "0") == "1":
            output_path.write_bytes(b"")
            return
        if shutil.which("ffmpeg") is None:
            raise StoryTtsError("ffmpeg not on PATH for silent test TTS")
        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=44100:cl=mono",
            "-t",
            f"{duration_s:.3f}",
            "-c:a",
            "aac",
            str(output_path),
        ]
        await asyncio.to_thread(
            subprocess.run, cmd, check=True, timeout=timeout_s, capture_output=True
        )


def narration_settings() -> NarrationSettings | None:
    provider = settings.story_tts_provider.strip().lower()
    if not provider:
        return None
    return NarrationSettings(
        provider=provider,
        voice=settings.story_tts_voice.strip() or "zh-TW-HsiaoChenNeural",
        model=settings.story_tts_model.strip() or provider,
        timeout_s=max(1.0, float(settings.story_tts_timeout_s)),
    )


def provider_for(name: str) -> TtsProvider:
    if name == "edge":
        return EdgeTtsProvider()
    if name in {"azure", "azure_v2"}:
        return AzureTtsProvider()
    if name in {"tencent", "qcloud"}:
        return TencentTtsProvider()
    if name in {"silent", "test"}:
        return SilentTestProvider()
    raise StoryTtsError(f"unsupported story TTS provider: {name}")


def text_hash(text: str) -> str:
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()


def artifact_path(
    project_id: int, story_script_id: int | None, item: StoryScriptItem, text_digest: str
) -> Path:
    script_part = story_script_id if story_script_id is not None else "latest"
    filename = f"item_{item.order:03d}_{text_digest[:12]}.m4a"
    return Path(settings.story_narration_dir) / str(project_id) / str(script_part) / filename


def probe_audio_duration_ms(path: Path) -> int | None:
    if os.environ.get("FFMPEG_FAKE", "0") == "1":
        return None
    if shutil.which("ffprobe") is None or not path.is_file():
        return None
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=10.0,
            check=True,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return None
    try:
        return max(1, int(round(float(result.stdout.strip()) * 1000)))
    except ValueError:
        return None


def item_needs_narration(item: StoryScriptItem) -> bool:
    return item.audio_intent in {"narration", "narration_with_original"} and bool(
        item.narration.strip()
    )


async def _latest_story_row(session: AsyncSession, project_id: int) -> StoryScript | None:
    return (
        await session.execute(
            select(StoryScript)
            .where(StoryScript.project_id == project_id)
            .order_by(StoryScript.created_at.desc(), StoryScript.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def _find_artifact(
    session: AsyncSession,
    *,
    project_id: int,
    story_script_id: int | None,
    item: StoryScriptItem,
    digest: str,
    config: NarrationSettings,
) -> StoryNarrationAsset | None:
    return (
        await session.execute(
            select(StoryNarrationAsset)
            .where(StoryNarrationAsset.project_id == project_id)
            .where(StoryNarrationAsset.story_script_id == story_script_id)
            .where(StoryNarrationAsset.story_item_order == item.order)
            .where(StoryNarrationAsset.narration_text_hash == digest)
            .where(StoryNarrationAsset.provider == config.provider)
            .where(StoryNarrationAsset.voice == config.voice)
            .order_by(StoryNarrationAsset.created_at.desc(), StoryNarrationAsset.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


def _artifact_is_reusable(row: StoryNarrationAsset) -> bool:
    if row.status != NARRATION_STATUS_DONE or not row.file_path or not row.duration_ms:
        return False
    if row.provider == "edge" and not _srt_artifact_path(Path(row.file_path)).is_file():
        return False
    return os.environ.get("FFMPEG_FAKE", "0") == "1" or Path(row.file_path).is_file()


async def generate_narration_assets(
    session: AsyncSession,
    document: StoryScriptDocument,
    *,
    draft_id: int | None = None,
    allow_fallback: bool = True,
    config: NarrationSettings | None = None,
    provider: TtsProvider | None = None,
) -> dict[int, StoryNarrationAsset]:
    """Generate or reuse narration assets keyed by StoryScript item order."""
    config = config or narration_settings()
    if config is None:
        return {}
    provider = provider or provider_for(config.provider)
    story_row = await _latest_story_row(session, document.project_id)
    story_script_id = story_row.id if story_row is not None else None
    out: dict[int, StoryNarrationAsset] = {}
    for item in document.items:
        if not item_needs_narration(item):
            continue
        digest = text_hash(item.narration)
        existing = await _find_artifact(
            session,
            project_id=document.project_id,
            story_script_id=story_script_id,
            item=item,
            digest=digest,
            config=config,
        )
        if existing is not None and _artifact_is_reusable(existing):
            out[item.order] = existing
            continue
        path = artifact_path(document.project_id, story_script_id, item, digest)
        row = existing or StoryNarrationAsset(
            project_id=document.project_id,
            story_script_id=story_script_id,
            story_item_order=item.order,
            narration_text_hash=digest,
            provider=config.provider,
            voice=config.voice,
        )
        row.draft_id = draft_id
        row.asset_id = item.asset_id
        row.source_start_ms = item.source_start_ms
        row.source_end_ms = item.source_end_ms
        row.model = config.model
        row.status = NARRATION_STATUS_PENDING
        row.error = None
        row.file_path = str(path)
        row.duration_ms = None
        if existing is None:
            session.add(row)
        await session.commit()
        await session.refresh(row)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            srt_text = ""
            synthesize_with_srt = getattr(provider, "synthesize_with_srt", None)
            if callable(synthesize_with_srt):
                srt_text = await synthesize_with_srt(
                    text=item.narration,
                    voice=config.voice,
                    output_path=path,
                    timeout_s=config.timeout_s,
                )
            else:
                await provider.synthesize(
                    text=item.narration,
                    voice=config.voice,
                    output_path=path,
                    timeout_s=config.timeout_s,
                )
            _write_srt_sidecar(path, srt_text)
            duration_ms = probe_audio_duration_ms(path) or max(700, item.duration_ms)
            row.status = NARRATION_STATUS_DONE
            row.error = None
            row.duration_ms = duration_ms
            row.file_path = str(path)
            await session.commit()
            await session.refresh(row)
            out[item.order] = row
        except Exception as exc:  # noqa: BLE001 - persist provider failures per item.
            row.status = NARRATION_STATUS_FAILED
            row.error = str(exc)
            await session.commit()
            if not allow_fallback:
                raise StoryTtsError(f"narration item {item.order} failed: {exc}") from exc
            logger.warning(
                "story narration item %d failed; using subtitle-only fallback: %s", item.order, exc
            )
    return out


def narration_durations_by_order(rows: dict[int, StoryNarrationAsset]) -> dict[int, int]:
    return {
        order: int(row.duration_ms)
        for order, row in rows.items()
        if row.status == NARRATION_STATUS_DONE and row.duration_ms is not None
    }


def narration_subtitles_by_order(rows: dict[int, StoryNarrationAsset]) -> dict[int, str]:
    subtitles: dict[int, str] = {}
    for order, row in rows.items():
        if row.status != NARRATION_STATUS_DONE or not row.file_path:
            continue
        srt_path = _srt_artifact_path(Path(row.file_path))
        if not srt_path.is_file():
            continue
        text = srt_path.read_text(encoding="utf-8").strip()
        if text:
            subtitles[order] = text
    return subtitles


def narration_subtitles_from_plan(plan: CutPlan) -> dict[int, str]:
    subtitles: dict[int, str] = {}
    for segment in plan.segments:
        if not segment.narration_audio_path:
            continue
        srt_path = _srt_artifact_path(Path(segment.narration_audio_path))
        if not srt_path.is_file():
            continue
        text = srt_path.read_text(encoding="utf-8").strip()
        if text:
            subtitles[segment.order] = text
    return subtitles


def narration_clips_for_plan(
    rows: dict[int, StoryNarrationAsset], *, timeline_starts_ms: dict[int, int]
) -> list[NarrationClip]:
    clips: list[NarrationClip] = []
    for order, row in sorted(rows.items()):
        if row.status != NARRATION_STATUS_DONE or not row.file_path or not row.duration_ms:
            continue
        clips.append(
            NarrationClip(
                order=order,
                audio_path=Path(row.file_path),
                start_ms=timeline_starts_ms.get(order, 0),
                duration_ms=int(row.duration_ms),
                audio_intent="narration",
            )
        )
    return clips
