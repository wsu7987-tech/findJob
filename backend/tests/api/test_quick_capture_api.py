from __future__ import annotations

from fastapi.testclient import TestClient


def test_quick_capture_ocr_returns_text(
    client: TestClient,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "backend.app.routers.quick_capture.extract_text_from_screenshot",
        lambda image_base64: {
            "raw_text": "Recognized text",
            "captured_at": "2026-04-21T10:30:00+08:00",
            "warnings": [],
        },
    )

    response = client.post(
        "/api/quick-capture/ocr",
        json={"image_base64": "ZmFrZS1wbmc="},
    )

    assert response.status_code == 200
    assert response.json()["raw_text"] == "Recognized text"
    assert response.json()["captured_at"] == "2026-04-21T10:30:00+08:00"
