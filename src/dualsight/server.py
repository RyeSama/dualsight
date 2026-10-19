"""FastAPI application: OpenAI-compatible gateway plus native endpoints."""

from __future__ import annotations

import json
import secrets
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .bridge import Bridge, PreparedRequest
from .cache import build_cache
from .chat import ChatRequest, Usage
from .config import Settings, load_settings, validate_settings
from .errors import AuthError, DualSightError, InvalidRequestError, NoImagesFound, NotFoundError
from .image_intake import ImageIntake
from .providers import build_providers
from .wire import parse_chat_request, parse_native_options

_STATIC_DIR = Path(__file__).with_name("static")


def _sse(data: Any) -> str:
    if isinstance(data, str):
        return f"data: {data}\n\n"
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return f"data: {payload}\n\n"


def _chunk(
    *,
    chunk_id: str,
    created: int,
    model: str,
    delta: dict[str, Any] | None = None,
    finish_reason: str | None = None,
    usage: dict[str, int] | None = None,
    dualsight: dict[str, Any] | None = None,
) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": chunk_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "system_fingerprint": f"dualsight-{__version__}",
        "choices": (
            [
                {
                    "index": 0,
                    "delta": delta or {},
                    "logprobs": None,
                    "finish_reason": finish_reason,
                }
            ]
            if finish_reason != "usage"
            else []
        ),
    }
    if usage is not None:
        data["usage"] = usage
    if dualsight is not None:
        data["dualsight"] = dualsight
    return data


async def _read_json(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except Exception as exc:  # noqa: BLE001 - client input
        raise InvalidRequestError("request body must be valid JSON") from exc
    if not isinstance(payload, dict):
        raise InvalidRequestError("request body must be a JSON object")
    return payload


def _extract_image(payload: dict[str, Any]) -> str:
    for key in ("image", "image_url", "url"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value
        if isinstance(value, dict) and isinstance(value.get("url"), str):
            return value["url"]
    raise InvalidRequestError(
        "an image is required: pass data:<mime>;base64,... or an http(s) URL",
        param="image",
    )


def create_app(
    settings: Settings | None = None,
    *,
    bridge: Bridge | None = None,
) -> FastAPI:
    settings = settings or load_settings()
    validate_settings(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        owned = False
        if app.state.bridge is None:
            vision, text = build_providers(settings)
            cache = build_cache(settings)
            app.state.bridge = Bridge(
                settings,
                vision,
                text,
                cache,
                intake=app.state.intake,
            )
            owned = True
        yield
        if owned:
            await app.state.bridge.aclose()
        await app.state.intake.close()

    app = FastAPI(
        title="DualSight",
        version=__version__,
        description=(
            "Give text-only LLMs eyes. DualSight runs a vision perception pass, "
            "turns the result into bounded evidence, and injects it into a text model."
        ),
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.bridge = bridge
    app.state.intake = ImageIntake(settings)

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(settings.cors_origins),
            allow_credentials=False,
            allow_methods=["GET", "POST", "OPTIONS"],
            allow_headers=["*"],
        )

    @app.middleware("http")
    async def body_limit(request: Request, call_next):  # type: ignore[no-untyped-def]
        if request.method == "POST":
            length = request.headers.get("content-length")
            if length and length.isdigit() and int(length) > settings.max_body_bytes:
                return JSONResponse(
                    {
                        "error": {
                            "message": (
                                f"request body too large: {length} bytes "
                                f"(limit {settings.max_body_bytes})"
                            ),
                            "type": "invalid_request_error",
                            "param": None,
                            "code": "payload_too_large",
                        }
                    },
                    status_code=413,
                )
        return await call_next(request)

    def check_auth(request: Request) -> None:
        if not settings.api_key:
            return
        header = request.headers.get("authorization", "")
        expected = f"Bearer {settings.api_key}"
        if not secrets.compare_digest(header, expected):
            raise AuthError("incorrect API key provided")

    @app.exception_handler(DualSightError)
    async def dualsight_error(request: Request, exc: DualSightError) -> JSONResponse:
        return JSONResponse(exc.to_openai(), status_code=exc.status_code)

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception) -> JSONResponse:
        # Avoid leaking internals; DualSightError is handled by its own handler.
        return JSONResponse(
            {
                "error": {
                    "message": "internal server error",
                    "type": "server_error",
                    "param": None,
                    "code": "internal_error",
                }
            },
            status_code=500,
        )

    @app.get("/healthz")
    async def healthz() -> dict[str, Any]:
        return {
            "status": "ok",
            "name": "dualsight",
            "version": __version__,
            "mock": settings.mock,
        }

    @app.get("/v1/models")
    async def models(request: Request) -> dict[str, Any]:
        check_auth(request)
        text_model = settings.text_model or "dualsight"
        data = [
            {
                "id": text_model,
                "object": "model",
                "created": 0,
                "owned_by": "dualsight",
                "metadata": {"role": "text", "provider": settings.text_provider},
            }
        ]
        if settings.vision_model and settings.vision_model != text_model:
            data.append(
                {
                    "id": settings.vision_model,
                    "object": "model",
                    "created": 0,
                    "owned_by": "dualsight",
                    "metadata": {"role": "vision", "provider": settings.vision_provider},
                }
            )
        return {"object": "list", "data": data}

    @app.get("/v1/dualsight/info")
    async def info(request: Request) -> dict[str, Any]:
        check_auth(request)
        return {
            "name": "DualSight",
            "version": __version__,
            "description": "A perception sidecar that gives text-only LLMs eyes.",
            "vision": {
                "provider": settings.vision_provider,
                "model": settings.vision_model,
            },
            "text": {
                "provider": settings.text_provider,
                "model": settings.text_model,
            },
            "features": {
                "streaming": True,
                "tools_passthrough": True,
                "perception_cache": settings.cache,
                "profiles": list(
                    ("auto", "photo", "document", "ui", "chart", "receipt", "diagram")
                ),
            },
            "endpoints": {
                "chat": "/v1/chat/completions",
                "perceive": "/v1/perceive",
                "vision_ask": "/v1/vision/ask",
                "docs": "/docs",
            },
        }

    @app.post("/v1/chat/completions")
    async def chat_completions(request: Request):  # type: ignore[no-untyped-def]
        check_auth(request)
        payload = await _read_json(request)
        parsed = await parse_chat_request(payload, intake=app.state.intake, settings=settings)
        prepared = await app.state.bridge.prepare(parsed.request, options=parsed.options)
        model = settings.text_model or app.state.bridge.text_provider.model
        if parsed.stream:
            return _stream_response(
                app.state.bridge,
                parsed.request,
                parsed.options,
                prepared,
                model=model,
            )
        result = await app.state.bridge.chat(
            parsed.request, options=parsed.options, prepared=prepared
        )
        message: dict[str, Any] = {
            "role": "assistant",
            "content": result.text or None,
        }
        if result.tool_calls:
            message["tool_calls"] = result.tool_calls
        body: dict[str, Any] = {
            "id": f"chatcmpl-{uuid.uuid4().hex}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model,
            "system_fingerprint": f"dualsight-{__version__}",
            "choices": [
                {
                    "index": 0,
                    "message": message,
                    "logprobs": None,
                    "finish_reason": result.finish_reason or "stop",
                }
            ],
            "dualsight": result.prepared.meta(include_perception=parsed.options.include_perception),
        }
        if result.usage is not None:
            body["usage"] = result.usage.to_dict()
        return JSONResponse(body)

    def _stream_response(
        bridge: Bridge,
        request: ChatRequest,
        options: Any,
        prepared: PreparedRequest,
        *,
        model: str,
    ) -> StreamingResponse:
        chunk_id = f"chatcmpl-{uuid.uuid4().hex}"
        created = int(time.time())
        meta = prepared.meta(include_perception=options.include_perception)

        async def generate() -> AsyncIterator[str]:
            first = True
            finish_reason = "stop"
            usage: Usage | None = None
            try:
                async for chunk in bridge.stream(request, options=options, prepared=prepared):
                    if chunk.prepared is not None and first:
                        continue
                    if first:
                        first = False
                        yield _sse(
                            _chunk(
                                chunk_id=chunk_id,
                                created=created,
                                model=model,
                                delta={"role": "assistant", "content": ""},
                            )
                        )
                    if chunk.delta_text:
                        yield _sse(
                            _chunk(
                                chunk_id=chunk_id,
                                created=created,
                                model=model,
                                delta={"content": chunk.delta_text},
                            )
                        )
                    if chunk.tool_calls_delta:
                        yield _sse(
                            _chunk(
                                chunk_id=chunk_id,
                                created=created,
                                model=model,
                                delta={"tool_calls": chunk.tool_calls_delta},
                            )
                        )
                    if chunk.finish_reason:
                        finish_reason = chunk.finish_reason
                    if chunk.usage is not None:
                        usage = chunk.usage
            except Exception as exc:  # noqa: BLE001 - stream already started
                from .errors import DualSightError

                message = str(exc) if isinstance(exc, DualSightError) else "internal error"
                code = exc.code if isinstance(exc, DualSightError) else "internal_error"
                yield _sse(
                    {
                        "error": {
                            "message": message,
                            "type": "upstream_error",
                            "param": None,
                            "code": code,
                        }
                    }
                )
                yield _sse("[DONE]")
                return

            if first:
                yield _sse(
                    _chunk(
                        chunk_id=chunk_id,
                        created=created,
                        model=model,
                        delta={"role": "assistant", "content": ""},
                    )
                )
            yield _sse(
                _chunk(
                    chunk_id=chunk_id,
                    created=created,
                    model=model,
                    delta={},
                    finish_reason=finish_reason,
                    dualsight=meta,
                )
            )
            yield _sse(
                _chunk(
                    chunk_id=chunk_id,
                    created=created,
                    model=model,
                    delta={},
                    finish_reason="usage",
                    usage=usage.to_dict() if usage else None,
                )
            )
            yield _sse("[DONE]")

        headers = {
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "X-DualSight-Nonce": prepared.nonce,
            "X-DualSight-Images": str(prepared.image_count),
            "X-DualSight-Degraded": "true" if prepared.degraded else "false",
        }
        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers=headers,
        )

    @app.post("/v1/perceive")
    async def perceive(request: Request) -> dict[str, Any]:
        check_auth(request)
        payload = await _read_json(request)
        profile, detail, bypass = parse_native_options(payload)
        try:
            reference = _extract_image(payload)
        except InvalidRequestError as exc:
            raise NoImagesFound("an image is required") from exc
        image = await app.state.intake.from_reference(reference, source="perceive")
        label = payload.get("label") if isinstance(payload.get("label"), str) else "image"
        result = await app.state.bridge.perceive(
            image,
            label=label,
            profile=profile,
            detail=detail,
            bypass_cache=bypass,
        )
        return {
            "image": result.trace.public(include_perception=True),
            "perception": result.trace.perception.to_dict() if result.trace.perception else None,
            "evidence": result.evidence,
            "raw_text": result.raw_text,
        }

    @app.post("/v1/vision/ask")
    async def vision_ask(request: Request) -> dict[str, Any]:
        check_auth(request)
        payload = await _read_json(request)
        profile, detail, _ = parse_native_options(payload)
        question = payload.get("question")
        if not isinstance(question, str) or not question.strip():
            raise InvalidRequestError("question is required", param="question")
        image = await app.state.intake.from_reference(_extract_image(payload), source="vision-ask")
        answer = await app.state.bridge.ask_vision(image, question, profile=profile, detail=detail)
        return {
            "answer": answer.text,
            "finish_reason": answer.finish_reason,
            "usage": answer.usage.to_dict() if answer.usage else None,
            "provider": answer.provider,
            "model": answer.model,
        }

    if _STATIC_DIR.is_dir():
        app.mount("/assets", StaticFiles(directory=_STATIC_DIR), name="assets")

    @app.get("/")
    async def index():  # type: ignore[no-untyped-def]
        index_file = _STATIC_DIR / "index.html"
        if settings.demo_enabled and index_file.is_file():
            return FileResponse(index_file, media_type="text/html; charset=utf-8")
        return {
            "name": "DualSight",
            "version": __version__,
            "message": "DualSight is running. See /docs or /v1/dualsight/info.",
            "demo": settings.demo_enabled,
        }

    @app.get("/favicon.svg")
    async def favicon():  # type: ignore[no-untyped-def]
        icon = _STATIC_DIR / "favicon.svg"
        if not icon.is_file():
            raise NotFoundError("favicon not found")
        return FileResponse(icon, media_type="image/svg+xml")

    return app


app = create_app()
