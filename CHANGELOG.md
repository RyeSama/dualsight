# Changelog

All notable changes to DualSight are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] — 2026-10-19

### Added

- OpenAI-compatible `/v1/chat/completions` bridge with streaming SSE and tool-call passthrough.
- Perception-only `/v1/perceive` and direct `/v1/vision/ask` endpoints.
- Provider adapters for OpenAI-compatible APIs, Anthropic, Gemini, and a deterministic offline mock.
- Structured perception schema: summary, OCR spans, objects, layout, colors, charts, uncertainties, and suggested questions.
- Nonce-delimited evidence rendering with delimiter escaping and explicit untrusted-data guidance.
- Perception caching by image hash, provider, model, profile, detail, and prompt version (memory or SQLite).
- Concurrent multi-image perception, duplicate-image deduplication, per-image degradation, and evidence budgeting.
- Image intake with EXIF orientation, alpha flattening, metadata stripping, resizing, hashing, and SSRF-checked URL fetching.
- CLI commands: `serve`, `ask`, `perceive`, and `doctor`.
- Offline browser demo with synthetic samples and a dependency-free frontend.
- Test suite covering configuration, intake, rendering, parsing, caching, request validation, providers, bridge, and HTTP API.
- Dockerfile, Compose file, GitHub Actions CI, and bilingual documentation.

[Unreleased]: https://github.com/RyeSama/dualsight/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/RyeSama/dualsight/releases/tag/v0.1.0
