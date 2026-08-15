from fastapi.testclient import TestClient

from backend.app.main import create_app


def test_healthcheck_returns_service_metadata() -> None:
    client = TestClient(create_app())
    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "knowledge-curator",
        "version": "0.1.0",
    }
