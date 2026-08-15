from __future__ import annotations

import json
import shutil
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from threading import RLock
from typing import Any

from backend.app.errors import AppError
from backend.app.utils import new_id, utc_now
from backend.app.services.web_capture.playwright_runner import PlaywrightRunner


@dataclass(slots=True)
class WebSessionProfile:
    id: str
    name: str
    mode: str
    browser_channel: str
    profile_path: str | None
    managed_profile_path: str | None
    login_url: str | None
    created_at: str
    updated_at: str


class WebSessionProfileStore:
    def __init__(self, storage_path: Path) -> None:
        self._storage_path = storage_path
        self._lock = RLock()
        self._profiles: dict[str, WebSessionProfile] = {}
        self._load()

    def list_profiles(self) -> list[WebSessionProfile]:
        with self._lock:
            return sorted(
                self._profiles.values(),
                key=lambda item: (item.updated_at, item.id),
                reverse=True,
            )

    def get_profile(self, profile_id: str) -> WebSessionProfile | None:
        with self._lock:
            return self._profiles.get(profile_id)

    def save_profile(self, profile: WebSessionProfile) -> WebSessionProfile:
        with self._lock:
            self._profiles[profile.id] = profile
            self._persist_locked()
            return profile

    def delete_profile(self, profile_id: str) -> bool:
        with self._lock:
            deleted = self._profiles.pop(profile_id, None) is not None
            if deleted:
                self._persist_locked()
            return deleted

    def _load(self) -> None:
        if not self._storage_path.exists():
            self._profiles = {}
            return
        raw = json.loads(self._storage_path.read_text(encoding="utf-8"))
        items = raw.get("profiles") if isinstance(raw, dict) else None
        if not isinstance(items, list):
            self._profiles = {}
            return
        self._profiles = {
            str(item["id"]): WebSessionProfile(
                id=str(item["id"]),
                name=str(item["name"]),
                mode=str(item["mode"]),
                browser_channel=str(item.get("browser_channel") or "chromium"),
                profile_path=None if item.get("profile_path") is None else str(item["profile_path"]),
                managed_profile_path=(
                    None
                    if item.get("managed_profile_path") is None
                    else str(item["managed_profile_path"])
                ),
                login_url=None if item.get("login_url") is None else str(item["login_url"]),
                created_at=str(item["created_at"]),
                updated_at=str(item["updated_at"]),
            )
            for item in items
            if isinstance(item, dict) and item.get("id")
        }

    def _persist_locked(self) -> None:
        self._storage_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "profiles": [
                asdict(profile)
                for profile in sorted(
                    self._profiles.values(),
                    key=lambda item: (item.updated_at, item.id),
                    reverse=True,
                )
            ]
        }
        self._storage_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )


class WebSessionProfileService:
    def __init__(
        self,
        *,
        store: WebSessionProfileStore,
        app_data_dir: Path,
        login_runner=None,
    ) -> None:
        self._store = store
        self._managed_root = app_data_dir / "web-session-profiles"
        self._login_runner = login_runner or run_managed_session_login

    def list_profiles(self) -> list[dict[str, object]]:
        return [self.serialize_profile(item) for item in self._store.list_profiles()]

    def get_profile(self, profile_id: str) -> dict[str, object]:
        return self.serialize_profile(self._require_profile(profile_id))

    def create_profile(
        self,
        *,
        name: str,
        mode: str,
        browser_channel: str | None,
        profile_path: str | None,
        login_url: str | None,
    ) -> dict[str, object]:
        profile_id = new_id()
        now = utc_now()
        normalized_mode = self._normalize_mode(mode)
        normalized_channel = self._normalize_channel(browser_channel)
        normalized_name = self._normalize_name(name)
        normalized_login_url = self._normalize_optional(login_url)
        normalized_profile_path = self._normalize_optional(profile_path)

        managed_profile_path = None
        if normalized_mode == "browser_profile":
            if not normalized_profile_path:
                raise AppError(
                    status_code=400,
                    error_category="VALIDATION_FAILED",
                    error_message="Browser profile mode requires a profile path.",
                )
        else:
            managed_profile_path = str((self._managed_root / profile_id / "user-data").resolve())
            Path(managed_profile_path).mkdir(parents=True, exist_ok=True)
            normalized_profile_path = None

        profile = WebSessionProfile(
            id=profile_id,
            name=normalized_name,
            mode=normalized_mode,
            browser_channel=normalized_channel,
            profile_path=normalized_profile_path,
            managed_profile_path=managed_profile_path,
            login_url=normalized_login_url,
            created_at=now,
            updated_at=now,
        )
        self._store.save_profile(profile)
        return self.serialize_profile(profile)

    def update_profile(
        self,
        profile_id: str,
        *,
        name: str | None = None,
        browser_channel: str | None = None,
        profile_path: str | None = None,
        login_url: str | None = None,
    ) -> dict[str, object]:
        profile = self._require_profile(profile_id)
        if name is not None:
            profile.name = self._normalize_name(name)
        if browser_channel is not None:
            profile.browser_channel = self._normalize_channel(browser_channel)
        if login_url is not None:
            profile.login_url = self._normalize_optional(login_url)
        if profile.mode == "browser_profile" and profile_path is not None:
            profile.profile_path = self._normalize_optional(profile_path)
            if not profile.profile_path:
                raise AppError(
                    status_code=400,
                    error_category="VALIDATION_FAILED",
                    error_message="Browser profile mode requires a profile path.",
                )
        profile.updated_at = utc_now()
        self._store.save_profile(profile)
        return self.serialize_profile(profile)

    def delete_profile(self, profile_id: str) -> bool:
        profile = self._store.get_profile(profile_id)
        deleted = self._store.delete_profile(profile_id)
        if deleted and profile and profile.mode == "app_session" and profile.managed_profile_path:
            self._delete_managed_profile_path(Path(profile.managed_profile_path))
        return deleted

    def start_managed_login(self, profile_id: str, *, login_url: str | None = None) -> dict[str, object]:
        profile = self._require_profile(profile_id)
        if profile.mode != "app_session" or not profile.managed_profile_path:
            raise AppError(
                status_code=400,
                error_category="VALIDATION_FAILED",
                error_message="Only app-managed sessions can open a login window.",
            )

        target_url = self._normalize_optional(login_url) or profile.login_url
        if not target_url:
            raise AppError(
                status_code=400,
                error_category="VALIDATION_FAILED",
                error_message="A login URL is required for app-managed session login.",
            )

        Path(profile.managed_profile_path).mkdir(parents=True, exist_ok=True)
        self._login_runner(
            profile_path=profile.managed_profile_path,
            browser_channel=profile.browser_channel,
            login_url=target_url,
        )
        profile.updated_at = utc_now()
        self._store.save_profile(profile)
        return self.serialize_profile(profile)

    def load_capture_profile(self, profile_id: str) -> dict[str, object] | None:
        profile = self._store.get_profile(profile_id)
        if profile is None:
            return None

        resolved_profile_path = profile.profile_path or profile.managed_profile_path
        return {
            "id": profile.id,
            "mode": profile.mode,
            "browser_channel": profile.browser_channel,
            "profile_path": resolved_profile_path,
            "storage_state_path": None,
        }

    def serialize_profile(self, profile: WebSessionProfile) -> dict[str, object]:
        status, status_detail = _resolve_profile_status(profile)
        return {
            "id": profile.id,
            "name": profile.name,
            "mode": profile.mode,
            "browser_channel": profile.browser_channel,
            "profile_path": profile.profile_path,
            "managed_profile_path": profile.managed_profile_path,
            "login_url": profile.login_url,
            "status": status,
            "status_detail": status_detail,
            "created_at": profile.created_at,
            "updated_at": profile.updated_at,
        }

    def _require_profile(self, profile_id: str) -> WebSessionProfile:
        profile = self._store.get_profile(profile_id)
        if profile is None:
            raise AppError(
                status_code=404,
                error_category="VALIDATION_FAILED",
                error_message="Web session profile not found.",
            )
        return profile

    @staticmethod
    def _normalize_name(value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise AppError(
                status_code=400,
                error_category="VALIDATION_FAILED",
                error_message="Session profile name cannot be empty.",
            )
        return normalized

    @staticmethod
    def _normalize_mode(value: str) -> str:
        normalized = value.strip()
        if normalized not in {"browser_profile", "app_session"}:
            raise AppError(
                status_code=400,
                error_category="VALIDATION_FAILED",
                error_message="Unsupported web session profile mode.",
            )
        return normalized

    @staticmethod
    def _normalize_channel(value: str | None) -> str:
        normalized = (value or "chromium").strip().lower()
        if normalized not in {"chromium", "chrome", "msedge"}:
            raise AppError(
                status_code=400,
                error_category="VALIDATION_FAILED",
                error_message="Unsupported browser channel.",
            )
        return normalized

    @staticmethod
    def _normalize_optional(value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    def _delete_managed_profile_path(self, managed_path: Path) -> None:
        resolved_root = self._managed_root.resolve()
        resolved_target = managed_path.resolve()
        if resolved_root == resolved_target or resolved_root not in resolved_target.parents:
            raise AppError(
                status_code=500,
                error_category="INGEST_FAILED",
                error_message="Managed session profile path is outside the application data root.",
            )
        shutil.rmtree(resolved_target, ignore_errors=True)


def _resolve_profile_status(profile: WebSessionProfile) -> tuple[str, str]:
    if profile.mode == "browser_profile":
        if profile.profile_path and Path(profile.profile_path).exists():
            return "ready", "本机浏览器 profile 可用。"
        return "invalid", "浏览器 profile 路径不存在或不可访问。"

    managed_path = Path(profile.managed_profile_path or "")
    if not profile.managed_profile_path:
        return "invalid", "应用内会话目录缺失。"
    if not managed_path.exists():
        return "needs_login", "应用内会话尚未初始化。"
    if any(item.is_file() for item in managed_path.rglob("*")):
        return "ready", "应用内会话已保存，可复用登录态。"
    return "needs_login", "应用内会话已创建，但还没有保存登录态。"


def run_managed_session_login(
    *,
    profile_path: str,
    browser_channel: str | None,
    login_url: str,
) -> None:
    try:
        sync_playwright = PlaywrightRunner._get_sync_playwright()
    except ImportError as exc:  # pragma: no cover - runtime dependency
        raise AppError(
            status_code=500,
            error_category="FETCH_FAILED",
            error_message=(
                "Playwright Python package is not available in the backend runtime. "
                "Run `uv sync --group test`, then restart the backend from `.venv`."
            ),
        ) from exc

    profile_dir = Path(profile_path)
    profile_dir.mkdir(parents=True, exist_ok=True)
    channel = None if browser_channel in {None, "chromium"} else browser_channel

    try:
        with sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir),
                channel=channel,
                headless=False,
            )
            try:
                page = context.pages[0] if context.pages else context.new_page()
                page.goto(login_url, wait_until="domcontentloaded", timeout=30000)
                deadline = time.monotonic() + 900
                while time.monotonic() < deadline:
                    try:
                        page.wait_for_timeout(500)
                    except Exception:
                        break
                else:
                    raise AppError(
                        status_code=408,
                        error_category="AUTH_REQUIRED",
                        error_message="Timed out waiting for the managed login session to finish.",
                    )
            finally:
                try:
                    context.close()
                except Exception:
                    pass
    except AppError:
        raise
    except Exception as exc:  # pragma: no cover - runtime dependency
        raise AppError(
            status_code=502,
            error_category="FETCH_FAILED",
            error_message=PlaywrightRunner._map_runtime_error(exc),
        ) from exc
