from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Callable
from urllib.parse import parse_qs, urlparse

from backend.app.services.fine_job.boss_scraper import boss_cdp_raw as engine


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


@dataclass(frozen=True, slots=True)
class BossCaptureResult:
    list_data: dict[str, object]
    details: list[dict[str, object]] | None
    jobs_path: Path
    details_path: Path | None
    source_url: str | None = None
    used_current_page: bool = False


@dataclass(frozen=True, slots=True)
class BossBrowserStatus:
    running: bool
    cdp_port: int
    current_url: str | None = None
    current_title: str | None = None
    is_search_page: bool = False


class BossScraperService:
    """FineJob 内部调用 CDP 采集引擎的薄服务层。

    关键边界：上游脚本保留采集实现，本类只负责稳定的函数接口、输出路径和
    单次任务隔离。其他 FineJob 模块应调用这里，不直接依赖引擎内部细节。
    """

    def __init__(self) -> None:
        self._interactive_target_id: str | None = None

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
            if request.include_details and list_data.get("jobs"):
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
            and parsed.path.rstrip("/") == "/web/geek/job"
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
