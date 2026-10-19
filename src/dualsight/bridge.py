"""Orchestration: vision evidence -> prompt injection -> text model."""

from __future__ import annotations

import asyncio
import secrets
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, replace
from typing import Any

from .cache import PerceptionCache
from .chat import (
    ChatRequest,
    ChatResult,
    ImageBlob,
    ImagePart,
    Message,
    StreamChunk,
    TextPart,
    Usage,
)
from .config import Settings
from .errors import (
    AuthError,
    DualSightError,
    ForbiddenError,
    TextModelError,
    UpstreamRateLimitError,
    UpstreamTimeoutError,
    VisionError,
)
from .image_intake import ImageIntake
from .models import Perception
from .perception import Perceiver, PerceptionOutcome
from .prompts import (
    VISION_ANSWER_SYSTEM_PROMPT,
    build_bridge_note,
    build_vision_question_prompt,
)
from .providers.base import ChatProvider
from .render import render_evidence, render_unavailable_evidence
from .wire import RequestOptions


@dataclass(frozen=True, slots=True)
class ImageTrace:
    image_id: str
    label: str
    source: str
    mime_type: str
    width: int
    height: int
    provider: str
    model: str
    profile: str
    detail: str
    cached: bool = False
    latency_ms: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    truncated: bool = False
    error: str | None = None
    perception: Perception | None = None
    notes: tuple[str, ...] = ()

    def public(self, *, include_perception: bool = False) -> dict[str, Any]:
        data: dict[str, Any] = {
            "id": self.image_id,
            "label": self.label,
            "source": self.source,
            "mime_type": self.mime_type,
            "width": self.width,
            "height": self.height,
            "provider": self.provider,
            "model": self.model,
            "profile": self.profile,
            "detail": self.detail,
            "cached": self.cached,
            "latency_ms": self.latency_ms,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "truncated": self.truncated,
            "error": self.error,
            "notes": list(self.notes),
        }
        if include_perception and self.perception is not None:
            data["perception"] = self.perception.to_dict()
        return data


@dataclass(frozen=True, slots=True)
class PreparedRequest:
    messages: list[Message]
    traces: list[ImageTrace]
    nonce: str
    image_count: int
    degraded: bool
    vision_usage: Usage | None = None

    def public_traces(self, *, include_perception: bool = False) -> list[dict[str, Any]]:
        return [trace.public(include_perception=include_perception) for trace in self.traces]

    def meta(self, *, include_perception: bool = False) -> dict[str, Any]:
        return {
            "version": "0.1",
            "nonce": self.nonce,
            "image_count": self.image_count,
            "degraded": self.degraded,
            "images": self.public_traces(include_perception=include_perception),
            "vision_usage": self.vision_usage.to_dict() if self.vision_usage else None,
        }


@dataclass(frozen=True, slots=True)
class BridgeResult:
    text: str
    finish_reason: str | None
    usage: Usage | None
    tool_calls: list[dict[str, Any]] | None
    prepared: PreparedRequest
    raw: dict[str, Any]


@dataclass(frozen=True, slots=True)
class BridgeChunk:
    delta_text: str = ""
    finish_reason: str | None = None
    usage: Usage | None = None
    tool_calls_delta: list[dict[str, Any]] | None = None
    prepared: PreparedRequest | None = None


@dataclass(frozen=True, slots=True)
class VisionAnswer:
    text: str
    finish_reason: str | None
    usage: Usage | None
    provider: str
    model: str
    profile: str
    detail: str


@dataclass(frozen=True, slots=True)
class PerceptionResult:
    trace: ImageTrace
    evidence: str
    raw_text: str


def _sum_usage(items: Sequence[Usage]) -> Usage | None:
    if not items:
        return None

    def total(attr: str) -> int | None:
        values = [getattr(item, attr) for item in items if getattr(item, attr) is not None]
        return sum(values) if values else None  # type: ignore[arg-type]

    return Usage(
        prompt_tokens=total("prompt_tokens"),
        completion_tokens=total("completion_tokens"),
        total_tokens=total("total_tokens"),
    )


def _collect_images(messages: Sequence[Message]) -> list[ImagePart]:
    images: list[ImagePart] = []
    seen: set[str] = set()
    for message in messages:
        if not isinstance(message.content, list):
            continue
        for part in message.content:
            if not isinstance(part, ImagePart):
                continue
            label = part.label
            if label in seen:
                label = f"image-{len(images) + 1}"
            seen.add(label)
            images.append(ImagePart(image=part.image, detail=part.detail, label=label))
    return images


class Bridge:
    def __init__(
        self,
        settings: Settings,
        vision_provider: ChatProvider,
        text_provider: ChatProvider,
        cache: PerceptionCache,
        *,
        intake: ImageIntake | None = None,
    ) -> None:
        self.settings = settings
        self.vision_provider = vision_provider
        self.text_provider = text_provider
        self.cache = cache
        self.intake = intake
        self.perceiver = Perceiver(
            settings=settings,
            provider=vision_provider,
            cache=cache,
        )

    async def prepare(
        self,
        request: ChatRequest,
        *,
        options: RequestOptions | None = None,
        profile: str | None = None,
        detail: str | None = None,
    ) -> PreparedRequest:
        options = options or RequestOptions()
        profile = profile or options.profile or self.settings.profile
        detail = detail or options.detail or self.settings.detail
        nonce = secrets.token_hex(8)
        images = _collect_images(request.messages)
        if not images:
            return PreparedRequest(
                messages=list(request.messages),
                traces=[],
                nonce=nonce,
                image_count=0,
                degraded=False,
            )

        unique: dict[str, list[tuple[int, ImagePart]]] = {}
        for index, image in enumerate(images):
            unique.setdefault(image.image.sha256, []).append((index, image))

        semaphore = asyncio.Semaphore(max(1, self.settings.perception_concurrency))
        bypass = options.bypass_cache

        async def run(image: ImageBlob) -> PerceptionOutcome | DualSightError:
            async with semaphore:
                try:
                    return await self.perceiver.perceive(
                        image,
                        profile=profile,
                        detail=detail,
                        bypass_cache=bypass,
                    )
                except DualSightError as exc:
                    return exc

        keys = list(unique.keys())
        results = await asyncio.gather(*(run(unique[key][0][1].image) for key in keys))
        outcomes: dict[str, PerceptionOutcome | DualSightError] = dict(
            zip(keys, results, strict=False)
        )

        hard_failure = next(
            (result for result in results if isinstance(result, DualSightError)), None
        )
        if hard_failure is not None and self.settings.on_vision_error == "fail":
            raise hard_failure

        budget = max(
            600,
            self.settings.max_evidence_chars // max(1, len(images)),
        )
        rendered: dict[str, tuple[str, bool]] = {}
        traces: list[ImageTrace] = []
        usages: list[Usage] = []
        degraded = False

        for key in keys:
            outcome = outcomes[key]
            image = unique[key][0][1]
            if isinstance(outcome, DualSightError):
                degraded = True
                failure = render_unavailable_evidence(
                    label=image.label,
                    nonce=nonce,
                    max_chars=budget,
                    error=str(outcome),
                )
                text, truncated = failure.text, failure.truncated
                rendered[key] = (text, truncated)
                traces.append(self._failed_trace(image, outcome, profile=profile, detail=detail))
                continue
            evidence = render_evidence(
                outcome.perception,
                label=image.label,
                nonce=nonce,
                max_chars=budget,
                source=image.image.source,
            )
            text, truncated = evidence.text, evidence.truncated
            rendered[key] = (text, truncated)
            if outcome.usage is not None:
                usages.append(outcome.usage)
            traces.append(
                self._trace(
                    image,
                    outcome,
                    profile=profile,
                    detail=detail,
                    truncated=truncated,
                )
            )

        blocks = [rendered[image.image.sha256][0] for image in images]
        note = build_bridge_note(image_count=len(images), nonce=nonce, degraded=degraded)
        evidence_text = note + "\n\n" + "\n\n".join(blocks)
        injected = self._inject(list(request.messages), evidence_text, nonce=nonce)
        cleaned = self._strip_images(injected)
        return PreparedRequest(
            messages=cleaned,
            traces=traces,
            nonce=nonce,
            image_count=len(images),
            degraded=degraded,
            vision_usage=_sum_usage(usages),
        )

    async def chat(
        self,
        request: ChatRequest,
        *,
        options: RequestOptions | None = None,
        profile: str | None = None,
        detail: str | None = None,
        prepared: PreparedRequest | None = None,
    ) -> BridgeResult:
        prepared = prepared or await self.prepare(
            request, options=options, profile=profile, detail=detail
        )
        text_request = self._text_request(request, prepared)
        try:
            result: ChatResult = await self.text_provider.complete(text_request)
        except DualSightError as exc:
            if isinstance(
                exc,
                (AuthError, ForbiddenError, UpstreamRateLimitError, UpstreamTimeoutError),
            ):
                raise
            raise TextModelError(f"text model call failed: {exc}") from exc
        return BridgeResult(
            text=result.text,
            finish_reason=result.finish_reason,
            usage=result.usage,
            tool_calls=result.tool_calls,
            prepared=prepared,
            raw=result.raw,
        )

    async def stream(
        self,
        request: ChatRequest,
        *,
        options: RequestOptions | None = None,
        profile: str | None = None,
        detail: str | None = None,
        prepared: PreparedRequest | None = None,
    ) -> AsyncIterator[BridgeChunk]:
        prepared = prepared or await self.prepare(
            request, options=options, profile=profile, detail=detail
        )
        yield BridgeChunk(prepared=prepared)
        text_request = self._text_request(request, prepared)
        try:
            async for chunk in self.text_provider.stream(text_request):
                assert isinstance(chunk, StreamChunk)
                yield BridgeChunk(
                    delta_text=chunk.delta_text,
                    finish_reason=chunk.finish_reason,
                    usage=chunk.usage,
                    tool_calls_delta=chunk.tool_calls_delta,
                )
        except DualSightError as exc:
            if isinstance(
                exc,
                (AuthError, ForbiddenError, UpstreamRateLimitError, UpstreamTimeoutError),
            ):
                raise
            raise TextModelError(f"text model stream failed: {exc}") from exc

    async def ask_vision(
        self,
        image: ImageBlob,
        question: str,
        *,
        profile: str | None = None,
        detail: str | None = None,
    ) -> VisionAnswer:
        profile = profile or self.settings.profile
        detail = detail or self.settings.detail
        messages = [
            Message(role="system", content=VISION_ANSWER_SYSTEM_PROMPT),
            Message(
                role="user",
                content=[
                    TextPart(build_vision_question_prompt(question)),
                    ImagePart(image=image, detail="high" if detail != "brief" else "low"),
                ],
            ),
        ]
        request = ChatRequest(
            model=self.vision_provider.model,
            messages=messages,
            temperature=0.0,
            max_tokens=self.settings.vision_max_tokens,
        )
        try:
            result = await self.vision_provider.complete(request)
        except DualSightError as exc:
            if isinstance(
                exc,
                (AuthError, ForbiddenError, UpstreamRateLimitError, UpstreamTimeoutError),
            ):
                raise
            raise VisionError(f"vision model call failed: {exc}") from exc
        return VisionAnswer(
            text=result.text,
            finish_reason=result.finish_reason,
            usage=result.usage,
            provider=self.vision_provider.name,
            model=self.vision_provider.model,
            profile=profile,
            detail=detail,
        )

    async def perceive(
        self,
        image: ImageBlob,
        *,
        label: str = "image",
        profile: str | None = None,
        detail: str | None = None,
        bypass_cache: bool = False,
    ) -> PerceptionResult:
        profile = profile or self.settings.profile
        detail = detail or self.settings.detail
        try:
            outcome = await self.perceiver.perceive(
                image,
                profile=profile,
                detail=detail,
                bypass_cache=bypass_cache,
            )
        except DualSightError:
            raise
        evidence = render_evidence(
            outcome.perception,
            label=label,
            nonce=secrets.token_hex(8),
            max_chars=self.settings.max_evidence_chars,
            source=image.source,
        )
        trace = self._trace(
            ImagePart(image=image, label=label),
            outcome,
            profile=profile,
            detail=detail,
            truncated=evidence.truncated,
        )
        return PerceptionResult(trace=trace, evidence=evidence.text, raw_text=outcome.raw_text)

    def _trace(
        self,
        image: ImagePart,
        outcome: PerceptionOutcome,
        *,
        profile: str,
        detail: str,
        truncated: bool,
    ) -> ImageTrace:
        blob = image.image
        usage = outcome.usage
        return ImageTrace(
            image_id=blob.sha256[:12],
            label=image.label,
            source=blob.source,
            mime_type=blob.mime_type,
            width=blob.width,
            height=blob.height,
            provider=self.vision_provider.name,
            model=self.vision_provider.model,
            profile=profile,
            detail=detail,
            cached=outcome.cached,
            latency_ms=outcome.latency_ms,
            prompt_tokens=usage.prompt_tokens if usage and usage.prompt_tokens else 0,
            completion_tokens=(usage.completion_tokens if usage and usage.completion_tokens else 0),
            truncated=truncated,
            perception=outcome.perception,
            notes=tuple(blob.notes),
        )

    def _failed_trace(
        self,
        image: ImagePart,
        error: DualSightError,
        *,
        profile: str,
        detail: str,
    ) -> ImageTrace:
        blob = image.image
        return ImageTrace(
            image_id=blob.sha256[:12],
            label=image.label,
            source=blob.source,
            mime_type=blob.mime_type,
            width=blob.width,
            height=blob.height,
            provider=self.vision_provider.name,
            model=self.vision_provider.model,
            profile=profile,
            detail=detail,
            error=str(error),
            notes=tuple(blob.notes),
        )

    def _text_request(self, request: ChatRequest, prepared: PreparedRequest) -> ChatRequest:
        model = request.model
        if not model or model == "dualsight" or not self.settings.allow_model_override:
            model = self.settings.text_model or self.text_provider.model
        # OpenAI-compatible extras (response_format, seed, ...) are not valid for
        # Anthropic or Gemini and would be rejected as unknown fields. Provider
        # specific payload extensions belong in text_extra_body instead.
        extra = dict(request.extra) if self.text_provider.name == "openai" else {}
        return ChatRequest(
            model=model,
            messages=prepared.messages,
            temperature=(
                request.temperature
                if request.temperature is not None
                else self.settings.text_temperature
            ),
            max_tokens=request.max_tokens or self.settings.text_max_tokens,
            stream=request.stream,
            tools=request.tools if self.text_provider.supports_tools else None,
            tool_choice=request.tool_choice if self.text_provider.supports_tools else None,
            extra=extra,
        )

    def _inject(self, messages: list[Message], evidence: str, *, nonce: str) -> list[Message]:
        if self.settings.inject_mode == "user":
            for index in range(len(messages) - 1, -1, -1):
                message = messages[index]
                if message.role != "user":
                    continue
                if isinstance(message.content, str):
                    content: str | list[TextPart | ImagePart] = [
                        TextPart(evidence),
                        TextPart(message.content),
                    ]
                else:
                    content = [TextPart(evidence), *message.content]
                messages[index] = replace(message, content=content)
                return messages
            messages.append(Message(role="user", content=evidence))
            return messages

        for index, message in enumerate(messages):
            if message.role in {"system", "developer"}:
                if isinstance(message.content, str):
                    content = message.content + "\n\n" + evidence
                else:
                    content = [*message.content, TextPart(evidence)]
                messages[index] = replace(message, content=content)
                return messages
        return [Message(role="system", content=evidence), *messages]

    def _strip_images(self, messages: list[Message]) -> list[Message]:
        cleaned: list[Message] = []
        for message in messages:
            if not isinstance(message.content, list):
                cleaned.append(message)
                continue
            parts: list[TextPart] = []
            for part in message.content:
                if isinstance(part, TextPart):
                    parts.append(part)
                elif isinstance(part, ImagePart):
                    parts.append(TextPart(f"[{part.label}: image content was analyzed separately]"))
            if not parts:
                content: str | list[TextPart] = ""
            elif all(isinstance(part, TextPart) for part in parts):
                content = "\n".join(part.text for part in parts)
            else:
                content = parts
            cleaned.append(replace(message, content=content))
        return cleaned

    async def aclose(self) -> None:
        await self.vision_provider.aclose()
        if self.text_provider is not self.vision_provider:
            await self.text_provider.aclose()
        self.cache.close()
