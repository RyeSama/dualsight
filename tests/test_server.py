"""FastAPI surface: health, demo, chat, perception, streaming, and auth."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from dualsight.config import load_settings
from dualsight.server import create_app


def _settings():
    return load_settings({"DUALSIGHT_MOCK": "true", "DUALSIGHT_CACHE": "none"})


def _payload(data_uri: str, stream: bool = False) -> dict:
    return {
        "model": "dualsight",
        "stream": stream,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "What is the total?"},
                    {"type": "image_url", "image_url": {"url": data_uri}},
                ],
            }
        ],
        "dualsight": {"profile": "receipt", "detail": "standard", "include_perception": True},
    }


def test_health_and_models(data_uri) -> None:
    with TestClient(create_app(_settings())) as client:
        health = client.get("/healthz")
        models = client.get("/v1/models")

    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert models.status_code == 200
    assert models.json()["object"] == "list"
    assert models.json()["data"]


def test_demo_page_is_served() -> None:
    with TestClient(create_app(_settings())) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "DualSight" in response.text


def test_chat_non_streaming(data_uri) -> None:
    with TestClient(create_app(_settings())) as client:
        response = client.post("/v1/chat/completions", json=_payload(data_uri()))

    assert response.status_code == 200
    body = response.json()
    assert "23.47" in body["choices"][0]["message"]["content"]
    assert body["dualsight"]["image_count"] == 1
    assert body["dualsight"]["images"][0]["profile"] == "receipt"
    assert body["usage"]["total_tokens"] > 0


def test_chat_streaming_sse(data_uri) -> None:
    events: list[dict] = []
    with (
        TestClient(create_app(_settings())) as client,
        client.stream("POST", "/v1/chat/completions", json=_payload(data_uri(), True)) as response,
    ):
        assert response.status_code == 200
        for line in response.iter_lines():
            if not line.startswith("data: "):
                continue
            data = line[6:]
            if data == "[DONE]":
                break
            events.append(json.loads(data))

    text = "".join(
        event["choices"][0].get("delta", {}).get("content", "")
        for event in events
        if event.get("choices")
    )
    assert "23.47" in text
    assert any(event["choices"][0].get("finish_reason") for event in events if event.get("choices"))
    assert any("usage" in event for event in events)


def test_perceive_endpoint(data_uri) -> None:
    with TestClient(create_app(_settings())) as client:
        response = client.post(
            "/v1/perceive",
            json={"image": data_uri(), "profile": "receipt", "detail": "brief"},
        )

    assert response.status_code == 200
    body = response.json()
    assert "VISUAL_EVIDENCE" in body["evidence"]
    assert "23.47" in body["evidence"]
    assert body["perception"]["summary"]


def test_perceive_accepts_url_alias(data_uri) -> None:
    with TestClient(create_app(_settings())) as client:
        response = client.post("/v1/perceive", json={"url": data_uri(), "profile": "chart"})

    assert response.status_code == 200
    assert "VISUAL_EVIDENCE" in response.json()["evidence"]


def test_vision_ask_endpoint(data_uri) -> None:
    with TestClient(create_app(_settings())) as client:
        response = client.post(
            "/v1/vision/ask",
            json={"image": data_uri(), "question": "What is visible?"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["answer"]
    assert body["provider"] == "mock"


def test_bearer_auth_is_enforced(data_uri) -> None:
    settings = load_settings(
        {
            "DUALSIGHT_MOCK": "true",
            "DUALSIGHT_CACHE": "none",
            "DUALSIGHT_SERVER_API_KEY": "top-secret",
        }
    )
    with TestClient(create_app(settings)) as client:
        unauthorized = client.post("/v1/chat/completions", json=_payload(data_uri()))
        authorized = client.post(
            "/v1/chat/completions",
            json=_payload(data_uri()),
            headers={"Authorization": "Bearer top-secret"},
        )

    assert unauthorized.status_code == 401
    assert authorized.status_code == 200


def test_invalid_request_uses_openai_error_envelope() -> None:
    with TestClient(create_app(_settings())) as client:
        response = client.post(
            "/v1/chat/completions",
            json={
                "model": "dualsight",
                "n": 2,
                "messages": [{"role": "user", "content": "hello"}],
            },
        )

    assert response.status_code == 400
    body = response.json()
    assert body["error"]["type"] == "invalid_request_error"


def test_missing_image_for_perceive_is_400() -> None:
    with TestClient(create_app(_settings())) as client:
        response = client.post("/v1/perceive", json={})

    assert response.status_code == 400
    assert response.json()["error"]["type"] == "invalid_request_error"
