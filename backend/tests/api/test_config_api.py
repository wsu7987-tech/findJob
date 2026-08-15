from __future__ import annotations

from fastapi.testclient import TestClient


def test_config_roundtrip_includes_quick_capture_fields(client: TestClient) -> None:
    response = client.patch(
        "/api/config",
        json={
            "quick_capture_hotkey": "CommandOrControl+Shift+Space",
            "quick_capture_screenshot_hotkey": "CommandOrControl+Shift+4",
            "close_to_tray": True,
            "quick_capture_always_on_top": True,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["quick_capture_hotkey"] == "CommandOrControl+Shift+Space"
    assert payload["quick_capture_screenshot_hotkey"] == "CommandOrControl+Shift+4"
    assert payload["close_to_tray"] is True
    assert payload["quick_capture_always_on_top"] is True
