"""Configuration loading and validation.

DualSight deliberately keeps configuration in a plain frozen dataclass instead of
pulling in ``pydantic-settings``.  The rules are small, explicit, and easy to
audit; every value can be supplied through ``DUALSIGHT_*`` environment variables
or a local ``.env`` file.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field, fields, replace
from pathlib import Path
from typing import Any

from .errors import ConfigError

PROVIDERS = ("openai", "anthropic", "gemini", "mock")
PROFILES = ("auto", "photo", "document", "ui", "chart", "receipt", "diagram")
DETAIL_LEVELS = ("brief", "standard", "deep")
CACHE_BACKENDS = ("none", "memory", "sqlite")
VISION_ERROR_MODES = ("degrade", "fail")
INJECT_MODES = ("system", "user")

_DEFAULT_BASE_URLS = {
    "openai": "https://api.openai.com/v1",
    "anthropic": "https://api.anthropic.com/v1",
    "gemini": "https://generativelanguage.googleapis.com/v1beta",
    "mock": "http://mock.invalid/v1",
}
_DEFAULT_VISION_MODELS = {
    "openai": "gpt-4o-mini",
    "anthropic": "claude-3-5-sonnet-latest",
    "gemini": "gemini-2.0-flash",
    "mock": "mock-vision",
}
_DEFAULT_TEXT_MODELS = {
    "openai": "gpt-4o-mini",
    "anthropic": "claude-3-5-haiku-latest",
    "gemini": "gemini-2.0-flash",
    "mock": "mock-reasoner",
}


@dataclass(frozen=True, slots=True)
class Settings:
    """Validated runtime configuration."""

    # HTTP server
    host: str = "127.0.0.1"
    port: int = 8000
    api_key: str | None = field(default=None, repr=False)
    cors_origins: tuple[str, ...] = ("*",)
    max_body_bytes: int = 25 * 1024 * 1024
    request_timeout_s: float = 180.0
    log_level: str = "INFO"
    log_json: bool = False
    demo_enabled: bool = True

    # Vision / perception model
    vision_provider: str = "openai"
    vision_base_url: str = _DEFAULT_BASE_URLS["openai"]
    vision_api_key: str | None = field(default=None, repr=False)
    vision_model: str = _DEFAULT_VISION_MODELS["openai"]
    vision_timeout_s: float = 60.0
    vision_max_retries: int = 2
    vision_max_tokens: int = 1600
    vision_temperature: float = 0.0
    vision_headers: dict[str, str] = field(default_factory=dict)
    vision_extra_body: dict[str, Any] = field(default_factory=dict)

    # Text / reasoning model
    text_provider: str = "openai"
    text_base_url: str = _DEFAULT_BASE_URLS["openai"]
    text_api_key: str | None = field(default=None, repr=False)
    text_model: str = _DEFAULT_TEXT_MODELS["openai"]
    text_timeout_s: float = 120.0
    text_max_retries: int = 2
    text_max_tokens: int = 2048
    text_temperature: float = 0.3
    text_headers: dict[str, str] = field(default_factory=dict)
    text_extra_body: dict[str, Any] = field(default_factory=dict)

    # Image intake
    image_max_bytes: int = 12 * 1024 * 1024
    image_max_pixels: int = 40_000_000
    image_max_dim: int = 1568
    image_jpeg_quality: int = 85
    image_fetch_timeout_s: float = 20.0
    max_images: int = 8
    allow_private_image_urls: bool = False

    # Bridge behaviour
    profile: str = "auto"
    detail: str = "standard"
    max_evidence_chars: int = 12_000
    perception_concurrency: int = 4
    on_vision_error: str = "degrade"
    inject_mode: str = "system"
    trace_in_response: bool = True
    allow_model_override: bool = False

    # Perception cache
    cache: str = "memory"
    cache_ttl_s: int = 86_400
    cache_max_entries: int = 512
    cache_path: str = ".dualsight/cache.sqlite3"

    # Convenience
    mock: bool = False
    pretty_json: bool = False

    def with_overrides(self, **kwargs: Any) -> Settings:
        """Return a copy with validated overrides (used by the CLI)."""

        return replace(self, **kwargs)


def default_base_url(provider: str) -> str:
    return _DEFAULT_BASE_URLS.get(provider, _DEFAULT_BASE_URLS["openai"])


def default_vision_model(provider: str) -> str:
    return _DEFAULT_VISION_MODELS.get(provider, _DEFAULT_VISION_MODELS["openai"])


def default_text_model(provider: str) -> str:
    return _DEFAULT_TEXT_MODELS.get(provider, _DEFAULT_TEXT_MODELS["openai"])


def _parse_dotenv(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, sep, value = line.partition("=")
        if not sep:
            continue
        key = key.strip()
        if not key:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values


def load_env_file(path: str | os.PathLike[str] | None = None) -> dict[str, str]:
    """Load a dotenv file, returning an empty mapping when it does not exist."""

    if path is None:
        path = os.environ.get("DUALSIGHT_ENV_FILE", ".env")
    candidate = Path(path)
    if not candidate.is_file():
        return {}
    try:
        return _parse_dotenv(candidate.read_text(encoding="utf-8"))
    except OSError as exc:  # pragma: no cover - unreadable local file
        raise ConfigError(f"cannot read env file {candidate}: {exc}") from exc


def _parse_bool(name: str, value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off", ""}:
        return False
    raise ConfigError(f"{name} must be a boolean, got {value!r}")


def _parse_int(name: str, value: str, *, minimum: int | None = None) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{name} must be an integer, got {value!r}") from exc
    if minimum is not None and parsed < minimum:
        raise ConfigError(f"{name} must be >= {minimum}, got {parsed}")
    return parsed


def _parse_float(name: str, value: str, *, minimum: float | None = None) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{name} must be a number, got {value!r}") from exc
    if minimum is not None and parsed < minimum:
        raise ConfigError(f"{name} must be >= {minimum}, got {parsed}")
    return parsed


def _parse_csv(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in value.split(",") if item.strip())


def _parse_json_object(name: str, value: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{name} must be a JSON object: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ConfigError(f"{name} must be a JSON object")
    return parsed


def _pick(env: Mapping[str, str], file_env: Mapping[str, str], key: str) -> str | None:
    if key in env and env[key] != "":
        return env[key]
    if key in file_env and file_env[key] != "":
        return file_env[key]
    return None


def _pick_any(env: Mapping[str, str], file_env: Mapping[str, str], *keys: str) -> str | None:
    for key in keys:
        value = _pick(env, file_env, key)
        if value is not None:
            return value
    return None


def _normalize_provider(name: str, value: str) -> str:
    provider = value.strip().lower()
    if provider not in PROVIDERS:
        raise ConfigError(f"{name} must be one of {', '.join(PROVIDERS)}; got {value!r}")
    return provider


def _validate_url(name: str, value: str) -> str:
    if not (value.startswith("http://") or value.startswith("https://")):
        raise ConfigError(f"{name} must start with http:// or https://")
    return value.rstrip("/")


def _validate_choice(name: str, value: str, choices: tuple[str, ...]) -> str:
    if value not in choices:
        raise ConfigError(f"{name} must be one of {', '.join(choices)}; got {value!r}")
    return value


def load_settings(
    env: Mapping[str, str] | None = None,
    *,
    env_file: str | os.PathLike[str] | None = None,
) -> Settings:
    """Build :class:`Settings` from defaults, a dotenv file, and the environment.

    Precedence: process environment > dotenv file > defaults.
    """

    process_env = dict(os.environ if env is None else env)
    file_env = load_env_file(env_file) if env is None else {}

    def get(name: str) -> str | None:
        return _pick(process_env, file_env, name)

    def get_any(*names: str) -> str | None:
        return _pick_any(process_env, file_env, *names)

    vision_provider = _normalize_provider(
        "DUALSIGHT_VISION_PROVIDER", get("DUALSIGHT_VISION_PROVIDER") or "openai"
    )
    text_provider_raw = get("DUALSIGHT_TEXT_PROVIDER")
    text_provider = _normalize_provider(
        "DUALSIGHT_TEXT_PROVIDER", text_provider_raw or vision_provider
    )

    vision_base_url = get("DUALSIGHT_VISION_BASE_URL") or default_base_url(vision_provider)
    text_base_url_raw = get("DUALSIGHT_TEXT_BASE_URL")
    if text_base_url_raw:
        text_base_url = text_base_url_raw
    elif text_provider == vision_provider:
        text_base_url = vision_base_url
    else:
        text_base_url = default_base_url(text_provider)

    vision_base_url = _validate_url("DUALSIGHT_VISION_BASE_URL", vision_base_url)
    text_base_url = _validate_url("DUALSIGHT_TEXT_BASE_URL", text_base_url)

    vision_api_key = get_any(
        "DUALSIGHT_VISION_API_KEY",
        "DUALSIGHT_API_KEY",
        {
            "openai": "OPENAI_API_KEY",
            "anthropic": "ANTHROPIC_API_KEY",
            "gemini": "GEMINI_API_KEY",
        }.get(vision_provider, ""),
    )
    text_api_key = get("DUALSIGHT_TEXT_API_KEY")
    if text_api_key is None and text_base_url == vision_base_url:
        text_api_key = vision_api_key
    if text_api_key is None:
        text_api_key = get(
            {
                "openai": "OPENAI_API_KEY",
                "anthropic": "ANTHROPIC_API_KEY",
                "gemini": "GEMINI_API_KEY",
            }.get(text_provider, "")
        )

    vision_model = get("DUALSIGHT_VISION_MODEL") or default_vision_model(vision_provider)
    text_model = get("DUALSIGHT_TEXT_MODEL") or default_text_model(text_provider)

    settings = Settings(
        host=get("DUALSIGHT_HOST") or "127.0.0.1",
        port=_parse_int("DUALSIGHT_PORT", get("DUALSIGHT_PORT") or "8000", minimum=1),
        api_key=get_any("DUALSIGHT_SERVER_API_KEY", "DUALSIGHT_BEARER_TOKEN"),
        cors_origins=_parse_csv(get("DUALSIGHT_CORS_ORIGINS") or "*"),
        max_body_bytes=_parse_int(
            "DUALSIGHT_MAX_BODY_BYTES",
            get("DUALSIGHT_MAX_BODY_BYTES") or str(25 * 1024 * 1024),
            minimum=1024,
        ),
        request_timeout_s=_parse_float(
            "DUALSIGHT_REQUEST_TIMEOUT_S",
            get("DUALSIGHT_REQUEST_TIMEOUT_S") or "180",
            minimum=1,
        ),
        log_level=(get("DUALSIGHT_LOG_LEVEL") or "INFO").upper(),
        log_json=_parse_bool("DUALSIGHT_LOG_JSON", get("DUALSIGHT_LOG_JSON") or "false"),
        demo_enabled=_parse_bool("DUALSIGHT_DEMO_ENABLED", get("DUALSIGHT_DEMO_ENABLED") or "true"),
        vision_provider=vision_provider,
        vision_base_url=vision_base_url,
        vision_api_key=vision_api_key,
        vision_model=vision_model,
        vision_timeout_s=_parse_float(
            "DUALSIGHT_VISION_TIMEOUT_S", get("DUALSIGHT_VISION_TIMEOUT_S") or "60", minimum=1
        ),
        vision_max_retries=_parse_int(
            "DUALSIGHT_VISION_MAX_RETRIES",
            get("DUALSIGHT_VISION_MAX_RETRIES") or "2",
            minimum=0,
        ),
        vision_max_tokens=_parse_int(
            "DUALSIGHT_VISION_MAX_TOKENS",
            get("DUALSIGHT_VISION_MAX_TOKENS") or "1600",
            minimum=64,
        ),
        vision_temperature=_parse_float(
            "DUALSIGHT_VISION_TEMPERATURE",
            get("DUALSIGHT_VISION_TEMPERATURE") or "0",
            minimum=0,
        ),
        vision_headers=_parse_json_object(
            "DUALSIGHT_VISION_HEADERS", get("DUALSIGHT_VISION_HEADERS") or "{}"
        ),
        vision_extra_body=_parse_json_object(
            "DUALSIGHT_VISION_EXTRA_BODY", get("DUALSIGHT_VISION_EXTRA_BODY") or "{}"
        ),
        text_provider=text_provider,
        text_base_url=text_base_url,
        text_api_key=text_api_key,
        text_model=text_model,
        text_timeout_s=_parse_float(
            "DUALSIGHT_TEXT_TIMEOUT_S", get("DUALSIGHT_TEXT_TIMEOUT_S") or "120", minimum=1
        ),
        text_max_retries=_parse_int(
            "DUALSIGHT_TEXT_MAX_RETRIES",
            get("DUALSIGHT_TEXT_MAX_RETRIES") or "2",
            minimum=0,
        ),
        text_max_tokens=_parse_int(
            "DUALSIGHT_TEXT_MAX_TOKENS",
            get("DUALSIGHT_TEXT_MAX_TOKENS") or "2048",
            minimum=64,
        ),
        text_temperature=_parse_float(
            "DUALSIGHT_TEXT_TEMPERATURE",
            get("DUALSIGHT_TEXT_TEMPERATURE") or "0.3",
            minimum=0,
        ),
        text_headers=_parse_json_object(
            "DUALSIGHT_TEXT_HEADERS", get("DUALSIGHT_TEXT_HEADERS") or "{}"
        ),
        text_extra_body=_parse_json_object(
            "DUALSIGHT_TEXT_EXTRA_BODY", get("DUALSIGHT_TEXT_EXTRA_BODY") or "{}"
        ),
        image_max_bytes=_parse_int(
            "DUALSIGHT_IMAGE_MAX_BYTES",
            get("DUALSIGHT_IMAGE_MAX_BYTES") or str(12 * 1024 * 1024),
            minimum=1024,
        ),
        image_max_pixels=_parse_int(
            "DUALSIGHT_IMAGE_MAX_PIXELS",
            get("DUALSIGHT_IMAGE_MAX_PIXELS") or "40000000",
            minimum=10_000,
        ),
        image_max_dim=_parse_int(
            "DUALSIGHT_IMAGE_MAX_DIM", get("DUALSIGHT_IMAGE_MAX_DIM") or "1568", minimum=256
        ),
        image_jpeg_quality=_parse_int(
            "DUALSIGHT_IMAGE_JPEG_QUALITY",
            get("DUALSIGHT_IMAGE_JPEG_QUALITY") or "85",
            minimum=40,
        ),
        image_fetch_timeout_s=_parse_float(
            "DUALSIGHT_IMAGE_FETCH_TIMEOUT_S",
            get("DUALSIGHT_IMAGE_FETCH_TIMEOUT_S") or "20",
            minimum=1,
        ),
        max_images=_parse_int(
            "DUALSIGHT_MAX_IMAGES", get("DUALSIGHT_MAX_IMAGES") or "8", minimum=1
        ),
        allow_private_image_urls=_parse_bool(
            "DUALSIGHT_ALLOW_PRIVATE_IMAGE_URLS",
            get("DUALSIGHT_ALLOW_PRIVATE_IMAGE_URLS") or "false",
        ),
        profile=_validate_choice("DUALSIGHT_PROFILE", get("DUALSIGHT_PROFILE") or "auto", PROFILES),
        detail=_validate_choice(
            "DUALSIGHT_DETAIL", get("DUALSIGHT_DETAIL") or "standard", DETAIL_LEVELS
        ),
        max_evidence_chars=_parse_int(
            "DUALSIGHT_MAX_EVIDENCE_CHARS",
            get("DUALSIGHT_MAX_EVIDENCE_CHARS") or "12000",
            minimum=500,
        ),
        perception_concurrency=_parse_int(
            "DUALSIGHT_PERCEPTION_CONCURRENCY",
            get("DUALSIGHT_PERCEPTION_CONCURRENCY") or "4",
            minimum=1,
        ),
        on_vision_error=_validate_choice(
            "DUALSIGHT_ON_VISION_ERROR",
            get("DUALSIGHT_ON_VISION_ERROR") or "degrade",
            VISION_ERROR_MODES,
        ),
        inject_mode=_validate_choice(
            "DUALSIGHT_INJECT_MODE", get("DUALSIGHT_INJECT_MODE") or "system", INJECT_MODES
        ),
        trace_in_response=_parse_bool(
            "DUALSIGHT_TRACE_IN_RESPONSE", get("DUALSIGHT_TRACE_IN_RESPONSE") or "true"
        ),
        allow_model_override=_parse_bool(
            "DUALSIGHT_ALLOW_MODEL_OVERRIDE",
            get("DUALSIGHT_ALLOW_MODEL_OVERRIDE") or "false",
        ),
        cache=_validate_choice(
            "DUALSIGHT_CACHE", get("DUALSIGHT_CACHE") or "memory", CACHE_BACKENDS
        ),
        cache_ttl_s=_parse_int(
            "DUALSIGHT_CACHE_TTL_S", get("DUALSIGHT_CACHE_TTL_S") or "86400", minimum=0
        ),
        cache_max_entries=_parse_int(
            "DUALSIGHT_CACHE_MAX_ENTRIES",
            get("DUALSIGHT_CACHE_MAX_ENTRIES") or "512",
            minimum=1,
        ),
        cache_path=get("DUALSIGHT_CACHE_PATH") or ".dualsight/cache.sqlite3",
        mock=_parse_bool("DUALSIGHT_MOCK", get("DUALSIGHT_MOCK") or "false"),
        pretty_json=_parse_bool("DUALSIGHT_PRETTY_JSON", get("DUALSIGHT_PRETTY_JSON") or "false"),
    )
    return validate_settings(settings)


def validate_settings(settings: Settings) -> Settings:
    """Validate invariants that depend on several fields at once."""

    if not 1 <= settings.port <= 65535:
        raise ConfigError("DUALSIGHT_PORT must be between 1 and 65535")
    if settings.image_max_dim > 4096:
        raise ConfigError("DUALSIGHT_IMAGE_MAX_DIM must be <= 4096")
    if settings.image_jpeg_quality > 100:
        raise ConfigError("DUALSIGHT_IMAGE_JPEG_QUALITY must be <= 100")
    if settings.vision_max_retries > 5 or settings.text_max_retries > 5:
        raise ConfigError("retry counts must be <= 5")
    if settings.max_body_bytes < settings.image_max_bytes:
        raise ConfigError("DUALSIGHT_MAX_BODY_BYTES must be >= DUALSIGHT_IMAGE_MAX_BYTES")
    if settings.cache == "sqlite" and not settings.cache_path:
        raise ConfigError("DUALSIGHT_CACHE_PATH is required for the sqlite cache")
    if settings.mock:
        settings = replace(
            settings,
            vision_provider="mock",
            text_provider="mock",
            vision_base_url=_DEFAULT_BASE_URLS["mock"],
            text_base_url=_DEFAULT_BASE_URLS["mock"],
            vision_model=settings.vision_model
            if settings.vision_model != _DEFAULT_VISION_MODELS["openai"]
            else _DEFAULT_VISION_MODELS["mock"],
            text_model=settings.text_model
            if settings.text_model != _DEFAULT_TEXT_MODELS["openai"]
            else _DEFAULT_TEXT_MODELS["mock"],
        )
    return settings


def redacted_settings(settings: Settings) -> dict[str, Any]:
    """A printable settings snapshot with secrets removed."""

    result: dict[str, Any] = {}
    for item in fields(settings):
        value = getattr(settings, item.name)
        if item.name.endswith("_api_key") or item.name == "api_key":
            result[item.name] = "***" if value else None
        elif isinstance(value, dict):
            result[item.name] = dict(value)
        elif isinstance(value, tuple):
            result[item.name] = list(value)
        else:
            result[item.name] = value
    return result
