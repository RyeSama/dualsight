# Contributing to DualSight

Thanks for helping. This project is intentionally small and readable; the goal is a dependable bridge, not a framework.

## Development setup

```bash
git clone https://github.com/RyeSama/dualsight.git
cd dualsight
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

Requires Python 3.10+.

## Checks

```bash
ruff check src tests
ruff format --check src tests
pytest
```

All three must pass. The suite is offline and needs no API keys. If you add a provider or a request shape, add a test that exercises the conversion without real HTTP.

## Design rules

- **The reasoning model never sees pixels.** Keep image parts out of the text-provider request; evidence is text.
- **Evidence is untrusted data.** Preserve the nonce delimiters, delimiter escaping, and the bridge note. Do not remove the warning because a prompt “works better” without it.
- **Degrade honestly.** A failed perception must produce an explicit `status="unavailable"` block (or a clear error when `on_vision_error=fail`), never invented content.
- **Cache keys must be complete.** If a change alters perception output, bump `PROMPT_VERSION` in `prompts.py`; the cache key includes it.
- **Provider-neutral core.** Provider-specific payload fields belong in the adapter or `*_extra_body`, not in the bridge.
- **No new runtime dependencies without a strong reason.** The current runtime is FastAPI, httpx, Pillow, Pydantic, and uvicorn.

## Adding a provider

1. Implement `ChatProvider` from `src/dualsight/providers/base.py` (`name`, `model`, `supports_tools`, `complete`, `stream`, `aclose`).
2. Reuse `ProviderHTTP` from `providers/transport.py` for retries, error mapping, and SSE framing.
3. Add the provider to `PROVIDERS`, defaults in `config.py`, and `build_provider()`.
4. Add request/response/stream tests with a fake `post_json` or `stream_sse`.
5. Document the environment variables in `docs/providers.md` and `.env.example`.

## Pull requests

- Keep changes focused; separate refactors from behavior changes.
- Include tests for bug fixes and new behavior.
- Update README/docs when user-visible behavior, configuration, or API shapes change.
- Use clear commit messages, for example `fix: keep evidence footer when truncating`.
- Do not commit secrets, `.env`, generated caches, or local screenshots.

## Reporting security issues

Do not open a public issue. Follow [SECURITY.md](SECURITY.md).
