"""Parse OpenAI-compatible and DualSight-native request bodies."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from .chat import ChatRequest, ImageBlob, ImagePart, Message, TextPart
from .config import DETAIL_LEVELS, PROFILES, Settings
from .errors import InvalidRequestError, PayloadTooLargeError
from .image_intake import ImageIntake

_ROLES = {"system", "developer", "user", "assistant", "tool"}
_CORE_KEYS = {
    "messages",
    "model",
    "stream",
    "temperature",
    "max_tokens",
    "max_completion_tokens",
    "tools",
    "tool_choice",
    "n",
    "dualsight",
}


@dataclass(frozen=True, slots=True)
class RequestOptions:
    profile: str | None = None
    detail: str | None = None
    bypass_cache: bool = False
    include_perception: bool = False


@dataclass(frozen=True, slots=True)
class ParsedChat:
    request: ChatRequest
    options: RequestOptions
    stream: bool


def _text_from_content(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            part.get("text", "")
            for part in content
            if isinstance(part, dict) and isinstance(part.get("text"), str)
        ]
        return "\n".join(parts)
    return str(content)


def _validate_choice(name: str, value: str | None, choices: tuple[str, ...]) -> str | None:
    if value is None:
        return None
    if value not in choices:
        raise InvalidRequestError(
            f"{name} must be one of {', '.join(choices)}; got {value!r}",
            param=name,
        )
    return value


def _strict_bool(value: Any, name: str, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    raise InvalidRequestError(f"{name} must be a boolean", param=name)


def _positive_int(value: Any, name: str, *, default: int | None = None) -> int | None:
    if value is None:
        return default
    if isinstance(value, bool):
        raise InvalidRequestError(f"{name} must be a positive integer", param=name)
    if isinstance(value, float):
        if not value.is_integer():
            raise InvalidRequestError(f"{name} must be a positive integer", param=name)
        value = int(value)
    if isinstance(value, str) and value.isdigit():
        value = int(value)
    if not isinstance(value, int) or value < 1:
        raise InvalidRequestError(f"{name} must be a positive integer", param=name)
    return value


def _temperature(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InvalidRequestError("temperature must be a number", param="temperature")
    number = float(value)
    if not math.isfinite(number) or not 0.0 <= number <= 2.0:
        raise InvalidRequestError("temperature must be between 0 and 2", param="temperature")
    return number


def parse_options(value: Any) -> RequestOptions:
    if value is None:
        return RequestOptions()
    if not isinstance(value, dict):
        raise InvalidRequestError("dualsight must be an object", param="dualsight")
    profile = value.get("profile")
    detail = value.get("detail")
    if profile is not None and not isinstance(profile, str):
        raise InvalidRequestError("dualsight.profile must be a string", param="dualsight.profile")
    if detail is not None and not isinstance(detail, str):
        raise InvalidRequestError("dualsight.detail must be a string", param="dualsight.detail")
    return RequestOptions(
        profile=_validate_choice("dualsight.profile", profile, PROFILES),
        detail=_validate_choice("dualsight.detail", detail, DETAIL_LEVELS),
        bypass_cache=_strict_bool(value.get("bypass_cache"), "dualsight.bypass_cache"),
        include_perception=_strict_bool(
            value.get("include_perception"), "dualsight.include_perception"
        ),
    )


async def _resolve_image_part(
    part: dict[str, Any],
    *,
    intake: ImageIntake,
    label: str,
) -> ImagePart | None:
    part_type = part.get("type")
    if part_type in {"image_url", "input_image"}:
        image_url = part.get("image_url")
        detail: str | None = None
        if isinstance(image_url, dict):
            detail = image_url.get("detail")
            image_url = image_url.get("url")
        if not isinstance(image_url, str) or not image_url.strip():
            raise InvalidRequestError("image_url must contain a non-empty url", param="messages")
        blob: ImageBlob = await intake.from_reference(image_url, source=label)
        return ImagePart(
            image=blob, detail=detail if detail in {"auto", "low", "high"} else None, label=label
        )
    if part_type == "image":
        source = part.get("source")
        if not isinstance(source, dict):
            raise InvalidRequestError("image part must contain a source object", param="messages")
        if source.get("type") == "base64":
            media_type = source.get("media_type") or source.get("mime_type") or "image/png"
            data = source.get("data")
            if not isinstance(data, str) or not data:
                raise InvalidRequestError("base64 image source is empty", param="messages")
            blob = intake.from_data_uri(f"data:{media_type};base64,{data}", source=label)
            return ImagePart(image=blob, label=label)
        if source.get("type") == "url" and isinstance(source.get("url"), str):
            blob = await intake.from_reference(source["url"], source=label)
            return ImagePart(image=blob, label=label)
        raise InvalidRequestError("unsupported image source type", param="messages")
    return None


async def _parse_message(
    raw: Any,
    *,
    intake: ImageIntake,
    image_counter: list[int],
    max_images: int,
) -> Message:
    if not isinstance(raw, dict):
        raise InvalidRequestError("each message must be an object", param="messages")
    role = raw.get("role")
    if role not in _ROLES:
        raise InvalidRequestError(f"unsupported message role: {role!r}", param="messages")
    content = raw.get("content")
    parsed: str | list[TextPart | ImagePart]
    if isinstance(content, list):
        parts: list[TextPart | ImagePart] = []
        for item in content:
            if not isinstance(item, dict):
                raise InvalidRequestError("content parts must be objects", param="messages")
            item_type = item.get("type")
            if item_type == "text" or (item_type is None and "text" in item):
                text = item.get("text")
                if isinstance(text, str) and text:
                    parts.append(TextPart(text))
                continue
            if item_type not in {"image_url", "input_image", "image"}:
                raise InvalidRequestError(
                    f"unsupported content part type: {item_type!r}", param="messages"
                )
            image_counter[0] += 1
            if image_counter[0] > max_images:
                raise PayloadTooLargeError(
                    f"too many images: at most {max_images} are allowed per request"
                )
            image_part = await _resolve_image_part(
                item, intake=intake, label=f"image-{image_counter[0]}"
            )
            if image_part is not None:
                parts.append(image_part)
        if all(isinstance(part, TextPart) for part in parts):
            parsed = "\n".join(part.text for part in parts if isinstance(part, TextPart))
        else:
            parsed = parts
    elif isinstance(content, str):
        parsed = content
    elif content is None:
        parsed = ""
    else:
        raise InvalidRequestError("message content must be a string or an array", param="messages")

    tool_calls = raw.get("tool_calls")
    if tool_calls is not None and not isinstance(tool_calls, list):
        raise InvalidRequestError("tool_calls must be an array", param="messages")
    return Message(
        role=role,
        content=parsed,
        name=raw.get("name") if isinstance(raw.get("name"), str) else None,
        tool_call_id=raw.get("tool_call_id") if isinstance(raw.get("tool_call_id"), str) else None,
        tool_calls=tool_calls,
    )


async def parse_chat_request(
    payload: Any,
    *,
    intake: ImageIntake,
    settings: Settings,
) -> ParsedChat:
    if not isinstance(payload, dict):
        raise InvalidRequestError("request body must be a JSON object")
    raw_messages = payload.get("messages")
    if not isinstance(raw_messages, list) or not raw_messages:
        raise InvalidRequestError("messages must be a non-empty array", param="messages")
    n = _positive_int(payload.get("n"), "n", default=1)
    if n != 1:
        raise InvalidRequestError("only n=1 is supported", param="n")

    counter = [0]
    messages = [
        await _parse_message(
            raw,
            intake=intake,
            image_counter=counter,
            max_images=settings.max_images,
        )
        for raw in raw_messages
    ]
    model = payload.get("model")
    max_tokens = payload.get("max_tokens")
    if max_tokens is None:
        max_tokens = payload.get("max_completion_tokens")
    tools = payload.get("tools")
    if tools is not None and not isinstance(tools, list):
        raise InvalidRequestError("tools must be an array", param="tools")
    extra = {
        key: value for key, value in payload.items() if key not in _CORE_KEYS and value is not None
    }
    request = ChatRequest(
        model=model if isinstance(model, str) else "",
        messages=messages,
        temperature=_temperature(payload.get("temperature")),
        max_tokens=_positive_int(max_tokens, "max_tokens"),
        stream=_strict_bool(payload.get("stream"), "stream"),
        tools=tools,
        tool_choice=payload.get("tool_choice"),
        extra=extra,
    )
    return ParsedChat(
        request=request,
        options=parse_options(payload.get("dualsight")),
        stream=request.stream,
    )


def parse_native_options(payload: dict[str, Any]) -> tuple[str | None, str | None, bool]:
    profile = payload.get("profile")
    detail = payload.get("detail")
    if profile is not None and not isinstance(profile, str):
        raise InvalidRequestError("profile must be a string", param="profile")
    if detail is not None and not isinstance(detail, str):
        raise InvalidRequestError("detail must be a string", param="detail")
    return (
        _validate_choice("profile", profile, PROFILES),
        _validate_choice("detail", detail, DETAIL_LEVELS),
        _strict_bool(payload.get("bypass_cache"), "bypass_cache"),
    )
