from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.main import create_app


def test_cors_preflight_allows_local_dev_origin(client: TestClient) -> None:
    response = client.options(
        "/api/pool/items",
        headers={
            "Origin": "http://127.0.0.1:5173",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://127.0.0.1:5173"


def test_cors_actual_get_allows_local_dev_origin(client: TestClient) -> None:
    response = client.get(
        "/api/config",
        headers={"Origin": "http://localhost:5173"},
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_cors_preflight_supports_patch_and_post(client: TestClient) -> None:
    patch_response = client.options(
        "/api/config",
        headers={
            "Origin": "http://127.0.0.1:5173",
            "Access-Control-Request-Method": "PATCH",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    post_response = client.options(
        "/api/results/example-snapshot/feedback",
        headers={
            "Origin": "http://127.0.0.1:5173",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )

    assert patch_response.status_code == 200
    assert patch_response.headers["access-control-allow-origin"] == "http://127.0.0.1:5173"
    assert "PATCH" in patch_response.headers["access-control-allow-methods"]
    assert post_response.status_code == 200
    assert post_response.headers["access-control-allow-origin"] == "http://127.0.0.1:5173"
    assert "POST" in post_response.headers["access-control-allow-methods"]


def test_cors_disallowed_origin_does_not_get_allow_origin_header(client: TestClient) -> None:
    response = client.get(
        "/api/config",
        headers={"Origin": "https://example.com"},
    )

    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers


def test_config_get_and_patch_round_trip(
    client: TestClient,
    app_paths: dict[str, Path],
) -> None:
    read_response = client.get("/api/config")

    assert read_response.status_code == 200
    assert read_response.json()["output_root"] == str(app_paths["output_root"])

    updated_output_root = app_paths["app_data_dir"] / "patched-outputs"
    patch_response = client.patch(
        "/api/config",
        json={
            "output_root": str(updated_output_root),
            "llm_provider": "patched-provider",
            "llm_model": "patched-model",
            "fetch_concurrency": 4,
            "llm_concurrency": 2,
            "embedding_concurrency": 3,
        },
    )

    assert patch_response.status_code == 200
    assert patch_response.json()["output_root"] == str(updated_output_root)
    assert patch_response.json()["summary_output_dir"] == str(updated_output_root / "summaries")
    assert patch_response.json()["report_output_dir"] == str(updated_output_root / "reports")
    assert patch_response.json()["llm_provider"] == "patched-provider"
    assert patch_response.json()["llm_model"] == "patched-model"
    assert patch_response.json()["fetch_concurrency"] == 4
    assert patch_response.json()["llm_concurrency"] == 2
    assert patch_response.json()["embedding_concurrency"] == 3
    assert updated_output_root.exists()
    assert (updated_output_root / "summaries").exists()
    assert (updated_output_root / "reports").exists()

    reread_response = client.get("/api/config")

    assert reread_response.status_code == 200
    assert reread_response.json()["output_root"] == str(updated_output_root)
    assert reread_response.json()["llm_provider"] == "patched-provider"
    assert reread_response.json()["fetch_concurrency"] == 4


def test_config_patch_rejects_extra_fields_and_non_positive_concurrency(
    client: TestClient,
) -> None:
    extra_field_response = client.patch(
        "/api/config",
        json={"unexpected": "value"},
    )
    invalid_concurrency_response = client.patch(
        "/api/config",
        json={"llm_concurrency": 0},
    )

    assert extra_field_response.status_code == 422
    assert invalid_concurrency_response.status_code == 422


def test_config_patch_persists_provider_endpoints_and_keys_across_app_restarts(
    app_paths: dict[str, Path],
    monkeypatch,
) -> None:
    monkeypatch.setenv("KNOWLEDGE_CURATOR_LLM_BASE_URL", "https://env-llm.example/v1")
    monkeypatch.setenv("KNOWLEDGE_CURATOR_LLM_API_KEY", "env-llm-key")
    monkeypatch.setenv(
        "KNOWLEDGE_CURATOR_EMBEDDING_BASE_URL",
        "https://env-embedding.example/v1",
    )
    monkeypatch.setenv("KNOWLEDGE_CURATOR_EMBEDDING_API_KEY", "env-embedding-key")

    with TestClient(create_app()) as first_client:
        initial_response = first_client.get("/api/config")
        assert initial_response.status_code == 200
        assert initial_response.json()["llm_base_url"] == "https://env-llm.example/v1"
        assert initial_response.json()["llm_api_key"] == "env-llm-key"
        assert initial_response.json()["llm_configured"] is True
        assert (
            initial_response.json()["embedding_base_url"]
            == "https://env-embedding.example/v1"
        )
        assert (
            initial_response.json()["embedding_api_key"] == "env-embedding-key"
        )
        assert initial_response.json()["embedding_configured"] is True

        patch_response = first_client.patch(
            "/api/config",
            json={
                "llm_base_url": "https://persisted-llm.example/v1",
                "llm_api_key": "persisted-llm-key",
                "embedding_base_url": "https://persisted-embedding.example/v1",
                "embedding_api_key": "persisted-embedding-key",
            },
        )

        assert patch_response.status_code == 200
        assert patch_response.json()["llm_base_url"] == "https://persisted-llm.example/v1"
        assert patch_response.json()["llm_api_key"] == "persisted-llm-key"
        assert patch_response.json()["llm_configured"] is True
        assert (
            patch_response.json()["embedding_base_url"]
            == "https://persisted-embedding.example/v1"
        )
        assert (
            patch_response.json()["embedding_api_key"] == "persisted-embedding-key"
        )
        assert patch_response.json()["embedding_configured"] is True

    persisted_path = app_paths["app_data_dir"] / "config.user.json"
    assert persisted_path.exists()
    assert json.loads(persisted_path.read_text(encoding="utf-8")) == {
        "llm_base_url": "https://persisted-llm.example/v1",
        "llm_api_key": "persisted-llm-key",
        "embedding_base_url": "https://persisted-embedding.example/v1",
        "embedding_api_key": "persisted-embedding-key",
    }

    with TestClient(create_app()) as restarted_client:
        reread_response = restarted_client.get("/api/config")

    assert reread_response.status_code == 200
    assert reread_response.json()["llm_base_url"] == "https://persisted-llm.example/v1"
    assert reread_response.json()["llm_api_key"] == "persisted-llm-key"
    assert reread_response.json()["llm_configured"] is True
    assert (
        reread_response.json()["embedding_base_url"]
        == "https://persisted-embedding.example/v1"
    )
    assert reread_response.json()["embedding_api_key"] == "persisted-embedding-key"
    assert reread_response.json()["embedding_configured"] is True


def test_config_patch_writes_provider_settings_to_new_app_data_dir(
    app_paths: dict[str, Path],
    monkeypatch,
) -> None:
    moved_app_data_dir = app_paths["app_data_dir"] / "moved-app-data"
    moved_sqlite_path = moved_app_data_dir / "knowledge-curator.db"
    moved_output_root = moved_app_data_dir / "outputs"

    with TestClient(create_app()) as client:
        response = client.patch(
            "/api/config",
            json={
                "app_data_dir": str(moved_app_data_dir),
                "llm_provider": "openai-compatible",
                "llm_model": "gpt-4o-mini",
                "llm_base_url": "https://llm.example/v1",
                "llm_api_key": "llm-key",
                "embedding_provider": "openai-compatible",
                "embedding_model": "text-embedding-3-small",
                "embedding_base_url": "https://embedding.example/v1",
                "embedding_api_key": "embedding-key",
            },
        )

    assert response.status_code == 200
    assert response.json()["app_data_dir"] == str(moved_app_data_dir)

    persisted_path = moved_app_data_dir / "config.user.json"
    assert persisted_path.exists()
    persisted_payload = json.loads(persisted_path.read_text(encoding="utf-8"))
    assert persisted_payload["llm_provider"] == "openai-compatible"
    assert persisted_payload["llm_api_key"] == "llm-key"
    assert persisted_payload["embedding_provider"] == "openai-compatible"
    assert persisted_payload["embedding_api_key"] == "embedding-key"

    monkeypatch.setenv("KNOWLEDGE_CURATOR_APP_DATA_DIR", str(moved_app_data_dir))
    monkeypatch.setenv("KNOWLEDGE_CURATOR_SQLITE_PATH", str(moved_sqlite_path))
    monkeypatch.setenv("KNOWLEDGE_CURATOR_OUTPUT_ROOT", str(moved_output_root))

    with TestClient(create_app()) as restarted_client:
        reread_response = restarted_client.get("/api/config")

    assert reread_response.status_code == 200
    assert reread_response.json()["app_data_dir"] == str(moved_app_data_dir)
    assert reread_response.json()["llm_provider"] == "openai-compatible"
    assert reread_response.json()["llm_api_key"] == "llm-key"
    assert reread_response.json()["embedding_provider"] == "openai-compatible"
    assert reread_response.json()["embedding_api_key"] == "embedding-key"
