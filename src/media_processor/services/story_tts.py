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
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from media_processor.api.config import settings
from media_processor.models import StoryNarrationAsset, StoryScript
from media_processor.services.story_script import StoryScriptDocument, StoryScriptItem

logger = logging.getLogger(__name__)

NARRATION_STATUS_PENDING = "pending"
NARRATION_STATUS_DONE = "done"
NARRATION_STATUS_FAILED = "failed"


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
    async def synthesize(self, *, text: str, voice: str, output_path: Path, timeout_s: float) -> None:
        """Write generated speech audio to output_path."""


class EdgeTtsProvider:
    async def synthesize(self, *, text: str, voice: str, output_path: Path, timeout_s: float) -> None:
        try:
            import edge_tts  # type: ignore[import-not-found]
        except Exception as exc:  # pragma: no cover - depends on optional runtime package.
            raise StoryTtsError("Edge TTS provider is not installed") from exc

        async def _run() -> None:
            communicate = edge_tts.Communicate(text, voice)
            await communicate.save(str(output_path))

        await asyncio.wait_for(_run(), timeout=timeout_s)


class SilentTestProvider:
    """Deterministic provider for tests and disabled-runtime smoke checks."""

    async def synthesize(self, *, text: str, voice: str, output_path: Path, timeout_s: float) -> None:
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
        await asyncio.to_thread(subprocess.run, cmd, check=True, timeout=timeout_s, capture_output=True)


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
    if name in {"silent", "test"}:
        return SilentTestProvider()
    raise StoryTtsError(f"unsupported story TTS provider: {name}")


def text_hash(text: str) -> str:
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()


def artifact_path(project_id: int, story_script_id: int | None, item: StoryScriptItem, text_digest: str) -> Path:
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
    return item.audio_intent in {"narration", "narration_with_original"} and bool(item.narration.strip())


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
            await provider.synthesize(
                text=item.narration,
                voice=config.voice,
                output_path=path,
                timeout_s=config.timeout_s,
            )
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
            logger.warning("story narration item %d failed; using subtitle-only fallback: %s", item.order, exc)
    return out


def narration_durations_by_order(rows: dict[int, StoryNarrationAsset]) -> dict[int, int]:
    return {
        order: int(row.duration_ms)
        for order, row in rows.items()
        if row.status == NARRATION_STATUS_DONE and row.duration_ms is not None
    }


def narration_clips_for_plan(rows: dict[int, StoryNarrationAsset], *, timeline_starts_ms: dict[int, int]) -> list[NarrationClip]:
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
