# DualSight

**Give your text-only LLM eyes.**

DualSight is a self-hosted, OpenAI-compatible **perception sidecar**. It sends every image to a second, vision-capable model, turns the result into compact structured evidence, and injects that evidence into the prompt of the text-only model you already use. The reasoning model never receives pixels.

[中文说明](README.zh-CN.md) · [Architecture](docs/architecture.md) · [API reference](docs/api.md) · [Providers](docs/providers.md) · [Perception schema](docs/perception-schema.md)

<p align="center">
  <img src="docs/assets/demo.png" alt="DualSight demo: a receipt question answered with a grounded $23.47 total and the structured OCR evidence beneath" width="1100">
</p>

```text
Client ── image + question ──▶ DualSight ──▶ vision model (second API)
                                   │
                                   ├── structured perception (JSON)
                                   ├── nonce-delimited evidence block
                                   ▼
                            text-only reasoning model ──▶ grounded answer
```

## Why

Text-only models are cheap, fast, well-tooled, and often already integrated. Giving them vision usually means one of three things:

- switching to a multimodal model,
- paying for image tokens on every request,
- or building a one-off image-description prompt.

DualSight makes vision an **infrastructure concern** instead: one OpenAI-compatible endpoint, any text model behind it, a swappable vision provider, and auditable evidence in between. It is useful when you want to add image understanding to an existing agent without changing its model or tool loop.

## Features

- **OpenAI-compatible** `POST /v1/chat/completions`, including streaming and tool-call passthrough.
- **Three native vision backends** — OpenAI-compatible APIs, Anthropic, and Gemini — plus a deterministic offline mock.
- **Structured perception**, not just a caption: summary, verbatim OCR, objects, layout, colors, chart observations, uncertainties, and suggested questions.
- **Question-agnostic perception pass**, so the expensive vision call can be cached and reused across questions.
- **Prompt-injection aware evidence**: random nonce delimiters, an explicit untrusted-data warning, and delimiter escaping.
- **Cache by image content + model + profile + detail + prompt version**; memory, SQLite, or no cache.
- **Concurrent multi-image perception** with per-image failure isolation. Degrade honestly instead of failing the whole request.
- **Image hygiene**: EXIF orientation, alpha flattening, metadata stripping, dimension/byte caps, and SSRF protection for remote URLs.
- **Observability**: `dualsight` metadata on responses, image IDs, cache hits, latency, token usage, and truncation flags.
- **Batteries included**: CLI (`serve`, `ask`, `perceive`, `doctor`), browser demo, synthetic samples, tests, Docker, and CI.

## Quickstart

### 1. Install

Python 3.10 or newer is required.

```bash
git clone https://github.com/RyeSama/dualsight.git
cd dualsight
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e .
```

### 2. Try it offline

No API keys, no network, no model calls:

```bash
dualsight serve --mock
```

Open <http://127.0.0.1:8000/>. Pick a synthetic receipt, chart, UI, or diagram and run the demo. The mock provider returns canned but coherent evidence so the full streaming and rendering path can be exercised offline.

### 3. Connect real models

```bash
cp .env.example .env
```

Set at least one vision key and one text key, for example:

```dotenv
DUALSIGHT_VISION_PROVIDER=openai
DUALSIGHT_VISION_API_KEY=sk-...
DUALSIGHT_VISION_MODEL=gpt-4o-mini

DUALSIGHT_TEXT_PROVIDER=openai
DUALSIGHT_TEXT_API_KEY=sk-...
DUALSIGHT_TEXT_MODEL=gpt-4o-mini
```

Then:

```bash
dualsight serve
```

The API listens on `http://127.0.0.1:8000/v1` by default. Interactive API docs are at `/docs`.

### Docker

```bash
cp .env.example .env
docker compose up --build
```

The demo and API are exposed on port `8000`. See [deployment notes](docs/deployment.md) for a production checklist.

## Use the API

DualSight accepts the OpenAI chat-completions shape. Put the image in a normal `image_url` content part; `data:` URIs and public `http(s)` URLs are supported. Use `model: "dualsight"` (or the model configured for the text provider).

```bash
curl http://127.0.0.1:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $DUALSIGHT_SERVER_API_KEY" \
  -d '{
    "model": "dualsight",
    "messages": [
      {
        "role": "user",
        "content": [
          {"type": "text", "text": "What is the total, and what should I double-check?"},
          {
            "type": "image_url",
            "image_url": {
              "url": "https://example.com/receipt.png",
              "detail": "high"
            }
          }
        ]
      }
    ],
    "dualsight": {
      "profile": "receipt",
      "detail": "standard",
      "include_perception": true
    }
  }'
```

The response is a standard chat completion with an extra `dualsight` object:

```json
{
  "id": "chatcmpl-...",
  "object": "chat.completion",
  "model": "gpt-4o-mini",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "The total is $23.47. The subtotal is $18.50 and tax is $1.76; the amounts add up correctly."
      },
      "finish_reason": "stop"
    }
  ],
  "usage": {"prompt_tokens": 1234, "completion_tokens": 42, "total_tokens": 1276},
  "dualsight": {
    "version": "0.1",
    "nonce": "ef84d0f52c67eccd",
    "image_count": 1,
    "degraded": false,
    "images": [
      {
        "id": "f1b60c5695bd",
        "label": "image-1",
        "source": "example.com",
        "mime_type": "image/png",
        "width": 760,
        "height": 1080,
        "provider": "openai",
        "model": "gpt-4o-mini",
        "profile": "receipt",
        "detail": "standard",
        "cached": false,
        "latency_ms": 1840,
        "prompt_tokens": 607,
        "completion_tokens": 261,
        "truncated": false,
        "error": null
      }
    ],
    "vision_usage": {"prompt_tokens": 607, "completion_tokens": 261, "total_tokens": 868}
  }
}
```

Set `stream: true` for Server-Sent Events. The final content chunk carries the same `dualsight` metadata; a usage-only chunk follows, then `[DONE]`.

### Request options

| Field | Values | Default | Purpose |
| --- | --- | --- | --- |
| `profile` | `auto`, `photo`, `document`, `ui`, `chart`, `receipt`, `diagram` | `auto` | Guides the perception prompt. Pick the closest one for better OCR and layout detail. |
| `detail` | `brief`, `standard`, `deep` | `standard` | Controls how much evidence the vision model is asked for. |
| `bypass_cache` | boolean | `false` | Force a fresh perception pass. |
| `include_perception` | boolean | `false` | Attach the full structured perception JSON to `dualsight.images[].perception`. |

Native endpoints:

| Endpoint | Purpose |
| --- | --- |
| `POST /v1/chat/completions` | OpenAI-compatible bridge endpoint. |
| `POST /v1/perceive` | Run only the perception pass and return evidence + structured JSON. |
| `POST /v1/vision/ask` | Ask the vision model a direct question, without the text model. |
| `GET /v1/models` | List the configured reasoning model. |
| `GET /v1/dualsight/info` | Runtime providers, features, and endpoints. |
| `GET /healthz` | Liveness probe. |

Examples for every endpoint are in [docs/api.md](docs/api.md).

## CLI

```bash
dualsight serve [--host 0.0.0.0] [--port 8000] [--mock] [--no-demo]
dualsight ask receipt.png "What is the total?" --profile receipt
dualsight perceive receipt.png --profile receipt --json
dualsight doctor --json
```

`ask` and `perceive` accept a local path, a `data:` URI, or a public `http(s)` URL. `doctor` prints the effective redacted configuration and checks that each configured provider has a key.

## How the bridge works

1. **Intake** — decode with Pillow, apply EXIF orientation, flatten transparency, strip metadata, downscale to the configured maximum, re-encode, and hash. Remote URLs are checked against private/link-local addresses before fetching.
2. **Perception** — one question-agnostic prompt asks the vision model for a fixed JSON schema. The parser tolerates fenced JSON, nested wrappers, and prose fallbacks.
3. **Render** — the perception is rendered as a bounded text block wrapped in `<<<VISUAL_EVIDENCE nonce>>> ... <<<END_VISUAL_EVIDENCE nonce>>>`, with an explicit note that the content is untrusted data.
4. **Reason** — the evidence is injected into the first system message (or the last user message), images are replaced by text placeholders, and the text model answers. Tool calls pass through when the provider supports them.
5. **Report** — the response carries provenance: provider, model, profile, cache hit, latency, token usage, truncation, and degradation.

The full sequence, cache key, and failure modes are documented in [docs/architecture.md](docs/architecture.md).

## Configuration

All settings use the `DUALSIGHT_` prefix and can come from the process environment or a `.env` file. Process environment wins. See [`.env.example`](.env.example) for the complete list.

| Variable | Default | Notes |
| --- | --- | --- |
| `DUALSIGHT_HOST` / `DUALSIGHT_PORT` | `127.0.0.1` / `8000` | Bind address for `dualsight serve`. |
| `DUALSIGHT_SERVER_API_KEY` | unset | When set, clients must send `Authorization: Bearer <key>`. |
| `DUALSIGHT_VISION_PROVIDER` | `openai` | `openai`, `anthropic`, `gemini`, or `mock`. |
| `DUALSIGHT_VISION_MODEL` | provider default | Any model the provider exposes. |
| `DUALSIGHT_VISION_API_KEY` | unset | Falls back to `DUALSIGHT_API_KEY` and the provider-native key. |
| `DUALSIGHT_TEXT_PROVIDER` | same as vision | Can be a different provider. |
| `DUALSIGHT_TEXT_MODEL` | provider default | The reasoning model. |
| `DUALSIGHT_TEXT_API_KEY` | unset | Falls back to the vision key when both roles use the same base URL. |
| `DUALSIGHT_CACHE` | `memory` | `none`, `memory`, or `sqlite`. |
| `DUALSIGHT_CACHE_PATH` | `.dualsight/cache.sqlite3` | Used by the SQLite backend. |
| `DUALSIGHT_MAX_IMAGES` | `8` | Per-request image cap. |
| `DUALSIGHT_MAX_EVIDENCE_CHARS` | `12000` | Total evidence budget, split across images. |
| `DUALSIGHT_ON_VISION_ERROR` | `degrade` | `degrade` injects an honest "unavailable" block; `fail` returns an error. |
| `DUALSIGHT_INJECT_MODE` | `system` | `system` or `user`. |
| `DUALSIGHT_ALLOW_MODEL_OVERRIDE` | `false` | Let clients choose an arbitrary text model. |
| `DUALSIGHT_MOCK` | `false` | Force the offline mock providers. |

Provider-native keys `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, and `GEMINI_API_KEY` are used as fallbacks when `DUALSIGHT_*` keys are absent.

## Security and privacy

- **Images leave your machine.** DualSight sends them to the configured vision provider. Do not use it with images you are not allowed to share with that provider.
- **Metadata is stripped** before the vision call, including EXIF and most container metadata.
- **Remote image URLs are SSRF-checked** by default. Loopback, private, link-local, multicast, reserved, and unspecified addresses are blocked; redirects are bounded. Set `DUALSIGHT_ALLOW_PRIVATE_IMAGE_URLS=true` only in a trusted network.
- **Image text is untrusted data.** Evidence is nonce-delimited, delimiters are escaped, and the bridge note tells the reasoning model not to follow instructions found inside the image. This reduces but does not eliminate prompt-injection risk.
- **API auth is optional but recommended** whenever the service is reachable beyond localhost. Put it behind TLS and a reverse proxy in production.
- **Request limits** are enforced for body size, image bytes, pixels, dimensions, image count, and evidence length.

See [SECURITY.md](SECURITY.md) for the threat model and reporting process.

## Limitations

- Static images only; no video, PDF, or multi-page document parsing.
- Perception quality is bounded by the vision model. Low-resolution images, unusual scripts, and dense tables can produce OCR errors; evidence includes uncertainties for a reason.
- The perception pass is comprehensive rather than question-specific. That is deliberate (cache reuse), but it costs more vision tokens than a narrow question.
- The cache key includes image bytes, provider, model, profile, detail, and prompt version. Changing the prompt invalidates it by design.
- Tool calling passes through, but the bridge itself does not execute tools.
- No multi-tenant isolation, quota system, or billing. Run one instance per trust boundary.

## Development

```bash
pip install -e ".[dev]"
ruff check src tests
ruff format --check src tests
pytest
```

The test suite uses no network and no real API keys. It covers configuration, image intake and SSRF rules, rendering and truncation, parsing/caching, request validation, all providers (with mocked HTTP), the bridge, and the HTTP server.

```
src/dualsight/
  bridge.py          orchestration: intake -> perception -> evidence -> text model
  perception.py      perception prompt call, tolerant JSON parsing, cache integration
  render.py          nonce-delimited evidence blocks
  prompts.py         perception system prompt, profiles, bridge note
  image_intake.py    decode, normalize, hash, URL fetching, SSRF checks
  providers/         OpenAI-compatible, Anthropic, Gemini, mock, transport
  server.py          FastAPI app and OpenAI-compatible endpoints
  wire.py            request parsing and validation
  cache.py           memory / SQLite perception cache
  cli.py             serve, ask, perceive, doctor
  static/            offline browser demo and synthetic samples
tests/               pytest suite
docs/                architecture, API, provider, schema, deployment notes
```

## Contributing

Issues and pull requests are welcome. Please read [CONTRIBUTING.md](CONTRIBUTING.md) first. For security reports, use the process in [SECURITY.md](SECURITY.md), not a public issue.

## License

MIT © DualSight Contributors. See [LICENSE](LICENSE).
