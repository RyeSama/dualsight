"""Perception parsing, caching, and failure semantics."""

from __future__ import annotations

import asyncio
import json

import pytest

from dualsight.cache import MemoryCache, NullCache
from dualsight.chat import ChatRequest, ChatResult, Usage
from dualsight.config import Settings
from dualsight.errors import AuthError, VisionError
from dualsight.perception import Perceiver, parse_perception


class CountingProvider:
    name = "fake"
    model = "fake-vision"
    supports_tools = False

    def __init__(self, text: str, *, fail: Exception | None = None) -> None:
        self.text = text
        self.fail = fail
        self.calls = 0
        self.requests: list[ChatRequest] = []

    async def complete(self, request: ChatRequest) -> ChatResult:
        self.calls += 1
        self.requests.append(request)
        if self.fail is not None:
            raise self.fail
        return ChatResult(text=self.text, usage=Usage(11, 7, 18))

    async def stream(self, request):  # pragma: no cover - Protocol completeness
        if False:
            yield request

    async def aclose(self) -> None:
        return None


def test_parse_plain_json() -> None:
    perception = parse_perception('{"summary": "A receipt", "ocr": [{"text": "TOTAL $1"}]}')
    assert perception.summary == "A receipt"
    assert perception.ocr_text == "TOTAL $1"


def test_parse_fenced_and_nested_json() -> None:
    raw = """Here is what I see:

```json
{"perception": {"description": "A settings dialog", "ocr": ["API key is missing"]}}
```
"""
    perception = parse_perception(raw)

    assert perception.summary == "A settings dialog"
    assert perception.ocr_text == "API key is missing"


def test_parse_non_json_falls_back_to_summary() -> None:
    perception = parse_perception("The image shows a blue card with white text.")

    assert perception.summary == "The image shows a blue card with white text."
    assert any("did not return structured JSON" in item for item in perception.uncertainties)


def test_parse_empty_text_is_empty() -> None:
    assert parse_perception("").is_empty is True


def test_perceive_caches_and_exposes_usage(mock_settings: Settings, blob) -> None:
    provider = CountingProvider(json.dumps({"summary": "A card", "colors": ["navy"]}))
    perceiver = Perceiver(
        settings=mock_settings,
        provider=provider,
        cache=MemoryCache(ttl_s=3600, max_entries=8),
    )

    first = asyncio.run(perceiver.perceive(blob, profile="photo", detail="standard"))
    second = asyncio.run(perceiver.perceive(blob, profile="photo", detail="standard"))

    assert first.cached is False
    assert second.cached is True
    assert second.raw_text == ""
    assert second.perception.summary == "A card"
    assert provider.calls == 1
    assert first.usage.total_tokens == 18
    assert first.cache_key


def test_bypass_cache_forces_a_new_call(mock_settings: Settings, blob) -> None:
    provider = CountingProvider('{"summary": "A card"}')
    perceiver = Perceiver(
        settings=mock_settings, provider=provider, cache=MemoryCache(ttl_s=3600, max_entries=8)
    )

    asyncio.run(perceiver.perceive(blob, profile="photo"))
    asyncio.run(perceiver.perceive(blob, profile="photo", bypass_cache=True))

    assert provider.calls == 2


def test_prompt_contains_profile_and_brief_uses_low_detail(mock_settings: Settings, blob) -> None:
    provider = CountingProvider('{"summary": "A card"}')
    perceiver = Perceiver(settings=mock_settings, provider=provider, cache=NullCache())

    asyncio.run(perceiver.perceive(blob, profile="receipt", detail="brief"))

    request = provider.requests[0]
    user_message = request.messages[-1]
    assert "Perception profile: receipt." in user_message.text()
    assert "Level of detail: brief." in user_message.text()
    image_part = user_message.content[1]  # type: ignore[index]
    assert image_part.detail == "low"


def test_empty_model_output_raises_vision_error(mock_settings: Settings, blob) -> None:
    provider = CountingProvider("   ")
    perceiver = Perceiver(settings=mock_settings, provider=provider, cache=NullCache())

    with pytest.raises(VisionError, match="empty"):
        asyncio.run(perceiver.perceive(blob, profile="photo"))


def test_auth_failure_is_not_masked(mock_settings: Settings, blob) -> None:
    provider = CountingProvider("", fail=AuthError("bad key"))
    perceiver = Perceiver(settings=mock_settings, provider=provider, cache=NullCache())

    with pytest.raises(AuthError):
        asyncio.run(perceiver.perceive(blob, profile="photo"))


def test_unexpected_provider_error_is_wrapped(mock_settings: Settings, blob) -> None:
    provider = CountingProvider("", fail=RuntimeError("secret internals"))
    perceiver = Perceiver(settings=mock_settings, provider=provider, cache=NullCache())

    with pytest.raises(VisionError, match="RuntimeError") as info:
        asyncio.run(perceiver.perceive(blob, profile="photo"))
    assert "secret internals" not in str(info.value)


def test_outcome_exposes_usage_and_timing(mock_settings: Settings, blob) -> None:
    provider = CountingProvider('{"summary": "A card"}')
    perceiver = Perceiver(settings=mock_settings, provider=provider, cache=NullCache())
    outcome = asyncio.run(perceiver.perceive(blob, profile="photo"))

    assert outcome.usage.prompt_tokens == 11
    assert outcome.latency_ms >= 0
    assert outcome.cache_key
    assert outcome.raw_text.startswith("{")
