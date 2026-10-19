"""Command line interface: serve, ask, perceive, doctor."""

from __future__ import annotations

import argparse
import asyncio
import json
import platform
import sys
from collections.abc import Sequence
from typing import Any

from . import __version__
from .bridge import Bridge
from .cache import build_cache
from .config import Settings, load_settings, redacted_settings, validate_settings
from .errors import ConfigError, DualSightError
from .image_intake import ImageIntake
from .log import setup_logging
from .providers import build_providers


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dualsight",
        description="Give text-only LLMs eyes through a second vision model.",
    )
    parser.add_argument("--version", action="version", version=f"dualsight {__version__}")
    parser.add_argument(
        "--env-file",
        default=None,
        help="Path to a .env file (default: .env in the current directory).",
    )
    sub = parser.add_subparsers(dest="command")

    serve = sub.add_parser("serve", help="Start the HTTP API and demo.")
    serve.add_argument("--host", default=None)
    serve.add_argument("--port", type=int, default=None)
    serve.add_argument("--mock", action="store_true", help="Force offline mock providers.")
    serve.add_argument(
        "--no-demo",
        action="store_true",
        help="Disable the bundled browser demo.",
    )

    ask = sub.add_parser("ask", help="Ask the vision model directly about an image.")
    ask.add_argument("image", help="Local path, data URI, or http(s) URL.")
    ask.add_argument("question", nargs="+", help="Question about the image.")
    ask.add_argument("--profile", default=None)
    ask.add_argument("--detail", default=None)
    ask.add_argument("--json", action="store_true", dest="as_json")

    perceive = sub.add_parser(
        "perceive", help="Run the perception pass and print structured evidence."
    )
    perceive.add_argument("image", help="Local path, data URI, or http(s) URL.")
    perceive.add_argument("--profile", default=None)
    perceive.add_argument("--detail", default=None)
    perceive.add_argument("--no-cache", action="store_true")
    perceive.add_argument("--json", action="store_true", dest="as_json")

    doctor = sub.add_parser("doctor", help="Print effective configuration and check it.")
    doctor.add_argument("--json", action="store_true", dest="as_json")

    return parser


def _load(args: argparse.Namespace) -> Settings:
    settings = load_settings(env_file=args.env_file)
    if args.command == "serve":
        overrides: dict[str, Any] = {}
        if args.host:
            overrides["host"] = args.host
        if args.port:
            overrides["port"] = args.port
        if args.mock:
            overrides["mock"] = True
        if args.no_demo:
            overrides["demo_enabled"] = False
        settings = settings.with_overrides(**overrides)
    validate_settings(settings)
    return settings


async def _bridge_for(settings: Settings) -> tuple[Bridge, ImageIntake]:
    vision, text = build_providers(settings)
    cache = build_cache(settings)
    intake = ImageIntake(settings)
    bridge = Bridge(settings, vision, text, cache, intake=intake)
    return bridge, intake


async def _load_image(intake: ImageIntake, reference: str):  # type: ignore[no-untyped-def]
    import os

    if os.path.exists(reference):
        return intake.from_path(reference)
    return await intake.from_reference(reference)


async def _ask(settings: Settings, args: argparse.Namespace) -> int:
    bridge, intake = await _bridge_for(settings)
    try:
        image = await _load_image(intake, args.image)
        answer = await bridge.ask_vision(
            image,
            " ".join(args.question),
            profile=args.profile,
            detail=args.detail,
        )
        if args.as_json:
            print(
                json.dumps(
                    {
                        "answer": answer.text,
                        "finish_reason": answer.finish_reason,
                        "usage": answer.usage.to_dict() if answer.usage else None,
                        "provider": answer.provider,
                        "model": answer.model,
                    },
                    indent=2 if settings.pretty_json else None,
                    ensure_ascii=False,
                )
            )
        else:
            print(answer.text)
            if answer.usage and answer.usage.total_tokens:
                print(
                    f"\n[tokens: {answer.usage.total_tokens} · {answer.provider}/{answer.model}]",
                    file=sys.stderr,
                )
        return 0
    finally:
        await bridge.aclose()


async def _perceive(settings: Settings, args: argparse.Namespace) -> int:
    bridge, intake = await _bridge_for(settings)
    try:
        image = await _load_image(intake, args.image)
        result = await bridge.perceive(
            image,
            profile=args.profile,
            detail=args.detail,
            bypass_cache=args.no_cache,
        )
        if args.as_json:
            print(
                json.dumps(
                    {
                        "image": result.trace.public(include_perception=True),
                        "perception": result.trace.perception.to_dict()
                        if result.trace.perception
                        else None,
                        "evidence": result.evidence,
                    },
                    indent=2 if settings.pretty_json else None,
                    ensure_ascii=False,
                )
            )
        else:
            print(result.evidence)
        return 0
    finally:
        await bridge.aclose()


def _doctor(settings: Settings, args: argparse.Namespace) -> int:
    report: dict[str, Any] = {
        "dualsight": __version__,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "config": redacted_settings(settings),
        "checks": [],
    }
    ok = True
    for role in ("vision", "text"):
        provider = getattr(settings, f"{role}_provider")
        key = getattr(settings, f"{role}_api_key")
        if settings.mock or provider == "mock":
            report["checks"].append(
                {"name": f"{role}_provider", "status": "ok", "detail": "mock mode"}
            )
        elif not key:
            ok = False
            report["checks"].append(
                {
                    "name": f"{role}_provider",
                    "status": "error",
                    "detail": f"missing API key for {provider}",
                }
            )
        else:
            report["checks"].append(
                {"name": f"{role}_provider", "status": "ok", "detail": provider}
            )
    report["ok"] = ok
    if args.as_json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print(f"DualSight {__version__} on Python {platform.python_version()}")
        print(f"  vision : {settings.vision_provider} / {settings.vision_model}")
        print(f"  text   : {settings.text_provider} / {settings.text_model}")
        print(f"  cache  : {settings.cache}")
        print(f"  demo   : {settings.demo_enabled}")
        for check in report["checks"]:
            mark = "ok" if check["status"] == "ok" else "!!"
            print(f"  [{mark}] {check['name']}: {check['detail']}")
    return 0 if ok else 1


def _serve(settings: Settings) -> int:
    import uvicorn

    from .server import create_app

    setup_logging(settings.log_level, json_mode=settings.log_json)
    app = create_app(settings)
    host = settings.host
    display_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
    print(f"DualSight {__version__} listening on http://{display_host}:{settings.port}")
    if settings.demo_enabled:
        print(f"Demo: http://{display_host}:{settings.port}/  ·  API docs: /docs")
    if settings.mock:
        print("Mode: mock (no API keys, network, or real models)")
    uvicorn.run(
        app,
        host=host,
        port=settings.port,
        log_level=settings.log_level.lower(),
        access_log=settings.log_level.upper() == "DEBUG",
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 2
    try:
        settings = _load(args)
        setup_logging(settings.log_level, json_mode=settings.log_json)
        if args.command == "serve":
            return _serve(settings)
        if args.command == "ask":
            return asyncio.run(_ask(settings, args))
        if args.command == "perceive":
            return asyncio.run(_perceive(settings, args))
        if args.command == "doctor":
            return _doctor(settings, args)
    except ConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2
    except DualSightError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    parser.print_help()
    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
