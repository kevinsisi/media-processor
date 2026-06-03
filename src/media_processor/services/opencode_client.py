"""OpenCode HTTP client for media-processor.

Implements the two-step OpenCode session protocol:
  1. POST /session  → session_id
  2. POST /session/{id}/message → response text
  3. DELETE /session/{id} (fire-and-forget cleanup)

OpenCode returns the complete response as JSON (not SSE streaming).
Password-protected servers use HTTP Basic auth; no-auth servers (canonical
HomeProject deployment at provider-amd.sisihome.org) send no Authorization header.
"""

from __future__ import annotations

import asyncio
import base64
import logging
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OpenCodeConfig:
    servers: tuple[str, ...]  # base URLs in priority order
    model: str  # "provider/id", e.g. "openai/gpt-5.5"
    variant: str  # "medium" | "high" | "default"
    password: str  # empty = no-auth
    timeout_s: float = 30.0


def _auth_headers(password: str) -> dict[str, str]:
    if not password:
        return {}
    token = base64.b64encode(f"opencode:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def _parse_model_ref(model: str) -> tuple[str, str]:
    """Split "provider/id" → (providerID, id). Defaults to ("openai", model)."""
    slash = model.find("/")
    if 0 < slash < len(model) - 1:
        return model[:slash], model[slash + 1 :]
    return "openai", model


async def call_opencode_text(
    *,
    prompt: str,
    system_prompt: str | None = None,
    server_url: str,
    password: str,
    model: str,
    variant: str,
    timeout_s: float,
) -> str | None:
    """One OpenCode text call. Returns the response text or None on any failure."""
    base = server_url.rstrip("/")
    headers = _auth_headers(password)
    provider_id, model_id = _parse_model_ref(model)

    async with httpx.AsyncClient(timeout=timeout_s) as client:
        # Step 1: create session
        try:
            resp = await client.post(
                f"{base}/session",
                json={
                    "title": "media-processor",
                    "agent": "user",
                    "model": {"providerID": provider_id, "id": model_id, "variant": variant},
                },
                headers=headers,
            )
        except httpx.HTTPError as exc:
            logger.warning("opencode /session transport error (%s): %s", base, exc)
            return None

        if resp.status_code >= 400:
            logger.warning("opencode /session returned HTTP %d (%s)", resp.status_code, base)
            return None

        try:
            session_id: str = resp.json()["id"]
        except Exception as exc:  # noqa: BLE001
            logger.warning("opencode /session response parse failed (%s): %s", base, exc)
            return None

        # Step 2: send message
        message_body: dict[str, object] = {
            "agent": "user",
            "model": {"providerID": provider_id, "modelID": model_id},
            "parts": [{"type": "text", "text": prompt}],
        }
        if system_prompt:
            message_body["system"] = system_prompt

        try:
            resp = await client.post(
                f"{base}/session/{session_id}/message",
                json=message_body,
                headers=headers,
            )
        except httpx.HTTPError as exc:
            logger.warning("opencode /message transport error (%s): %s", base, exc)
            asyncio.create_task(_delete_session(base, session_id, headers))
            return None

        asyncio.create_task(_delete_session(base, session_id, headers))

        if resp.status_code >= 400:
            logger.warning("opencode /message returned HTTP %d (%s)", resp.status_code, base)
            return None

        try:
            parts = resp.json().get("parts") or []
            text = next(
                (p["text"] for p in parts if isinstance(p, dict) and p.get("type") == "text"),
                None,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("opencode /message response parse failed (%s): %s", base, exc)
            return None

        if not isinstance(text, str) or not text.strip():
            logger.warning("opencode returned empty text (%s)", base)
            return None
        return text


async def call_opencode_vision(
    *,
    prompt: str,
    images: list[tuple[str, str]],
    system_prompt: str | None = None,
    server_url: str,
    password: str,
    model: str,
    variant: str,
    timeout_s: float,
) -> str | None:
    """One OpenCode multimodal call with base64 image parts.

    ``images`` is ``[(mime_type, base64_data), ...]``. Servers or models that
    do not accept image/file parts return ``None`` so callers can fall back.
    """
    base = server_url.rstrip("/")
    headers = _auth_headers(password)
    provider_id, model_id = _parse_model_ref(model)

    async with httpx.AsyncClient(timeout=timeout_s) as client:
        try:
            resp = await client.post(
                f"{base}/session",
                json={
                    "title": "media-processor-vision",
                    "agent": "user",
                    "model": {"providerID": provider_id, "id": model_id, "variant": variant},
                },
                headers=headers,
            )
        except httpx.HTTPError as exc:
            logger.warning("opencode vision /session transport error (%s): %s", base, exc)
            return None
        if resp.status_code >= 400:
            logger.warning("opencode vision /session returned HTTP %d (%s)", resp.status_code, base)
            return None

        try:
            session_id: str = resp.json()["id"]
        except Exception as exc:  # noqa: BLE001
            logger.warning("opencode vision /session response parse failed (%s): %s", base, exc)
            return None

        parts: list[dict[str, object]] = [{"type": "text", "text": prompt}]
        parts.extend(
            {
                "type": "file",
                "mime": mime_type,
                "mimeType": mime_type,
                "data": data,
            }
            for mime_type, data in images
        )
        body: dict[str, object] = {
            "agent": "user",
            "model": {"providerID": provider_id, "modelID": model_id},
            "parts": parts,
        }
        if system_prompt:
            body["system"] = system_prompt

        try:
            resp = await client.post(
                f"{base}/session/{session_id}/message",
                json=body,
                headers=headers,
            )
        except httpx.HTTPError as exc:
            logger.warning("opencode vision /message transport error (%s): %s", base, exc)
            asyncio.create_task(_delete_session(base, session_id, headers))
            return None

        asyncio.create_task(_delete_session(base, session_id, headers))
        if resp.status_code >= 400:
            logger.warning("opencode vision /message returned HTTP %d (%s)", resp.status_code, base)
            return None

        try:
            parts_out = resp.json().get("parts") or []
            text = next(
                (p["text"] for p in parts_out if isinstance(p, dict) and p.get("type") == "text"),
                None,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("opencode vision /message response parse failed (%s): %s", base, exc)
            return None

        if not isinstance(text, str) or not text.strip():
            logger.warning("opencode vision returned empty text (%s)", base)
            return None
        return text


async def _delete_session(base: str, session_id: str, headers: dict[str, str]) -> None:
    """Fire-and-forget session cleanup. Errors are silently ignored."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.delete(f"{base}/session/{session_id}", headers=headers)
    except Exception:  # noqa: BLE001
        pass
