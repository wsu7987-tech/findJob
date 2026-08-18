from __future__ import annotations


def test_save_and_read_platform_session(configured_client) -> None:
    empty_response = configured_client.get("/api/fine-job/platform-sessions")

    assert empty_response.status_code == 200
    assert empty_response.json()["sessions"] == []

    save_response = configured_client.put(
        "/api/fine-job/platform-sessions/boss",
        json={
            "platform": "boss",
            "display_name": "BOSS 直聘",
            "login_url": "https://www.zhipin.com/",
            "browser_profile": "fine-job-boss",
            "browser_channel": "chrome",
            "status": "ready",
            "status_detail": "用户已确认当前浏览器会话可用",
        },
    )

    assert save_response.status_code == 200
    saved = save_response.json()["session"]
    assert saved["ready"] is True
    assert saved["status"] == "ready"
    assert saved["browser_channel"] == "chrome"
    assert saved["last_checked_at"] is not None

    read_response = configured_client.get("/api/fine-job/platform-sessions/boss")

    assert read_response.status_code == 200
    assert read_response.json()["session"]["browser_profile"] == "fine-job-boss"


def test_platform_session_invalid_status_is_not_ready(configured_client) -> None:
    response = configured_client.put(
        "/api/fine-job/platform-sessions/boss",
        json={
            "platform": "boss",
            "display_name": "BOSS 直聘",
            "login_url": "https://www.zhipin.com/",
            "browser_profile": "fine-job-boss",
            "browser_channel": "chrome",
            "status": "invalid",
            "status_detail": "登录态失效",
        },
    )

    assert response.status_code == 200
    assert response.json()["session"]["ready"] is False
    assert response.json()["session"]["last_checked_at"] is None


def test_open_boss_login_window_uses_selected_browser(configured_client, monkeypatch) -> None:
    configured_client.put(
        "/api/fine-job/platform-sessions/boss",
        json={
            "platform": "boss",
            "display_name": "BOSS 直聘",
            "login_url": "https://www.zhipin.com/",
            "browser_profile": "fine-job-boss",
            "browser_channel": "chrome",
            "status": "needs_login",
            "status_detail": "",
        },
    )
    called: dict[str, object] = {}

    class FakeBrowserService:
        def start_browser(self, *, wait_login: bool) -> int:
            called["wait_login"] = wait_login
            return 0

        def open_login_page(self) -> str:
            called["login_url"] = "https://www.zhipin.com/web/user/"
            return str(called["login_url"])

    monkeypatch.setattr(
        "backend.app.routers.fine_job.platform_sessions.open_boss_login_window",
        lambda db, config: __import__(
            "backend.app.services.fine_job.platform_sessions",
            fromlist=["open_boss_login_window"],
        ).open_boss_login_window(
            db=db,
            config=config,
            browser_service=FakeBrowserService(),
        ),
    )

    response = configured_client.post("/api/fine-job/platform-sessions/boss/login-window")

    assert response.status_code == 200
    assert called["login_url"] == "https://www.zhipin.com/web/user/"
    assert called["wait_login"] is False
    assert response.json()["session"]["status"] == "needs_login"
    assert response.json()["session"]["browser_channel"] == "chrome"


def test_check_boss_login_status_marks_ready(configured_client, monkeypatch) -> None:
    class FakeBrowserService:
        def check_login(self) -> tuple[bool, str]:
            return True, "检测到 BOSS 登录态，登录态可用。"

    monkeypatch.setattr(
        "backend.app.routers.fine_job.platform_sessions.check_boss_login_status",
        lambda db, config: __import__(
            "backend.app.services.fine_job.platform_sessions",
            fromlist=["check_boss_login_status"],
        ).check_boss_login_status(
            db=db,
            config=config,
            browser_service=FakeBrowserService(),
        ),
    )

    response = configured_client.post("/api/fine-job/platform-sessions/boss/check")

    assert response.status_code == 200
    assert response.json()["session"]["status"] == "ready"
    assert response.json()["session"]["ready"] is True
