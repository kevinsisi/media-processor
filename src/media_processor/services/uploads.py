"""Disk-side helpers for the chunked upload protocol.

Pure-Python, sync-IO. The DB layer (in the routers) is the source of truth for
which chunk indexes are present. These helpers handle byte-level operations:
writing a chunk, listing what's on disk, assembling the final file, removing
the scratch dir, and probing media metadata.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

CHUNK_NAME_FMT = "{:04d}"
CHUNK_NAME_RE = re.compile(r"^\d{4}$")
FFPROBE_TIMEOUT_S = 10.0


@dataclass(frozen=True)
class MediaProbe:
    duration_ms: int
    resolution: str | None
    fps: float | None
    codec: str | None


def session_dir(uploads_root: str | Path, session_id: str) -> Path:
    return Path(uploads_root) / session_id


def chunks_dir(uploads_root: str | Path, session_id: str) -> Path:
    return session_dir(uploads_root, session_id) / "chunks"


def chunk_path(uploads_root: str | Path, session_id: str, index: int) -> Path:
    return chunks_dir(uploads_root, session_id) / CHUNK_NAME_FMT.format(index)


def write_chunk(uploads_root: str | Path, session_id: str, index: int, data: bytes) -> int:
    """Write a chunk to disk, returning the byte count written.

    Idempotent: re-PUTting the same index overwrites the existing file.
    """
    target = chunk_path(uploads_root, session_id, index)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp")
    with tmp.open("wb") as fh:
        fh.write(data)
    os.replace(tmp, target)
    return len(data)


def list_present_chunks(uploads_root: str | Path, session_id: str) -> list[int]:
    """Return sorted list of chunk indexes actually present on disk."""
    d = chunks_dir(uploads_root, session_id)
    if not d.is_dir():
        return []
    out: list[int] = []
    for entry in d.iterdir():
        if entry.is_file() and CHUNK_NAME_RE.match(entry.name):
            out.append(int(entry.name))
    out.sort()
    return out


def assemble_file(
    uploads_root: str | Path,
    session_id: str,
    target_path: str | Path,
    expected_total_chunks: int,
) -> int:
    """Concatenate chunks 0..n-1 into ``target_path``. Returns the total bytes written."""
    target = Path(target_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".part")
    written = 0
    with tmp.open("wb") as out:
        for i in range(expected_total_chunks):
            cp = chunk_path(uploads_root, session_id, i)
            if not cp.exists():
                tmp.unlink(missing_ok=True)
                raise FileNotFoundError(f"missing chunk index {i} for session {session_id}")
            with cp.open("rb") as src:
                shutil.copyfileobj(src, out)
                written += cp.stat().st_size
    os.replace(tmp, target)
    return written


def cleanup_session_dir(uploads_root: str | Path, session_id: str) -> None:
    sd = session_dir(uploads_root, session_id)
    if sd.exists():
        shutil.rmtree(sd, ignore_errors=True)


def probe_media(path: str | Path) -> MediaProbe:
    """Run ``ffprobe`` against ``path``. Returns a degraded probe on any error."""
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=codec_name,width,height,r_frame_rate",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=FFPROBE_TIMEOUT_S,
            check=True,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return MediaProbe(duration_ms=0, resolution=None, fps=None, codec=None)

    fields: dict[str, str] = {}
    for line in result.stdout.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            fields[k.strip()] = v.strip()

    duration_ms = 0
    if "duration" in fields:
        try:
            duration_ms = int(float(fields["duration"]) * 1000)
        except ValueError:
            duration_ms = 0

    resolution = None
    width, height = fields.get("width"), fields.get("height")
    if width and height:
        try:
            resolution = f"{int(width)}x{int(height)}"
        except ValueError:
            resolution = None

    fps = None
    rate = fields.get("r_frame_rate")
    if rate and "/" in rate:
        num, den = rate.split("/", 1)
        try:
            n, d = float(num), float(den)
            if d > 0:
                fps = n / d
        except ValueError:
            fps = None

    codec = fields.get("codec_name") or None
    return MediaProbe(duration_ms=duration_ms, resolution=resolution, fps=fps, codec=codec)


def expected_chunk_count(total_size: int, chunk_size: int) -> int:
    if total_size == 0:
        return 0
    return (total_size + chunk_size - 1) // chunk_size
