"""Health endpoint."""

from importlib.metadata import PackageNotFoundError, version
from typing import Any

from fastapi import APIRouter

from media_processor.core.db import ping_postgres, ping_redis

router = APIRouter()


def _package_version() -> str:
    """Read media-processor version from the installed package metadata.

    Single source of truth: pyproject.toml. Avoids the M3/M4 drift where
    a hardcoded VERSION string in this module silently lagged the bumps
    in pyproject.toml + main.py.
    """
    try:
        return version("media-processor")
    except PackageNotFoundError:
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
