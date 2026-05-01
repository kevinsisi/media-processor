"""Settings endpoints — runtime-managed LLM key pool.

Mirrors sheet-to-car's pattern: a textarea-friendly batch-import for the
Gemini key pool, plus a one-click sync from the homelab key-manager.
Values are stored in the ``app_settings`` table; the env var still acts
as a fallback (see ``services/settings_store``).
"""

from __future__ import annotations

import logging
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from media_processor.api.config import settings as app_settings
from media_processor.api.deps import get_session
from media_processor.services.settings_store import (
    clear_llm_api_keys,
    get_pool_summary,
    parse_keys_input,
    set_llm_api_keys,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/settings", tags=["settings"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]

DEFAULT_KEY_MANAGER_URL = "http://key.sisihome.org:7823"


class KeyPoolOut(BaseModel):
    count: int
    source: str  # "db" | "env" | "none"
    masked_suffixes: list[str]


class SettingsOut(BaseModel):
    llm_model: str
    llm_timeout_s: float
    llm_api_keys: KeyPoolOut


class LLMKeysUpdateIn(BaseModel):
    # Free-form textarea input — comma-separated, newline-separated,
    # KEY=VALUE, or "export KEY=VALUE" all accepted.
    raw: str = Field(..., min_length=0, max_length=200_000)
    replace: bool = Field(
        default=True,
        description="True replaces the pool; False merges with the existing DB pool.",
    )


class LLMKeysUpdateOut(BaseModel):
    stored_count: int
    accepted_count: int
    rejected_count: int


class SyncFromManagerIn(BaseModel):
    url: str = Field(default=DEFAULT_KEY_MANAGER_URL)
    trusted_only: bool = True
    replace: bool = False


class SyncFromManagerOut(BaseModel):
    fetched: int
    imported: int
    skipped: int
    stored_count: int


@router.get("", response_model=SettingsOut)
async def get_settings(session: SessionDep) -> SettingsOut:
    summary = await get_pool_summary(session)
    return SettingsOut(
        llm_model=app_settings.llm_model,
        llm_timeout_s=app_settings.llm_timeout_s,
        llm_api_keys=KeyPoolOut(
            count=summary.count,
            source=summary.source,
            masked_suffixes=list(summary.masked_suffixes),
        ),
    )


@router.put("/llm-api-keys", response_model=LLMKeysUpdateOut)
async def update_llm_api_keys(
    payload: LLMKeysUpdateIn,
    session: SessionDep,
) -> LLMKeysUpdateOut:
    parsed = parse_keys_input(payload.raw)
    accepted = len(parsed)
    raw_lines = sum(
        1 for line in payload.raw.replace("\r\n", "\n").split("\n") if line.strip()
    )
    rejected = max(0, raw_lines - accepted)

    if not payload.replace:
        from sqlalchemy import select  # local to keep router import surface tight

        from media_processor.models.app_setting import AppSetting
        from media_processor.services.settings_store import LLM_API_KEYS_KEY

        existing = (
            await session.execute(
                select(AppSetting.value).where(AppSetting.key == LLM_API_KEYS_KEY)
            )
        ).scalar_one_or_none()
        existing_keys = [k.strip() for k in (existing or "").split(",") if k.strip()]
        merged: list[str] = []
        seen: set[str] = set()
        for k in existing_keys + parsed:
            if k in seen:
                continue
            seen.add(k)
            merged.append(k)
        stored = await set_llm_api_keys(session, merged)
    else:
        stored = await set_llm_api_keys(session, parsed)

    return LLMKeysUpdateOut(
        stored_count=stored,
        accepted_count=accepted,
        rejected_count=rejected,
    )


@router.delete("/llm-api-keys", status_code=status.HTTP_204_NO_CONTENT)
async def clear_llm_keys(session: SessionDep) -> None:
    await clear_llm_api_keys(session)


@router.post("/sync-from-key-manager", response_model=SyncFromManagerOut)
async def sync_from_key_manager(
    payload: SyncFromManagerIn,
    session: SessionDep,
) -> SyncFromManagerOut:
    base = payload.url.rstrip("/")
    export_url = f"{base}/api/keys/export"
    params = {"trusted_only": "1"} if payload.trusted_only else {}

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(export_url, params=params)
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"key-manager unreachable: {exc}",
        ) from exc

    if resp.status_code != 200:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"key-manager returned HTTP {resp.status_code}",
        )

    data = resp.json()
    groups = data.get("groups") or {}
    fetched: list[str] = []
    for keys in groups.values():
        if isinstance(keys, list):
            fetched.extend(str(k) for k in keys)

    parsed = parse_keys_input(",".join(fetched))

    from sqlalchemy import select

    from media_processor.models.app_setting import AppSetting
    from media_processor.services.settings_store import LLM_API_KEYS_KEY

    existing = (
        await session.execute(
            select(AppSetting.value).where(AppSetting.key == LLM_API_KEYS_KEY)
        )
    ).scalar_one_or_none()
    existing_keys = [k.strip() for k in (existing or "").split(",") if k.strip()]

    if payload.replace:
        merged = parsed
        skipped = 0
        imported = len(parsed)
    else:
        existing_set = set(existing_keys)
        merged_list: list[str] = list(existing_keys)
        seen = set(existing_set)
        imported = 0
        skipped = 0
        for k in parsed:
            if k in seen:
                skipped += 1
                continue
            seen.add(k)
            merged_list.append(k)
            imported += 1
        merged = merged_list

    stored = await set_llm_api_keys(session, merged)
    return SyncFromManagerOut(
        fetched=len(parsed),
        imported=imported,
        skipped=skipped,
        stored_count=stored,
    )
