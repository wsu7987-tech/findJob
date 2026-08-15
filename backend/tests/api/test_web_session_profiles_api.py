from __future__ import annotations

import json
from pathlib import Path


def test_web_session_profiles_can_be_created_and_listed(configured_client) -> None:
    response = configured_client.post(
        "/api/web/session-profiles",
        json={
            "name": "Chrome 登录态",
            "mode": "browser_profile",
            "browser_channel": "chrome",
            "profile_path": "C:/Users/su/AppData/Local/Google/Chrome/User Data",
            "login_url": "https://example.com/login",
        },
    )

    assert response.status_code == 201
    body = response.json()["profile"]
    assert body["name"] == "Chrome 登录态"
    assert body["mode"] == "browser_profile"
    assert body["browser_channel"] == "chrome"
    assert body["profile_path"] == "C:/Users/su/AppData/Local/Google/Chrome/User Data"

    listed = configured_client.get("/api/web/session-profiles")
    assert listed.status_code == 200
    assert len(listed.json()["profiles"]) == 1


def test_web_session_profiles_persist_across_app_restarts(app_paths: dict[str, Path]) -> None:
    from fastapi.testclient import TestClient

    from backend.app.main import create_app

    with TestClient(create_app()) as first_client:
        created = first_client.post(
            "/api/web/session-profiles",
            json={
                "name": "App 会话",
                "mode": "app_session",
                "browser_channel": "chromium",
                "login_url": "https://example.com/login",
            },
        )
        assert created.status_code == 201
        profile = created.json()["profile"]
        assert profile["mode"] == "app_session"
        assert profile["managed_profile_path"]

    persisted_path = app_paths["app_data_dir"] / "web-session-profiles.json"
    assert persisted_path.exists()
    persisted_payload = json.loads(persisted_path.read_text(encoding="utf-8"))
    assert persisted_payload["profiles"][0]["name"] == "App 会话"

    with TestClient(create_app()) as restarted_client:
        listed = restarted_client.get("/api/web/session-profiles")

    assert listed.status_code == 200
    assert listed.json()["profiles"][0]["name"] == "App 会话"


def test_web_session_profile_login_uses_managed_profile_runner(
    configured_client,
    monkeypatch,
) -> None:
    created = configured_client.post(
        "/api/web/session-profiles",
        json={
            "name": "App 会话",
            "mode": "app_session",
            "browser_channel": "chromium",
            "login_url": "https://example.com/login",
        },
    ).json()["profile"]

    called: dict[str, str] = {}

    def fake_login_runner(*, profile_path: str, browser_channel: str | None, login_url: str) -> None:
        called["profile_path"] = profile_path
        called["browser_channel"] = browser_channel or ""
        called["login_url"] = login_url
        marker = Path(profile_path) / "Default" / "Cookies"
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("cookie", encoding="utf-8")

    monkeypatch.setattr(
        "backend.app.routers.web_session_profiles.run_managed_session_login",
        fake_login_runner,
    )

    response = configured_client.post(
        f"/api/web/session-profiles/{created['id']}/login",
        json={},
    )

    assert response.status_code == 202
    profile = response.json()["profile"]
    assert called["browser_channel"] == "chromium"
    assert called["login_url"] == "https://example.com/login"
    assert profile["status"] == "ready"

