"""Anthropic Messages API adapter."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from ..chat import (
    ChatRequest,
    ChatResult,
    ImagePart,
    Message,
    StreamChunk,
    TextPart,
    Usage,
)
from ..errors import UpstreamError
from .transport import ProviderHTTP

_STOP_REASONS = {
    "end_turn": "stop",
    "stop_sequence": "stop",
    "max_tokens": "length",
    "tool_use": "tool_calls",
}


def _content_blocks(message: Message) -> list[dict[str, Any]]:
    if isinstance(message.content, str):
        blocks: list[dict[str, Any]] = [{"type": "text", "text": message.content}]
    else:
        blocks = []
        for part in message.content:
            if isinstance(part, TextPart):
                blocks.append({"type": "text", "text": part.text})
            elif isinstance(part, ImagePart):
                blocks.append(
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": part.image.mime_type,
                            "data": part.image.base64_data(),
                        },
                    }
                )
    if message.role == "assistant" and message.tool_calls:
        for index, call in enumerate(message.tool_calls):
            function = call.get("function") if isinstance(call, dict) else None
            if not isinstance(function, dict):
                continue
            arguments = function.get("arguments") or "{}"
            if isinstance(arguments, str):
                try:
                    parsed = json.loads(arguments)
                except json.JSONDecodeError:
                    parsed = {"raw": arguments}
            else:
                parsed = arguments
            blocks.append(
                {
                    "type": "tool_use",
                    "id": call.get("id") or f"tool_{index}",
                    "name": function.get("name") or "tool",
                    "input": parsed,
                }
            )
    return blocks


def _convert_messages(messages: list[Message]) -> tuple[str, list[dict[str, Any]]]:
    system_parts: list[str] = []
    converted: list[dict[str, Any]] = []

    def append(role: str, blocks: list[dict[str, Any]]) -> None:
        if not blocks:
            return
        if converted and converted[-1]["role"] == role:
            converted[-1]["content"].extend(blocks)
        else:
            converted.append({"role": role, "content": blocks})

    for message in messages:
        if message.role in ("system", "developer"):
            text = message.text().strip()
            if text:
                system_parts.append(text)
            continue
        if message.role == "tool":
            append(
                "user",
                [
                    {
                        "type": "tool_result",
                        "tool_use_id": message.tool_call_id or "unknown",
                        "content": message.text(),
                    }
                ],
            )
            continue
        role = "assistant" if message.role == "assistant" else "user"
        append(role, _content_blocks(message))
    return "\n\n".join(system_parts), converted


def _convert_tools(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
    if not tools:
        return None
    result: list[dict[str, Any]] = []
    for tool in tools:
        if not isinstance(tool, dict) or tool.get("type") != "function":
            continue
        function = tool.get("function") or {}
        if not isinstance(function, dict) or not function.get("name"):
            continue
        result.append(
            {
                "name": function["name"],
                "description": function.get("description") or "",
                "input_schema": function.get("parameters") or {"type": "object", "properties": {}},
            }
        )
    return result or None


def _convert_tool_choice(choice: Any) -> dict[str, Any] | None:
    if choice is None:
        return None
    if isinstance(choice, str):
        return {"type": {"required": "any", "none": "none"}.get(choice, choice)}
    if isinstance(choice, dict):
        if choice.get("type") == "function":
            function = choice.get("function") or {}
            return {"type": "tool", "name": function.get("name") or ""}
        if choice.get("type") in {"auto", "any", "tool", "none"}:
            return choice
    return None


class AnthropicProvider:
    name = "anthropic"
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
        request_headers: dict[str, str] = {
            "Content-Type": "application/json",
            "anthropic-version": "2023-06-01",
        }
        if api_key:
            request_headers["x-api-key"] = api_key
        request_headers.update(headers or {})
        self.http = ProviderHTTP(
            base_url=self.base_url,
            api_key=None,
            timeout_s=timeout_s,
            max_retries=max_retries,
            headers=request_headers,
        )

    @property
    def endpoint(self) -> str:
        if self.base_url.endswith("/messages"):
            return self.base_url
        return f"{self.base_url}/messages"

    def _payload(self, request: ChatRequest, *, stream: bool) -> dict[str, Any]:
        system, messages = _convert_messages(request.messages)
        core: dict[str, Any] = {
            "model": request.model or self.model,
            "messages": messages,
            "max_tokens": request.max_tokens or 1024,
            "stream": stream,
        }
        if system:
            core["system"] = system
        if request.temperature is not None:
            core["temperature"] = min(1.0, max(0.0, request.temperature))
        tools = _convert_tools(request.tools)
        if tools:
            core["tools"] = tools
            choice = _convert_tool_choice(request.tool_choice)
            if choice:
                core["tool_choice"] = choice
        return {**self.extra_body, **request.extra, **core}

    async def complete(self, request: ChatRequest) -> ChatResult:
        data = await self.http.post_json(self.endpoint, self._payload(request, stream=False))
        blocks = data.get("content")
        if not isinstance(blocks, list):
            raise UpstreamError("Anthropic response contained no content")
        texts: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        for block in blocks:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text" and isinstance(block.get("text"), str):
                texts.append(block["text"])
            elif block.get("type") == "tool_use":
                tool_calls.append(
                    {
                        "id": block.get("id"),
                        "type": "function",
                        "function": {
                            "name": block.get("name"),
                            "arguments": json.dumps(block.get("input") or {}),
                        },
                    }
                )
        usage_data = data.get("usage") or {}
        prompt_tokens = usage_data.get("input_tokens")
        completion_tokens = usage_data.get("output_tokens")
        total = None
        if isinstance(prompt_tokens, int) and isinstance(completion_tokens, int):
            total = prompt_tokens + completion_tokens
        return ChatResult(
            text="".join(texts),
            finish_reason=_STOP_REASONS.get(str(data.get("stop_reason")), "stop"),
            usage=Usage(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total,
            ),
            tool_calls=tool_calls or None,
            raw=data,
        )

    async def stream(self, request: ChatRequest) -> AsyncIterator[StreamChunk]:
        prompt_tokens: int | None = None
        completion_tokens: int | None = None
        finish_reason: str | None = None
        async for event in self.http.stream_sse(self.endpoint, self._payload(request, stream=True)):
            event_type = event.get("type")
            if event_type == "message_start":
                usage = (event.get("message") or {}).get("usage") or {}
                if isinstance(usage.get("input_tokens"), int):
                    prompt_tokens = usage["input_tokens"]
            elif event_type == "content_block_start":
                block = event.get("content_block") or {}
                index = event.get("index", 0)
                if block.get("type") == "tool_use":
                    yield StreamChunk(
                        tool_calls_delta=[
                            {
                                "index": index,
                                "id": block.get("id"),
                                "type": "function",
                                "function": {"name": block.get("name"), "arguments": ""},
                            }
                        ]
                    )
            elif event_type == "content_block_delta":
                delta = event.get("delta") or {}
                index = event.get("index", 0)
                if delta.get("type") == "text_delta" and delta.get("text"):
                    yield StreamChunk(delta_text=str(delta["text"]))
                elif delta.get("type") == "input_json_delta":
                    yield StreamChunk(
                        tool_calls_delta=[
                            {
                                "index": index,
                                "function": {"arguments": delta.get("partial_json") or ""},
                            }
                        ]
                    )
            elif event_type == "message_delta":
                delta = event.get("delta") or {}
                if delta.get("stop_reason"):
                    finish_reason = _STOP_REASONS.get(str(delta["stop_reason"]), "stop")
                usage = event.get("usage") or {}
                if isinstance(usage.get("output_tokens"), int):
                    completion_tokens = usage["output_tokens"]
            elif event_type == "error":
                error = event.get("error") or {}
                raise UpstreamError(
                    f"Anthropic stream error: {error.get('message') or 'unknown error'}"
                )

        total = None
        if prompt_tokens is not None and completion_tokens is not None:
            total = prompt_tokens + completion_tokens
        yield StreamChunk(
            finish_reason=finish_reason or "stop",
            usage=Usage(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total,
            ),
        )

    async def aclose(self) -> None:
        await self.http.aclose()
