"""Image validation, normalization, redirects, and SSRF guardrails."""

from __future__ import annotations

import asyncio
import io
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
from PIL import Image

from dualsight.config import Settings
from dualsight.errors import ImageError, ImageTooLargeError, UnsupportedImageError
from dualsight.image_intake import ImageIntake


def test_data_uri_is_normalized_and_labelled(mock_settings: Settings, data_uri) -> None:
    blob = ImageIntake(mock_settings).from_data_uri(data_uri(), source="image-7")

    assert blob.source == "image-7"
    assert blob.mime_type in {"image/png", "image/jpeg"}
    assert blob.width == 320
    assert blob.height == 220
    assert blob.original_bytes > 0
    assert blob.size_bytes <= blob.original_bytes
    assert len(blob.sha256) == 64
    assert len(blob.original_sha256) == 64
    assert blob.data_uri().startswith(f"data:{blob.mime_type};base64,")


def test_alpha_is_flattened(mock_settings: Settings, png_bytes) -> None:
    blob = ImageIntake(mock_settings).from_bytes(png_bytes(mode="RGBA"), source="alpha")

    with Image.open(io.BytesIO(blob.data)) as image:
        assert image.mode in {"RGB", "L"}


def test_large_image_is_thumbnailed(mock_settings: Settings, png_bytes) -> None:
    settings = replace(mock_settings, image_max_dim=96)
    blob = ImageIntake(settings).from_bytes(png_bytes(width=800, height=600), source="big")

    assert max(blob.width, blob.height) <= 96
    assert blob.original_width == 800


def test_rejects_pixel_bomb(mock_settings: Settings, png_bytes) -> None:
    settings = replace(mock_settings, image_max_pixels=1000, image_max_dim=4096)
    with pytest.raises(ImageTooLargeError, match="pixels"):
        ImageIntake(settings).from_bytes(png_bytes(width=200, height=200), source="bomb")


def test_rejects_svg_data_uri(mock_settings: Settings) -> None:
    with pytest.raises(UnsupportedImageError, match="SVG"):
        ImageIntake(mock_settings).from_data_uri(
            "data:image/svg+xml;base64,PHN2Zy8+", source="vector"
        )


def test_rejects_malformed_image(mock_settings: Settings) -> None:
    with pytest.raises(UnsupportedImageError, match="corrupt"):
        ImageIntake(mock_settings).from_bytes(b"not a real png", source="broken")


def test_rejects_file_references(mock_settings: Settings) -> None:
    with pytest.raises(ImageError, match="file"):
        asyncio.run(ImageIntake(mock_settings).from_reference("file:///etc/passwd"))


def test_private_network_is_blocked_by_default(mock_settings: Settings) -> None:
    with pytest.raises(ImageError, match="not allowed|private|local"):
        asyncio.run(ImageIntake(mock_settings).from_reference("http://127.0.0.1/a.png"))


def test_url_fetch_and_redirect_with_mock_transport(mock_settings: Settings, png_bytes) -> None:
    raw = png_bytes()
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        if request.url.path == "/start":
            return httpx.Response(302, headers={"location": "/final"})
        return httpx.Response(200, content=raw, headers={"content-type": "image/png"})

    settings = replace(mock_settings, allow_private_image_urls=True)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            intake = ImageIntake(settings, client=client)
            return await intake.from_url("http://example.test/start")

    blob = asyncio.run(run())

    assert requests == ["http://example.test/start", "http://example.test/final"]
    assert blob.source == "example.test"
    assert blob.width == 320


def test_url_rejects_wrong_content_type(mock_settings: Settings) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>", headers={"content-type": "text/html"})

    settings = replace(mock_settings, allow_private_image_urls=True)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await ImageIntake(settings, client=client).from_url("http://example.test/a")

    with pytest.raises(ImageError, match="not an image"):
        asyncio.run(run())


def test_url_rejects_oversized_response(mock_settings: Settings) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * 5000, headers={"content-type": "image/png"})

    settings = replace(
        mock_settings,
        allow_private_image_urls=True,
        image_max_bytes=1000,
        image_max_pixels=40_000_000,
    )

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await ImageIntake(settings, client=client).from_url("http://example.test/a")

    with pytest.raises(ImageTooLargeError, match="larger than"):
        asyncio.run(run())


def test_from_path_reads_local_file(
    mock_settings: Settings, png_bytes, workspace_tmp: Path
) -> None:
    path = workspace_tmp / "sample.png"
    path.write_bytes(png_bytes())

    blob = ImageIntake(mock_settings).from_path(path)

    assert blob.source == "file"
    assert blob.width == 320
