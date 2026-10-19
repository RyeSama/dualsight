"""Typed errors that map cleanly onto OpenAI-style HTTP error bodies."""

from __future__ import annotations

from typing import Any


class DualSightError(Exception):
    """Base class for all expected DualSight failures."""

    status_code = 500
    error_type = "server_error"
    code = "internal_error"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        status_code: int | None = None,
        param: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        if status_code is not None:
            self.status_code = status_code
        self.param = param
        self.details = details or {}

    def to_openai(self) -> dict[str, Any]:
        error: dict[str, Any] = {
            "message": self.message,
            "type": self.error_type,
            "param": self.param,
            "code": self.code,
        }
        if self.details:
            error["details"] = self.details
        return {"error": error}

    def __str__(self) -> str:  # pragma: no cover - inherited behaviour is fine
        return self.message


class ConfigError(DualSightError):
    status_code = 500
    error_type = "configuration_error"
    code = "configuration_error"


class AuthError(DualSightError):
    status_code = 401
    error_type = "authentication_error"
    code = "invalid_api_key"


class ForbiddenError(DualSightError):
    status_code = 403
    error_type = "permission_error"
    code = "permission_denied"


class NotFoundError(DualSightError):
    status_code = 404
    error_type = "invalid_request_error"
    code = "not_found"


class InvalidRequestError(DualSightError):
    status_code = 400
    error_type = "invalid_request_error"
    code = "invalid_request"


class PayloadTooLargeError(DualSightError):
    status_code = 413
    error_type = "invalid_request_error"
    code = "payload_too_large"


class ImageError(DualSightError):
    status_code = 400
    error_type = "invalid_request_error"
    code = "invalid_image"


class UnsupportedImageError(ImageError):
    code = "unsupported_image"


class ImageTooLargeError(ImageError):
    status_code = 413
    code = "image_too_large"


class UpstreamError(DualSightError):
    status_code = 502
    error_type = "upstream_error"
    code = "upstream_error"


class UpstreamTimeoutError(UpstreamError):
    status_code = 504
    code = "upstream_timeout"


class UpstreamRateLimitError(UpstreamError):
    status_code = 503
    code = "upstream_rate_limited"

    def __init__(
        self,
        message: str,
        *,
        retry_after: str | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(message, **kwargs)
        self.retry_after = retry_after
        if retry_after:
            self.details.setdefault("retry_after", retry_after)


class VisionError(UpstreamError):
    code = "vision_error"


class TextModelError(UpstreamError):
    code = "text_model_error"


class NoImagesFound(DualSightError):
    status_code = 400
    error_type = "invalid_request_error"
    code = "no_images_found"
