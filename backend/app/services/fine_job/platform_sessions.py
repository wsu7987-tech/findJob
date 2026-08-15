from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from backend.app.config import AppConfig
from backend.app.db import Database
from backend.app.errors import AppError
from backend.app.schemas.fine_job.platform_sessions import FineJobPlatformSessionPayload
from backend.app.utils import utc_now


DEFAULT_PLATFORM = "boss"
BOSS_LOGIN_URL = "https://www.zhipin.com/web/user/"
BOSS_AUTH_COOKIE_NAMES = {
    "__zp_stoken__",
    "zp_stoken",
    "wt2",
    "wbg",
    "geek_zp_token",
}


def list_platform_sessions(db: Database) -> list[dict[str, object]]:
    session = get_platform_session(db, DEFAULT_PLATFORM)
    return [session] if session is not None else []


def get_platform_session(db: Database, platform: str = DEFAULT_PLATFORM) -> dict[str, object] | None:
    with db.connect() as connection:
        row = connection.execute(
            """
            SELECT platform, display_name, login_url, browser_profile, browser_channel, status,
                   status_detail, last_checked_at, created_at, updated_at
            FROM fj_platform_sessions
            WHERE platform = ?
            """,
            (platform,),
        ).fetchone()
    if row is None:
        return None
    return _serialize_session(row)


def save_platform_session(
    db: Database,
    payload: FineJobPlatformSessionPayload,
) -> dict[str, object]:
    now = utc_now()
    existing = get_platform_session(db, payload.platform)
    created_at = str(existing["created_at"]) if existing else now
    last_checked_at = now if payload.status == "ready" else None
    with db.connect() as connection:
        connection.execute(
            """
            INSERT INTO fj_platform_sessions (
              platform, display_name, login_url, browser_profile, status,
              status_detail, last_checked_at, created_at, updated_at, browser_channel
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(platform) DO UPDATE SET
              display_name = excluded.display_name,
              login_url = excluded.login_url,
              browser_profile = excluded.browser_profile,
              browser_channel = excluded.browser_channel,
              status = excluded.status,
              status_detail = excluded.status_detail,
              last_checked_at = excluded.last_checked_at,
              updated_at = excluded.updated_at
            """,
            (
                payload.platform,
                payload.display_name.strip() or "BOSS直聘",
                payload.login_url.strip() or BOSS_LOGIN_URL,
                payload.browser_profile.strip() or "fine-job-boss",
                payload.status,
                payload.status_detail.strip(),
                last_checked_at,
                created_at,
                now,
                _normalize_browser_channel(payload.browser_channel),
            ),
        )
    session = get_platform_session(db, payload.platform)
    assert session is not None
    return session


def open_boss_login_window(
    *,
    db: Database,
    config: AppConfig,
    session: dict[str, object] | None = None,
    login_window_runner=None,
) -> dict[str, object]:
    current = session or get_platform_session(db, DEFAULT_PLATFORM) or _default_boss_session()
    browser_channel = str(current.get("browser_channel") or "chrome")
    login_url = BOSS_LOGIN_URL
    runner = login_window_runner or start_boss_login_helper
    runner(
        config=config,
        login_url=login_url,
        browser_channel=browser_channel,
    )
    return save_platform_session(
        db,
        FineJobPlatformSessionPayload(
            platform="boss",
            display_name=str(current.get("display_name") or "BOSS直聘"),
            login_url=login_url,
            browser_profile=str(current.get("browser_profile") or "fine-job-boss"),
            browser_channel=browser_channel,
            status="needs_login",
            status_detail="登录窗口已打开。完成 BOSS 登录后，登录态会自动保存。",
        ),
    )


def check_boss_login_status(
    *,
    db: Database,
    config: AppConfig,
    session_checker=None,
) -> dict[str, object]:
    current = get_platform_session(db, DEFAULT_PLATFORM) or _default_boss_session()
    checker = session_checker or detect_boss_login_status
    ok, detail = checker(config=config)
    return save_platform_session(
        db,
        FineJobPlatformSessionPayload(
            platform="boss",
            display_name=str(current.get("display_name") or "BOSS直聘"),
            login_url=BOSS_LOGIN_URL,
            browser_profile=str(current.get("browser_profile") or "fine-job-boss"),
            browser_channel=str(current.get("browser_channel") or "chrome"),
            status="ready" if ok else "needs_login",
            status_detail=detail,
        ),
    )


def start_boss_login_helper(
    *,
    config: AppConfig,
    login_url: str,
    browser_channel: str | None,
) -> None:
    paths = get_boss_auth_state_paths(config)
    paths["dir"].mkdir(parents=True, exist_ok=True)
    _write_json(paths["status"], {"status": "starting", "message": "登录助手启动中", "updated_at": utc_now()})
    repo_root = Path(__file__).resolve().parents[4]
    script_path = repo_root / "apps" / "desktop" / "scripts" / "fine-job-boss-login-helper.mjs"
    command = [
        "node",
        str(script_path),
        "--auth-dir",
        str(paths["dir"]),
        "--login-url",
        login_url or BOSS_LOGIN_URL,
        "--browser-channel",
        _normalize_browser_channel(browser_channel),
    ]
    try:
        subprocess.Popen(
            command,
            cwd=str(repo_root / "apps" / "desktop"),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
    except OSError as exc:
        raise AppError(
            status_code=502,
            error_category="FETCH_FAILED",
            error_message=f"无法启动 BOSS 登录助手：{exc}",
        ) from exc


def detect_boss_login_status(*, config: AppConfig) -> tuple[bool, str]:
    if has_saved_boss_auth_state(config):
        return True, "已保存 BOSS 登录态，可以开始投递。"

    status = read_boss_login_helper_status(config)
    if status:
        state = str(status.get("status") or "")
        message = str(status.get("message") or "")
        if state == "failed":
            return False, message or "登录助手执行失败，请重新打开登录窗口。"
        if state in {"starting", "running"}:
            return False, message or "登录助手运行中，请完成 BOSS 登录。"
        if state == "closed":
            return False, message or "登录窗口已关闭，但未保存登录态，请重新登录。"
    return False, "未检测到有效 BOSS 登录态，请先打开登录窗口完成登录。"


def read_boss_login_helper_status(config: AppConfig) -> dict[str, Any] | None:
    path = get_boss_auth_state_paths(config)["status"]
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def get_boss_auth_state_paths(config: AppConfig) -> dict[str, Path]:
    auth_dir = (config.app_data_dir / "fine-job" / "platform-sessions" / "boss-auth-state").resolve()
    return {
        "dir": auth_dir,
        "status": auth_dir / "boss-login-status.json",
        "cookies": auth_dir / "boss-cookies.json",
        "local_storage": auth_dir / "boss-local-storage.json",
    }


def has_saved_boss_auth_state(config: AppConfig) -> bool:
    paths = get_boss_auth_state_paths(config)
    if not paths["cookies"].exists():
        return False
    try:
        cookies = json.loads(paths["cookies"].read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False
    if not isinstance(cookies, list):
        return False
    return any(str(cookie.get("name") or "") in BOSS_AUTH_COOKIE_NAMES for cookie in cookies)


def load_boss_auth_state(config: AppConfig) -> tuple[list[dict[str, Any]], dict[str, str]]:
    paths = get_boss_auth_state_paths(config)
    if not paths["cookies"].exists():
        raise AppError(
            status_code=401,
            error_category="AUTH_REQUIRED",
            error_message="还没有保存 BOSS 登录态，请先打开登录窗口并完成登录。",
        )
    try:
        cookies = json.loads(paths["cookies"].read_text(encoding="utf-8"))
        local_storage = (
            json.loads(paths["local_storage"].read_text(encoding="utf-8"))
            if paths["local_storage"].exists()
            else {}
        )
    except json.JSONDecodeError as exc:
        raise AppError(
            status_code=401,
            error_category="AUTH_REQUIRED",
            error_message="BOSS 登录态文件损坏，请重新登录。",
        ) from exc
    if not isinstance(cookies, list):
        raise AppError(
            status_code=401,
            error_category="AUTH_REQUIRED",
            error_message="BOSS 登录态文件格式不正确，请重新登录。",
        )
    if not isinstance(local_storage, dict):
        local_storage = {}
    return cookies, {str(key): str(value) for key, value in local_storage.items()}


def _serialize_session(row) -> dict[str, object]:
    status = row["status"]
    return {
        "platform": row["platform"],
        "display_name": row["display_name"],
        "login_url": row["login_url"],
        "browser_profile": row["browser_profile"],
        "browser_channel": row["browser_channel"],
        "status": status,
        "status_detail": row["status_detail"],
        "ready": status == "ready",
        "last_checked_at": row["last_checked_at"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _default_boss_session() -> dict[str, object]:
    return {
        "platform": "boss",
        "display_name": "BOSS直聘",
        "login_url": BOSS_LOGIN_URL,
        "browser_profile": "fine-job-boss",
        "browser_channel": "chrome",
        "status": "needs_login",
        "status_detail": "",
    }


def _normalize_browser_channel(value: str | None) -> str:
    normalized = (value or "chrome").strip().lower()
    if normalized in {"edge", "msedge"}:
        return "msedge"
    return "chrome"


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
