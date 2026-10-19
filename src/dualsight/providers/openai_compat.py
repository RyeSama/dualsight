"""OpenAI-compatible ``/chat/completions`` adapter.

This is the widest compatibility surface in DualSight: OpenAI itself, Azure
OpenAI-compatible gateways, DeepSeek, Together, Groq, Ollama, vLLM, LM Studio,
OpenRouter, and most self-hosted proxies.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from ..chat import ChatRequest, ChatResult, StreamChunk, Usage, message_to_dict
from ..errors import UpstreamError
from .transport import ProviderHTTP


def content_text(content: Any) -> str:
    """Normalize string and content-part-array responses to plain text."""

    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict):
                text = part.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    if content is None:
        return ""
    return str(content)


class OpenAICompatibleProvider:
    name = "openai"
    supports_tools = True

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str | None,
        model: str,
        timeout_s: float = 60.0,
        max_retries: int = 2,
        headers: dict[str, str] | None = None,
        extra_body: dict[str, Any] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.extra_body = dict(extra_body or {})
        self.http = ProviderHTTP(
            base_url=self.base_url,
            api_key=api_key,
            timeout_s=timeout_s,
            max_retries=max_retries,
            headers={"Content-Type": "application/json", **(headers or {})},
        )

    @property
    def endpoint(self) -> str:
        if self.base_url.endswith("/chat/completions"):
            return self.base_url
        return f"{self.base_url}/chat/completions"

    def _payload(self, request: ChatRequest, *, stream: bool) -> dict[str, Any]:
        core: dict[str, Any] = {
            "model": request.model or self.model,
            "messages": [message_to_dict(message) for message in request.messages],
            "stream": stream,
        }
        if request.temperature is not None:
            core["temperature"] = request.temperature
        if request.max_tokens is not None:
            core["max_tokens"] = request.max_tokens
        if request.tools:
            core["tools"] = request.tools
        if request.tool_choice is not None:
            core["tool_choice"] = request.tool_choice
        return {**self.extra_body, **request.extra, **core}

    async def complete(self, request: ChatRequest) -> ChatResult:
        data = await self.http.post_json(self.endpoint, self._payload(request, stream=False))
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            raise UpstreamError("OpenAI-compatible response contained no choices")
        choice = choices[0] if isinstance(choices[0], dict) else {}
        message = choice.get("message") if isinstance(choice.get("message"), dict) else {}
        tool_calls = message.get("tool_calls")
        if not isinstance(tool_calls, list):
            tool_calls = None
        return ChatResult(
            text=content_text(message.get("content")),
            finish_reason=str(choice.get("finish_reason") or "stop"),
            usage=Usage.from_openai(data.get("usage")),
            tool_calls=tool_calls,
            raw=data,
        )

    async def stream(self, request: ChatRequest) -> AsyncIterator[StreamChunk]:
        async for event in self.http.stream_sse(self.endpoint, self._payload(request, stream=True)):
            choices = event.get("choices")
            usage = Usage.from_openai(event.get("usage"))
            if not isinstance(choices, list) or not choices:
                if usage is not None:
                    yield StreamChunk(usage=usage)
                continue
            choice = choices[0] if isinstance(choices[0], dict) else {}
            delta = choice.get("delta") if isinstance(choice.get("delta"), dict) else {}
            tool_deltas = delta.get("tool_calls")
            if not isinstance(tool_deltas, list):
                tool_deltas = None
            yield StreamChunk(
                delta_text=content_text(delta.get("content")),
                finish_reason=choice.get("finish_reason"),
                usage=usage,
                tool_calls_delta=tool_deltas,
            )

    async def aclose(self) -> None:
        await self.http.aclose()
