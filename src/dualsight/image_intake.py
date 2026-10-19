"""Secure image intake and normalization.

Everything that arrives from a client is treated as hostile: URLs are checked
for SSRF, downloads are size-capped, images are decoded with a pixel budget,
metadata is stripped, and the result is re-encoded before it reaches a vision
API.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import io
import ipaddress
import re
import socket
from collections.abc import Iterable
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx
from PIL import Image, ImageOps, UnidentifiedImageError

from .chat import ImageBlob
from .config import Settings
from .errors import ImageError, ImageTooLargeError, UnsupportedImageError

_DATA_URI_RE = re.compile(
    r"^data:(?P<mime>[^;,]+)?(?P<base64>;base64)?,(?P<data>.*)$", re.IGNORECASE | re.DOTALL
)
_REDIRECT_STATUSES = {301, 302, 303, 307, 308}
_MAX_REDIRECTS = 3
_BLOCKED_HOST_SUFFIXES = (
    ".localhost",
    ".local",
    ".internal",
    ".lan",
    ".home",
    ".home.arpa",
    ".invalid",
    ".test",
    ".example",
)
_BLOCKED_HOSTS = {
    "localhost",
    "localhost.localdomain",
    "metadata",
    "metadata.google.internal",
    "instance-data",
    "0.0.0.0",
}
_USER_AGENT = "DualSight/0.1 (+https://github.com/RyeSama/dualsight)"


async def _resolve_host(host: str, port: int) -> list[str]:
    """Resolve a hostname to every address we might connect to."""

    infos = await asyncio.to_thread(
        socket.getaddrinfo, host, port, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP
    )
    addresses: list[str] = []
    for info in infos:
        sockaddr = info[4]
        if sockaddr and sockaddr[0] not in addresses:
            addresses.append(sockaddr[0])
    return addresses


def _is_public_address(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    if address.version == 6 and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    if address.is_loopback or address.is_link_local or address.is_private:
        return False
    if address.is_multicast or address.is_reserved or address.is_unspecified:
        return False
    return address.is_global


def _looks_like_svg(data: bytes) -> bool:
    head = data[:4096].lstrip().lower()
    return head.startswith(b"<svg") or (head.startswith(b"<?xml") and b"<svg" in head)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class ImageIntake:
    """Turns data URIs, HTTP(S) URLs, and local bytes into :class:`ImageBlob`."""

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self.settings = settings
        self._client = client
        self._owns_client = client is None

    async def close(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    async def from_reference(self, reference: str, *, source: str | None = None) -> ImageBlob:
        if not isinstance(reference, str) or not reference.strip():
            raise ImageError("image reference must be a non-empty string")
        value = reference.strip()
        if value.startswith("data:"):
            return self.from_data_uri(value, source=source or "inline")
        if value.startswith(("http://", "https://")):
            return await self.from_url(value)
        if value.startswith("file://"):
            raise ImageError(
                "file:// references are not accepted by the API; use the CLI to read local images"
            )
        raise ImageError("image reference must be an http(s) URL or a base64 data URI")

    def from_data_uri(self, value: str, *, source: str = "inline") -> ImageBlob:
        match = _DATA_URI_RE.match(value)
        if not match:
            raise ImageError("malformed data URI")
        declared_mime = (match.group("mime") or "application/octet-stream").lower()
        if not match.group("base64"):
            raise ImageError("data URI must use base64 encoding")
        if declared_mime != "application/octet-stream" and not declared_mime.startswith("image/"):
            raise UnsupportedImageError(f"data URI is not an image: {declared_mime}")
        payload = re.sub(r"\s+", "", match.group("data"))
        try:
            data = base64.b64decode(payload, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ImageError("data URI contains invalid base64") from exc
        if not data:
            raise ImageError("image is empty")
        return self.from_bytes(data, source=source)

    async def from_url(self, url: str) -> ImageBlob:
        current = url
        for hop in range(_MAX_REDIRECTS + 1):
            await self._validate_url(current)
            next_url: str | None = None
            try:
                async with self._request(current) as response:
                    if response.status_code in _REDIRECT_STATUSES:
                        if hop >= _MAX_REDIRECTS:
                            raise ImageError("image URL redirected too many times")
                        location = response.headers.get("location")
                        if not location:
                            raise ImageError("image URL returned a redirect without a Location")
                        next_url = urljoin(current, location)
                    else:
                        response.raise_for_status()
                        self._check_content_type(response.headers.get("content-type", ""))
                        data = await self._read_limited(response)
            except httpx.TimeoutException as exc:
                raise ImageError(
                    "timed out while fetching image", code="image_fetch_timeout", status_code=504
                ) from exc
            except httpx.HTTPStatusError as exc:
                raise ImageError(
                    f"image URL returned HTTP {exc.response.status_code}",
                    code="image_fetch_failed",
                ) from exc
            except httpx.RequestError as exc:
                raise ImageError(
                    f"could not fetch image: {type(exc).__name__}", code="image_fetch_failed"
                ) from exc

            if next_url is None:
                host = urlsplit(current).hostname or "url"
                return self.from_bytes(data, source=host)
            current = next_url
        raise ImageError("image URL redirected too many times")  # pragma: no cover

    def from_bytes(self, data: bytes, *, source: str = "inline") -> ImageBlob:
        if not data:
            raise ImageError("image is empty")
        if len(data) > self.settings.image_max_bytes:
            raise ImageTooLargeError(
                f"image is larger than {self.settings.image_max_bytes} bytes",
                details={"bytes": len(data), "limit": self.settings.image_max_bytes},
            )
        if _looks_like_svg(data):
            raise UnsupportedImageError(
                "SVG images are not supported; render them to PNG/JPEG first"
            )

        try:
            with Image.open(io.BytesIO(data)) as opened:
                original_width, original_height = opened.size
                if original_width * original_height > self.settings.image_max_pixels:
                    raise ImageTooLargeError(
                        f"image has more than {self.settings.image_max_pixels} pixels",
                        details={
                            "width": original_width,
                            "height": original_height,
                            "limit": self.settings.image_max_pixels,
                        },
                    )
                source_format = (opened.format or "").upper()
                notes: list[str] = []
                if getattr(opened, "is_animated", False):
                    notes.append(f"animated {source_format or 'image'}: first frame used")
                    opened.seek(0)
                opened.load()
                image = ImageOps.exif_transpose(opened)
                if image is opened:
                    image = image.copy()
                image = self._flatten(image)
                image.thumbnail(
                    (self.settings.image_max_dim, self.settings.image_max_dim),
                    Image.Resampling.LANCZOS,
                )
                normalized, mime_type = self._encode(image, source_format)
        except ImageTooLargeError:
            raise
        except Image.DecompressionBombError as exc:
            raise ImageTooLargeError("image exceeds Pillow's decompression-bomb limit") from exc
        except UnidentifiedImageError as exc:
            raise UnsupportedImageError(
                "unsupported or corrupt image format; use PNG, JPEG, WebP, GIF, BMP, or TIFF"
            ) from exc
        except OSError as exc:
            raise ImageError(f"could not decode image: {exc}") from exc

        return ImageBlob(
            data=normalized,
            mime_type=mime_type,
            width=image.width,
            height=image.height,
            sha256=_sha256(normalized),
            original_sha256=_sha256(data),
            source=source,
            original_width=original_width,
            original_height=original_height,
            original_bytes=len(data),
            notes=tuple(notes),
        )

    def from_path(self, path: str) -> ImageBlob:
        """Read a local image. Only used by the CLI, never by the HTTP API."""

        from pathlib import Path

        candidate = Path(path)
        try:
            data = candidate.read_bytes()
        except OSError as exc:
            raise ImageError(f"could not read image {candidate}: {exc}") from exc
        return self.from_bytes(data, source="file")

    def _request(self, url: str) -> Any:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.settings.image_fetch_timeout_s),
                follow_redirects=False,
                headers={"User-Agent": _USER_AGENT, "Accept": "image/*,*/*;q=0.8"},
            )
        return self._client.stream(
            "GET",
            url,
            timeout=httpx.Timeout(self.settings.image_fetch_timeout_s),
        )

    async def _read_limited(self, response: httpx.Response) -> bytes:
        declared = response.headers.get("content-length")
        if declared:
            try:
                if int(declared) > self.settings.image_max_bytes:
                    raise ImageTooLargeError(
                        f"image is larger than {self.settings.image_max_bytes} bytes"
                    )
            except ValueError:
                pass
        buffer = bytearray()
        async for chunk in response.aiter_bytes():
            buffer.extend(chunk)
            if len(buffer) > self.settings.image_max_bytes:
                raise ImageTooLargeError(
                    f"image is larger than {self.settings.image_max_bytes} bytes"
                )
        if not buffer:
            raise ImageError("image response was empty")
        return bytes(buffer)

    def _check_content_type(self, content_type: str) -> None:
        media_type = content_type.split(";", 1)[0].strip().lower()
        if not media_type:
            return
        if media_type.startswith("image/"):
            return
        if media_type in {"application/octet-stream", "binary/octet-stream"}:
            return
        raise UnsupportedImageError(
            f"image URL returned {media_type!r}, not an image",
            details={"content_type": media_type},
        )

    async def _validate_url(self, url: str) -> None:
        if len(url) > 2048:
            raise ImageError("image URL is too long")
        try:
            parts = urlsplit(url)
            port = parts.port or (443 if parts.scheme == "https" else 80)
        except ValueError as exc:
            raise ImageError("image URL is malformed") from exc
        if parts.scheme not in {"http", "https"}:
            raise ImageError("image URL must use http or https")
        if parts.username or parts.password:
            raise ImageError("image URL must not contain credentials")
        host = (parts.hostname or "").strip().rstrip(".").lower()
        if not host:
            raise ImageError("image URL has no hostname")
        if self.settings.allow_private_image_urls:
            return
        if host in _BLOCKED_HOSTS or any(
            host.endswith(suffix) for suffix in _BLOCKED_HOST_SUFFIXES
        ):
            raise ImageError("image URL points to a private or reserved host")
        try:
            addresses = await _resolve_host(host, port)
        except socket.gaierror as exc:
            raise ImageError(f"could not resolve image host {host!r}") from exc
        if not addresses:
            raise ImageError(f"could not resolve image host {host!r}")
        blocked = [address for address in addresses if not _is_public_address(address)]
        if blocked:
            raise ImageError(
                "image URL resolves to a private or reserved address",
                details={"host": host},
            )

    def _flatten(self, image: Image.Image) -> Image.Image:
        if image.mode in {"RGBA", "LA"} or (image.mode == "P" and "transparency" in image.info):
            rgba = image.convert("RGBA")
            background = Image.new("RGB", rgba.size, (255, 255, 255))
            background.paste(rgba, mask=rgba.getchannel("A"))
            return background
        return image.convert("RGB")

    def _encode(self, image: Image.Image, source_format: str) -> tuple[bytes, str]:
        prefer_png = source_format in {"PNG", "WEBP", "GIF"}
        current = image
        for scale in (1.0, 0.85, 0.7, 0.55, 0.4):
            if scale != 1.0:
                size = (max(1, int(image.width * scale)), max(1, int(image.height * scale)))
                current = image.resize(size, Image.Resampling.LANCZOS)
            if prefer_png:
                png = self._encode_png(current)
                if len(png) <= self.settings.image_max_bytes:
                    return png, "image/png"
            for quality in (
                self.settings.image_jpeg_quality,
                78,
                68,
                58,
                48,
            ):
                jpeg = self._encode_jpeg(current, quality)
                if len(jpeg) <= self.settings.image_max_bytes:
                    return jpeg, "image/jpeg"
        raise ImageTooLargeError(
            "image could not be compressed below the configured size limit",
            details={"limit": self.settings.image_max_bytes},
        )

    @staticmethod
    def _encode_png(image: Image.Image) -> bytes:
        buffer = io.BytesIO()
        image.save(buffer, format="PNG", optimize=True)
        return buffer.getvalue()

    @staticmethod
    def _encode_jpeg(image: Image.Image, quality: int) -> bytes:
        buffer = io.BytesIO()
        image.save(
            buffer,
            format="JPEG",
            quality=quality,
            optimize=True,
            progressive=False,
            subsampling=0,
        )
        return buffer.getvalue()


def count_pixels(blobs: Iterable[ImageBlob]) -> int:
    return sum(blob.width * blob.height for blob in blobs)
