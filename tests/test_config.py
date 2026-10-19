"""Configuration loading, validation, fallbacks, and redaction."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from dualsight.config import load_settings, redacted_settings
from dualsight.errors import ConfigError


def test_mock_mode_forces_mock_providers() -> None:
    settings = load_settings({"DUALSIGHT_MOCK": "true"})

    assert settings.mock is True
    assert settings.vision_provider == "mock"
    assert settings.text_provider == "mock"
    assert settings.vision_model == "mock-vision"
    assert settings.text_model == "mock-reasoner"


def test_text_provider_inherits_vision_provider_and_native_key() -> None:
    settings = load_settings(
        {
            "DUALSIGHT_VISION_PROVIDER": "anthropic",
            "ANTHROPIC_API_KEY": "test-anthropic",
        }
    )

    assert settings.vision_provider == "anthropic"
    assert settings.text_provider == "anthropic"
    assert settings.vision_api_key == "test-anthropic"
    assert settings.text_api_key == "test-anthropic"
    assert settings.text_base_url == settings.vision_base_url


def test_explicit_text_selection_overrides_inheritance() -> None:
    settings = load_settings(
        {
            "DUALSIGHT_VISION_PROVIDER": "openai",
            "DUALSIGHT_VISION_API_KEY": "vision-key",
            "DUALSIGHT_TEXT_PROVIDER": "gemini",
            "GEMINI_API_KEY": "gemini-key",
        }
    )

    assert settings.text_provider == "gemini"
    assert settings.vision_provider == "openai"
    assert settings.vision_api_key == "vision-key"
    assert settings.text_api_key == "gemini-key"


def test_env_file_loads_but_process_env_wins(workspace_tmp) -> None:
    env_file = workspace_tmp / ".env"
    env_file.write_text(
        "DUALSIGHT_PORT=9100\nDUALSIGHT_VISION_MODEL=from-file\n",
        encoding="utf-8",
    )

    from_file = load_settings(env_file=env_file)
    overridden = load_settings({"DUALSIGHT_PORT": "9200"}, env_file=env_file)

    assert from_file.port == 9100
    assert from_file.vision_model == "from-file"
    assert overridden.port == 9200


def test_invalid_provider_is_rejected() -> None:
    with pytest.raises(ConfigError, match="DUALSIGHT_VISION_PROVIDER must be one of"):
        load_settings({"DUALSIGHT_VISION_PROVIDER": "not-a-provider"})


def test_body_limit_must_fit_an_uploaded_image() -> None:
    with pytest.raises(ConfigError, match="DUALSIGHT_MAX_BODY_BYTES"):
        load_settings(
            {
                "DUALSIGHT_MAX_BODY_BYTES": "1000000",
                "DUALSIGHT_IMAGE_MAX_BYTES": "12582912",
            }
        )


def test_redaction_hides_provider_keys() -> None:
    settings = load_settings({"DUALSIGHT_MOCK": "true", "OPENAI_API_KEY": "sk-secret"})
    redacted = redacted_settings(settings)

    assert redacted["vision_api_key"] == "***"
    assert redacted["text_api_key"] == "***"
    assert "sk-secret" not in repr(redacted)


def test_settings_are_frozen() -> None:
    settings = load_settings({"DUALSIGHT_MOCK": "true"})

    with pytest.raises(FrozenInstanceError):
        settings.port = 1  # type: ignore[misc]
