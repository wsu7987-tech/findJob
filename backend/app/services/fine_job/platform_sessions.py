from __future__ import annotations

from backend.app.config import AppConfig
from backend.app.db import Database
from backend.app.errors import AppError
from backend.app.schemas.fine_job.platform_sessions import FineJobPlatformSessionPayload
from backend.app.services.fine_job.boss_scraper.service import boss_scraper_service
from backend.app.utils import utc_now


DEFAULT_PLATFORM = "boss"
BOSS_LOGIN_URL = "https://www.zhipin.com/web/user/"


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
    browser_service=None,
) -> dict[str, object]:
    current = session or get_platform_session(db, DEFAULT_PLATFORM) or _default_boss_session()
    service = browser_service or boss_scraper_service
    if service.start_browser(wait_login=False) != 0:
        raise AppError(
            status_code=502,
            error_category="FETCH_FAILED",
            error_message="FineJob 专用 Chrome 启动失败。",
        )
    service.open_login_page()
    return save_platform_session(
        db,
        FineJobPlatformSessionPayload(
            platform="boss",
            display_name=str(current.get("display_name") or "BOSS直聘"),
            login_url=BOSS_LOGIN_URL,
            browser_profile=str(current.get("browser_profile") or "fine-job-boss"),
            browser_channel="chrome",
            status="needs_login",
            status_detail="FineJob 专用 Chrome 已打开。请完成 BOSS 登录后点击检测登录状态。",
        ),
    )


def check_boss_login_status(
    *,
    db: Database,
    config: AppConfig,
    browser_service=None,
) -> dict[str, object]:
    current = get_platform_session(db, DEFAULT_PLATFORM) or _default_boss_session()
    service = browser_service or boss_scraper_service
    ok, detail = service.check_login()
    return save_platform_session(
        db,
        FineJobPlatformSessionPayload(
            platform="boss",
            display_name=str(current.get("display_name") or "BOSS直聘"),
            login_url=BOSS_LOGIN_URL,
            browser_profile=str(current.get("browser_profile") or "fine-job-boss"),
            browser_channel="chrome",
            status="ready" if ok else "needs_login",
            status_detail=detail,
        ),
    )


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


