"""Run the perception model and parse its structured output."""

from __future__ import annotations

import contextlib
import json
import time
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from .cache import CacheEntry, PerceptionCache, cache_key
from .chat import ChatRequest, ImageBlob, ImagePart, Message, TextPart, Usage
from .config import Settings
from .errors import DualSightError, VisionError
from .models import Perception
from .prompts import PERCEPTION_SYSTEM_PROMPT, PROMPT_VERSION, build_perception_prompt
from .providers.base import ChatProvider


@dataclass(frozen=True, slots=True)
class PerceptionOutcome:
    perception: Perception
    raw_text: str
    usage: Usage | None
    cached: bool
    latency_ms: int
    cache_key: str


def _strip_fences(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _json_candidates(text: str) -> list[Any]:
    stripped = _strip_fences(text)
    candidates: list[Any] = []
    with contextlib.suppress(json.JSONDecodeError, ValueError):
        candidates.append(json.loads(stripped))
    if "{" in stripped and "}" in stripped:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if end > start:
            with contextlib.suppress(json.JSONDecodeError, ValueError):
                candidates.append(json.loads(stripped[start : end + 1]))
    decoder = json.JSONDecoder()
    for index, character in enumerate(stripped):
        if character != "{":
            continue
        try:
            value, _ = decoder.raw_decode(stripped[index:])
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(value, dict):
            candidates.append(value)
    return candidates


def _coerce_for_model(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    data = dict(value)
    for key in ("perception", "observation", "result", "data"):
        nested = data.get(key)
        if isinstance(nested, dict) and ("summary" in nested or "ocr" in nested):
            data = dict(nested)
            break
    if isinstance(data.get("ocr"), list):
        data["ocr"] = [{"text": item} if isinstance(item, str) else item for item in data["ocr"]]
    if isinstance(data.get("objects"), list):
        data["objects"] = [
            {"name": item} if isinstance(item, str) else item for item in data["objects"]
        ]
    return data


def parse_perception(raw_text: str) -> Perception:
    """Parse a model response into :class:`Perception`.

    Models are inconsistent. We try strict JSON, fenced JSON, a JSON substring,
    and every decodable object in the text. If all of that fails, the raw text
    becomes a summary plus an explicit uncertainty, which is far more useful
    than a 500.
    """

    text = raw_text.strip()
    if not text:
        return Perception(
            summary="",
            uncertainties=["The vision model returned no output."],
        )
    for candidate in _json_candidates(text):
        value = _coerce_for_model(candidate)
        if not isinstance(value, dict):
            continue
        try:
            perception = Perception.model_validate(value)
        except ValidationError:
            continue
        if perception.summary or perception.ocr or perception.objects or perception.chart:
            return perception
    return Perception(
        summary=text[:8000],
        uncertainties=[
            "The vision model did not return structured JSON; this summary is its raw response."
        ],
    )


class Perceiver:
    """Cache-aware wrapper around a vision provider."""

    def __init__(
        self,
        *,
        settings: Settings,
        provider: ChatProvider,
        cache: PerceptionCache,
        prompt_version: str = PROMPT_VERSION,
    ) -> None:
        self.settings = settings
        self.provider = provider
        self.cache = cache
        self.prompt_version = prompt_version

    async def perceive(
        self,
        image: ImageBlob,
        *,
        profile: str | None = None,
        detail: str | None = None,
        bypass_cache: bool = False,
    ) -> PerceptionOutcome:
        profile = profile or self.settings.profile
        detail = detail or self.settings.detail
        key = cache_key(
            image_sha256=image.sha256,
            provider=self.provider.name,
            model=self.provider.model,
            profile=profile,
            detail=detail,
            prompt_version=self.prompt_version,
        )
        if not bypass_cache:
            cached = self.cache.get(key)
            if cached is not None:
                return PerceptionOutcome(
                    perception=cached.perception,
                    raw_text="",
                    usage=cached.usage,
                    cached=True,
                    latency_ms=0,
                    cache_key=key,
                )

        request = ChatRequest(
            model=self.provider.model,
            messages=[
                Message(role="system", content=PERCEPTION_SYSTEM_PROMPT),
                Message(
                    role="user",
                    content=[
                        TextPart(build_perception_prompt(profile, detail)),
                        ImagePart(
                            image=image,
                            detail="low" if detail == "brief" else "high",
                        ),
                    ],
                ),
            ],
            temperature=self.settings.vision_temperature,
            max_tokens=self.settings.vision_max_tokens,
        )
        started = time.perf_counter()
        try:
            result = await self.provider.complete(request)
        except DualSightError:
            raise
        except Exception as exc:  # unexpected provider bug: never leak internals
            raise VisionError(f"vision model call failed: {type(exc).__name__}") from exc
        latency_ms = int((time.perf_counter() - started) * 1000)
        perception = parse_perception(result.text)
        if not perception.summary.strip() and not perception.ocr and not perception.objects:
            raise VisionError("vision model returned an empty observation")

        entry = CacheEntry(
            perception=perception,
            usage=result.usage,
            created_at=time.time(),
            provider=self.provider.name,
            model=self.provider.model,
            profile=profile,
            detail=detail,
        )
        self.cache.set(key, entry)
        return PerceptionOutcome(
            perception=perception,
            raw_text=result.text,
            usage=result.usage,
            cached=False,
            latency_ms=latency_ms,
            cache_key=key,
        )
