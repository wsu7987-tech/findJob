from __future__ import annotations

import random
from dataclasses import dataclass
from urllib.parse import urlencode

from backend.app.config import AppConfig
from backend.app.errors import AppError
from backend.app.services.fine_job.platform_sessions import load_boss_auth_state
from backend.app.services.web_capture.playwright_runner import PlaywrightRunner


BOSS_SEARCH_URL = "https://www.zhipin.com/web/geek/job"
BOSS_LOCAL_STORAGE_URL = "https://www.zhipin.com/desktop/"
CITY_CODES = {
    "全国": "100010000",
    "北京": "101010100",
    "上海": "101020100",
    "广州": "101280100",
    "深圳": "101280600",
    "杭州": "101210100",
    "成都": "101270100",
    "武汉": "101200100",
    "南京": "101190100",
    "苏州": "101190400",
    "远程": "100010000",
}
RISK_MARKERS = ["验证码", "安全验证", "风险", "异常访问", "扫码", "请输入手机号"]
LOGIN_MARKERS = ["登录/注册", "扫码登录", "微信登录", "验证码登录"]


@dataclass(slots=True)
class BossCollectedJob:
    keyword: str
    city: str
    job_url: str
    job_title: str
    company_name: str
    salary_text: str
    location_text: str
    experience_text: str
    education_text: str
    hr_active_text: str
    jd_text: str


def collect_boss_jobs(
    *,
    config: AppConfig,
    browser_channel: str | None,
    keyword: str,
    city: str,
    max_jobs: int = 3,
) -> list[BossCollectedJob]:
    """Collect BOSS job cards and JD text with saved cookies/localStorage."""
    try:
        sync_playwright = PlaywrightRunner._get_sync_playwright()
    except ImportError as exc:  # pragma: no cover - 运行时依赖
        raise AppError(
            status_code=500,
            error_category="FETCH_FAILED",
            error_message="Playwright Python package is not available in the backend runtime.",
        ) from exc

    cookies, local_storage = load_boss_auth_state(config)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                channel=_playwright_channel(browser_channel),
                headless=False,
                args=_normal_chrome_args(),
                ignore_default_args=["--enable-automation"],
                slow_mo=random.randint(80, 180),
            )
            try:
                context = browser.new_context(
                    viewport={"width": 1440, "height": 860},
                    locale="zh-CN",
                    timezone_id="Asia/Shanghai",
                )
                context.add_cookies(_normalize_cookies(cookies))
                page = context.new_page()
                _restore_local_storage(page, local_storage)
                _human_wait(page, 900, 1800)
                page.goto(
                    _build_search_url(keyword=keyword, city=city),
                    wait_until="domcontentloaded",
                    timeout=45000,
                )
                _human_wait(page, 1600, 3200)
                _raise_if_risk_page(page)
                _human_scroll(page)
                cards = _extract_cards(page, max_jobs=max_jobs)
                results: list[BossCollectedJob] = []
                for card in cards:
                    _human_wait(page, 800, 1800)
                    jd_text = _fetch_detail_text(context, card["job_url"])
                    results.append(BossCollectedJob(keyword=keyword, city=city, jd_text=jd_text, **card))
                return results
            finally:
                try:
                    browser.close()
                except Exception:
                    pass
    except AppError:
        raise
    except Exception as exc:  # pragma: no cover - 运行时依赖
        raise AppError(
            status_code=502,
            error_category="FETCH_FAILED",
            error_message=PlaywrightRunner._map_runtime_error(exc),
        ) from exc


def _build_search_url(*, keyword: str, city: str) -> str:
    params = {"query": keyword, "city": CITY_CODES.get(city, CITY_CODES["全国"])}
    return f"{BOSS_SEARCH_URL}?{urlencode(params)}"


def _human_wait(page, min_ms: int, max_ms: int) -> None:
    page.wait_for_timeout(random.randint(min_ms, max_ms))


def _normal_chrome_args() -> list[str]:
    return [
        "--start-maximized",
        "--disable-blink-features=AutomationControlled",
        "--no-default-browser-check",
        "--disable-search-engine-choice-screen",
    ]


def _playwright_channel(value: str | None) -> str | None:
    normalized = (value or "chrome").strip().lower()
    if normalized in {"edge", "msedge"}:
        return "msedge"
    if normalized in {"chromium", "system"}:
        return None
    return "chrome"


def _normalize_cookies(cookies: list[dict]) -> list[dict]:
    normalized = []
    for cookie in cookies:
        item = dict(cookie)
        if item.get("sameSite") == "unspecified":
            item.pop("sameSite", None)
        normalized.append(item)
    return normalized


def _restore_local_storage(page, local_storage: dict[str, str]) -> None:
    if not local_storage:
        return
    page.goto(BOSS_LOCAL_STORAGE_URL, wait_until="domcontentloaded", timeout=30000)
    page.evaluate(
        """
        (items) => {
          for (const [key, value] of Object.entries(items)) {
            window.localStorage.setItem(key, value);
          }
        }
        """,
        local_storage,
    )


def _human_scroll(page) -> None:
    for _ in range(random.randint(2, 4)):
        page.mouse.wheel(0, random.randint(350, 760))
        _human_wait(page, 600, 1400)


def _raise_if_risk_page(page) -> None:
    text = _safe_body_text(page)
    if any(marker in text for marker in [*RISK_MARKERS, *LOGIN_MARKERS]):
        raise AppError(
            status_code=423,
            error_category="AUTH_REQUIRED",
            error_message="BOSS 页面出现登录、验证码或风险提示，采集已暂停。",
        )


def _extract_cards(page, *, max_jobs: int) -> list[dict[str, str]]:
    cards = page.locator(".job-card-wrapper, .job-primary, li.job-card-wrapper, [class*='job-card']").all()
    results: list[dict[str, str]] = []
    seen_urls: set[str] = set()
    for card in cards:
        if len(results) >= max_jobs:
            break
        try:
            text = card.inner_text(timeout=1500).strip()
            if not text:
                continue
            href = card.locator("a").first.get_attribute("href", timeout=1000) or ""
            url = _normalize_url(href)
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            title = _first_text(card, [".job-name", ".job-title", "[class*='job-name']"]) or lines[0]
            company = _first_text(card, [".company-name", "[class*='company-name']"])
            salary = _first_text(card, [".salary", ".red", "[class*='salary']"])
            results.append(
                {
                    "job_url": url,
                    "job_title": title[:160],
                    "company_name": (company or _guess_company(lines))[:160],
                    "salary_text": (salary or _guess_salary(lines))[:80],
                    "location_text": _guess_by_markers(lines, ["上海", "北京", "杭州", "深圳", "广州", "远程"])[:80],
                    "experience_text": _guess_by_markers(lines, ["经验", "年"])[:80],
                    "education_text": _guess_by_markers(lines, ["本科", "大专", "硕士", "博士", "学历"])[:80],
                    "hr_active_text": _guess_by_markers(lines, ["活跃", "刚刚", "在线", "回复"])[:120],
                }
            )
        except Exception:
            continue
    return results


def _fetch_detail_text(context, url: str) -> str:
    if not url:
        return ""
    page = context.new_page()
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        _human_wait(page, 900, 1800)
        _raise_if_risk_page(page)
        return _safe_body_text(page)[:6000]
    finally:
        try:
            page.close()
        except Exception:
            pass


def _first_text(scope, selectors: list[str]) -> str:
    for selector in selectors:
        try:
            value = scope.locator(selector).first.inner_text(timeout=500).strip()
            if value:
                return value
        except Exception:
            continue
    return ""


def _safe_body_text(page) -> str:
    try:
        return page.locator("body").inner_text(timeout=3000)
    except Exception:
        return ""


def _normalize_url(href: str) -> str:
    if href.startswith("//"):
        return f"https:{href}"
    if href.startswith("/"):
        return f"https://www.zhipin.com{href}"
    return href


def _guess_company(lines: list[str]) -> str:
    return lines[1] if len(lines) > 1 else ""


def _guess_salary(lines: list[str]) -> str:
    for line in lines:
        if "K" in line or "k" in line or "薪" in line:
            return line
    return ""


def _guess_by_markers(lines: list[str], markers: list[str]) -> str:
    for line in lines:
        if any(marker in line for marker in markers):
            return line
    return ""
