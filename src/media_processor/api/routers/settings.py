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
    OpenCodeServer,
    clear_llm_api_keys,
    clear_opencode_settings,
    clear_story_tts_settings,
    get_opencode_servers,
    get_opencode_text_model,
    get_opencode_text_variant,
    get_pool_summary,
    get_story_tts_model,
    get_story_tts_provider,
    get_story_tts_timeout,
    get_story_tts_voice,
    parse_keys_input,
    set_llm_api_keys,
    set_opencode_settings,
    set_story_tts_settings,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/settings", tags=["settings"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]

DEFAULT_KEY_MANAGER_URL = "http://100.126.226.79:7823"


class KeyPoolOut(BaseModel):
    count: int
    source: str  # "db" | "env" | "none"
    masked_suffixes: list[str]


class SettingsOut(BaseModel):
    llm_model: str
    llm_timeout_s: float
    llm_api_keys: KeyPoolOut


class StoryTtsStatusOut(BaseModel):
    provider: str
    provider_source: str
    voice: str
    voice_source: str
    model: str
    model_source: str
    timeout_s: float
    timeout_source: str


class StoryTtsSettingsIn(BaseModel):
    provider: str | None = Field(default=None, max_length=32)
    voice: str | None = Field(default=None, max_length=128)
    model: str | None = Field(default=None, max_length=128)
    timeout_s: float | None = Field(default=None, ge=1.0, le=300.0)


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


class OpenCodeServerOut(BaseModel):
    id: str
    label: str
    base_url: str


class OpenCodeStatusOut(BaseModel):
    servers: list[OpenCodeServerOut]
    servers_source: str
    text_model: str
    text_model_source: str
    text_variant: str
    text_variant_source: str


class OpenCodeSettingsIn(BaseModel):
    servers: str | None = None
    text_model: str | None = None
    text_variant: str | None = None


class OpenCodeModelOut(BaseModel):
    id: str
    name: str
    provider: str


class OpenCodeModelsOut(BaseModel):
    models: list[OpenCodeModelOut]
    source_server_id: str | None
    warning: str | None


def _oc_server_out(s: OpenCodeServer) -> OpenCodeServerOut:
    return OpenCodeServerOut(id=s.id, label=s.label, base_url=s.base_url)


def _opencode_models_from_payload(data: object) -> list[OpenCodeModelOut]:
    """Normalize OpenCode /provider payloads across server versions."""
    providers: object
    if isinstance(data, list):
        providers = data
    elif isinstance(data, dict):
        providers = data.get("providers") or data.get("all") or data.get("data") or []
    else:
        providers = []

    models: list[OpenCodeModelOut] = []
    for provider_entry in providers if isinstance(providers, list) else []:
        if not isinstance(provider_entry, dict):
            continue
        provider_id = str(provider_entry.get("id") or "")
        # opencode-go is the paid Zen subscription (every model 401s on a 0
        # balance); openai is banned by the No-OpenAI rule. Never surface either.
        if provider_id in ("opencode-go", "openai"):
            continue
        raw_models = provider_entry.get("models") or []
        model_entries = raw_models.values() if isinstance(raw_models, dict) else raw_models
        for model_entry in model_entries:
            if not isinstance(model_entry, dict):
                continue
            # Only free models: paid Zen models return 401 "Insufficient balance".
            cost_raw = model_entry.get("cost")
            cost: dict[object, object] = cost_raw if isinstance(cost_raw, dict) else {}
            if not (cost.get("input") == 0 and cost.get("output") == 0):
                continue
            model_id = str(model_entry.get("id") or "")
            model_name = str(model_entry.get("name") or model_id)
            model_provider = str(model_entry.get("providerID") or provider_id)
            if model_id:
                models.append(
                    OpenCodeModelOut(
                        id=f"{model_provider}/{model_id}" if model_provider else model_id,
                        name=model_name,
                        provider=model_provider,
                    )
                )
    return models


async def _build_opencode_status(session: SessionDep) -> OpenCodeStatusOut:
    servers, srv_source = await get_opencode_servers(session)
    model, model_source = await get_opencode_text_model(session)
    variant, variant_source = await get_opencode_text_variant(session)
    return OpenCodeStatusOut(
        servers=[_oc_server_out(s) for s in servers],
        servers_source=srv_source,
        text_model=model,
        text_model_source=model_source,
        text_variant=variant,
        text_variant_source=variant_source,
    )


async def _build_story_tts_status(session: SessionDep) -> StoryTtsStatusOut:
    provider, provider_source = await get_story_tts_provider(session)
    voice, voice_source = await get_story_tts_voice(session)
    model, model_source = await get_story_tts_model(session)
    timeout_s, timeout_source = await get_story_tts_timeout(session)
    return StoryTtsStatusOut(
        provider=provider,
        provider_source=provider_source,
        voice=voice,
        voice_source=voice_source,
        model=model,
        model_source=model_source,
        timeout_s=timeout_s,
        timeout_source=timeout_source,
    )


@router.get("/story-tts", response_model=StoryTtsStatusOut)
async def get_story_tts_settings(session: SessionDep) -> StoryTtsStatusOut:
    return await _build_story_tts_status(session)


@router.put("/story-tts", response_model=StoryTtsStatusOut)
async def update_story_tts_settings(
    payload: StoryTtsSettingsIn,
    session: SessionDep,
) -> StoryTtsStatusOut:
    await set_story_tts_settings(
        session,
        provider=payload.provider,
        voice=payload.voice,
        model=payload.model,
        timeout_s=payload.timeout_s,
    )
    return await _build_story_tts_status(session)


@router.delete("/story-tts", response_model=StoryTtsStatusOut)
async def delete_story_tts_settings(session: SessionDep) -> StoryTtsStatusOut:
    await clear_story_tts_settings(session)
    return await _build_story_tts_status(session)


@router.get("/opencode", response_model=OpenCodeStatusOut)
async def get_opencode_settings(session: SessionDep) -> OpenCodeStatusOut:
    return await _build_opencode_status(session)


@router.put("/opencode", response_model=OpenCodeStatusOut)
async def update_opencode_settings(
    payload: OpenCodeSettingsIn,
    session: SessionDep,
) -> OpenCodeStatusOut:
    await set_opencode_settings(
        session,
        servers_raw=payload.servers,
        text_model=payload.text_model,
        text_variant=payload.text_variant,
    )
    return await _build_opencode_status(session)


@router.delete("/opencode", response_model=OpenCodeStatusOut)
async def delete_opencode_settings(session: SessionDep) -> OpenCodeStatusOut:
    await clear_opencode_settings(session)
    return await _build_opencode_status(session)


@router.get("/opencode/models", response_model=OpenCodeModelsOut)
async def get_opencode_models(session: SessionDep) -> OpenCodeModelsOut:
    servers, _ = await get_opencode_servers(session)
    if not servers:
        return OpenCodeModelsOut(
            models=[], source_server_id=None, warning="no OpenCode servers configured"
        )

    password = app_settings.opencode_server_password
    headers: dict[str, str] = {}
    if password:
        import base64

        token = base64.b64encode(f"opencode:{password}".encode()).decode()
        headers["Authorization"] = f"Basic {token}"

    last_warning: str | None = None
    async with httpx.AsyncClient(timeout=10.0) as client:
        for server in servers:
            try:
                resp = await client.get(f"{server.base_url.rstrip('/')}/provider", headers=headers)
            except httpx.HTTPError as exc:
                last_warning = f"server {server.id} unreachable: {exc}"
                logger.warning("opencode /provider failed (%s): %s", server.base_url, exc)
                continue
            if resp.status_code != 200:
                last_warning = f"server {server.id} returned HTTP {resp.status_code}"
                logger.warning("opencode /provider HTTP %d (%s)", resp.status_code, server.base_url)
                continue
            try:
                data = resp.json()
            except Exception as exc:  # noqa: BLE001
                last_warning = f"server {server.id} response parse failed: {exc}"
                continue
            models = _opencode_models_from_payload(data)
            if models:
                return OpenCodeModelsOut(
                    models=models,
                    source_server_id=server.id,
                    warning=None,
                )
    return OpenCodeModelsOut(
        models=[],
        source_server_id=None,
        warning=last_warning or "all servers returned empty model list",
    )


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
    raw_lines = sum(1 for line in payload.raw.replace("\r\n", "\n").split("\n") if line.strip())
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
        await session.execute(select(AppSetting.value).where(AppSetting.key == LLM_API_KEYS_KEY))
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
