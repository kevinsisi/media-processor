"""Health endpoint."""

import tomllib
from pathlib import Path
from typing import Any

from fastapi import APIRouter

from media_processor.core.db import ping_postgres, ping_redis

router = APIRouter()


def _package_version() -> str:
    """Read media-processor version directly from pyproject.toml at startup.

    Single source of truth: pyproject.toml. The api Dockerfile copies
    pyproject.toml to /app/pyproject.toml so the runtime can read it
    without the package being pip-installed. importlib.metadata.version()
    is not used because the api image installs the dependencies but does
    NOT install media-processor itself, so the metadata is absent.
    """
    candidates = [
        Path("/app/pyproject.toml"),
        Path(__file__).resolve().parents[4] / "pyproject.toml",
    ]
    for path in candidates:
        if path.exists():
            with path.open("rb") as fh:
                data = tomllib.load(fh)
            project = data.get("project", {})
            v = project.get("version")
            if isinstance(v, str) and v:
                return v
    return "0.0.0"


VERSION = _package_version()


@router.get("/health")
async def health() -> dict[str, Any]:
    pg_ok = await ping_postgres()
    redis_ok = await ping_redis()
    return {
        "status": "ok" if (pg_ok and redis_ok) else "degraded",
        "version": VERSION,
        "dependencies": {
            "postgres": "up" if pg_ok else "down",
            "redis": "up" if redis_ok else "down",
        },
    }
