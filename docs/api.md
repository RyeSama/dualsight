# API reference

Base URL: `http://127.0.0.1:8000`. All request and response bodies are JSON. Errors use the OpenAI envelope.

If `DUALSIGHT_SERVER_API_KEY` is set, every `/v1/*` request needs:

```http
Authorization: Bearer <key>
```

Interactive OpenAPI docs are served at `/docs` when the app is running.

---

## `POST /v1/chat/completions`

The main integration point. It is wire-compatible with the OpenAI chat completions API, plus a `dualsight` request extension.

### Request

```json
{
  "model": "dualsight",
  "messages": [
    {
      "role": "user",
      "content": [
        {"type": "text", "text": "What does this receipt total?"},
        {
          "type": "image_url",
          "image_url": {"url": "data:image/png;base64,....", "detail": "high"}
        }
      ]
    }
  ],
  "stream": false,
  "temperature": 0.2,
  "max_tokens": 600,
  "dualsight": {
    "profile": "receipt",
    "detail": "standard",
    "bypass_cache": false,
    "include_perception": true
  }
}
```

| Field | Type | Notes |
| --- | --- | --- |
| `model` | string | Ignored unless `DUALSIGHT_ALLOW_MODEL_OVERRIDE=true`. Use `dualsight`. |
| `messages` | array | OpenAI message objects. `content` may be a string or an array of text/image parts. |
| `stream` | boolean | `true` returns SSE. Default `false`. |
| `temperature` | number | 0–2, forwarded to the text provider. |
| `max_tokens` / `max_completion_tokens` | integer | Positive. Forwarded as the text provider's output limit. |
| `tools`, `tool_choice` | array/object | Passed through when the text provider supports tools. |
| `n` | integer | Must be `1`. |
| `dualsight.profile` | string | `auto`, `photo`, `document`, `ui`, `chart`, `receipt`, `diagram`. |
| `dualsight.detail` | string | `brief`, `standard`, `deep`. |
| `dualsight.bypass_cache` | boolean | Skip the perception cache for this request. |
| `dualsight.include_perception` | boolean | Add the full structured perception to each trace. |

Image parts accepted:

```json
{"type": "image_url", "image_url": {"url": "https://example.com/receipt.png"}}
{"type": "input_image", "input_image": {"url": "https://example.com/receipt.png"}}
{"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "..."}}
```

`data:` URIs, public `http(s)` URLs, and Anthropic-style base64 blocks are supported. `file://` is rejected by the API. Up to `DUALSIGHT_MAX_IMAGES` images per request.

### Non-streaming response

```json
{
  "id": "chatcmpl-...",
  "object": "chat.completion",
  "created": 1760000000,
  "model": "gpt-4o-mini",
  "choices": [
    {
      "index": 0,
      "message": {"role": "assistant", "content": "The receipt total is $23.47."},
      "finish_reason": "stop"
    }
  ],
  "usage": {"prompt_tokens": 964, "completion_tokens": 81, "total_tokens": 1045},
  "dualsight": {
    "version": "0.1",
    "nonce": "3f9c2a7d1b5e4c60",
    "image_count": 1,
    "degraded": false,
    "images": [
      {
        "id": "image-1",
        "label": "image-1",
        "source": "upload",
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
        "error": null,
        "notes": [],
        "perception": {"summary": "...", "ocr": [], "objects": []}
      }
    ],
    "vision_usage": {"prompt_tokens": 607, "completion_tokens": 261, "total_tokens": 868}
  }
}
```

`dualsight.images[].perception` appears only when `include_perception=true`. `notes` is omitted from the public trace.

### Streaming response

`stream: true` returns `text/event-stream`:

```text
data: {"id":"chatcmpl-...","object":"chat.completion.chunk","created":1760000000,"model":"gpt-4o-mini","choices":[{"index":0,"delta":{"role":"assistant"},"finish_reason":null}]}

data: {"id":"chatcmpl-...","object":"chat.completion.chunk","created":1760000000,"model":"gpt-4o-mini","choices":[{"index":0,"delta":{"content":"The "},"finish_reason":null}]}

data: {"id":"chatcmpl-...","object":"chat.completion.chunk","created":1760000000,"model":"gpt-4o-mini","choices":[{"index":0,"delta":{},"finish_reason":"stop"}],"dualsight":{...}}

data: {"id":"chatcmpl-...","object":"chat.completion.chunk","created":1760000000,"model":"gpt-4o-mini","choices":[],"usage":{"prompt_tokens":964,"completion_tokens":81,"total_tokens":1045}}

data: [DONE]
```

Response headers:

| Header | Meaning |
| --- | --- |
| `X-DualSight-Nonce` | Nonce used for evidence delimiters. |
| `X-DualSight-Images` | Number of image occurrences in the request. |
| `X-DualSight-Degraded` | `true` if at least one perception failed and was degraded. |

Tool-call deltas are emitted in OpenAI's `choices[0].delta.tool_calls` shape when the text provider supports tools.

### cURL

```bash
curl -s http://127.0.0.1:8000/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "dualsight",
    "messages": [{"role":"user","content":[
      {"type":"text","text":"Summarize this chart."},
      {"type":"image_url","image_url":{"url":"https://example.com/chart.png"}}
    ]}],
    "dualsight": {"profile":"chart"}
  }'
```

---

## `POST /v1/perceive`

Run only the perception pass. Useful for building your own prompt or inspecting evidence quality.

### Request

```json
{
  "image": "data:image/png;base64,....",
  "profile": "document",
  "detail": "deep",
  "bypass_cache": false
}
```

`image` accepts a data URI or public URL. `image_url`, `url`, and `source` are aliases. Optional `profile`, `detail`, and `bypass_cache`.

### Response

```json
{
  "image": {
    "id": "image-1",
    "label": "image-1",
    "source": "upload",
    "mime_type": "image/jpeg",
    "width": 1200,
    "height": 800,
    "provider": "openai",
    "model": "gpt-4o-mini",
    "profile": "document",
    "detail": "deep",
    "cached": false,
    "latency_ms": 2100,
    "prompt_tokens": 800,
    "completion_tokens": 300,
    "truncated": false,
    "error": null,
    "perception": {"summary": "...", "ocr": [], "objects": []}
  },
  "perception": {"summary": "...", "ocr": [], "objects": []},
  "evidence": "<<<VISUAL_EVIDENCE ...>>>...<<<END_VISUAL_EVIDENCE ...>>>",
  "raw_text": "..."
}
```

`raw_text` is empty on a cache hit.

### cURL

```bash
curl -s http://127.0.0.1:8000/v1/perceive \
  -H 'Content-Type: application/json' \
  -d '{"image":"https://example.com/receipt.png","profile":"receipt","detail":"standard"}'
```

---

## `POST /v1/vision/ask`

Ask the vision model directly, without the reasoning model. This is a convenience endpoint, not the main bridge.

### Request

```json
{
  "image": "data:image/png;base64,....",
  "question": "What is the total?",
  "profile": "receipt",
  "detail": "standard"
}
```

`question` is required and non-empty. `profile`/`detail` are accepted for parity.

### Response

```json
{
  "answer": "The total is $23.47.",
  "finish_reason": "stop",
  "usage": {"prompt_tokens": 700, "completion_tokens": 20, "total_tokens": 720},
  "provider": "openai",
  "model": "gpt-4o-mini"
}
```

---

## `GET /v1/dualsight/info`

Returns configuration visible to clients. API keys and headers are never included.

```json
{
  "name": "DualSight",
  "version": "0.1.0",
  "description": "A perception sidecar for text-only language models.",
  "vision": {"provider": "openai", "model": "gpt-4o-mini"},
  "text": {"provider": "openai", "model": "gpt-4o-mini"},
  "features": {
    "streaming": true,
    "tools_passthrough": true,
    "perception_cache": true,
    "profiles": ["auto", "photo", "document", "ui", "chart", "receipt", "diagram"]
  },
  "endpoints": {
    "chat": "/v1/chat/completions",
    "perceive": "/v1/perceive",
    "vision_ask": "/v1/vision/ask",
    "docs": "/docs"
  }
}
```

## `GET /v1/models`

OpenAI-style model list containing the configured text model:

```json
{
  "object": "list",
  "data": [{"id": "gpt-4o-mini", "object": "model", "created": 0, "owned_by": "dualsight"}]
}
```

## `GET /healthz`

```json
{"status": "ok", "name": "dualsight", "version": "0.1.0", "mock": false}
```

## Errors

```json
{
  "error": {
    "message": "messages[0].content[1] has an unsupported image part",
    "type": "invalid_request_error",
    "code": "invalid_request"
  }
}
```

| Status | Type | Typical cause |
| --- | --- | --- |
| 400 | `invalid_request_error` | Malformed JSON, `n != 1`, unsupported content part, missing image/question. |
| 401 | `authentication_error` | Missing or wrong bearer token; provider key rejected. |
| 403 | `permission_error` | Provider key lacks access. |
| 413 | `invalid_request_error` (`payload_too_large`, `image_too_large`) | Request body or image exceeds a configured cap. |
| 400 | `invalid_request_error` (`unsupported_image`, `invalid_image`) | SVG, HEIC, corrupt image, blocked URL, or invalid data URI. |
| 502 | `upstream_error` | Provider returned an error with no more specific mapping. |
| 503 | `upstream_error` (`upstream_rate_limited`) | Provider rate limit; `details.retry_after` may be present. |
| 504 | `upstream_error` (`upstream_timeout`) | Provider timed out after retries. |
| 500 | `internal_error` | Unexpected bug; details are logged, not returned. |
