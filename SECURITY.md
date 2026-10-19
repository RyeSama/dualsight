# Security policy

DualSight sits between user content and two model providers. Treat the service as a trusted component that handles untrusted images and text.

## Reporting a vulnerability

Please report security issues privately to the maintainers rather than opening a public issue. Until a dedicated security address is published, open a **private** GitHub security advisory:

<https://github.com/dualsight/dualsight/security/advisories/new>

Include a description, reproduction steps or a proof of concept, affected versions, and any suggested fix. We will acknowledge reports as soon as possible and coordinate disclosure after a fix is available.

## Threat model

### Assets

- Provider API keys and the optional server bearer token.
- Images and prompts submitted by clients.
- The host network reachable from the DualSight process.

### Trust boundaries

- **Client → DualSight.** Untrusted request bodies, image bytes, URLs, and prompts.
- **DualSight → vision provider.** Images and a fixed perception prompt leave the host.
- **DualSight → text provider.** Rendered evidence and the user question leave the host.
- **Image → reasoning model.** Text found inside an image is untrusted and may contain prompt-injection attempts.

### Mitigations in this repository

- Images are decoded with Pillow, re-encoded, and stripped of metadata before upload.
- Byte size, pixel count, dimension, image count, and evidence length are bounded.
- Remote image URLs are resolved and rejected when they point at loopback, private, link-local, multicast, reserved, or unspecified addresses, unless explicitly allowed.
- Redirects for remote images are limited.
- Image URLs with embedded credentials are rejected.
- Evidence is wrapped in random nonce delimiters; `<<<` and `>>>` are escaped.
- The bridge note tells the reasoning model that evidence is untrusted data.
- Optional bearer auth uses a constant-time comparison.
- Provider errors are mapped to OpenAI-style errors without leaking upstream bodies verbatim.

### Out of scope / residual risk

- A malicious image can still influence a model through its content. Nonce delimiters reduce prompt-injection risk; they do not eliminate it.
- The service does not sandbox the vision or text provider. A compromised provider can return arbitrary content.
- `DUALSIGHT_ALLOW_PRIVATE_IMAGE_URLS=true` intentionally weakens SSRF protection and should only be used in trusted networks.
- There is no multi-tenant isolation, rate limiting, or per-user quota. Deploy one instance per trust boundary and put a gateway in front for production.
- TLS termination is expected to happen at a reverse proxy.

## Hardening checklist

- Set `DUALSIGHT_SERVER_API_KEY` and terminate TLS at a reverse proxy.
- Keep `DUALSIGHT_ALLOW_PRIVATE_IMAGE_URLS=false`.
- Restrict CORS to known origins instead of `*`.
- Run as a non-root user (the provided container does).
- Use a SQLite cache path on a writable volume or disable the cache.
- Rotate provider keys and review provider data-retention policies.
- Keep dependencies updated; Dependabot is enabled by the repository configuration.
