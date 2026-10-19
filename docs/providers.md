# Providers

DualSight uses two provider roles:

- **vision** runs the perception pass;
- **text** runs the reasoning pass.

They may be the same provider/model or different ones. Text settings inherit from vision settings when omitted, so the minimal configuration is one vision provider plus one key.

## Selection rules

```text
DUALSIGHT_VISION_PROVIDER = openai | anthropic | gemini | mock
DUALSIGHT_TEXT_PROVIDER   = openai | anthropic | gemini | mock   (default: vision provider)
```

For each role, Base URL and model fall back to the provider default when empty. The text API key falls back to the vision key, then to the provider-native environment variable.

| Provider | Default base URL | Default vision model | Default text model |
| --- | --- | --- | --- |
| `openai` | `https://api.openai.com/v1` | `gpt-4o-mini` | `gpt-4o-mini` |
| `anthropic` | `https://api.anthropic.com/v1` | `claude-3-5-sonnet-latest` | `claude-3-5-haiku-latest` |
| `gemini` | `https://generativelanguage.googleapis.com/v1beta` | `gemini-2.0-flash` | `gemini-2.0-flash` |
| `mock` | none | `mock-vision` | `mock-reasoner` |

Keys are resolved in this order:

1. `DUALSIGHT_VISION_API_KEY` / `DUALSIGHT_TEXT_API_KEY`;
2. provider-native fallback (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`);
3. text role also falls back to the vision role's key.

`DUALSIGHT_MOCK=true` forces both roles to the mock provider and models; it does not require any key.

## OpenAI-compatible

Works with OpenAI and any gateway that implements `POST /chat/completions` (Azure OpenAI-compatible proxies, LiteLLM, vLLM, OpenRouter, local servers, and so on).

```bash
DUALSIGHT_VISION_PROVIDER=openai
DUALSIGHT_VISION_BASE_URL=https://api.openai.com/v1
DUALSIGHT_VISION_API_KEY=sk-...
DUALSIGHT_VISION_MODEL=gpt-4o-mini

DUALSIGHT_TEXT_PROVIDER=openai
DUALSIGHT_TEXT_MODEL=gpt-4o-mini
```

- Auth: `Authorization: Bearer <key>`.
- Images are sent as `image_url` content parts with a data URI.
- Streaming parses SSE `data:` frames.
- Tool definitions, `tool_choice`, assistant tool calls, and tool results pass through in OpenAI shape.
- Client-supplied OpenAI-only fields (`seed`, `response_format`, `logit_bias`, …) are forwarded only to this provider. Prefer `DUALSIGHT_TEXT_EXTRA_BODY` for fixed per-deployment options.

## Anthropic

```bash
DUALSIGHT_VISION_PROVIDER=anthropic
DUALSIGHT_VISION_API_KEY=sk-ant-...
DUALSIGHT_VISION_MODEL=claude-3-5-sonnet-latest

DUALSIGHT_TEXT_PROVIDER=anthropic
DUALSIGHT_TEXT_API_KEY=sk-ant-...
DUALSIGHT_TEXT_MODEL=claude-3-5-haiku-latest
```

- Auth: `x-api-key`, plus `anthropic-version: 2023-06-01`.
- System/developer messages become the top-level `system` string.
- Images become base64 `{"type":"image","source":{...}}` blocks.
- OpenAI `tools` are converted to Anthropic `{name, description, input_schema}`; `tool_choice` maps to `auto`/`any`/`none`/`tool`.
- Assistant tool calls become `tool_use`; tool messages become `tool_result` user blocks.
- Consecutive same-role messages are merged.
- `max_tokens` defaults to 1024 when the request does not set one; temperature is clamped to 0–1.
- Stop reasons map to OpenAI `stop`/`length`/`tool_calls`.

## Gemini

```bash
DUALSIGHT_VISION_PROVIDER=gemini
DUALSIGHT_VISION_API_KEY=...
DUALSIGHT_VISION_MODEL=gemini-2.0-flash

DUALSIGHT_TEXT_PROVIDER=gemini
DUALSIGHT_TEXT_MODEL=gemini-2.0-flash
```

- Auth: `x-goog-api-key`.
- Endpoint: `{base_url}/models/{url-quoted-model}:generateContent`; streaming uses `:streamGenerateContent?alt=sse`.
- A leading `models/` in the model name is stripped before quoting.
- System messages become `systemInstruction`; images become `inlineData{mimeType,data}`.
- Tools become `functionDeclarations`; `tool_choice` becomes `functionCallingConfig` (`AUTO`, `ANY`, `NONE`).
- Usage is read from `usageMetadata`.
- A blocked prompt raises a 400 `upstream_error` rather than returning an empty answer.

## Mock

The mock provider is deterministic and offline. It is designed for the demo, tests, and CI.

```bash
DUALSIGHT_MOCK=true
dualsight serve --mock
```

- Perception selects a canned scenario from the requested profile: card, receipt, chart, UI, diagram, or photo. `auto` uses the card scenario.
- The receipt scenario includes `TOTAL $23.47`, matching the sample image.
- Bridge answers quote the total, visible text, or app state from the evidence block.
- It reports `supports_tools=false`.
- Streaming splits the canned answer into word chunks.

Do not use mock mode to evaluate real model quality.

## Headers and extra body

Both roles support:

```bash
DUALSIGHT_VISION_HEADERS={"X-Project":"dualsight"}
DUALSIGHT_VISION_EXTRA_BODY={"service_tier":"auto"}
DUALSIGHT_TEXT_HEADERS={}
DUALSIGHT_TEXT_EXTRA_BODY={"service_tier":"auto"}
```

Values must be JSON objects. Unknown top-level fields in `extra_body` are sent as-is, so this is the escape hatch for provider-specific parameters that the adapters do not model.

Merge order for OpenAI-compatible payloads is:

```text
provider extra_body  <  client request.extra  <  DualSight core fields
```

Core fields (`model`, `messages`, `stream`, temperature, token limit, tools) always win.

## Timeouts and retries

`DUALSIGHT_*_TIMEOUT_S` applies to each HTTP request. `DUALSIGHT_*_MAX_RETRIES` applies to connection failures, timeouts, 408, 409, 425, 429, 500, 502, 503, and 504. Retries use exponential backoff capped at 4 seconds. A streaming response is never retried after bytes have been consumed.

Auth failures (401/403), invalid requests (other 4xx), and an exhausted 429 are surfaced as typed errors, not retried indefinitely.

## Using two different providers

The common setup is a strong vision model plus a cheap text model:

```bash
DUALSIGHT_VISION_PROVIDER=openai
DUALSIGHT_VISION_MODEL=gpt-4o

DUALSIGHT_TEXT_PROVIDER=anthropic
DUALSIGHT_TEXT_API_KEY=sk-ant-...
DUALSIGHT_TEXT_MODEL=claude-3-5-haiku-latest
```

No adapter shares state; each role gets its own HTTP client, and both are closed by the app lifespan.

## Adding a provider

See [CONTRIBUTING.md](../CONTRIBUTING.md#adding-a-provider). The provider contract is in `src/dualsight/providers/base.py`: `name`, `model`, `supports_tools`, `complete()`, `stream()`, `aclose()`.
