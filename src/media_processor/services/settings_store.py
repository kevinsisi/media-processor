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

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from media_processor.api.config import settings
from media_processor.models.app_setting import AppSetting

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
        await session.execute(
            select(AppSetting.value).where(AppSetting.key == LLM_API_KEYS_KEY)
        )
    ).scalar_one_or_none()
    if row:
        keys = [k.strip() for k in row.split(",") if k.strip()]
        if keys:
            return tuple(keys)
    return tuple(_split_env_keys())


async def get_pool_summary(session: AsyncSession) -> KeyPoolSummary:
    row = (
        await session.execute(
            select(AppSetting.value).where(AppSetting.key == LLM_API_KEYS_KEY)
        )
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
