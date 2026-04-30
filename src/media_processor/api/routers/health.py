"""Health endpoint."""

from typing import Any

from fastapi import APIRouter

from media_processor.core.db import ping_postgres, ping_redis

router = APIRouter()

VERSION = "0.2.0"


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
