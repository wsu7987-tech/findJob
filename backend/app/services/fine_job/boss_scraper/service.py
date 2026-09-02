from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Callable
from urllib.parse import parse_qs, urlencode, urlparse

from backend.app.services.fine_job.boss_scraper import boss_cdp_raw as engine
from backend.app.services.fine_job.boss_scraper.boss_job_detail import fetch_job_detail


# 引擎的请求计数和 CDP 事件缓冲是进程级状态，因此即使调用方创建多个服务实例，
# 也必须共享同一把锁，避免并行任务交叉消费响应或覆盖请求预算。
_CAPTURE_LOCK = Lock()


@dataclass(frozen=True, slots=True)
class BossCaptureRequest:
    keyword: str
    city: str
    pages: int = 3
    filters: dict[str, str] = field(default_factory=dict)
    include_details: bool = True
    max_details: int | None = None
    output_dir: Path | None = None
    cdp_port: int = engine.DEFAULT_CDP_PORT
    output_format: str = "json"
    prefer_current_page: bool = False
    filter_strategy_id: str | None = None


@dataclass(frozen=True, slots=True)
class BossCaptureResult:
    list_data: dict[str, object]
    details: list[dict[str, object]] | None
    jobs_path: Path
    details_path: Path | None
    source_url: str | None = None
    used_current_page: bool = False
    capture_target_id: str | None = None


@dataclass(frozen=True, slots=True)
class BossBrowserStatus:
    running: bool
    cdp_port: int
    current_url: str | None = None
    current_title: str | None = None
    is_search_page: bool = False


class BossScraperService:
    """FineJob 内部调用 CDP 采集引擎的薄服务层。

    关键边界：本类只负责稳定的函数接口、输出路径和单次任务隔离。
    其他 FineJob 模块应调用这里，不直接依赖引擎内部细节。
    """

    def __init__(self) -> None:
        self._interactive_target_id: str | None = None
        self._chat_target_id: str | None = None

    def check_environment(self, *, cdp_port: int = engine.DEFAULT_CDP_PORT) -> int:
        return engine.run_check(cdp_port)

    def list_cities(self) -> list[dict[str, str]]:
        """读取内置城市码表，避免城市下拉框额外访问 BOSS 在线接口。"""
        name_to_code, _code_to_name = engine.load_local_city_map()
        return [
            {"name": name, "code": code}
            for name, code in sorted(name_to_code.items(), key=lambda item: item[0])
        ]

    def smoke_test(self, *, cdp_port: int = engine.DEFAULT_CDP_PORT) -> int:
        return engine.run_smoke_test(cdp_port)

    def start_browser(
        self,
        *,
        cdp_port: int = engine.DEFAULT_CDP_PORT,
        wait_login: bool = True,
        login_timeout: int = engine.DEFAULT_LOGIN_TIMEOUT,
    ) -> int:
        return engine.run_setup_chrome(
            cdp_port,
            copy_login_state=False,
            reset_profile=False,
            wait_login=wait_login,
            login_timeout=login_timeout,
        )

    def stop_browser(self) -> int:
        self._interactive_target_id = None
        self._chat_target_id = None
        return engine.run_stop_chrome()

    def open_login_page(
        self,
        *,
        cdp_port: int = engine.DEFAULT_CDP_PORT,
    ) -> str:
        if not engine.is_cdp_ready(cdp_port):
            raise RuntimeError("FineJob 专用 Chrome 未启动，请先打开浏览器。")
        login_url = "https://www.zhipin.com/web/user/"
        cdp = engine.CDPSession(cdp_port)
        try:
            created = cdp.send(
                "Target.createTarget",
                {"url": login_url, "background": False},
            )
            self._interactive_target_id = str(created["result"]["targetId"])
        finally:
            cdp.close()
        return login_url

    def capture_chat_friend_list(
        self,
        *,
        cdp_port: int = engine.DEFAULT_CDP_PORT,
        timeout: int = 30,
    ) -> dict[str, object]:
        """打开聊天页并旁听页面自身发出的联系人列表请求。"""
        if not engine.is_cdp_ready(cdp_port):
            raise RuntimeError("FineJob 专用 Chrome 未启动，请先打开浏览器。")

        chat_url = "https://www.zhipin.com/web/geek/chat?ka=header-message"
        with _CAPTURE_LOCK:
            cdp = engine.CDPSession(cdp_port)
            target_id = ""
            session_id = ""
            try:
                targets = cdp.send("Target.getTargets").get("result", {}).get("targetInfos", [])
                target = next(
                    (
                        item for item in targets
                        if item.get("type") == "page"
                        and str(item.get("targetId") or "") == self._chat_target_id
                    ),
                    None,
                )
                if target is None:
                    target = next(
                        (
                            item for item in targets
                            if item.get("type") == "page"
                            and urlparse(str(item.get("url") or "")).path.rstrip("/") == "/web/geek/chat"
                        ),
                        None,
                    )
                if target is None:
                    created = cdp.send(
                        "Target.createTarget",
                        {"url": "about:blank", "background": False},
                    )
                    target_id = str(created["result"]["targetId"])
                else:
                    target_id = str(target["targetId"])
                session_id = engine.attach_page_session(cdp, target_id)
                capture = engine.NetworkChatFriendListCapture(cdp, session_id)
                capture.enable()
                cdp.send("Page.navigate", {"url": chat_url}, session_id)
                cdp.send("Target.activateTarget", {"targetId": target_id})
                data = capture.wait_next_response(timeout=timeout)
                if not isinstance(data, dict):
                    raise RuntimeError("聊天页未捕获到联系人列表接口响应，请确认已登录 BOSS。")
                account_uid = cdp.eval_js(
                    "String((window._PAGE && (window._PAGE.uid || window._PAGE.userId)) || '')",
                    session_id,
                )
                account_uid = str(account_uid or "").strip()
                if not account_uid:
                    raise RuntimeError("未能从聊天页读取当前 BOSS 账号，请确认登录状态。")
                self._chat_target_id = target_id
                self._interactive_target_id = target_id
                return {
                    "url": chat_url,
                    "account_uid": account_uid,
                    "response": data,
                    "target_id": target_id,
                }
            finally:
                try:
                    if session_id:
                        cdp.send("Network.disable", {}, session_id, timeout=3)
                except Exception:
                    pass
                cdp.close()

    def capture_chat_history(
        self,
        *,
        boss_id: str,
        security_id: str,
        max_message_id: str = "0",
        cdp_port: int = engine.DEFAULT_CDP_PORT,
        page_size: int = 20,
        timeout: int = 30,
    ) -> dict[str, object]:
        """使用聊天页登录态请求指定 HR 的历史消息接口。"""
        boss_id = boss_id.strip()
        security_id = security_id.strip()
        if not boss_id or not security_id:
            raise ValueError("获取聊天记录缺少 encryptFriendId 或 securityId。")
        if not engine.is_cdp_ready(cdp_port):
            raise RuntimeError("FineJob 专用 Chrome 未启动，请先打开浏览器。")

        with _CAPTURE_LOCK:
            cdp = engine.CDPSession(cdp_port)
            session_id = ""
            try:
                targets = cdp.send("Target.getTargets").get("result", {}).get("targetInfos", [])
                target = next(
                    (
                        item for item in targets
                        if item.get("type") == "page"
                        and str(item.get("targetId") or "") == self._chat_target_id
                    ),
                    None,
                )
                if target is None:
                    target = next(
                        (
                            item for item in targets
                            if item.get("type") == "page"
                            and urlparse(str(item.get("url") or "")).path.rstrip("/") == "/web/geek/chat"
                        ),
                        None,
                )
                if target is None:
                    raise RuntimeError("BOSS 聊天页尚未准备，请先点击“更新信息”。")

                target_id = str(target["targetId"])
                session_id = engine.attach_page_session(cdp, target_id)
                query = urlencode({
                    "bossId": boss_id,
                    "maxMsgId": max_message_id,
                    "c": page_size,
                    "page": 1,
                    "src": 0,
                    "securityId": security_id,
                })
                url = f"https://www.zhipin.com{engine.API_CHAT_HISTORY_PATH}?{query}"
                expression = (
                    "(async () => {"
                    f"const response = await fetch({json.dumps(url)}, {{credentials: 'include'}});"
                    "return {status: response.status, text: await response.text()};"
                    "})()"
                )
                result = cdp.send(
                    "Runtime.evaluate",
                    {
                        "expression": expression,
                        "awaitPromise": True,
                        "returnByValue": True,
                    },
                    session_id,
                    timeout=timeout,
                )
                value = result.get("result", {}).get("result", {}).get("value") or {}
                if int(value.get("status") or 0) != 200:
                    raise RuntimeError(f"聊天历史接口请求失败，HTTP 状态码 {value.get('status')}。")
                try:
                    payload = json.loads(str(value.get("text") or ""))
                except (TypeError, ValueError) as exc:
                    raise RuntimeError("聊天历史接口返回内容不是有效 JSON。") from exc
                if payload.get("code") not in (None, 0):
                    raise RuntimeError(str(payload.get("message") or "BOSS 聊天历史接口返回失败。"))
                zp_data = payload.get("zpData") or {}
                page_messages = zp_data.get("messages") if isinstance(zp_data, dict) else None
                if not isinstance(page_messages, list):
                    raise RuntimeError("聊天历史接口响应中没有 messages。")
                messages = [message for message in page_messages if isinstance(message, dict)]
                has_more = bool(zp_data.get("hasMore"))
                next_cursor = str(zp_data.get("minMsgId") or "") if has_more else ""
                return {
                    "url": url,
                    "messages": messages,
                    "target_id": target_id,
                    "has_more": bool(has_more and next_cursor),
                    "next_cursor": next_cursor,
                }
            finally:
                try:
                    if session_id:
                        cdp.send("Runtime.disable", {}, session_id, timeout=3)
                except Exception:
                    pass
                cdp.close()

    def get_browser_status(
        self,
        *,
        cdp_port: int = engine.DEFAULT_CDP_PORT,
    ) -> BossBrowserStatus:
        if not engine.is_cdp_ready(cdp_port):
            return BossBrowserStatus(running=False, cdp_port=cdp_port)

        target = self._find_interactive_target(cdp_port)
        return BossBrowserStatus(
            running=True,
            cdp_port=cdp_port,
            current_url=str(target.get("url") or "") if target else None,
            current_title=str(target.get("title") or "") if target else None,
            is_search_page=self._is_search_url(str(target.get("url") or "")) if target else False,
        )

    def check_login(
        self,
        *,
        cdp_port: int = engine.DEFAULT_CDP_PORT,
    ) -> tuple[bool, str]:
        if not engine.is_cdp_ready(cdp_port):
            return False, "FineJob 专用 Chrome 未启动，请先打开浏览器。"
        result = engine.check_login_state(cdp_port)
        if result.status is engine.LoginProbeStatus.AVAILABLE:
            return True, "已检测到 BOSS 登录态，搜索结果可正常读取。"
        return False, engine.describe_login_probe_result(result)

    def locate_search_page(
        self,
        *,
        keyword: str,
        city: str,
        filters: dict[str, str] | None = None,
        cdp_port: int = engine.DEFAULT_CDP_PORT,
    ) -> str:
        keyword = keyword.strip()
        city = city.strip()
        if not keyword:
            raise ValueError("keyword must not be empty")
        if not city:
            raise ValueError("city must not be empty")
        if not engine.is_cdp_ready(cdp_port):
            raise RuntimeError("FineJob 专用 Chrome 未启动，请先打开浏览器。")

        _city_name, city_code = engine.resolve_city(city)
        url = engine.build_search_url(keyword, city_code, 1, filters or {})
        cdp = engine.CDPSession(cdp_port)
        try:
            target = self._find_interactive_target(cdp_port)
            if target:
                target_id = str(target["targetId"])
                session_id = engine.attach_page_session(cdp, target_id)
                cdp.send("Page.navigate", {"url": url}, session_id)
                cdp.send("Target.activateTarget", {"targetId": target_id})
            else:
                created = cdp.send("Target.createTarget", {"url": url, "background": False})
                target_id = str(created["result"]["targetId"])
            self._interactive_target_id = target_id
            return url
        finally:
            cdp.close()

    def open_job_page(
        self,
        url: str,
        *,
        cdp_port: int = engine.DEFAULT_CDP_PORT,
    ) -> str:
        """在FineJob专用Chrome的交互标签页打开岗位，但不执行任何平台动作。"""
        parsed = urlparse(url)
        if (
            parsed.scheme != "https"
            or parsed.hostname not in {"www.zhipin.com", "zhipin.com"}
            or "/job_detail/" not in parsed.path
        ):
            raise ValueError("岗位地址不是受支持的 BOSS HTTPS 详情页。")
        if not engine.is_cdp_ready(cdp_port):
            raise RuntimeError("FineJob 专用 Chrome 未启动，请先打开浏览器。")

        cdp = engine.CDPSession(cdp_port)
        try:
            target = self._find_interactive_target(cdp_port)
            if target:
                target_id = str(target["targetId"])
                session_id = engine.attach_page_session(cdp, target_id)
                cdp.send("Page.navigate", {"url": url}, session_id)
                cdp.send("Target.activateTarget", {"targetId": target_id})
            else:
                created = cdp.send("Target.createTarget", {"url": url, "background": False})
                target_id = str(created["result"]["targetId"])
            self._interactive_target_id = target_id
            return target_id
        finally:
            cdp.close()

    def reload_job_page(
        self,
        target_id: str,
        expected_encrypt_job_id: str,
        *,
        cdp_port: int = engine.DEFAULT_CDP_PORT,
    ) -> str:
        """只刷新已记录的同一岗位标签页，不创建新页面，也不执行平台业务动作。"""
        if not engine.is_cdp_ready(cdp_port):
            raise RuntimeError("FineJob 专用 Chrome 未启动，请先打开浏览器。")
        expected_encrypt_job_id = expected_encrypt_job_id.strip()
        if not target_id or not expected_encrypt_job_id:
            raise ValueError("刷新验证缺少目标标签页或岗位标识。")

        cdp = engine.CDPSession(cdp_port)
        try:
            response = cdp.send("Target.getTargets")
            target = next(
                (
                    item for item in response.get("result", {}).get("targetInfos", [])
                    if item.get("type") == "page" and str(item.get("targetId") or "") == target_id
                ),
                None,
            )
            if target is None:
                raise RuntimeError("用于验证的BOSS岗位标签页已经关闭。")
            current_url = str(target.get("url") or "")
            if not (
                "/job_detail/" in urlparse(current_url).path
                and expected_encrypt_job_id in urlparse(current_url).path
            ):
                raise RuntimeError("用于验证的标签页已经不是原岗位详情页。")
            session_id = engine.attach_page_session(cdp, target_id)
            cdp.send("Page.reload", {"ignoreCache": False}, session_id)
            cdp.send("Target.activateTarget", {"targetId": target_id})
            self._interactive_target_id = target_id
            return target_id
        finally:
            cdp.close()

    def capture_jobs(
        self,
        request: BossCaptureRequest,
        *,
        progress_callback: Callable[[dict[str, object]], None] | None = None,
        should_stop: Callable[[], bool] | None = None,
    ) -> BossCaptureResult:
        if not request.keyword.strip():
            raise ValueError("keyword must not be empty")
        if not request.city.strip():
            raise ValueError("city must not be empty")
        if request.pages < 1 or request.pages > engine.MAX_PAGES:
            raise ValueError(f"pages must be between 1 and {engine.MAX_PAGES}")
        if request.output_format not in {"json", "csv"}:
            raise ValueError("output_format must be json or csv")

        output_dir = Path(request.output_dir or engine.DEFAULT_RESULT_DIR).expanduser().resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        jobs_path = output_dir / f"boss_jobs_{timestamp}.json"
        details_path = (
            output_dir / f"boss_details_{timestamp}.json" if request.include_details else None
        )

        with _CAPTURE_LOCK:
            # CLI 每次启动都会自然重置该计数；内置为长生命周期模块后需显式重置，
            # 才能保持“每个采集任务独立计算请求预算”的原始语义。
            engine._request_counter = 0
            capture_target = None
            source_url = None
            used_current_page = False
            if request.prefer_current_page:
                capture_target = self._find_interactive_target(request.cdp_port)
                if capture_target and self._is_search_url(str(capture_target.get("url") or "")):
                    source_url = str(capture_target["url"])
                    used_current_page = True
                else:
                    source_url = self.locate_search_page(
                        keyword=request.keyword,
                        city=request.city,
                        filters=request.filters,
                        cdp_port=request.cdp_port,
                    )
                    capture_target = self._find_interactive_target(request.cdp_port)

            scrape_kwargs = {
                "cdp_port": request.cdp_port,
                "fmt": request.output_format,
                "allow_dom_fallback": False,
            }
            if capture_target:
                scrape_kwargs.update(
                    target_id=str(capture_target["targetId"]),
                    start_url=source_url,
                    close_target=False,
                )
            if progress_callback:
                scrape_kwargs["progress_callback"] = progress_callback
            if should_stop:
                scrape_kwargs["should_stop"] = should_stop

            list_keyword, list_city = self._capture_metadata(
                source_url,
                fallback_keyword=request.keyword.strip(),
                fallback_city=request.city.strip(),
            )
            try:
                list_data = engine.scrape_list(
                    list_keyword,
                    list_city,
                    request.pages,
                    dict(request.filters),
                    str(jobs_path),
                    **scrape_kwargs,
                )
            except engine.LoginGateError as exc:
                raise RuntimeError(str(exc)) from exc
            details = None
            if (
                request.include_details
                and list_data.get("jobs")
                and not list_data.get("stopped")
            ):
                if progress_callback:
                    progress_callback({
                        "stage": "list_completed",
                        "current": 0,
                        "total": len(list_data["jobs"]),
                        "jobs": list_data["jobs"],
                        "jobs_path": str(jobs_path),
                        "details_path": str(details_path) if details_path else None,
                        "message": f"岗位列表采集完成，共获得 {len(list_data['jobs'])} 个岗位。",
                    })
                detail_kwargs = {
                    "cdp_port": request.cdp_port,
                    "fmt": request.output_format,
                }
                if progress_callback:
                    detail_kwargs["progress_callback"] = progress_callback
                details = engine.scrape_details(
                    list_data,
                    request.max_details,
                    str(details_path),
                    **detail_kwargs,
                )
            elif progress_callback:
                jobs = list_data.get("jobs") or []
                progress_callback({
                    "stage": "list_completed",
                    "current": len(jobs),
                    "total": len(jobs),
                    "jobs": jobs,
                    "jobs_path": str(jobs_path),
                    "details_path": None,
                    "message": f"岗位列表采集完成，共获得 {len(jobs)} 个岗位。",
                })

        return BossCaptureResult(
            list_data=list_data,
            details=details,
            jobs_path=jobs_path,
            details_path=details_path,
            source_url=source_url,
            used_current_page=used_current_page,
            capture_target_id=(
                str(capture_target.get("targetId") or "") or None
                if capture_target
                else None
            ),
        )

    def capture_more_jobs(
        self,
        request: BossCaptureRequest,
        *,
        list_data: dict[str, object],
        jobs_path: Path,
        expected_target_id: str,
        progress_callback: Callable[[dict[str, object]], None] | None = None,
        should_stop: Callable[[], bool] | None = None,
    ) -> BossCaptureResult:
        """在首次采集保留的搜索页面中继续下滑并追加岗位。"""
        if request.pages < 1 or request.pages > engine.MAX_PAGES:
            raise ValueError(f"pages must be between 1 and {engine.MAX_PAGES}")
        with _CAPTURE_LOCK:
            capture_target = self._find_interactive_target(request.cdp_port)
            target_id = str((capture_target or {}).get("targetId") or "")
            current_url = str((capture_target or {}).get("url") or "")
            if not capture_target or target_id != expected_target_id:
                raise RuntimeError("原 BOSS 搜索页面已经关闭或被替换，无法继续下滑采集。")
            if not self._is_search_url(current_url):
                raise RuntimeError("原 BOSS 搜索页面已离开搜索结果，无法继续下滑采集。")
            keyword, city = self._capture_metadata(
                current_url,
                fallback_keyword=request.keyword.strip(),
                fallback_city=request.city.strip(),
            )
            scrape_kwargs: dict[str, object] = {
                "cdp_port": request.cdp_port,
                "fmt": request.output_format,
                "allow_dom_fallback": False,
                "target_id": target_id,
                "start_url": current_url,
                "close_target": False,
                "existing_jobs": list(list_data.get("jobs") or []),
                "continue_current_page": True,
            }
            if progress_callback:
                scrape_kwargs["progress_callback"] = progress_callback
            if should_stop:
                scrape_kwargs["should_stop"] = should_stop
            continued_list = engine.scrape_list(
                keyword,
                city,
                request.pages,
                dict(request.filters),
                str(jobs_path),
                **scrape_kwargs,
            )
            if progress_callback:
                jobs = list(continued_list.get("jobs") or [])
                progress_callback(
                    {
                        "stage": "list_completed",
                        "current": len(jobs),
                        "total": len(jobs),
                        "jobs": jobs,
                        "jobs_path": str(jobs_path),
                        "details_path": None,
                        "message": (
                            f"继续下滑采集完成，新增 {continued_list.get('new_jobs_count', 0)} 个岗位，"
                            f"累计 {len(jobs)} 个岗位。"
                        ),
                    }
                )
        return BossCaptureResult(
            list_data=continued_list,
            details=None,
            jobs_path=jobs_path,
            details_path=None,
            source_url=current_url,
            used_current_page=True,
            capture_target_id=target_id,
        )

    def capture_selected_details(
        self,
        *,
        list_data: dict[str, object],
        job_ids: list[str],
        output_path: Path,
        cdp_port: int = engine.DEFAULT_CDP_PORT,
        progress_callback: Callable[[dict[str, object]], None] | None = None,
    ) -> list[dict[str, object]]:
        selected = set(job_ids)
        jobs = [
            job
            for job in list_data.get("jobs") or []
            if isinstance(job, dict) and str(job.get("job_id") or "") in selected
        ]
        if not jobs:
            raise ValueError("请至少选择一个尚未完成详情采集的岗位")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with _CAPTURE_LOCK:
            return engine.scrape_details(
                {**list_data, "jobs": jobs},
                None,
                str(output_path),
                cdp_port=cdp_port,
                fmt="json",
                progress_callback=progress_callback,
            )

    def capture_chat_job_detail(
        self,
        *,
        job: dict[str, object],
        output_path: Path,
        cdp_port: int = engine.DEFAULT_CDP_PORT,
        progress_callback: Callable[[dict[str, object]], None] | None = None,
    ) -> dict[str, object]:
        """使用独立详情脚本获取聊天岗位字段，保持普通批量详情链路不变。"""
        with _CAPTURE_LOCK:
            return fetch_job_detail(
                job,
                output_path=output_path,
                cdp_port=cdp_port,
                progress_callback=progress_callback,
            )

    def _find_interactive_target(self, cdp_port: int) -> dict[str, object] | None:
        if not engine.is_cdp_ready(cdp_port):
            return None
        cdp = engine.CDPSession(cdp_port)
        try:
            response = cdp.send("Target.getTargets")
            targets = [
                target
                for target in response.get("result", {}).get("targetInfos", [])
                if target.get("type") == "page"
            ]
        finally:
            cdp.close()

        if self._interactive_target_id:
            selected = next(
                (target for target in targets if target.get("targetId") == self._interactive_target_id),
                None,
            )
            if selected:
                return selected
            self._interactive_target_id = None

        selected = next(
            (target for target in reversed(targets) if self._is_search_url(str(target.get("url") or ""))),
            None,
        )
        if selected:
            self._interactive_target_id = str(selected["targetId"])
        return selected

    @staticmethod
    def _is_search_url(url: str) -> bool:
        parsed = urlparse(url)
        return (
            parsed.hostname in {"www.zhipin.com", "zhipin.com"}
            # 兼容旧单数路径和 BOSS 当前重定向后的复数搜索路径。
            and parsed.path.rstrip("/") in {"/web/geek/job", "/web/geek/jobs"}
        )

    @staticmethod
    def _capture_metadata(
        source_url: str | None,
        *,
        fallback_keyword: str,
        fallback_city: str,
    ) -> tuple[str, str]:
        if not source_url:
            return fallback_keyword, fallback_city
        query = parse_qs(urlparse(source_url).query)
        keyword = str((query.get("query") or [fallback_keyword])[0]).strip() or fallback_keyword
        city = str((query.get("city") or [fallback_city])[0]).strip() or fallback_city
        return keyword, city


boss_scraper_service = BossScraperService()
