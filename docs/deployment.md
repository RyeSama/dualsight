# Deployment

DualSight is a stateless HTTP service with an optional local cache. It is designed to sit behind a reverse proxy on a trusted network.

## Docker

```bash
docker build -t dualsight:0.1.0 .
docker run --rm -p 8000:8000 \
  -e DUALSIGHT_VISION_PROVIDER=openai \
  -e DUALSIGHT_VISION_API_KEY=sk-... \
  -e DUALSIGHT_TEXT_PROVIDER=openai \
  dualsight:0.1.0
```

The image runs as a non-root user, listens on `0.0.0.0:8000`, and includes a health check against `/healthz`.

For a persistent SQLite perception cache:

```bash
docker run --rm -p 8000:8000 \
  -e DUALSIGHT_VISION_API_KEY=sk-... \
  -e DUALSIGHT_CACHE=sqlite \
  -e DUALSIGHT_CACHE_PATH=/data/cache.sqlite3 \
  -v dualsight-cache:/data \
  dualsight:0.1.0
```

## Compose

```bash
cp .env.example .env
# edit .env with real provider keys
docker compose up --build
```

The provided `compose.yaml` reads `.env`, publishes port `8000`, persists `/data` in a named volume, and restarts unless stopped.

## Configuration

All settings are environment variables prefixed `DUALSIGHT_`. See [`.env.example`](../.env.example) for the full list. The most important production values:

| Variable | Recommendation |
| --- | --- |
| `DUALSIGHT_SERVER_API_KEY` | Set a long random value; put it in the proxy or client, never in a browser bundle. |
| `DUALSIGHT_HOST` | `0.0.0.0` only inside a container or private network. |
| `DUALSIGHT_CORS_ORIGINS` | Restrict to the origins that call the API; avoid `*` on the public internet. |
| `DUALSIGHT_ALLOW_PRIVATE_IMAGE_URLS` | Keep `false`. |
| `DUALSIGHT_CACHE` | `sqlite` for a single instance; `memory` for ephemeral; `none` when images must not be retained. |
| `DUALSIGHT_ON_VISION_ERROR` | `degrade` for availability, `fail` when every answer must be vision-grounded. |
| `DUALSIGHT_MAX_BODY_BYTES` | Lower it if clients only send small images. Must be ≥ `IMAGE_MAX_BYTES`. |

Secrets can also come from the provider-native variables (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`) instead of `DUALSIGHT_*_API_KEY`.

## Reverse proxy

Terminate TLS at nginx, Caddy, Traefik, or your cloud load balancer. Forward:

```nginx
location / {
    proxy_pass http://127.0.0.1:8000;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_buffering off;          # required for smooth SSE streaming
    proxy_read_timeout 300s;
    client_max_body_size 26m;     # >= DUALSIGHT_MAX_BODY_BYTES
}
```

`proxy_buffering off` matters: otherwise tokens arrive in bursts. The server sets `Cache-Control: no-cache` and `X-Accel-Buffering: no` on SSE responses as a second line of defense.

If the proxy adds its own auth, either remove `DUALSIGHT_SERVER_API_KEY` or forward the same bearer token; both layers are supported.

## Health and readiness

- `GET /healthz` returns `{"status":"ok","name":"dualsight","version":"0.1.0","mock":false}`.
- It does not call providers; a 200 means the process is up and configuration loaded.
- Use it for container and load-balancer probes. A failing provider key will show up as a 401 on the first real request, not on `/healthz`.

## Scaling

- The service is stateless except for the SQLite cache. Scale horizontally with `DUALSIGHT_CACHE=none` or `memory`, or give each replica its own SQLite file.
- SQLite WAL is fine for a single node with moderate concurrency; it is not a shared cache across replicas.
- Perception concurrency is per request (`DUALSIGHT_PERCEPTION_CONCURRENCY`). Total upstream load is roughly `replicas × concurrent requests × concurrency`; size provider rate limits accordingly.
- Streaming requests hold a connection for the duration of the text generation. Ensure the proxy read timeout exceeds `DUALSIGHT_TEXT_TIMEOUT_S`.

## Data handling

- Images are normalized and metadata-stripped before being sent to the vision provider, but the image content still leaves your host.
- Perception evidence and the user's question are sent to the text provider.
- The cache stores parsed perception JSON, not image bytes. Cache keys are hashes; labels and OCR text are stored inside the cached perception.
- Set `DUALSIGHT_CACHE=none` if retaining OCR text on disk is not acceptable.
- Provider retention policies apply. Choose providers and regions that match your data requirements.

## Operational limits

| Limit | Default | Setting |
| --- | --- | --- |
| Request body | 25 MiB | `DUALSIGHT_MAX_BODY_BYTES` |
| Image bytes | 12 MiB | `DUALSIGHT_IMAGE_MAX_BYTES` |
| Image pixels | 40 MP | `DUALSIGHT_IMAGE_MAX_PIXELS` |
| Uploaded longest side | 1568 px | `DUALSIGHT_IMAGE_MAX_DIM` |
| Images per request | 8 | `DUALSIGHT_MAX_IMAGES` |
| Evidence characters | 12,000 | `DUALSIGHT_MAX_EVIDENCE_CHARS` |
| Provider retries | 2 | `DUALSIGHT_*_MAX_RETRIES` |

## Upgrades

1. Read `CHANGELOG.md`.
2. Rebuild the image or reinstall the package.
3. If the perception prompt changed, the cache key changes automatically (`PROMPT_VERSION`), so stale entries are not reused.
4. Restart; there is no migration step for the SQLite cache. Old entries simply expire.
