"""Google Gemini ``generateContent`` adapter."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import quote

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

_FINISH_REASONS = {
    "STOP": "stop",
    "MAX_TOKENS": "length",
    "SAFETY": "content_filter",
    "RECITATION": "content_filter",
    "BLOCKLIST": "content_filter",
    "PROHIBITED_CONTENT": "content_filter",
    "SPII": "content_filter",
    "OTHER": "stop",
}


def _parts(message: Message) -> list[dict[str, Any]]:
    if isinstance(message.content, str):
        parts: list[dict[str, Any]] = [{"text": message.content}]
    else:
        parts = []
        for part in message.content:
            if isinstance(part, TextPart):
                parts.append({"text": part.text})
            elif isinstance(part, ImagePart):
                parts.append(
                    {
                        "inlineData": {
                            "mimeType": part.image.mime_type,
                            "data": part.image.base64_data(),
                        }
                    }
                )
    if message.role == "assistant" and message.tool_calls:
        for call in message.tool_calls:
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
            parts.append({"functionCall": {"name": function.get("name"), "args": parsed}})
    return [part for part in parts if part.get("text") != ""]


def _convert_messages(messages: list[Message]) -> tuple[str, list[dict[str, Any]]]:
    system_parts: list[str] = []
    contents: list[dict[str, Any]] = []
    for message in messages:
        if message.role in ("system", "developer"):
            text = message.text().strip()
            if text:
                system_parts.append(text)
            continue
        if message.role == "tool":
            text = f"Tool result ({message.tool_call_id or 'unknown'}): {message.text()}"
            role = "user"
            parts = [{"text": text}]
        else:
            role = "model" if message.role == "assistant" else "user"
            parts = _parts(message)
        if not parts:
            continue
        if contents and contents[-1]["role"] == role:
            contents[-1]["parts"].extend(parts)
        else:
            contents.append({"role": role, "parts": parts})
    return "\n\n".join(system_parts), contents


def _convert_tools(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
    if not tools:
        return None
    declarations: list[dict[str, Any]] = []
    for tool in tools:
        if not isinstance(tool, dict) or tool.get("type") != "function":
            continue
        function = tool.get("function") or {}
        if not isinstance(function, dict) or not function.get("name"):
            continue
        declaration: dict[str, Any] = {
            "name": function["name"],
            "description": function.get("description") or "",
        }
        if function.get("parameters"):
            declaration["parameters"] = function["parameters"]
        declarations.append(declaration)
    if not declarations:
        return None
    return [{"functionDeclarations": declarations}]


def _convert_tool_config(choice: Any) -> dict[str, Any] | None:
    if choice is None:
        return None
    if isinstance(choice, str):
        mode = {"auto": "AUTO", "required": "ANY", "none": "NONE"}.get(choice)
        return {"functionCallingConfig": {"mode": mode}} if mode else None
    if isinstance(choice, dict):
        if choice.get("type") == "function":
            function = choice.get("function") or {}
            name = function.get("name")
            if name:
                return {
                    "functionCallingConfig": {
                        "mode": "ANY",
                        "allowedFunctionNames": [name],
                    }
                }
        if choice.get("type") in {"auto", "any", "none"}:
            return {
                "functionCallingConfig": {
                    "mode": {"auto": "AUTO", "any": "ANY", "none": "NONE"}[choice["type"]]
                }
            }
    return None


class GeminiProvider:
    name = "gemini"
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
        request_headers: dict[str, str] = {"Content-Type": "application/json"}
        if api_key:
            request_headers["x-goog-api-key"] = api_key
        request_headers.update(headers or {})
        self.http = ProviderHTTP(
            base_url=self.base_url,
            api_key=None,
            timeout_s=timeout_s,
            max_retries=max_retries,
            headers=request_headers,
        )

    def _model_path(self, model: str) -> str:
        clean = model[7:] if model.startswith("models/") else model
        return quote(clean, safe="")

    def endpoint(self, *, stream: bool) -> str:
        suffix = "streamGenerateContent?alt=sse" if stream else "generateContent"
        return f"{self.base_url}/models/{self._model_path(self.model)}:{suffix}"

    def _payload(self, request: ChatRequest) -> dict[str, Any]:
        system, contents = _convert_messages(request.messages)
        core: dict[str, Any] = {"contents": contents}
        if system:
            core["systemInstruction"] = {"parts": [{"text": system}]}
        generation: dict[str, Any] = {}
        if request.temperature is not None:
            generation["temperature"] = request.temperature
        if request.max_tokens is not None:
            generation["maxOutputTokens"] = request.max_tokens
        if generation:
            core["generationConfig"] = generation
        tools = _convert_tools(request.tools)
        if tools:
            core["tools"] = tools
            config = _convert_tool_config(request.tool_choice)
            if config:
                core["toolConfig"] = config
        return {**self.extra_body, **request.extra, **core}

    @staticmethod
    def _parse_candidate(data: dict[str, Any]) -> tuple[str, list[dict[str, Any]], str]:
        candidates = data.get("candidates")
        if not isinstance(candidates, list) or not candidates:
            feedback = data.get("promptFeedback") or {}
            blocked = feedback.get("blockReason")
            if blocked:
                raise UpstreamError(f"Gemini blocked the prompt: {blocked}", status_code=400)
            raise UpstreamError("Gemini response contained no candidates")
        candidate = candidates[0] if isinstance(candidates[0], dict) else {}
        content = candidate.get("content") or {}
        parts = content.get("parts") if isinstance(content, dict) else None
        texts: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        if isinstance(parts, list):
            for index, part in enumerate(parts):
                if not isinstance(part, dict):
                    continue
                if isinstance(part.get("text"), str):
                    texts.append(part["text"])
                call = part.get("functionCall")
                if isinstance(call, dict):
                    tool_calls.append(
                        {
                            "id": f"call_{index}",
                            "type": "function",
                            "function": {
                                "name": call.get("name"),
                                "arguments": json.dumps(call.get("args") or {}),
                            },
                        }
                    )
        finish = _FINISH_REASONS.get(str(candidate.get("finishReason")), "stop")
        return "".join(texts), tool_calls, finish

    @staticmethod
    def _usage(data: dict[str, Any]) -> Usage | None:
        usage = data.get("usageMetadata")
        if not isinstance(usage, dict):
            return None
        return Usage(
            prompt_tokens=usage.get("promptTokenCount"),
            completion_tokens=usage.get("candidatesTokenCount"),
            total_tokens=usage.get("totalTokenCount"),
        )

    async def complete(self, request: ChatRequest) -> ChatResult:
        data = await self.http.post_json(self.endpoint(stream=False), self._payload(request))
        text, tool_calls, finish = self._parse_candidate(data)
        return ChatResult(
            text=text,
            finish_reason=finish,
            usage=self._usage(data),
            tool_calls=tool_calls or None,
            raw=data,
        )

    async def stream(self, request: ChatRequest) -> AsyncIterator[StreamChunk]:
        finish: str | None = None
        usage: Usage | None = None
        call_index = 0
        async for event in self.http.stream_sse(self.endpoint(stream=True), self._payload(request)):
            text, tool_calls, event_finish = self._parse_candidate(event)
            if event_finish:
                finish = event_finish
            if text:
                yield StreamChunk(delta_text=text)
            for call in tool_calls:
                yield StreamChunk(
                    tool_calls_delta=[
                        {
                            "index": call_index,
                            "id": call["id"],
                            "type": "function",
                            "function": call["function"],
                        }
                    ]
                )
                call_index += 1
            event_usage = self._usage(event)
            if event_usage is not None:
                usage = event_usage
        yield StreamChunk(finish_reason=finish or "stop", usage=usage)

    async def aclose(self) -> None:
        await self.http.aclose()
