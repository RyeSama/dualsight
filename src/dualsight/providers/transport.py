"""Shared HTTP plumbing for provider adapters."""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from ..errors import (
    AuthError,
    UpstreamError,
    UpstreamRateLimitError,
    UpstreamTimeoutError,
)

_RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}


async def _stream_detail(response: httpx.Response) -> str:
    with contextlib.suppress(httpx.HTTPError):
        await response.aread()
    return _safe_detail(response)


def _safe_detail(response: httpx.Response) -> str:
    try:
        data = response.json()
    except (json.JSONDecodeError, ValueError):
        text = response.text.strip()
        return text[:400] if text else response.reason_phrase
    if isinstance(data, dict):
        error = data.get("error")
        if isinstance(error, dict):
            message = error.get("message") or error.get("type")
            if message:
                return str(message)[:400]
        if isinstance(error, str):
            return error[:400]
        message = data.get("message")
        if message:
            return str(message)[:400]
    return str(data)[:400]


class ProviderHTTP:
    """A very small retrying JSON/SSE client.

    Retries cover connection errors, timeouts, 429, and 5xx before any response
    bytes are consumed.  Once an SSE stream has started we surface the failure
    instead of silently replaying a partially delivered answer.
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str | None,
        timeout_s: float,
        max_retries: int,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout_s = timeout_s
        self.max_retries = max_retries
        self.headers = dict(headers or {})
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_s, connect=min(10.0, timeout_s)),
            follow_redirects=False,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    def _request_headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        headers.update(self.headers)
        if extra:
            headers.update(extra)
        return headers

    async def _sleep(self, attempt: int) -> None:
        await asyncio.sleep(min(4.0, 0.4 * (2**attempt)))

    async def post_json(
        self,
        url: str,
        payload: dict[str, Any],
        *,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                response = await self._client.post(
                    url, json=payload, headers=self._request_headers(headers)
                )
            except httpx.TimeoutException as exc:
                last_error = exc
                if attempt < self.max_retries:
                    await self._sleep(attempt)
                    continue
                raise UpstreamTimeoutError("upstream provider timed out") from exc
            except httpx.HTTPError as exc:
                last_error = exc
                if attempt < self.max_retries:
                    await self._sleep(attempt)
                    continue
                raise UpstreamError(
                    f"cannot reach upstream provider: {type(exc).__name__}"
                ) from exc

            if response.status_code == 401 or response.status_code == 403:
                raise AuthError(
                    f"upstream provider rejected the API key ({response.status_code}): "
                    f"{_safe_detail(response)}"
                )
            if response.status_code == 429:
                if attempt < self.max_retries:
                    await self._sleep(attempt)
                    continue
                raise UpstreamRateLimitError(
                    f"upstream rate limit: {_safe_detail(response)}",
                    retry_after=response.headers.get("retry-after"),
                )
            if response.status_code in _RETRYABLE_STATUS and attempt < self.max_retries:
                await self._sleep(attempt)
                continue
            if response.status_code >= 400:
                raise UpstreamError(
                    f"upstream returned HTTP {response.status_code}: {_safe_detail(response)}",
                    status_code=response.status_code,
                )
            try:
                data = response.json()
            except (json.JSONDecodeError, ValueError) as exc:
                raise UpstreamError("upstream returned a non-JSON response") from exc
            if not isinstance(data, dict):
                raise UpstreamError("upstream returned an unexpected JSON shape")
            return data
        raise UpstreamError(
            f"upstream request failed after retries: {type(last_error).__name__ if last_error else 'unknown'}"
        )

    async def stream_sse(
        self,
        url: str,
        payload: dict[str, Any],
        *,
        headers: dict[str, str] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield decoded JSON objects from a ``text/event-stream`` response."""

        response: httpx.Response | None = None
        for attempt in range(self.max_retries + 1):
            request = self._client.build_request(
                "POST",
                url,
                json=payload,
                headers=self._request_headers({"Accept": "text/event-stream", **(headers or {})}),
            )
            try:
                response = await self._client.send(request, stream=True)
            except httpx.TimeoutException as exc:
                if attempt < self.max_retries:
                    await self._sleep(attempt)
                    continue
                raise UpstreamTimeoutError("upstream provider timed out") from exc
            except httpx.HTTPError as exc:
                if attempt < self.max_retries:
                    await self._sleep(attempt)
                    continue
                raise UpstreamError(
                    f"cannot reach upstream provider: {type(exc).__name__}"
                ) from exc

            if response.status_code == 401 or response.status_code == 403:
                detail = await _stream_detail(response)
                await response.aclose()
                raise AuthError(
                    f"upstream provider rejected the API key ({response.status_code}): {detail}"
                )
            if response.status_code == 429:
                detail = await _stream_detail(response)
                retry_after = response.headers.get("retry-after")
                await response.aclose()
                if attempt < self.max_retries:
                    await self._sleep(attempt)
                    continue
                raise UpstreamRateLimitError(
                    f"upstream rate limit: {detail}",
                    retry_after=retry_after,
                )
            if response.status_code in _RETRYABLE_STATUS and attempt < self.max_retries:
                await response.aclose()
                await self._sleep(attempt)
                continue
            if response.status_code >= 400:
                detail = await _stream_detail(response)
                await response.aclose()
                raise UpstreamError(
                    f"upstream returned HTTP {response.status_code}: {detail}",
                    status_code=response.status_code,
                )
            break

        if response is None:
            raise UpstreamError("upstream stream never started")

        try:
            async for line in response.aiter_lines():
                line = line.strip()
                if not line or line.startswith(":") or not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if not data or data == "[DONE]":
                    if data == "[DONE]":
                        break
                    continue
                try:
                    event = json.loads(data)
                except json.JSONDecodeError:
                    continue
                if isinstance(event, dict):
                    yield event
        except httpx.HTTPError as exc:
            raise UpstreamError(f"upstream stream failed: {type(exc).__name__}") from exc
        finally:
            await response.aclose()
