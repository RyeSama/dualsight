"""Internal chat and image types shared by providers and the bridge."""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from typing import Any, Literal

Role = Literal["system", "user", "assistant", "tool", "developer"]


@dataclass(frozen=True, slots=True)
class ImageBlob:
    """A normalized, privacy-scrubbed image ready to send to a vision API."""

    data: bytes
    mime_type: str
    width: int
    height: int
    sha256: str
    original_sha256: str
    source: str
    original_width: int
    original_height: int
    original_bytes: int
    notes: tuple[str, ...] = ()

    @property
    def size_bytes(self) -> int:
        return len(self.data)

    def data_uri(self) -> str:
        encoded = base64.b64encode(self.data).decode("ascii")
        return f"data:{self.mime_type};base64,{encoded}"

    def base64_data(self) -> str:
        return base64.b64encode(self.data).decode("ascii")


@dataclass(frozen=True, slots=True)
class TextPart:
    text: str


@dataclass(frozen=True, slots=True)
class ImagePart:
    image: ImageBlob
    detail: str | None = None
    label: str = "image"


ContentPart = TextPart | ImagePart
Content = str | list[ContentPart]


@dataclass(slots=True)
class Message:
    role: Role
    content: Content
    name: str | None = None
    tool_call_id: str | None = None
    tool_calls: list[dict[str, Any]] | None = None

    def has_images(self) -> bool:
        return isinstance(self.content, list) and any(
            isinstance(part, ImagePart) for part in self.content
        )

    def text(self) -> str:
        if isinstance(self.content, str):
            return self.content
        return "\n".join(part.text for part in self.content if isinstance(part, TextPart))


@dataclass(frozen=True, slots=True)
class Usage:
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None

    def to_dict(self) -> dict[str, int]:
        result: dict[str, int] = {}
        if self.prompt_tokens is not None:
            result["prompt_tokens"] = self.prompt_tokens
        if self.completion_tokens is not None:
            result["completion_tokens"] = self.completion_tokens
        if self.total_tokens is not None:
            result["total_tokens"] = self.total_tokens
        return result

    @classmethod
    def from_openai(cls, data: dict[str, Any] | None) -> Usage | None:
        if not data:
            return None
        return cls(
            prompt_tokens=data.get("prompt_tokens"),
            completion_tokens=data.get("completion_tokens"),
            total_tokens=data.get("total_tokens"),
        )


@dataclass(slots=True)
class ChatResult:
    text: str
    finish_reason: str = "stop"
    usage: Usage | None = None
    tool_calls: list[dict[str, Any]] | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class StreamChunk:
    delta_text: str = ""
    finish_reason: str | None = None
    usage: Usage | None = None
    tool_calls_delta: list[dict[str, Any]] | None = None


@dataclass(slots=True)
class ChatRequest:
    model: str
    messages: list[Message]
    temperature: float | None = None
    max_tokens: int | None = None
    stream: bool = False
    tools: list[dict[str, Any]] | None = None
    tool_choice: Any = None
    extra: dict[str, Any] = field(default_factory=dict)


def message_to_dict(message: Message) -> dict[str, Any]:
    """Convert an internal message to an OpenAI-compatible message dict."""

    if isinstance(message.content, str):
        content: Any = message.content
    else:
        parts: list[dict[str, Any]] = []
        for part in message.content:
            if isinstance(part, TextPart):
                parts.append({"type": "text", "text": part.text})
            else:
                parts.append(
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": part.image.data_uri(),
                            "detail": part.detail or "auto",
                        },
                    }
                )
        content = parts
    result: dict[str, Any] = {"role": message.role, "content": content}
    if message.name is not None:
        result["name"] = message.name
    if message.tool_call_id is not None:
        result["tool_call_id"] = message.tool_call_id
    if message.tool_calls is not None:
        result["tool_calls"] = message.tool_calls
    return result
