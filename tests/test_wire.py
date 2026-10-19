"""OpenAI-compatible request parsing and validation."""

from __future__ import annotations

import asyncio
import base64
from dataclasses import replace

import pytest

from dualsight.config import Settings
from dualsight.errors import InvalidRequestError, PayloadTooLargeError
from dualsight.image_intake import ImageIntake
from dualsight.wire import parse_chat_request, parse_native_options, parse_options


def _payload(data_uri: str, **overrides):
    payload = {
        "model": "dualsight",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "What does the receipt say?"},
                    {
                        "type": "image_url",
                        "image_url": {"url": data_uri, "detail": "high"},
                    },
                ],
            }
        ],
        "stream": False,
        "dualsight": {"profile": "receipt", "detail": "deep", "bypass_cache": True},
    }
    payload.update(overrides)
    return payload


def _parse(payload, settings: Settings):
    return asyncio.run(parse_chat_request(payload, intake=ImageIntake(settings), settings=settings))


def test_parses_text_and_image(mock_settings: Settings, data_uri) -> None:
    parsed = _parse(_payload(data_uri()), mock_settings)

    assert parsed.stream is False
    assert parsed.options.profile == "receipt"
    assert parsed.options.detail == "deep"
    assert parsed.options.bypass_cache is True
    message = parsed.request.messages[0]
    assert message.has_images()
    text_part, image_part = message.content  # type: ignore[misc]
    assert text_part.text == "What does the receipt say?"
    assert image_part.label == "image-1"
    assert image_part.detail == "high"


def test_accepts_input_image_alias(mock_settings: Settings, data_uri) -> None:
    payload = _payload(
        "",
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Describe this"},
                    {"type": "input_image", "image_url": data_uri()},
                ],
            }
        ],
    )
    parsed = _parse(payload, mock_settings)

    assert parsed.request.messages[0].has_images()


def test_accepts_anthropic_base64_image(mock_settings: Settings, png_bytes) -> None:
    encoded = base64.b64encode(png_bytes()).decode("ascii")
    payload = _payload(
        "",
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Describe this"},
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": encoded,
                        },
                    },
                ],
            }
        ],
    )
    parsed = _parse(payload, mock_settings)

    assert parsed.request.messages[0].has_images()


def test_bool_options_are_strict(mock_settings: Settings, data_uri) -> None:
    with pytest.raises(InvalidRequestError, match="bypass_cache"):
        _parse(_payload(data_uri(), dualsight={"bypass_cache": "false"}), mock_settings)


def test_n_other_than_one_is_rejected(mock_settings: Settings, data_uri) -> None:
    with pytest.raises(InvalidRequestError, match="n"):
        _parse(_payload(data_uri(), n="two"), mock_settings)
    with pytest.raises(InvalidRequestError, match="n"):
        _parse(_payload(data_uri(), n=2), mock_settings)


def test_stream_must_be_boolean(mock_settings: Settings, data_uri) -> None:
    with pytest.raises(InvalidRequestError, match="stream"):
        _parse(_payload(data_uri(), stream="false"), mock_settings)


def test_temperature_is_bounded(mock_settings: Settings, data_uri) -> None:
    with pytest.raises(InvalidRequestError, match="temperature"):
        _parse(_payload(data_uri(), temperature=3), mock_settings)


def test_unknown_content_part_is_rejected(mock_settings: Settings) -> None:
    payload = _payload(
        "",
        messages=[
            {
                "role": "user",
                "content": [{"type": "video_url", "video_url": {"url": "https://example.test/a"}}],
            }
        ],
    )
    with pytest.raises(InvalidRequestError, match="content part"):
        _parse(payload, mock_settings)


def test_max_images_is_enforced(mock_settings: Settings, data_uri) -> None:
    settings = replace(mock_settings, max_images=1)
    payload = _payload(
        data_uri(),
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Compare"},
                    {"type": "image_url", "image_url": {"url": data_uri()}},
                    {"type": "image_url", "image_url": {"url": data_uri()}},
                ],
            }
        ],
    )
    with pytest.raises(PayloadTooLargeError, match="images"):
        _parse(payload, settings)


def test_text_only_request_has_no_images(mock_settings: Settings) -> None:
    parsed = _parse(
        {
            "model": "dualsight",
            "messages": [{"role": "user", "content": "Hello"}],
        },
        mock_settings,
    )

    assert parsed.request.messages[0].has_images() is False


def test_parse_options_defaults_and_validation() -> None:
    options = parse_options({})
    assert options.profile is None
    assert options.detail is None
    assert options.bypass_cache is False

    with pytest.raises(InvalidRequestError, match="profile"):
        parse_options({"profile": "unknown"})
    with pytest.raises(InvalidRequestError, match="detail"):
        parse_options({"detail": "ultra"})
    with pytest.raises(InvalidRequestError, match="include_perception"):
        parse_options({"include_perception": "yes"})


def test_parse_native_options() -> None:
    assert parse_native_options({"profile": "chart", "detail": "brief", "bypass_cache": True}) == (
        "chart",
        "brief",
        True,
    )
