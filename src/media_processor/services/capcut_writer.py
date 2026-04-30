"""Stage 7 — CapCut/剪映 draft writer.

Emits a zip containing `draft_content.json` + `draft_meta_info.json`. The exact
JSON schema is provisional pending Step 0 reverse-engineering against a real
CapCut Pro Mac sample (see project design §6.9 / §11.1). Until then the writer
emits structurally-correct top-level keys (`version`, `tracks`) marked with
`SCHEMA_VERSION = "step0-pending"` so the worker can detect and refuse mismatched
versions in M3.
"""

from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class WriterSegment:
    """One video segment as fed to the CapCut writer."""

    order: int
    asset_path: str
    asset_start_ms: int
    asset_end_ms: int
    on_timeline_start_ms: int
    on_timeline_end_ms: int
    transition: str | None = None
    reframe_keyframes: list[dict[str, Any]] | None = None
    blurred_source_path: str | None = None


@dataclass(frozen=True)
class WriterCaption:
    start_ms: int
    end_ms: int
    text: str


@dataclass(frozen=True)
class WriterBGM:
    file_path: str
    start_ms: int = 0


@dataclass(frozen=True)
class WriterDraftMeta:
    name: str
    profile_name: str
    target_duration_ms: int


class CapCutDraftWriter:
    """Adapter that turns the internal timeline model into a CapCut draft zip."""

    SCHEMA_VERSION = "step0-pending"
    """Marker until the real CapCut version is locked from a sample (Step 0)."""

    def write(
        self,
        meta: WriterDraftMeta,
        segments: list[WriterSegment],
        output_path: Path,
        *,
        bgm: WriterBGM | None = None,
        captions: list[WriterCaption] | None = None,
    ) -> Path:
        """Write a draft zip to `output_path` and return the path."""
        content = self._build_content(meta, segments, bgm=bgm, captions=captions)
        meta_info = self._build_meta(meta)

        # `sort_keys=True` + no whitespace + fixed timestamps make the writer
        # idempotent: same input → byte-identical JSON.
        content_bytes = json.dumps(content, sort_keys=True, ensure_ascii=False).encode("utf-8")
        meta_bytes = json.dumps(meta_info, sort_keys=True, ensure_ascii=False).encode("utf-8")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            self._write_zip_entry(zf, "draft_content.json", content_bytes)
            self._write_zip_entry(zf, "draft_meta_info.json", meta_bytes)
        return output_path

    @staticmethod
    def _write_zip_entry(zf: zipfile.ZipFile, name: str, data: bytes) -> None:
        # Fixed mtime → deterministic archives across runs.
        info = zipfile.ZipInfo(filename=name, date_time=(2026, 1, 1, 0, 0, 0))
        info.compress_type = zipfile.ZIP_DEFLATED
        zf.writestr(info, data)

    def _build_content(
        self,
        meta: WriterDraftMeta,
        segments: list[WriterSegment],
        *,
        bgm: WriterBGM | None,
        captions: list[WriterCaption] | None,
    ) -> dict[str, Any]:
        tracks: list[dict[str, Any]] = [self._video_track(segments)]
        if bgm is not None:
            tracks.append(self._audio_track(bgm))
        if captions:
            tracks.append(self._text_track(captions))

        return {
            "version": self.SCHEMA_VERSION,
            "name": meta.name,
            "profile_name": meta.profile_name,
            "target_duration_ms": meta.target_duration_ms,
            "tracks": tracks,
        }

    @staticmethod
    def _video_track(segments: list[WriterSegment]) -> dict[str, Any]:
        return {
            "type": "video",
            "segments": [
                {
                    "order": s.order,
                    "source_path": s.blurred_source_path or s.asset_path,
                    "asset_start_ms": s.asset_start_ms,
                    "asset_end_ms": s.asset_end_ms,
                    "on_timeline_start_ms": s.on_timeline_start_ms,
                    "on_timeline_end_ms": s.on_timeline_end_ms,
                    "transition": s.transition,
                    "reframe_keyframes": s.reframe_keyframes or [],
                }
                for s in segments
            ],
        }

    @staticmethod
    def _audio_track(bgm: WriterBGM) -> dict[str, Any]:
        return {
            "type": "audio",
            "source_path": bgm.file_path,
            "start_ms": bgm.start_ms,
        }

    @staticmethod
    def _text_track(captions: list[WriterCaption]) -> dict[str, Any]:
        return {
            "type": "text",
            "lines": [
                {"start_ms": c.start_ms, "end_ms": c.end_ms, "text": c.text} for c in captions
            ],
        }

    def _build_meta(self, meta: WriterDraftMeta) -> dict[str, Any]:
        return {
            "version": self.SCHEMA_VERSION,
            "name": meta.name,
            "profile_name": meta.profile_name,
            "target_duration_ms": meta.target_duration_ms,
        }
