"""Provider factory."""

from __future__ import annotations

from ..config import Settings
from ..errors import ConfigError
from .anthropic import AnthropicProvider
from .base import ChatProvider
from .gemini import GeminiProvider
from .mock import MockProvider
from .openai_compat import OpenAICompatibleProvider

_API_KEY_ENV = {
    "openai": "DUALSIGHT_VISION_API_KEY / DUALSIGHT_API_KEY / OPENAI_API_KEY",
    "anthropic": "DUALSIGHT_VISION_API_KEY / ANTHROPIC_API_KEY",
    "gemini": "DUALSIGHT_VISION_API_KEY / GEMINI_API_KEY",
}


def build_provider(settings: Settings, role: str) -> ChatProvider:
    """Build one provider for ``role`` ("vision" or "text")."""

    if role not in {"vision", "text"}:
        raise ValueError(f"unknown provider role: {role}")
    prefix = "vision" if role == "vision" else "text"
    name = getattr(settings, f"{prefix}_provider")
    api_key = getattr(settings, f"{prefix}_api_key")
    model = getattr(settings, f"{prefix}_model")
    base_url = getattr(settings, f"{prefix}_base_url")
    timeout_s = getattr(settings, f"{prefix}_timeout_s")
    max_retries = getattr(settings, f"{prefix}_max_retries")
    headers = getattr(settings, f"{prefix}_headers")
    extra_body = getattr(settings, f"{prefix}_extra_body")

    if name == "mock" or settings.mock:
        return MockProvider(model=model)
    if not api_key:
        expected = _API_KEY_ENV.get(name, "DUALSIGHT_API_KEY")
        raise ConfigError(
            f"the {prefix} provider '{name}' has no API key. Set {expected}, "
            "or run with DUALSIGHT_MOCK=true for the offline demo."
        )
    if name == "openai":
        return OpenAICompatibleProvider(
            base_url=base_url,
            api_key=api_key,
            model=model,
            timeout_s=timeout_s,
            max_retries=max_retries,
            headers=headers,
            extra_body=extra_body,
        )
    if name == "anthropic":
        return AnthropicProvider(
            base_url=base_url,
            api_key=api_key,
            model=model,
            timeout_s=timeout_s,
            max_retries=max_retries,
            headers=headers,
            extra_body=extra_body,
        )
    if name == "gemini":
        return GeminiProvider(
            base_url=base_url,
            api_key=api_key,
            model=model,
            timeout_s=timeout_s,
            max_retries=max_retries,
            headers=headers,
            extra_body=extra_body,
        )
    raise ConfigError(f"unsupported provider: {name}")


def build_providers(settings: Settings) -> tuple[ChatProvider, ChatProvider]:
    return build_provider(settings, "vision"), build_provider(settings, "text")


__all__ = ["ChatProvider", "build_provider", "build_providers"]
