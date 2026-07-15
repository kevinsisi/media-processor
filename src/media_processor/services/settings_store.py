"""DB-backed runtime settings with env fallback.

The LLM key pool used by scene-tagging, script-coverage, and the draft
patcher is managed here. Resolution order:

1. ``app_settings`` row (key=``llm_api_keys``) if non-empty — set via the
   Settings UI / ``PUT /settings/llm-api-keys``.
2. ``LLM_API_KEYS`` env var (read at startup into ``settings.llm_api_keys``).

Storing in the DB means edits in the UI are picked up on the next
pipeline call without restarting containers.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from media_processor.api.config import settings
from media_processor.models.app_setting import AppSetting

if TYPE_CHECKING:
    from media_processor.services.opencode_client import OpenCodeConfig
    from media_processor.services.story_tts import NarrationSettings

LLM_API_KEYS_KEY = "llm_api_keys"

# Google AI keys: AIzaSy + 33 base64url chars. Match the canonical format
# so a fat-fingered paste with a stray comma / quote / "export " prefix
# doesn't corrupt the pool.
_GEMINI_KEY_RE = re.compile(r"^AIzaSy[0-9A-Za-z_\-]{30,40}$")


@dataclass(frozen=True)
class KeyPoolSummary:
    """UI-safe view of the configured key pool — no full key values."""

    count: int
    source: str  # "db" | "env" | "none"
    masked_suffixes: tuple[str, ...]


def parse_keys_input(raw: str) -> list[str]:
    """Tolerant parser for textarea pastes.

    Accepts comma-separated, newline-separated, or KEY=VALUE / export-prefixed
    lines. Mirrors sheet-to-car's ``parseBatchInput`` so the homelab
    workflows feel consistent.
    """

    if not raw:
        return []
    out: list[str] = []
    for raw_line in raw.replace("\r\n", "\n").split("\n"):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" in line:
            lhs, _, rhs = line.partition("=")
            if re.fullmatch(r"\w+", lhs.strip()):
                line = rhs.strip().strip("\"'")
        if "," in line:
            out.extend(p.strip() for p in line.split(",") if p.strip())
        else:
            out.append(line)
    # Filter to plausibly-Gemini keys, dedupe preserving order
    seen: set[str] = set()
    valid: list[str] = []
    for k in out:
        if not _GEMINI_KEY_RE.fullmatch(k) or k in seen:
            continue
        seen.add(k)
        valid.append(k)
    return valid


def _split_env_keys() -> list[str]:
    return [k.strip() for k in settings.llm_api_keys.split(",") if k.strip()]


async def get_llm_api_keys(session: AsyncSession) -> tuple[str, ...]:
    """Resolve the active key pool: DB row first, env fallback."""

    row = (
        await session.execute(select(AppSetting.value).where(AppSetting.key == LLM_API_KEYS_KEY))
    ).scalar_one_or_none()
    if row:
        keys = [k.strip() for k in row.split(",") if k.strip()]
        if keys:
            return tuple(keys)
    return tuple(_split_env_keys())


async def get_pool_summary(session: AsyncSession) -> KeyPoolSummary:
    row = (
        await session.execute(select(AppSetting.value).where(AppSetting.key == LLM_API_KEYS_KEY))
    ).scalar_one_or_none()
    db_keys = [k.strip() for k in (row or "").split(",") if k.strip()]
    if db_keys:
        return KeyPoolSummary(
            count=len(db_keys),
            source="db",
            masked_suffixes=tuple(k[-4:] for k in db_keys),
        )
    env_keys = _split_env_keys()
    if env_keys:
        return KeyPoolSummary(
            count=len(env_keys),
            source="env",
            masked_suffixes=tuple(k[-4:] for k in env_keys),
        )
    return KeyPoolSummary(count=0, source="none", masked_suffixes=())


async def set_llm_api_keys(session: AsyncSession, keys: list[str]) -> int:
    """Persist a deduped key list. Returns the stored count."""

    deduped: list[str] = []
    seen: set[str] = set()
    for k in keys:
        k = k.strip()
        if not k or k in seen:
            continue
        seen.add(k)
        deduped.append(k)

    stmt = pg_insert(AppSetting).values(
        key=LLM_API_KEYS_KEY,
        value=",".join(deduped),
        updated_at=datetime.now(UTC),
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["key"],
        set_={"value": stmt.excluded.value, "updated_at": stmt.excluded.updated_at},
    )
    await session.execute(stmt)
    await session.commit()
    return len(deduped)


async def clear_llm_api_keys(session: AsyncSession) -> None:
    """Drop the DB-stored pool — pipeline falls back to the env value."""

    stmt = pg_insert(AppSetting).values(
        key=LLM_API_KEYS_KEY,
        value="",
        updated_at=datetime.now(UTC),
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["key"],
        set_={"value": "", "updated_at": stmt.excluded.updated_at},
    )
    await session.execute(stmt)
    await session.commit()


# ---------- OpenCode settings ----------

OPENCODE_SERVERS_KEY = "opencode_servers"
OPENCODE_TEXT_MODEL_KEY = "opencode_text_model"
OPENCODE_TEXT_VARIANT_KEY = "opencode_text_variant"
STORY_TTS_PROVIDER_KEY = "story_tts_provider"
STORY_TTS_VOICE_KEY = "story_tts_voice"
STORY_TTS_MODEL_KEY = "story_tts_model"
STORY_TTS_TIMEOUT_KEY = "story_tts_timeout_s"


@dataclass(frozen=True)
class OpenCodeServer:
    id: str
    label: str
    base_url: str


async def _get_setting(session: AsyncSession, key: str) -> str | None:
    return (
        await session.execute(select(AppSetting.value).where(AppSetting.key == key))
    ).scalar_one_or_none()


async def _upsert_setting(session: AsyncSession, key: str, value: str) -> None:
    stmt = pg_insert(AppSetting).values(key=key, value=value, updated_at=datetime.now(UTC))
    stmt = stmt.on_conflict_do_update(
        index_elements=["key"],
        set_={"value": stmt.excluded.value, "updated_at": stmt.excluded.updated_at},
    )
    await session.execute(stmt)


def _parse_server_urls(raw: str) -> list[str]:
    urls: list[str] = []
    for chunk in raw.replace("\r\n", "\n").replace(",", "\n").split("\n"):
        u = chunk.strip()
        if u and (u.startswith("http://") or u.startswith("https://")):
            urls.append(u)
    return list(dict.fromkeys(urls))  # dedupe, preserve order


def _servers_from_urls(urls: list[str]) -> list[OpenCodeServer]:
    return [
        OpenCodeServer(id=f"opencode-{i + 1}", label=f"OpenCode {i + 1}", base_url=url)
        for i, url in enumerate(urls)
    ]


async def get_opencode_servers(session: AsyncSession) -> tuple[list[OpenCodeServer], str]:
    """Resolve OpenCode servers (DB → env → empty). Returns (servers, source)."""
    row = await _get_setting(session, OPENCODE_SERVERS_KEY)
    if row and row.strip():
        urls = _parse_server_urls(row)
        if urls:
            return _servers_from_urls(urls), "setting"
    env_val = settings.opencode_servers
    if env_val and env_val.strip():
        urls = _parse_server_urls(env_val)
        if urls:
            return _servers_from_urls(urls), "env"
    return [], "none"


async def get_opencode_text_model(session: AsyncSession) -> tuple[str, str]:
    """Returns (model, source). source: 'setting' | 'env' | 'default'."""
    row = await _get_setting(session, OPENCODE_TEXT_MODEL_KEY)
    if row and row.strip():
        return row.strip(), "setting"
    env_val = settings.opencode_model
    if env_val and env_val.strip():
        return env_val.strip(), "env"
    return "opencode/mimo-v2.5-free", "default"


async def get_opencode_text_variant(session: AsyncSession) -> tuple[str, str]:
    """Returns (variant, source). source: 'setting' | 'env' | 'default'."""
    row = await _get_setting(session, OPENCODE_TEXT_VARIANT_KEY)
    if row and row.strip():
        return row.strip(), "setting"
    env_val = settings.opencode_variant
    if env_val and env_val.strip():
        return env_val.strip(), "env"
    return "medium", "default"


async def set_opencode_settings(
    session: AsyncSession,
    *,
    servers_raw: str | None = None,
    text_model: str | None = None,
    text_variant: str | None = None,
) -> None:
    """Persist OpenCode settings. Pass empty string to clear a field."""
    if servers_raw is not None:
        await _upsert_setting(session, OPENCODE_SERVERS_KEY, servers_raw)
    if text_model is not None:
        await _upsert_setting(session, OPENCODE_TEXT_MODEL_KEY, text_model)
    if text_variant is not None:
        await _upsert_setting(session, OPENCODE_TEXT_VARIANT_KEY, text_variant)
    await session.commit()


async def clear_opencode_settings(session: AsyncSession) -> None:
    """Clear all DB-stored OpenCode settings (falls through to env on next read)."""
    for key in (OPENCODE_SERVERS_KEY, OPENCODE_TEXT_MODEL_KEY, OPENCODE_TEXT_VARIANT_KEY):
        await _upsert_setting(session, key, "")
    await session.commit()


async def build_opencode_config(
    session: AsyncSession,
) -> OpenCodeConfig | None:
    """Resolve OpenCode settings and return a ready-to-use config, or None if not configured."""
    from media_processor.services.opencode_client import OpenCodeConfig

    servers, _ = await get_opencode_servers(session)
    if not servers:
        return None
    model, _ = await get_opencode_text_model(session)
    variant, _ = await get_opencode_text_variant(session)
    return OpenCodeConfig(
        servers=tuple(s.base_url for s in servers),
        model=model,
        variant=variant,
        password=settings.opencode_server_password,
        timeout_s=settings.llm_timeout_s,
    )


# ---------- Story TTS settings ----------


async def get_story_tts_provider(session: AsyncSession) -> tuple[str, str]:
    row = await _get_setting(session, STORY_TTS_PROVIDER_KEY)
    if row and row.strip():
        return row.strip().lower(), "setting"
    env_val = settings.story_tts_provider.strip().lower()
    if env_val:
        return env_val, "env"
    return "", "none"


async def get_story_tts_voice(session: AsyncSession) -> tuple[str, str]:
    row = await _get_setting(session, STORY_TTS_VOICE_KEY)
    if row and row.strip():
        return row.strip(), "setting"
    env_val = settings.story_tts_voice.strip()
    if env_val:
        return env_val, "env"
    return "zh-TW-HsiaoChenNeural", "default"


async def get_story_tts_model(session: AsyncSession) -> tuple[str, str]:
    row = await _get_setting(session, STORY_TTS_MODEL_KEY)
    if row and row.strip():
        return row.strip(), "setting"
    env_val = settings.story_tts_model.strip()
    if env_val:
        return env_val, "env"
    return "edge-tts", "default"


async def get_story_tts_timeout(session: AsyncSession) -> tuple[float, str]:
    row = await _get_setting(session, STORY_TTS_TIMEOUT_KEY)
    if row and row.strip():
        try:
            return max(1.0, float(row.strip())), "setting"
        except ValueError:
            pass
    return max(1.0, float(settings.story_tts_timeout_s)), "env"


async def set_story_tts_settings(
    session: AsyncSession,
    *,
    provider: str | None = None,
    voice: str | None = None,
    model: str | None = None,
    timeout_s: float | None = None,
) -> None:
    if provider is not None:
        await _upsert_setting(session, STORY_TTS_PROVIDER_KEY, provider.strip().lower())
    if voice is not None:
        await _upsert_setting(session, STORY_TTS_VOICE_KEY, voice.strip())
    if model is not None:
        await _upsert_setting(session, STORY_TTS_MODEL_KEY, model.strip())
    if timeout_s is not None:
        await _upsert_setting(session, STORY_TTS_TIMEOUT_KEY, str(max(1.0, float(timeout_s))))
    await session.commit()


async def clear_story_tts_settings(session: AsyncSession) -> None:
    for key in (
        STORY_TTS_PROVIDER_KEY,
        STORY_TTS_VOICE_KEY,
        STORY_TTS_MODEL_KEY,
        STORY_TTS_TIMEOUT_KEY,
    ):
        await _upsert_setting(session, key, "")
    await session.commit()


async def build_story_tts_config(session: AsyncSession) -> NarrationSettings | None:
    from media_processor.services.story_tts import NarrationSettings

    provider, _ = await get_story_tts_provider(session)
    if not provider:
        return None
    voice, _ = await get_story_tts_voice(session)
    model, _ = await get_story_tts_model(session)
    timeout_s, _ = await get_story_tts_timeout(session)
    return NarrationSettings(
        provider=provider,
        voice=voice or "zh-TW-HsiaoChenNeural",
        model=model or provider,
        timeout_s=timeout_s,
    )
