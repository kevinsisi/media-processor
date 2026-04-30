"""FastAPI dependencies — async DB session, LLM patcher, profile loader."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from pathlib import Path

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from media_processor.api.config import settings
from media_processor.core.db import async_session_maker
from media_processor.profile import load_profile
from media_processor.profile.loader import ProfileSpec, ProfileValidationError
from media_processor.services.llm_patcher import GeminiKeyPoolConfig, LLMPatcher


async def get_session() -> AsyncIterator[AsyncSession]:
    async with async_session_maker() as session:
        yield session


def get_llm_patcher() -> LLMPatcher:
    """Build an ``LLMPatcher`` from the configured key pool.

    Raises ``503`` when no keys are configured so callers can fall back to a
    non-LLM recut path (see spec §6.5 fallback).
    """
    keys = tuple(k.strip() for k in settings.llm_api_keys.split(",") if k.strip())
    if not keys:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="LLM patcher not configured: set LLM_API_KEYS",
        )
    config = GeminiKeyPoolConfig(
        api_keys=keys,
        model=settings.llm_model,
        timeout_s=settings.llm_timeout_s,
    )
    return LLMPatcher(config)


def get_profile_loader() -> Callable[[str], ProfileSpec]:
    """Return a function that loads a profile YAML by ``profile_name``.

    Raises ``404`` when the profile YAML is missing or invalid so the patch
    endpoint can return a clear error to the client.
    """
    base = Path(settings.profiles_dir)

    def _load(profile_name: str) -> ProfileSpec:
        path = base / f"{profile_name}.yaml"
        try:
            return load_profile(path)
        except ProfileValidationError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"profile '{profile_name}' not found or invalid: {exc}",
            ) from exc

    return _load
