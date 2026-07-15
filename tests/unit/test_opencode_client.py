from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from media_processor.services import opencode_client


def _patch_client(
    monkeypatch: pytest.MonkeyPatch,
    handler: Callable[[httpx.Request], httpx.Response],
) -> None:
    real_async_client = httpx.AsyncClient

    def patched_async_client(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(opencode_client.httpx, "AsyncClient", patched_async_client)


@pytest.mark.asyncio
async def test_call_opencode_text_uses_general_agent_and_text_system_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content.decode()))
        if request.url.path == "/session":
            return httpx.Response(200, json={"id": "ses_1"})
        if request.url.path == "/session/ses_1/message":
            return httpx.Response(200, json={"parts": [{"type": "text", "text": "OK"}]})
        return httpx.Response(200, json={})

    _patch_client(monkeypatch, handler)

    text = await opencode_client.call_opencode_text(
        prompt="只回覆 OK",
        system_prompt="你是精準執行器。",
        server_url="http://opencode.test",
        password="",
        model="opencode/mimo-v2.5-free",
        variant="medium",
        timeout_s=5.0,
    )

    assert text == "OK"
    assert bodies[0]["agent"] == "general"
    assert bodies[1]["agent"] == "general"
    assert bodies[1]["variant"] == "medium"
    assert "system" not in bodies[1]
    assert bodies[1]["parts"] == [
        {"type": "text", "text": "[system]\n你是精準執行器。\n\n只回覆 OK"}
    ]


@pytest.mark.asyncio
async def test_call_opencode_vision_uses_text_system_prompt_and_file_parts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content.decode()))
        if request.url.path == "/session":
            return httpx.Response(200, json={"id": "ses_1"})
        if request.url.path == "/session/ses_1/message":
            return httpx.Response(200, json={"parts": [{"type": "text", "text": "{}"}]})
        return httpx.Response(200, json={})

    _patch_client(monkeypatch, handler)

    text = await opencode_client.call_opencode_vision(
        prompt="描述圖片",
        images=[("image/jpeg", "abc123")],
        system_prompt="只輸出 JSON。",
        server_url="http://opencode.test",
        password="",
        model="opencode/mimo-v2.5-free",
        variant="medium",
        timeout_s=5.0,
    )

    assert text == "{}"
    assert bodies[1]["agent"] == "general"
    assert bodies[1]["variant"] == "medium"
    assert "system" not in bodies[1]
    assert bodies[1]["parts"][0] == {
        "type": "text",
        "text": "[system]\n只輸出 JSON。\n\n描述圖片",
    }
    assert bodies[1]["parts"][1] == {
        "type": "file",
        "mime": "image/jpeg",
        "mimeType": "image/jpeg",
        "data": "abc123",
    }
