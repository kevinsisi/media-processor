"""Pexels stock footage search and download service.

Integrates with the Pexels Videos API to search for stock clips by
keyword, download selected videos to the assets storage directory, and
create Asset database records ready for the NarratoAI editorial pipeline.

API reference: https://www.pexels.com/api/documentation/#videos-search
"""

from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from media_processor.api.config import settings

logger = logging.getLogger(__name__)

_PEXELS_BASE = "https://api.pexels.com/videos"
_DOWNLOAD_TIMEOUT_S = 120.0
_SEARCH_TIMEOUT_S = 15.0


class PexelsError(RuntimeError):
    """Pexels API or download failure."""


@dataclass
class PexelsVideoFile:
    """A single video file variant from Pexels."""
    url: str
    width: int
    height: int
    fps: float
    duration_s: int
    quality: str  # "hd" | "sd" | "uhd"


@dataclass
class PexelsVideo:
    """A Pexels video search result."""
    id: int
    url: str
    duration_s: int
    width: int
    height: int
    user_name: str
    files: list[PexelsVideoFile]

    @property
    def aspect_ratio(self) -> str:
        if self.width == 0 or self.height == 0:
            return "unknown"
        ratio = self.width / self.height
        if ratio > 1.5:
            return "16:9"
        if ratio < 0.7:
            return "9:16"
        return "1:1"

    def best_file(self, prefer_hd: bool = True) -> PexelsVideoFile | None:
        """Return the best quality video file, preferring HD."""
        if not self.files:
            return None
        quality_order = ["hd", "uhd", "sd"] if prefer_hd else ["uhd", "hd", "sd"]
        for q in quality_order:
            matches = [f for f in self.files if f.quality == q]
            if matches:
                return sorted(matches, key=lambda f: f.width * f.height, reverse=True)[0]
        return self.files[0]


def _parse_video(raw: dict[str, Any]) -> PexelsVideo:
    files = [
        PexelsVideoFile(
            url=f.get("link", ""),
            width=int(f.get("width", 0)),
            height=int(f.get("height", 0)),
            fps=float(f.get("fps", 0)),
            duration_s=int(raw.get("duration", 0)),
            quality=str(f.get("quality", "sd")),
        )
        for f in raw.get("video_files", [])
        if f.get("link")
    ]
    user = raw.get("user", {})
    return PexelsVideo(
        id=int(raw.get("id", 0)),
        url=raw.get("url", ""),
        duration_s=int(raw.get("duration", 0)),
        width=int(raw.get("width", 0)),
        height=int(raw.get("height", 0)),
        user_name=user.get("name", ""),
        files=files,
    )


def _api_key() -> str:
    key = settings.pexels_api_key.strip()
    if not key:
        raise PexelsError(
            "PEXELS_API_KEY not configured — set it in settings or .env"
        )
    return key


async def search_videos(
    query: str,
    *,
    per_page: int = 10,
    page: int = 1,
    orientation: str | None = None,  # "landscape" | "portrait" | "square"
    min_duration_s: int = 3,
    max_duration_s: int = 60,
) -> list[PexelsVideo]:
    """Search Pexels for stock videos matching *query*.

    Returns a list of PexelsVideo objects sorted by relevance (Pexels API order).
    Filters out clips shorter than min_duration_s or longer than max_duration_s.
    """
    params: dict[str, Any] = {
        "query": query,
        "per_page": min(80, max(1, per_page)),
        "page": max(1, page),
    }
    if orientation:
        params["orientation"] = orientation

    async with httpx.AsyncClient(timeout=_SEARCH_TIMEOUT_S) as client:
        resp = await client.get(
            f"{_PEXELS_BASE}/search",
            params=params,
            headers={"Authorization": _api_key()},
        )

    if resp.status_code == 401:
        raise PexelsError("Pexels API key rejected (401 Unauthorized)")
    if resp.status_code != 200:
        raise PexelsError(f"Pexels search error {resp.status_code}: {resp.text[:300]}")

    data = resp.json()
    videos = [_parse_video(v) for v in data.get("videos", [])]
    return [
        v for v in videos
        if min_duration_s <= v.duration_s <= max_duration_s
    ]


async def download_video(
    video: PexelsVideo,
    *,
    target_dir: str | None = None,
    prefer_hd: bool = True,
) -> Path:
    """Download the best file variant of *video* to *target_dir*.

    Returns the local file path. Skips download if the file already exists
    (keyed by Pexels video ID so re-searches don't re-download).
    """
    dest_dir = Path(target_dir or settings.assets_dir) / "pexels"
    dest_dir.mkdir(parents=True, exist_ok=True)

    file_obj = video.best_file(prefer_hd=prefer_hd)
    if file_obj is None:
        raise PexelsError(f"no downloadable file for Pexels video {video.id}")

    ext = "mp4"
    dest_path = dest_dir / f"pexels_{video.id}_{file_obj.quality}.{ext}"
    if dest_path.is_file() and dest_path.stat().st_size > 0:
        logger.info("Pexels video %d already cached at %s", video.id, dest_path)
        return dest_path

    logger.info("Downloading Pexels video %d from %s", video.id, file_obj.url)
    async with httpx.AsyncClient(timeout=_DOWNLOAD_TIMEOUT_S, follow_redirects=True) as client:
        async with client.stream("GET", file_obj.url) as resp:
            if resp.status_code != 200:
                raise PexelsError(
                    f"Pexels download error {resp.status_code} for video {video.id}"
                )
            with open(dest_path, "wb") as f:
                async for chunk in resp.aiter_bytes(65536):
                    f.write(chunk)

    logger.info("Downloaded Pexels video %d → %s (%.1f MB)",
                video.id, dest_path, dest_path.stat().st_size / 1_048_576)
    return dest_path


def video_to_asset_info(video: PexelsVideo, local_path: Path) -> dict[str, Any]:
    """Build minimal Asset-compatible metadata from a downloaded Pexels video.

    The caller (router) creates the actual Asset ORM row; this returns the
    dict of fields to set.
    """
    file_obj = video.best_file()
    sha256 = _sha256_file(local_path)
    return {
        "file_path": str(local_path),
        "duration_ms": video.duration_s * 1000,
        "resolution": f"{video.width}x{video.height}" if file_obj else None,
        "fps": file_obj.fps if file_obj else None,
        "sha256": sha256,
        "source": "pexels",
        "pexels_id": video.id,
        "pexels_url": video.url,
        "pexels_author": video.user_name,
    }


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()
