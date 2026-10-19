"""Shared test fixtures: deterministic images, settings, and data URIs."""

from __future__ import annotations

import base64
import io
import shutil
from collections.abc import Callable
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from dualsight.chat import ImageBlob
from dualsight.config import Settings, load_settings
from dualsight.image_intake import ImageIntake


@pytest.fixture(autouse=True)
def _isolate_proxy_environment(monkeypatch) -> None:
    """Keep host proxy variables (including a bracketed NO_PROXY) out of httpx."""

    for name in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "NO_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
        "no_proxy",
    ):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def workspace_tmp() -> Path:
    path = Path(__file__).with_name("_tmp")
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True)
    yield path
    shutil.rmtree(path, ignore_errors=True)


@pytest.fixture
def mock_settings() -> Settings:
    return load_settings({"DUALSIGHT_MOCK": "true", "DUALSIGHT_CACHE": "none"})


@pytest.fixture
def png_bytes() -> Callable[..., bytes]:
    def make(width: int = 320, height: int = 220, mode: str = "RGB") -> bytes:
        color = (18, 26, 44, 255) if mode == "RGBA" else (18, 26, 44)
        image = Image.new(mode, (width, height), color)
        draw = ImageDraw.Draw(image)
        draw.rectangle((24, 24, width - 24, height - 24), outline=(90, 210, 255), width=4)
        draw.text((40, height // 2 - 8), "TOTAL $23.47", fill=(240, 240, 240))
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()

    return make


@pytest.fixture
def data_uri(png_bytes: Callable[..., bytes]) -> Callable[[bytes | None], str]:
    def make(raw: bytes | None = None) -> str:
        payload = raw if raw is not None else png_bytes()
        return "data:image/png;base64," + base64.b64encode(payload).decode("ascii")

    return make


@pytest.fixture
def blob(mock_settings: Settings, png_bytes: Callable[..., bytes]) -> ImageBlob:
    return ImageIntake(mock_settings).from_bytes(png_bytes(), source="fixture")
