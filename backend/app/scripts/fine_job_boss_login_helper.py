from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from playwright.sync_api import sync_playwright


BOSS_AUTH_COOKIE_NAMES = {
    "__zp_stoken__",
    "zp_stoken",
    "wt2",
    "wbg",
    "geek_zp_token",
}
BOSS_LOCAL_STORAGE_URL = "https://www.zhipin.com/desktop/"


def main() -> int:
    args = parse_args()
    auth_dir = Path(args.auth_dir).resolve()
    auth_dir.mkdir(parents=True, exist_ok=True)
    status_path = auth_dir / "boss-login-status.json"
    cookies_path = auth_dir / "boss-cookies.json"
    local_storage_path = auth_dir / "boss-local-storage.json"

    write_status(status_path, "running", "登录窗口已打开，请在 BOSS 页面完成登录。")
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                channel=playwright_channel(args.browser_channel),
                headless=False,
                args=normal_chrome_args(),
                ignore_default_args=["--enable-automation"],
                slow_mo=120,
            )
            context = browser.new_context(
                viewport={"width": 1440, "height": 860},
                locale="zh-CN",
                timezone_id="Asia/Shanghai",
            )
            page = context.new_page()
            page.goto(args.login_url, wait_until="domcontentloaded", timeout=45000)

            while True:
                page.wait_for_timeout(1200)
                if not context.pages:
                    write_status(status_path, "closed", "登录窗口已关闭，但未检测到登录成功。")
                    break
                active_page = context.pages[-1]
                if has_boss_auth_cookie(context):
                    save_auth_state(
                        context=context,
                        page=active_page,
                        cookies_path=cookies_path,
                        local_storage_path=local_storage_path,
                    )
                    write_status(status_path, "ready", "BOSS 登录态已保存。")
                    break
                if active_page.is_closed():
                    continue
            browser.close()
        return 0
    except Exception as exc:
        write_status(status_path, "failed", f"登录助手执行失败：{exc}")
        return 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--auth-dir", required=True)
    parser.add_argument("--login-url", required=True)
    parser.add_argument("--browser-channel", default="chrome")
    return parser.parse_args()


def playwright_channel(value: str | None) -> str | None:
    normalized = (value or "chrome").strip().lower()
    if normalized in {"edge", "msedge"}:
        return "msedge"
    if normalized in {"chromium", "system"}:
        return None
    return "chrome"


def normal_chrome_args() -> list[str]:
    return [
        "--start-maximized",
        "--disable-blink-features=AutomationControlled",
        "--no-default-browser-check",
        "--disable-search-engine-choice-screen",
    ]


def has_boss_auth_cookie(context) -> bool:
    try:
        cookies = context.cookies(["https://www.zhipin.com", "https://www.zhipin.com/"])
    except Exception:
        return False
    return any(str(cookie.get("name") or "") in BOSS_AUTH_COOKIE_NAMES for cookie in cookies)


def save_auth_state(*, context, page, cookies_path: Path, local_storage_path: Path) -> None:
    cookies = context.cookies(["https://www.zhipin.com", "https://www.zhipin.com/"])
    local_storage = read_page_local_storage(page)
    cookies_path.write_text(json.dumps(cookies, ensure_ascii=False, indent=2), encoding="utf-8")
    local_storage_path.write_text(
        json.dumps(local_storage, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def read_page_local_storage(page) -> dict[str, str]:
    try:
        if not page.url.startswith("https://www.zhipin.com"):
            page.goto(BOSS_LOCAL_STORAGE_URL, wait_until="domcontentloaded", timeout=30000)
        value = page.evaluate(
            """
            () => Object.fromEntries(
              Array.from({ length: window.localStorage.length }, (_, index) => {
                const key = window.localStorage.key(index);
                return [key, window.localStorage.getItem(key)];
              }).filter(([key]) => key)
            )
            """
        )
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def write_status(path: Path, status: str, message: str) -> None:
    payload: dict[str, Any] = {"status": status, "message": message}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
