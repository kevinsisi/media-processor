"""FastAPI dependencies — async DB session, LLM patcher, profile loader."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Annotated

from fastapi import Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from media_processor.api.config import settings
from media_processor.core.db import async_session_maker
from media_processor.profile import load_profile
from media_processor.profile.loader import ProfileSpec, ProfileValidationError
from media_processor.services.llm_patcher import GeminiKeyPoolConfig, LLMPatcher
from media_processor.services.settings_store import build_opencode_config, get_llm_api_keys


async def get_session() -> AsyncIterator[AsyncSession]:
    async with async_session_maker() as session:
        yield session


async def get_llm_patcher(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> LLMPatcher:
    """Build an LLMPatcher — OpenCode primary, Gemini fallback.

    Resolves settings from DB with env fallback. Raises 503 when neither
    OpenCode servers nor Gemini keys are configured.
    """
    keys = await get_llm_api_keys(session)
    opencode_config = await build_opencode_config(session)
    if not keys and opencode_config is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="LLM patcher not configured: set OPENCODE_SERVERS or LLM_API_KEYS",
        )
    config = GeminiKeyPoolConfig(
        api_keys=keys,
        model=settings.llm_model,
        timeout_s=settings.llm_timeout_s,
    )
    return LLMPatcher(config, opencode_config=opencode_config)


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
