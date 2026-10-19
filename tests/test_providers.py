"""Provider payload conversion and transport behaviour (no network)."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest

from dualsight.chat import ChatRequest, ImagePart, Message, TextPart, Usage
from dualsight.errors import AuthError, UpstreamRateLimitError
from dualsight.providers.anthropic import AnthropicProvider
from dualsight.providers.gemini import GeminiProvider
from dualsight.providers.openai_compat import OpenAICompatibleProvider
from dualsight.providers.transport import ProviderHTTP


def _request(blob, text: str = "Describe this image.", model: str = "model-name") -> ChatRequest:
    return ChatRequest(
        model=model,
        messages=[
            Message(role="system", content="You are a helpful assistant."),
            Message(role="user", content=[TextPart(text), ImagePart(blob, detail="high")]),
        ],
        temperature=0.2,
        max_tokens=128,
    )


def test_openai_payload_and_response(mock_settings, blob) -> None:
    provider = OpenAICompatibleProvider(
        base_url="https://api.example/v1",
        api_key="secret",
        model="gpt-test",
        extra_body={"service_tier": "auto"},
    )
    captured: dict[str, Any] = {}

    async def fake_post(url: str, payload: dict[str, Any], headers=None):
        captured.update(url=url, payload=payload, headers=headers)
        return {
            "choices": [
                {
                    "message": {"content": "A blue card.", "tool_calls": []},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
        }

    provider.http.post_json = fake_post  # type: ignore[method-assign]
    result = asyncio.run(provider.complete(_request(blob, model="gpt-test")))

    assert captured["url"] == "https://api.example/v1/chat/completions"
    assert captured["payload"]["model"] == "gpt-test"
    assert captured["payload"]["stream"] is False
    assert captured["payload"]["service_tier"] == "auto"
    assert captured["payload"]["temperature"] == 0.2
    assert captured["payload"]["max_tokens"] == 128
    message = captured["payload"]["messages"][1]
    assert message["content"][0] == {"type": "text", "text": "Describe this image."}
    assert message["content"][1]["type"] == "image_url"
    assert message["content"][1]["image_url"]["detail"] == "high"
    assert message["content"][1]["image_url"]["url"].startswith("data:image/")
    assert result.text == "A blue card."
    assert result.usage == Usage(5, 3, 8)
    asyncio.run(provider.aclose())


def test_openai_stream_events(mock_settings, blob) -> None:
    provider = OpenAICompatibleProvider(
        base_url="https://api.example/v1", api_key="secret", model="gpt-test"
    )

    async def fake_stream(url: str, payload: dict[str, Any], headers=None):
        assert payload["stream"] is True
        yield {"choices": [{"delta": {"content": "Hello "}}]}
        yield {"choices": [{"delta": {"content": "world"}, "finish_reason": "stop"}]}
        yield {
            "choices": [],
            "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
        }

    provider.http.stream_sse = fake_stream  # type: ignore[method-assign]

    async def run():
        request = _request(blob)
        request.stream = True
        return [chunk async for chunk in provider.stream(request)]

    chunks = asyncio.run(run())
    assert "".join(chunk.delta_text for chunk in chunks) == "Hello world"
    assert any(chunk.finish_reason == "stop" for chunk in chunks)
    assert chunks[-1].usage == Usage(1, 2, 3)
    asyncio.run(provider.aclose())


def test_anthropic_payload_and_response(mock_settings, blob) -> None:
    provider = AnthropicProvider(
        base_url="https://api.anthropic.com/v1", api_key="secret", model="claude-test"
    )
    captured: dict[str, Any] = {}

    async def fake_post(url: str, payload: dict[str, Any], headers=None):
        captured.update(url=url, payload=payload)
        return {
            "content": [{"type": "text", "text": "A card."}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 7, "output_tokens": 2},
        }

    provider.http.post_json = fake_post  # type: ignore[method-assign]
    result = asyncio.run(provider.complete(_request(blob, model="claude-test")))

    assert captured["url"].endswith("/messages")
    payload = captured["payload"]
    assert payload["system"] == "You are a helpful assistant."
    assert payload["model"] == "claude-test"
    assert payload["max_tokens"] == 128
    user = payload["messages"][0]
    assert user["role"] == "user"
    assert user["content"][0]["type"] == "text"
    assert user["content"][1]["type"] == "image"
    assert user["content"][1]["source"]["media_type"] == "image/png"
    assert user["content"][1]["source"]["data"]
    assert result.text == "A card."
    assert result.usage == Usage(7, 2, 9)
    asyncio.run(provider.aclose())


def test_gemini_payload_and_response(mock_settings, blob) -> None:
    provider = GeminiProvider(
        base_url="https://generativelanguage.googleapis.com/v1beta",
        api_key="secret",
        model="gemini-test",
    )
    captured: dict[str, Any] = {}

    async def fake_post(url: str, payload: dict[str, Any], headers=None):
        captured.update(url=url, payload=payload)
        return {
            "candidates": [
                {
                    "content": {"parts": [{"text": "A card."}]},
                    "finishReason": "STOP",
                }
            ],
            "usageMetadata": {
                "promptTokenCount": 4,
                "candidatesTokenCount": 2,
                "totalTokenCount": 6,
            },
        }

    provider.http.post_json = fake_post  # type: ignore[method-assign]
    result = asyncio.run(provider.complete(_request(blob, model="gemini-test")))

    assert "models/gemini-test:generateContent" in captured["url"]
    payload = captured["payload"]
    assert payload["systemInstruction"]["parts"][0]["text"] == "You are a helpful assistant."
    parts = payload["contents"][0]["parts"]
    assert parts[0] == {"text": "Describe this image."}
    assert parts[1]["inlineData"]["mimeType"] == "image/png"
    assert parts[1]["inlineData"]["data"]
    assert payload["generationConfig"]["maxOutputTokens"] == 128
    assert result.text == "A card."
    assert result.usage == Usage(4, 2, 6)
    asyncio.run(provider.aclose())


def test_transport_maps_auth_error() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(401, json={"error": "no"}))
    http = ProviderHTTP(
        base_url="https://api.example/v1",
        api_key="secret",
        timeout_s=5,
        max_retries=0,
    )
    asyncio.run(http._client.aclose())
    http._client = httpx.AsyncClient(transport=transport)

    with pytest.raises(AuthError):
        asyncio.run(http.post_json("https://api.example/v1/chat/completions", {}))
    asyncio.run(http.aclose())


def test_transport_retries_server_errors() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(500, json={"error": "temporary"})
        return httpx.Response(200, json={"ok": True})

    http = ProviderHTTP(
        base_url="https://api.example/v1",
        api_key="secret",
        timeout_s=5,
        max_retries=1,
    )
    asyncio.run(http._client.aclose())
    http._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    result = asyncio.run(http.post_json("https://api.example/v1/chat/completions", {}))

    assert result == {"ok": True}
    assert calls == 2
    asyncio.run(http.aclose())


def test_transport_rate_limit_has_retry_after() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            429, headers={"retry-after": "9"}, json={"error": {"message": "slow down"}}
        )
    )
    http = ProviderHTTP(
        base_url="https://api.example/v1",
        api_key="secret",
        timeout_s=5,
        max_retries=0,
    )
    asyncio.run(http._client.aclose())
    http._client = httpx.AsyncClient(transport=transport)

    with pytest.raises(UpstreamRateLimitError) as info:
        asyncio.run(http.post_json("https://api.example/v1/chat/completions", {}))
    assert info.value.retry_after == "9"
    asyncio.run(http.aclose())


def test_stream_sse_parses_data_lines() -> None:
    body = 'data: {"choices":[{"delta":{"content":"Hi"}}]}\n\n: keep-alive\n\ndata: [DONE]\n\n'
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200, text=body, headers={"content-type": "text/event-stream"}
        )
    )
    http = ProviderHTTP(
        base_url="https://api.example/v1",
        api_key="secret",
        timeout_s=5,
        max_retries=0,
    )
    asyncio.run(http._client.aclose())
    http._client = httpx.AsyncClient(transport=transport)

    async def run():
        return [
            event
            async for event in http.stream_sse(
                "https://api.example/v1/chat/completions", {"stream": True}
            )
        ]

    events = asyncio.run(run())
    assert events == [{"choices": [{"delta": {"content": "Hi"}}]}]
    asyncio.run(http.aclose())
