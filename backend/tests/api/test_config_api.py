from __future__ import annotations

from fastapi.testclient import TestClient

from backend.app.services.reasoning.codex_exec import CodexCliStatus


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


def test_config_roundtrip_includes_codex_executor_fields(client: TestClient) -> None:
    response = client.patch(
        "/api/config",
        json={
            "reasoning_executor": "codex-cli",
            "codex_cli_path": "codex",
            "codex_model": "gpt-5.6",
            "codex_reasoning_effort": "high",
            "codex_timeout_seconds": 240,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["reasoning_executor"] == "codex-cli"
    assert payload["codex_model"] == "gpt-5.6"
    assert payload["codex_reasoning_effort"] == "high"
    assert payload["codex_timeout_seconds"] == 240


def test_codex_connectivity_endpoint_returns_cli_status(
    client: TestClient,
    monkeypatch,
) -> None:
    client.patch(
        "/api/config",
        json={"reasoning_executor": "codex-cli", "codex_model": "gpt-5.6"},
    )
    monkeypatch.setattr(
        "backend.app.routers.config.check_codex_cli",
        lambda _path: CodexCliStatus(
            ok=True,
            status="ready",
            cli_path="D:/tools/codex.exe",
            cli_version="0.147.0",
            authenticated=True,
            detail="ready",
        ),
    )

    response = client.post("/api/config/check-codex")

    assert response.status_code == 200
    assert response.json() == {
        "capability": "codex-cli",
        "ok": True,
        "status": "ready",
        "cli_path": "D:/tools/codex.exe",
        "cli_version": "0.147.0",
        "authenticated": True,
        "model": "gpt-5.6",
        "reasoning_effort": None,
        "detail": "ready",
        "error_category": None,
        "checked_at": response.json()["checked_at"],
    }
