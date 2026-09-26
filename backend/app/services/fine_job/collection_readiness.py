from __future__ import annotations

import time

from backend.app.errors import AppError
from backend.app.services.fine_job.collection_start_operations import current_operation, set_phase
from backend.app.services.fine_job.boss_scraper import boss_cdp_raw as engine
from backend.app.services.fine_job.boss_scraper.service import boss_scraper_service


def ensure_ready(*, expected_target_id: str | None = None) -> None:
    operation = current_operation()
    if operation and operation["ready"]:
        return
    token = engine.START_DEADLINE.set(time.monotonic() + 120)
    try:
        set_phase("checking_browser")
        status = boss_scraper_service.get_browser_status()
        if expected_target_id:
            # 原页续采只承认原 target；打开新页不能恢复旧游标。
            assert_original_target(expected_target_id)
        elif not status.running:
            set_phase("opening_browser")
            if boss_scraper_service.start_browser(wait_login=False) != 0:
                raise AppError(502, "BROWSER_OPEN_FAILED", "无法打开 FineJob 专用浏览器。")
        if not expected_target_id and boss_scraper_service._find_interactive_target(engine.DEFAULT_CDP_PORT) is None:
            boss_scraper_service.open_login_page()
        set_phase("checking_login")
        result = engine.check_login_state()
        engine.remaining_timeout(1)
        if result.status is not engine.LoginProbeStatus.AVAILABLE:
            raise AppError(409, "LOGIN_" + result.status.value.upper(), engine.describe_login_probe_result(result))
        if expected_target_id:
            assert_original_target(expected_target_id)
        if operation:
            operation["ready"] = True
        set_phase("dispatching")
    except TimeoutError as exc:
        raise AppError(504, "LOGIN_CHECK_TIMEOUT", "浏览器与登录检查超时，请检查启动结果。") from exc
    finally:
        engine.START_DEADLINE.reset(token)


def assert_original_target(target_id: str) -> None:
    if not engine.is_cdp_ready():
        raise AppError(409, "CAPTURE_PAGE_NOT_REUSABLE", "原搜索页已关闭或被替换，无法续采；已采集结果已保留。")
    cdp = engine.CDPSession()
    try:
        targets = cdp.send("Target.getTargets").get("result", {}).get("targetInfos", [])
        if not any(str(target.get("targetId")) == target_id and boss_scraper_service._is_search_url(str(target.get("url") or "")) for target in targets):
            raise AppError(409, "CAPTURE_PAGE_NOT_REUSABLE", "原搜索页已关闭或被替换，无法续采；已采集结果已保留。")
    finally:
        cdp.close()
