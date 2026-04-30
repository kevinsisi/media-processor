"""SQLAlchemy async engine + session factory."""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from media_processor.api.config import settings

engine = create_async_engine(settings.postgres_dsn, echo=False, pool_pre_ping=True)
async_session_maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def ping_postgres() -> bool:
    """Lightweight check that the database is reachable."""
    from sqlalchemy import text

    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


async def ping_redis() -> bool:
    """Lightweight Redis reachability check."""
    import redis.asyncio as redis_asyncio

    client = redis_asyncio.from_url(settings.redis_url, socket_timeout=2.0)
    try:
        # redis-py's stubs declare Redis.ping as `Awaitable[bool] | bool` because
        # the same class serves both sync and async modes; in async mode the call
        # is always awaitable, but mypy can't see that. Cast keeps strict mode.
        from typing import Awaitable, cast

        result = cast(Awaitable[bool], client.ping())
        return bool(await result)
    except Exception:
        return False
    finally:
        await client.aclose()
