"""Bridge orchestration: perception, injection, stripping, streaming, degradation."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace

import pytest

from dualsight.bridge import Bridge
from dualsight.cache import NullCache
from dualsight.chat import ChatRequest, ChatResult, ImagePart, Message, TextPart
from dualsight.config import Settings
from dualsight.errors import UpstreamError
from dualsight.providers.mock import MockProvider
from dualsight.wire import RequestOptions


def _bridge(settings: Settings, vision=None, text=None) -> Bridge:
    return Bridge(
        settings,
        vision or MockProvider(model="mock-vision"),
        text or MockProvider(model="mock-reasoner"),
        NullCache(),
    )


def _request(blob, question: str = "What is the total?") -> ChatRequest:
    return ChatRequest(
        model="dualsight",
        messages=[
            Message(
                role="user",
                content=[TextPart(question), ImagePart(blob, label="image-1")],
            )
        ],
    )


def test_prepare_injects_evidence_and_strips_images(mock_settings: Settings, blob) -> None:
    bridge = _bridge(mock_settings)
    prepared = asyncio.run(
        bridge.prepare(
            _request(blob),
            options=RequestOptions(profile="receipt", detail="standard"),
        )
    )

    assert prepared.image_count == 1
    assert prepared.degraded is False
    assert len(prepared.traces) == 1
    assert prepared.traces[0].profile == "receipt"
    assert prepared.traces[0].perception is not None
    assert "23.47" in json.dumps(prepared.traces[0].perception.model_dump(), ensure_ascii=False)
    assert all(not message.has_images() for message in prepared.messages)
    combined = "\n".join(message.text() for message in prepared.messages)
    assert "VISUAL_EVIDENCE" in combined
    assert 'image="image-1"' in combined
    assert "TOTAL" in combined
    assert "23.47" in combined
    assert prepared.nonce in combined


def test_chat_answers_from_evidence(mock_settings: Settings, blob) -> None:
    bridge = _bridge(mock_settings)
    result = asyncio.run(
        bridge.chat(
            _request(blob),
            options=RequestOptions(profile="receipt", detail="standard"),
        )
    )

    assert result.prepared.image_count == 1
    assert "23.47" in result.text
    assert result.finish_reason == "stop"
    assert result.usage is not None


def test_stream_first_chunk_carries_prepared_request(mock_settings: Settings, blob) -> None:
    bridge = _bridge(mock_settings)

    async def collect():
        chunks = []
        async for chunk in bridge.stream(
            _request(blob),
            options=RequestOptions(profile="receipt", detail="standard"),
        ):
            chunks.append(chunk)
        return chunks

    chunks = asyncio.run(collect())

    assert chunks[0].prepared is not None
    assert chunks[0].prepared.image_count == 1
    assert chunks[-1].finish_reason == "stop"
    text = "".join(chunk.delta_text for chunk in chunks)
    assert "23.47" in text


def test_duplicate_images_are_perceived_once(mock_settings: Settings, blob) -> None:
    bridge = _bridge(mock_settings)
    request = ChatRequest(
        model="dualsight",
        messages=[
            Message(role="user", content=[TextPart("Compare"), ImagePart(blob), ImagePart(blob)])
        ],
    )

    prepared = asyncio.run(
        bridge.prepare(request, options=RequestOptions(profile="receipt", detail="standard"))
    )

    assert prepared.image_count == 2
    assert len(prepared.traces) == 1
    assert prepared.traces[0].perception is not None


def test_text_only_request_passes_through(mock_settings: Settings) -> None:
    bridge = _bridge(mock_settings)
    request = ChatRequest(model="dualsight", messages=[Message(role="user", content="Say hello.")])

    prepared = asyncio.run(bridge.prepare(request))
    result = asyncio.run(bridge.chat(request, prepared=prepared))

    assert prepared.image_count == 0
    assert prepared.degraded is False
    assert isinstance(result.text, str) and result.text


class FailingVision:
    name = "failing"
    model = "failing-vision"
    supports_tools = False

    async def complete(self, request: ChatRequest) -> ChatResult:
        raise UpstreamError("vision backend exploded")

    async def stream(self, request):  # pragma: no cover
        if False:
            yield request

    async def aclose(self) -> None:
        return None


def test_degrade_mode_marks_evidence_unavailable(mock_settings: Settings, blob) -> None:
    settings = replace(mock_settings, on_vision_error="degrade")
    bridge = _bridge(settings, vision=FailingVision())

    prepared = asyncio.run(
        bridge.prepare(_request(blob), options=RequestOptions(profile="receipt"))
    )

    assert prepared.degraded is True
    assert prepared.traces[0].error
    combined = "\n".join(message.text() for message in prepared.messages)
    assert 'status="unavailable"' in combined
    assert "vision backend exploded" in combined


def test_fail_mode_raises(mock_settings: Settings, blob) -> None:
    settings = replace(mock_settings, on_vision_error="fail")
    bridge = _bridge(settings, vision=FailingVision())

    with pytest.raises(UpstreamError, match="exploded"):
        asyncio.run(bridge.prepare(_request(blob), options=RequestOptions(profile="receipt")))


def test_client_extra_is_forwarded_only_to_openai(mock_settings: Settings, blob) -> None:
    bridge = _bridge(mock_settings)
    request = _request(blob)
    request.extra = {"seed": 7}
    prepared = asyncio.run(
        bridge.prepare(request, options=RequestOptions(profile="receipt", detail="standard"))
    )

    text_request = bridge._text_request(request, prepared)
    assert text_request.extra == {}

    bridge.text_provider = type(
        "OpenAILike", (), {"name": "openai", "model": "gpt", "supports_tools": True}
    )()
    text_request = bridge._text_request(request, prepared)
    assert text_request.extra == {"seed": 7}

    bridge.text_provider = type(
        "AnthropicLike", (), {"name": "anthropic", "model": "claude", "supports_tools": True}
    )()
    text_request = bridge._text_request(request, prepared)
    assert text_request.extra == {}


def test_ask_vision_returns_provider_answer(mock_settings: Settings, blob) -> None:
    bridge = _bridge(mock_settings)
    answer = asyncio.run(bridge.ask_vision(blob, "What is visible?", profile="receipt"))

    assert answer.text
    assert answer.provider == "mock"
    assert answer.model == "mock-vision"
    assert answer.usage is not None


def test_prepare_meta_is_json_serializable(mock_settings: Settings, blob) -> None:
    bridge = _bridge(mock_settings)
    prepared = asyncio.run(
        bridge.prepare(_request(blob), options=RequestOptions(profile="receipt", detail="standard"))
    )

    payload = prepared.meta()
    json.dumps(payload)
    assert payload["image_count"] == 1
    assert payload["images"][0]["profile"] == "receipt"


def test_perceive_returns_evidence_and_trace(mock_settings: Settings, blob) -> None:
    bridge = _bridge(mock_settings)
    result = asyncio.run(bridge.perceive(blob, profile="chart", detail="brief"))

    assert "VISUAL_EVIDENCE" in result.evidence
    assert result.trace.profile == "chart"
    assert result.trace.perception is not None
