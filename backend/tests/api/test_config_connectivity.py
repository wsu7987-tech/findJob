from __future__ import annotations

from fastapi.testclient import TestClient

from backend.app.errors import AppError


def test_llm_connectivity_check_succeeds_for_stub_provider(
    configured_client: TestClient,
) -> None:
    response = configured_client.post("/api/config/check-llm")

    assert response.status_code == 200
    assert response.json()["capability"] == "llm"
    assert response.json()["ok"] is True
    assert response.json()["status"] == "ready"


def test_embedding_connectivity_check_reports_invalid_config(
    client: TestClient,
) -> None:
    response = client.post("/api/config/check-embedding")

    assert response.status_code == 200
    assert response.json()["capability"] == "embedding"
    assert response.json()["ok"] is False
    assert response.json()["status"] == "invalid"
    assert response.json()["error_category"] == "CONFIG_INVALID"


def test_llm_connectivity_check_uses_upstream_probe(
    configured_client: TestClient,
    monkeypatch,
) -> None:
    configured_client.patch(
        "/api/config",
        json={
            "llm_provider": "openai-compatible",
            "llm_model": "gpt-4o-mini",
            "llm_base_url": "https://llm.example/v1",
            "llm_api_key": "secret-key",
        },
    )

    def fake_probe(*, base_url: str, api_key: str, model_name: str, timeout_seconds: int) -> None:
        assert base_url == "https://llm.example/v1"
        assert api_key == "secret-key"
        assert model_name == "gpt-4o-mini"
        assert timeout_seconds > 0

    monkeypatch.setattr("backend.app.services.ai._probe_llm_connection", fake_probe)

    response = configured_client.post("/api/config/check-llm")

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.json()["status"] == "ready"


def test_llm_connectivity_check_accepts_deepseek_alias(
    configured_client: TestClient,
    monkeypatch,
) -> None:
    configured_client.patch(
        "/api/config",
        json={
            "llm_provider": "deepseek",
            "llm_model": "deepseek-chat",
            "llm_base_url": "https://api.deepseek.com/v1",
            "llm_api_key": "secret-key",
        },
    )

    def fake_probe(*, base_url: str, api_key: str, model_name: str, timeout_seconds: int) -> None:
        assert base_url == "https://api.deepseek.com/v1"
        assert api_key == "secret-key"
        assert model_name == "deepseek-chat"
        assert timeout_seconds > 0

    monkeypatch.setattr("backend.app.services.ai._probe_llm_connection", fake_probe)

    response = configured_client.post("/api/config/check-llm")

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.json()["status"] == "ready"


def test_embedding_connectivity_check_surfaces_upstream_failures(
    configured_client: TestClient,
    monkeypatch,
) -> None:
    configured_client.patch(
        "/api/config",
        json={
            "embedding_provider": "openai-compatible",
            "embedding_model": "text-embedding-3-small",
            "embedding_base_url": "https://embedding.example/v1",
            "embedding_api_key": "secret-key",
        },
    )

    def fake_probe(*, base_url: str, api_key: str, model_name: str, timeout_seconds: int) -> None:
        raise AppError(
            status_code=502,
            error_category="UPSTREAM_FAILED",
            error_message="Embedding upstream failed",
        )

    monkeypatch.setattr(
        "backend.app.services.ai._probe_embedding_connection",
        fake_probe,
    )

    response = configured_client.post("/api/config/check-embedding")

    assert response.status_code == 200
    assert response.json()["capability"] == "embedding"
    assert response.json()["ok"] is False
    assert response.json()["status"] == "failed"
    assert response.json()["error_category"] == "UPSTREAM_FAILED"
    assert response.json()["detail"] == "Embedding upstream failed"


def test_embedding_connectivity_check_accepts_qianwen_alias(
    configured_client: TestClient,
    monkeypatch,
) -> None:
    configured_client.patch(
        "/api/config",
        json={
            "embedding_provider": "qianwen",
            "embedding_model": "text-embedding-v4",
            "embedding_base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "embedding_api_key": "secret-key",
        },
    )

    def fake_probe(*, base_url: str, api_key: str, model_name: str, timeout_seconds: int) -> None:
        assert base_url == "https://dashscope.aliyuncs.com/compatible-mode/v1"
        assert api_key == "secret-key"
        assert model_name == "text-embedding-v4"
        assert timeout_seconds > 0

    monkeypatch.setattr(
        "backend.app.services.ai._probe_embedding_connection",
        fake_probe,
    )

    response = configured_client.post("/api/config/check-embedding")

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.json()["status"] == "ready"
