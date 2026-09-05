from __future__ import annotations

import json
from typing import Any, Callable

from backend.app.config import AppConfig
from backend.app.db import Database
from backend.app.errors import AppError
from backend.app.services.fine_job.boss_capture_history import (
    get_capture_history_job,
    update_capture_job_delivery_evaluation,
)
from backend.app.services.fine_job.boss_capture_tasks import boss_capture_task_manager
from backend.app.services.fine_job.boss_chat import (
    cancel_reply,
    confirm_reply,
    get_session,
    list_sessions,
)
from backend.app.services.fine_job.boss_scraper.service import (
    BossCaptureRequest,
    boss_scraper_service,
)
from backend.app.services.fine_job.codex_authorization import (
    classify_outbound_content,
    resolve_codex_authorization,
)
from backend.app.services.fine_job.resumes import list_resume_facts, list_resumes
from backend.app.schemas.fine_job.resume_analysis_v2 import ResumeAnalysisRunCreate
from backend.app.services.fine_job import (
    profile_analysis,
    profile_store,
    profile_v3,
    resume_analysis_v2,
)
from backend.app.services.fine_job.delivery_strategies import get_delivery_strategy
from backend.app.services.fine_job.job_evaluation import evaluate_filter_strategy
from backend.app.services.fine_job.profile_context import get_profile_context
from backend.app.services.fine_job.strategies import (
    get_filter_strategy,
    get_recommendation_strategy,
    list_filter_strategies,
    list_recommendation_strategies,
    list_search_keywords,
)
from backend.app.services.fine_job.workflow import (
    approve_review_item,
    record_evaluation_and_route,
    reject_review_item,
)
from backend.app.services.fine_job import companies
from backend.app.services.fine_job.filter_exclusions import (
    apply_filter_exclusions,
    assert_job_action_allowed,
    record_job_event,
)
from backend.app.services.fine_job.job_applications import set_job_application
from backend.app.services.fine_job import job_hunt_analysis, job_hunt_refresh
from backend.app.utils import new_id, utc_now


CORE_TOOLS = (
    "finejob.get_capabilities",
    "finejob.get_job_hunt_refresh_run",
    "finejob.list_job_hunt_refresh_items",
    "finejob.refresh_job_hunt_chat_batch",
    "finejob.refresh_job_hunt_chat_messages",
    "finejob.refresh_job_hunt_related_job",
    "finejob.prepare_job_hunt_refresh_analysis",
    "finejob.get_job_hunt_refresh_analysis_item_context",
    "finejob.list_job_hunt_refresh_analysis_items",
    "finejob.save_job_hunt_refresh_analysis",
    "finejob.complete_job_hunt_refresh_run",
    "finejob.search_jobs",
    "finejob.list_companies",
    "finejob.set_company_type",
    "finejob.set_company_blacklist",
    "finejob.record_job_application",
    "finejob.list_job_strategies",
    "finejob.get_job_evaluation_context",
    "finejob.start_job_capture",
    "finejob.continue_job_capture",
    "finejob.stop_job_capture",
    "finejob.apply_job_filter",
    "finejob.collect_job_details",
    "finejob.get_job_jd",
    "finejob.get_job_context",
    "finejob.collect_job_detail",
    "finejob.list_resumes",
    "finejob.get_resume_facts",
    "finejob.list_profiles",
    "finejob.get_profile_analysis_input",
    "finejob.save_profile_analysis_draft",
    "finejob.list_resume_families",
    "finejob.get_resume_analysis_plan",
    "finejob.get_resume_operation_input",
    "finejob.save_resume_operation_result",
    "finejob.get_resume_analysis_run",
    "finejob.list_resume_family_strategies",
    "finejob.get_profile_context",
    "finejob.list_profile_questions",
    "finejob.list_chat_sessions",
    "finejob.get_chat_context",
    "finejob.save_job_evaluation",
    "finejob.create_greeting_preview",
    "finejob.save_chat_reply_draft",
    "finejob.request_greeting_execution",
    "finejob.request_chat_send",
    "finejob.get_operation_status",
)


def _loads(value: object, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(str(value))
    except json.JSONDecodeError:
        return default


def _resource(resource_type: str, resource_id: str, version: int = 1) -> dict[str, object]:
    return {"type": resource_type, "id": resource_id, "version": version}


def _result(
    *,
    result_type: str,
    status: str,
    data: Any,
    resource: dict[str, object] | None = None,
    terminal: bool = True,
    requires_confirmation: bool = False,
    message: str = "",
    authorization: dict[str, object] | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "ok": True,
        "result_type": result_type,
        "resource": resource,
        "status": status,
        "terminal": terminal,
        "requires_confirmation": requires_confirmation,
        "sensitive_operation": bool(authorization),
        "authorization_mode": (authorization or {}).get("authorization_mode"),
        "message": message,
        "data": data,
        "error": None,
    }
    if authorization:
        payload["authorization"] = authorization
    return payload


class CodexToolService:
    """将稳定 MCP 契约映射到 FineJob 现有领域服务和业务表。"""

    def __init__(self, db: Database, config: AppConfig) -> None:
        self.db = db
        self.config = config
        self._handlers: dict[str, Callable[[dict[str, Any]], dict[str, object]]] = {
            "finejob.get_capabilities": self.get_capabilities,
            "finejob.get_job_hunt_refresh_run": self.get_job_hunt_refresh_run,
            "finejob.list_job_hunt_refresh_items": self.list_job_hunt_refresh_items,
            "finejob.refresh_job_hunt_chat_batch": self.refresh_job_hunt_chat_batch,
            "finejob.refresh_job_hunt_chat_messages": self.refresh_job_hunt_chat_messages,
            "finejob.refresh_job_hunt_related_job": self.refresh_job_hunt_related_job,
            "finejob.prepare_job_hunt_refresh_analysis": self.prepare_job_hunt_refresh_analysis,
            "finejob.get_job_hunt_refresh_analysis_item_context": self.get_job_hunt_refresh_analysis_item_context,
            "finejob.list_job_hunt_refresh_analysis_items": self.list_job_hunt_refresh_analysis_items,
            "finejob.save_job_hunt_refresh_analysis": self.save_job_hunt_refresh_analysis,
            "finejob.complete_job_hunt_refresh_run": self.complete_job_hunt_refresh_run,
            "finejob.list_job_strategies": self.list_job_strategies,
            "finejob.get_job_evaluation_context": self.get_job_evaluation_context,
            "finejob.start_job_capture": self.start_job_capture,
            "finejob.continue_job_capture": self.continue_job_capture,
            "finejob.stop_job_capture": self.stop_job_capture,
            "finejob.apply_job_filter": self.apply_job_filter,
            "finejob.collect_job_details": self.collect_job_details,
            "finejob.get_job_jd": self.get_job_jd,
            "finejob.search_jobs": self.search_jobs,
            "finejob.get_job_context": self.get_job_context,
            "finejob.collect_job_detail": self.collect_job_detail,
            "finejob.list_resumes": self.list_resumes,
            "finejob.get_resume_facts": self.get_resume_facts,
            "finejob.list_profiles": self.list_profiles,
            "finejob.get_profile_analysis_input": self.get_profile_analysis_input,
            "finejob.save_profile_analysis_draft": self.save_profile_analysis_draft,
            "finejob.list_resume_families": self.list_resume_families,
            "finejob.get_resume_analysis_plan": self.get_resume_analysis_plan,
            "finejob.get_resume_operation_input": self.get_resume_operation_input,
            "finejob.save_resume_operation_result": self.save_resume_operation_result,
            "finejob.get_resume_analysis_run": self.get_resume_analysis_run,
            "finejob.list_resume_family_strategies": self.list_resume_family_strategies,
            "finejob.get_profile_context": self.get_profile_context,
            "finejob.list_profile_questions": self.list_profile_questions,
            "finejob.list_chat_sessions": self.list_chat_sessions,
            "finejob.get_chat_context": self.get_chat_context,
            "finejob.save_job_evaluation": self.save_job_evaluation,
            "finejob.create_greeting_preview": self.create_greeting_preview,
            "finejob.save_chat_reply_draft": self.save_chat_reply_draft,
            "finejob.request_greeting_execution": self.request_greeting_execution,
            "finejob.request_chat_send": self.request_chat_send,
            "finejob.get_operation_status": self.get_operation_status,
            "finejob.list_companies": self.list_companies,
            "finejob.set_company_type": self.set_company_type,
            "finejob.set_company_blacklist": self.set_company_blacklist,
            "finejob.record_job_application": self.record_job_application,
        }

    def call(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, object]:
        handler = self._handlers.get(tool_name)
        if handler is None:
            raise AppError(
                status_code=404,
                error_category="CAPABILITY_NOT_IMPLEMENTED",
                error_message=f"FineJob 未注册工具：{tool_name}",
            )
        return handler(arguments)

    def get_capabilities(self, _arguments: dict[str, Any]) -> dict[str, object]:
        browser = boss_scraper_service.get_browser_status()
        with self.db.connect() as connection:
            executor = connection.execute(
                "SELECT browser_connected, queue_state FROM fj_boss_executor_instances ORDER BY updated_at DESC LIMIT 1"
            ).fetchone()
            chat_runtime = connection.execute(
                "SELECT send_enabled FROM fj_chat_runtime ORDER BY updated_at DESC LIMIT 1"
            ).fetchone()
        data = {
            "mcp_contract_version": "v1",
            "finejob_internal_api_version": "v1",
            "finejob_capabilities_version": "v3",
            "registered_tools": list(CORE_TOOLS),
            "runtime": {
                "boss_browser_running": bool(browser.running),
                "job_capture_ready": bool(browser.running),
                "job_capture_blockers": [] if browser.running else ["boss_browser_not_running"],
                "boss_executor_required_for_job_capture": False,
                "boss_executor_online": bool(executor and executor["browser_connected"]),
                "boss_executor_permission": "authorized" if executor else "not_authorized",
                "boss_executor_queue": executor["queue_state"] if executor else "paused",
                "boss_executor_scope": "仅用于审批后的打招呼动作，不参与岗位采集、筛选、详情获取或建议生成",
                "chat_send_enabled": bool(chat_runtime and chat_runtime["send_enabled"]),
            },
            "future_capabilities": {
                "application_preview": False,
                "application_execution": False,
                "delivery_run_request": False,
            },
        }
        return _result(result_type="data", status="succeeded", data=data)

    def get_job_hunt_refresh_run(self, arguments: dict[str, Any]) -> dict[str, object]:
        run_id = str(arguments.get("run_id") or "").strip()
        run = job_hunt_refresh.get_run(self.db, run_id)
        return _result(
            result_type="data",
            status=str(run["status"]),
            resource=_resource("job_hunt_refresh_run", run_id),
            data=run,
            terminal=str(run["status"]) in job_hunt_refresh.TERMINAL_RUN_STATUSES,
        )

    def list_job_hunt_refresh_items(self, arguments: dict[str, Any]) -> dict[str, object]:
        run_id = str(arguments.get("run_id") or "").strip()
        item_type = str(arguments.get("item_type") or "").strip()
        items = job_hunt_refresh.list_actionable_items(
            self.db,
            run_id,
            item_type=item_type,
        )
        return _result(
            result_type="data",
            status="succeeded",
            resource=_resource("job_hunt_refresh_run", run_id),
            data={"run_id": run_id, "item_type": item_type, "items": items},
        )

    def refresh_job_hunt_chat_messages(self, arguments: dict[str, Any]) -> dict[str, object]:
        run_id = str(arguments.get("run_id") or "").strip()
        item_id = str(arguments.get("item_id") or "").strip()
        data = job_hunt_refresh.refresh_chat_messages(self.db, run_id, item_id)
        return _result(
            result_type="data",
            status=str(data["status"]),
            resource=_resource("job_hunt_refresh_item", item_id),
            data=data,
            terminal=bool(data["terminal"]),
        )

    def refresh_job_hunt_chat_batch(self, arguments: dict[str, Any]) -> dict[str, object]:
        run_id = str(arguments.get("run_id") or "").strip()
        data = job_hunt_refresh.refresh_chat_messages_batch(self.db, self.config, run_id)
        operation = data.get("operation")
        resource = (
            _resource(str(operation["type"]), str(operation["id"]))
            if isinstance(operation, dict)
            else _resource("job_hunt_refresh_run", run_id)
        )
        return _result(
            result_type="task" if not data["terminal"] else "data",
            status=str(data["status"]),
            resource=resource,
            data=data,
            terminal=bool(data["terminal"]),
        )

    def refresh_job_hunt_related_job(self, arguments: dict[str, Any]) -> dict[str, object]:
        run_id = str(arguments.get("run_id") or "").strip()
        item_id = str(arguments.get("item_id") or "").strip()
        data = job_hunt_refresh.refresh_related_job(
            self.db,
            self.config,
            run_id,
            item_id,
        )
        operation = data.get("operation")
        resource = (
            _resource(str(operation["type"]), str(operation["id"]))
            if isinstance(operation, dict)
            else _resource("job_hunt_refresh_item", item_id)
        )
        return _result(
            result_type="task" if not data["terminal"] else "data",
            status=str(data["status"]),
            resource=resource,
            data=data,
            terminal=bool(data["terminal"]),
        )

    def prepare_job_hunt_refresh_analysis(self, arguments: dict[str, Any]) -> dict[str, object]:
        run_id = str(arguments.get("run_id") or "").strip()
        data = job_hunt_analysis.prepare_run_analysis(self.db, run_id)
        status = str(data.get("status") or ("succeeded" if not data.get("enabled") else "prepared"))
        return _result(
            result_type="data",
            status=status,
            resource=_resource("job_hunt_refresh_run", run_id),
            data=data,
            terminal=True,
            message="本次 Refresh Run 的统一分析任务清单已准备完成。",
        )

    def get_job_hunt_refresh_analysis_item_context(self, arguments: dict[str, Any]) -> dict[str, object]:
        run_id = str(arguments.get("run_id") or "").strip()
        item_type = str(arguments.get("item_type") or "").strip()
        item_id = str(arguments.get("item_id") or "").strip()
        data = job_hunt_analysis.get_run_analysis_item_context(
            self.db,
            run_id,
            item_type=item_type,
            item_id=item_id,
        )
        return _result(
            result_type="data",
            status=str(data.get("status") or "ready"),
            resource=_resource("job_hunt_refresh_analysis_item", item_id),
            data=data,
            terminal=True,
            message="分析 item 上下文已读取。",
        )

    def list_job_hunt_refresh_analysis_items(self, arguments: dict[str, Any]) -> dict[str, object]:
        run_id = str(arguments.get("run_id") or "").strip()
        item_type = str(arguments.get("item_type") or "").strip()
        data = job_hunt_analysis.list_run_analysis_items(
            self.db,
            run_id,
            item_type=item_type,
        )
        return _result(
            result_type="data",
            status="succeeded",
            resource=_resource("job_hunt_refresh_run", run_id),
            data=data,
            terminal=True,
            message="Refresh Run 分析 item 明细已读取。",
        )

    def save_job_hunt_refresh_analysis(self, arguments: dict[str, Any]) -> dict[str, object]:
        run_id = str(arguments.get("run_id") or "").strip()
        analysis_result = arguments.get("analysis_result")
        if not isinstance(analysis_result, dict):
            raise AppError(422, "VALIDATION_FAILED", "analysis_result 必须是对象。")
        data = job_hunt_analysis.save_run_analysis(
            self.db,
            run_id,
            analysis_result,
            final_batch=bool(arguments.get("final_batch", True)),
        )
        return _result(
            result_type="data",
            status=str(data.get("status") or "saved"),
            resource=_resource("job_hunt_refresh_run", run_id),
            data=data,
            terminal=True,
            message="本次统一分析结果已保存。",
        )

    def complete_job_hunt_refresh_run(self, arguments: dict[str, Any]) -> dict[str, object]:
        run_id = str(arguments.get("run_id") or "").strip()
        run = job_hunt_refresh.complete_run(self.db, run_id, self.config)
        return _result(
            result_type="data",
            status=str(run["status"]),
            resource=_resource("job_hunt_refresh_run", run_id),
            data=run,
            terminal=True,
        )

    def list_companies(self, arguments: dict[str, Any]) -> dict[str, object]:
        data = companies.list_companies(
            self.db,
            query=str(arguments.get("query") or ""),
            company_type=str(arguments.get("company_type") or ""),
            blacklist_status=str(arguments.get("blacklist_status") or "all"),
            page=max(1, int(arguments.get("page") or 1)),
            page_size=min(100, max(10, int(arguments.get("page_size") or 20))),
        )
        return _result(result_type="data", status="succeeded", data=data)

    def set_company_type(self, arguments: dict[str, Any]) -> dict[str, object]:
        name = str(arguments.get("company_name") or "").strip()
        company_type = str(arguments.get("company_type") or "").strip()
        if company_type not in {"unknown", "direct", "outsourcing"}:
            raise AppError(422, "VALIDATION_FAILED", "公司类型无效。")
        company = companies.create_company(
            self.db,
            name=name,
            company_type=company_type,
            notes=str(arguments.get("notes") or ""),
            source="mcp",
        )
        for alias in arguments.get("aliases") or []:
            if str(alias).strip():
                company = companies.add_company_alias(
                    self.db, str(company["id"]), str(alias)
                )
        return _result(
            result_type="data",
            status="succeeded",
            resource=_resource("company", str(company["id"]), int(company["version"])),
            data={"company": company},
            message="公司类型已更新。",
        )

    def set_company_blacklist(self, arguments: dict[str, Any]) -> dict[str, object]:
        company_id = str(arguments.get("company_id") or "").strip()
        company_name = str(arguments.get("company_name") or "").strip()
        if not company_id:
            matches = companies.list_companies(
                self.db, query=company_name, page=1, page_size=100
            )["items"]
            exact = next(
                (
                    item for item in matches
                    if companies.normalize_company_name(str(item["canonical_name"]))
                    == companies.normalize_company_name(company_name)
                ),
                None,
            )
            if exact is None:
                exact = companies.create_company(self.db, name=company_name, source="mcp")
            company_id = str(exact["id"])
        company = companies.set_company_blacklist(
            self.db,
            company_id,
            blacklisted=bool(arguments.get("blacklisted", True)),
            reason=str(arguments.get("reason") or ""),
        )
        return _result(
            result_type="data",
            status="succeeded",
            resource=_resource("company", company_id, int(company["version"])),
            data={"company": company},
            message="公司黑名单状态已更新。",
        )

    def record_job_application(self, arguments: dict[str, Any]) -> dict[str, object]:
        application = set_job_application(
            self.db,
            str(arguments.get("job_id") or ""),
            applied=bool(arguments.get("applied", True)),
            source="mcp",
            applied_at=str(arguments.get("applied_at") or "") or None,
            note=str(arguments.get("note") or ""),
        )
        return _result(
            result_type="data",
            status="succeeded",
            resource=_resource("job", str(application["job_id"])),
            data={"application": application},
            message="岗位投递状态已更新。",
        )

    def search_jobs(self, arguments: dict[str, Any]) -> dict[str, object]:
        query = str(arguments.get("query") or "").strip()
        search_keyword = str(arguments.get("search_keyword") or "").strip()
        city = str(arguments.get("city") or "").strip()
        detail_status = str(arguments.get("status") or "").strip()
        page = max(1, int(arguments.get("page") or 1))
        page_size = min(100, max(1, int(arguments.get("page_size") or 20)))
        clauses: list[str] = []
        values: list[object] = []
        if query:
            clauses.append("(j.title LIKE ? OR j.company_name LIKE ? OR j.skills LIKE ?)")
            values.extend([f"%{query}%"] * 3)
        if search_keyword:
            clauses.append("j.search_keyword = ?")
            values.append(search_keyword)
        if city:
            clauses.append("j.location LIKE ?")
            values.append(f"%{city}%")
        if detail_status:
            clauses.append("j.detail_status = ?")
            values.append(detail_status)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self.db.connect() as connection:
            total = int(connection.execute(f"SELECT COUNT(*) FROM fj_boss_jobs j {where}", values).fetchone()[0])
            rows = connection.execute(
                f"""
                SELECT j.id, j.title, j.company_name, j.salary, j.location, j.search_keyword,
                       j.detail_status, j.detail_version, j.last_collected_at,
                       j.company_id, COALESCE(c.company_type, 'unknown') AS company_type,
                       COALESCE(c.is_blacklisted, 0) AS is_blacklisted,
                       a.status AS application_status, a.applied_at,
                       e.id AS evaluation_id, e.decision AS evaluation_decision
                FROM fj_boss_jobs j
                LEFT JOIN fj_companies c ON c.id = j.company_id
                LEFT JOIN fj_job_applications a ON a.job_id = j.id
                LEFT JOIN fj_job_evaluations e ON e.id = (
                  SELECT id FROM fj_job_evaluations WHERE job_id = j.id
                  ORDER BY created_at DESC LIMIT 1
                )
                {where}
                ORDER BY j.last_collected_at DESC, j.id DESC LIMIT ? OFFSET ?
                """,
                [*values, page_size, (page - 1) * page_size],
            ).fetchall()
        items = [dict(row) for row in rows]
        return _result(
            result_type="data",
            status="succeeded",
            data={"items": items, "total": total, "page": page, "page_size": page_size},
        )

    def list_job_strategies(self, arguments: dict[str, Any]) -> dict[str, object]:
        enabled_only = bool(arguments.get("enabled_only", True))
        filter_items: list[dict[str, object]] = []
        for strategy in list_filter_strategies(self.db):
            if enabled_only and not strategy.get("enabled"):
                continue
            item = _strategy_with_resolved_profile(self.db, strategy)
            item["search_keyword_records"] = [
                keyword
                for keyword in list_search_keywords(self.db, str(strategy["id"]))
                if keyword.get("enabled")
            ]
            filter_items.append(item)
        recommendation_items = [
            _strategy_with_resolved_profile(self.db, strategy)
            for strategy in list_recommendation_strategies(self.db)
            if not enabled_only or strategy.get("enabled")
        ]
        return _result(
            result_type="data",
            status="succeeded",
            data={
                "filter_strategies": filter_items,
                "recommendation_strategies": recommendation_items,
            },
        )

    def get_job_evaluation_context(self, arguments: dict[str, Any]) -> dict[str, object]:
        recommendation_strategy_id = str(
            arguments.get("recommendation_strategy_id") or ""
        ).strip()
        recommendation_strategy = _strategy_with_resolved_profile(
            self.db,
            get_recommendation_strategy(self.db, recommendation_strategy_id),
        )
        filter_strategy_id = str(arguments.get("filter_strategy_id") or "").strip()
        filter_strategy_id = filter_strategy_id or str(
            recommendation_strategy.get("filter_strategy_id") or ""
        )
        if not filter_strategy_id:
            raise AppError(
                status_code=422,
                error_category="FILTER_STRATEGY_REQUIRED",
                error_message="建议投递策略尚未关联岗位筛选策略，请先在策略管理中完成关联。",
            )
        filter_strategy = get_filter_strategy(self.db, filter_strategy_id)
        profile_id = str(recommendation_strategy.get("candidate_profile_id") or "")
        resume_version_id = str(recommendation_strategy.get("resume_version_id") or "")
        if not profile_id or not resume_version_id:
            raise AppError(
                status_code=422,
                error_category="RECOMMENDATION_PROFILE_REQUIRED",
                error_message="建议投递策略尚未关联候选人档案和具体简历。",
            )
        stale_action = str(arguments.get("context_stale_action") or "").strip() or None
        if stale_action not in {None, "regenerate", "use_current", "cancel"}:
            raise AppError(
                status_code=422,
                error_category="VALIDATION_FAILED",
                error_message="上下文过期处理选项无效。",
            )
        resolution = profile_v3.resolve_task_context(
            self.db,
            profile_id,
            resume_version_id,
            "evaluation",
            stale_action,
        )
        context = dict(resolution.get("context") or {})
        current_revision = context.get("current_revision")
        revision = dict(current_revision) if isinstance(current_revision, dict) else None
        keyword_records = [
            keyword
            for keyword in list_search_keywords(self.db, filter_strategy_id)
            if keyword.get("enabled")
        ]
        cities = [str(city) for city in filter_strategy.get("cities") or [] if str(city)]
        delivery_strategy = get_delivery_strategy(self.db)
        auto_greeting_enabled = bool(
            delivery_strategy
            and delivery_strategy.get("ready")
            and delivery_strategy.get("automation_level") == "auto_greeting"
            and delivery_strategy.get("auto_greeting_enabled")
        )
        data = {
            "filter_strategy": filter_strategy,
            "recommendation_strategy": recommendation_strategy,
            "search_keywords": keyword_records,
            "cities": cities,
            "default_search_keyword": str(keyword_records[0]["keyword"]) if keyword_records else "",
            "default_city": cities[0] if cities else "",
            "candidate_profile_id": profile_id,
            "resume_version_id": resume_version_id,
            "context": revision,
            "context_status": resolution["status"],
            "auto_greeting": {
                "enabled": auto_greeting_enabled,
                "message": (
                    "当前自动招呼策略已启用，建议投递且满足现有条件的岗位可能直接进入自动路由。"
                    if auto_greeting_enabled
                    else "当前结果将按现有人工确认规则进入审批路由。"
                ),
            },
        }
        if resolution["status"] == "confirmation_required":
            return _result(
                result_type="confirmation",
                status="awaiting_confirmation",
                data=data,
                resource=_resource("candidate_context", str(context.get("id") or resume_version_id)),
                requires_confirmation=True,
                message="岗位评估上下文已过期，请选择重新生成、继续使用当前版本或取消。",
            )
        if resolution["status"] == "cancelled":
            return _result(
                result_type="data",
                status="cancelled",
                data=data,
                message="已取消本次岗位评估上下文读取。",
            )
        revision_id = str((revision or {}).get("id") or "")
        return _result(
            result_type="data",
            status="succeeded",
            data=data,
            resource=_resource("context_revision", revision_id),
        )

    def start_job_capture(self, arguments: dict[str, Any]) -> dict[str, object]:
        filter_strategy_id = str(arguments.get("filter_strategy_id") or "").strip()
        strategy = get_filter_strategy(self.db, filter_strategy_id)
        if not strategy.get("enabled"):
            raise AppError(409, "FILTER_STRATEGY_DISABLED", "所选岗位筛选策略当前未启用。")
        keyword_records = [
            keyword
            for keyword in list_search_keywords(self.db, filter_strategy_id)
            if keyword.get("enabled")
        ]
        allowed_keywords = [str(item["keyword"]) for item in keyword_records]
        allowed_cities = [str(city) for city in strategy.get("cities") or [] if str(city)]
        keyword = str(arguments.get("keyword") or "").strip()
        city = str(arguments.get("city") or "").strip()
        keyword = keyword or (allowed_keywords[0] if allowed_keywords else "")
        city = city or (allowed_cities[0] if allowed_cities else "")
        if not keyword or keyword not in allowed_keywords:
            raise AppError(422, "SEARCH_KEYWORD_INVALID", "请选择该筛选策略中已启用的搜索词。")
        if not city or city not in allowed_cities:
            raise AppError(422, "SEARCH_CITY_INVALID", "请选择该筛选策略中的城市。")
        if not boss_scraper_service.get_browser_status().running:
            raise AppError(409, "BROWSER_NOT_RUNNING", "FineJob 专用 Chrome 未运行。")
        pages = min(10, max(1, int(arguments.get("pages") or 1)))
        task = boss_capture_task_manager.start_capture(
            BossCaptureRequest(
                keyword=keyword,
                city=city,
                pages=pages,
                filters={},
                include_details=False,
                max_details=None,
                output_dir=self.config.output_root / "fine-job" / "boss-capture",
                prefer_current_page=True,
                filter_strategy_id=filter_strategy_id,
            ),
            output_dir=self.config.output_root / "fine-job" / "boss-capture",
            db=self.db,
        )
        task_id = str(task["id"])
        return _result(
            result_type="task",
            status=str(task.get("status") or "queued"),
            data=task,
            resource=_resource("capture_task", task_id),
            terminal=False,
            message=f"已使用搜索词“{keyword}”和城市“{city}”启动岗位采集。",
        )

    def continue_job_capture(self, arguments: dict[str, Any]) -> dict[str, object]:
        task_id = str(arguments.get("capture_task_id") or "").strip()
        pages = min(10, max(1, int(arguments.get("pages") or 1)))
        if not boss_scraper_service.get_browser_status().running:
            raise AppError(409, "BROWSER_NOT_RUNNING", "FineJob 专用 Chrome 未运行。")
        task = boss_capture_task_manager.continue_capture(task_id, pages=pages)
        return _result(
            result_type="task",
            status=str(task.get("status") or "queued"),
            data=task,
            resource=_resource("capture_task", task_id),
            terminal=False,
            message=f"已在原搜索页面继续下滑采集 {pages} 页。",
        )

    def stop_job_capture(self, arguments: dict[str, Any]) -> dict[str, object]:
        task_id = str(arguments.get("capture_task_id") or "").strip()
        task = boss_capture_task_manager.stop_capture(task_id)
        return _result(
            result_type="task",
            status=str(task.get("status") or "running"),
            data=task,
            resource=_resource("capture_task", task_id),
            terminal=False,
            message="已请求停止当前下滑采集；已获得岗位将保留，原搜索页面可继续使用。",
        )

    def apply_job_filter(self, arguments: dict[str, Any]) -> dict[str, object]:
        task_id = str(arguments.get("capture_task_id") or "").strip()
        filter_strategy_id = str(arguments.get("filter_strategy_id") or "").strip()
        task = boss_capture_task_manager.get_task(task_id)
        strategy = get_filter_strategy(self.db, filter_strategy_id)
        results = evaluate_filter_strategy(list(task.get("jobs") or []), strategy)
        _enriched_jobs, results = apply_filter_exclusions(
            self.db, strategy, list(task.get("jobs") or []), results
        )
        updated = boss_capture_task_manager.apply_filter_results(task_id, results)
        jobs_by_id = {
            str(job.get("job_id") or ""): job for job in updated.get("jobs") or []
        }
        selected = []
        for result in results:
            if result.get("status") not in {"pass", "review"}:
                continue
            job = jobs_by_id.get(str(result.get("job_id") or ""), {})
            if job.get("processing_state") == "duplicate":
                continue
            selected.append(
                {
                    "job_id": result.get("job_id"),
                    "history_job_id": job.get("history_record_id"),
                    "status": result.get("status"),
                }
            )
        continuation = {
            "available": bool(updated.get("continuation_available")),
            "has_more": bool(updated.get("has_more", True)),
            "capture_task_id": task_id,
            "next_tool": "finejob.continue_job_capture",
        }
        message = "岗位筛选已完成，最新结果已写入历史岗位。"
        if not selected and continuation["available"] and continuation["has_more"]:
            message = (
                "本页岗位均未进入候选集合，筛选结果已写入历史岗位；"
                "原搜索页仍可继续下滑。上层目标需要候选岗位且尚未达标时，"
                "请调用 finejob.continue_job_capture。"
            )
        return _result(
            result_type="data",
            status="succeeded",
            data={
                "results": results,
                "selected_jobs": selected,
                "continuation": continuation,
                "task": updated,
            },
            resource=_resource("capture_task", task_id),
            message=message,
        )

    def collect_job_details(self, arguments: dict[str, Any]) -> dict[str, object]:
        task_id = str(arguments.get("capture_task_id") or "").strip()
        job_ids = [str(value) for value in arguments.get("job_ids") or [] if str(value)]
        manual_override = bool(arguments.get("manual_override", False))
        current = boss_capture_task_manager.get_task(task_id)
        jobs_by_id = {str(job.get("job_id") or ""): job for job in current.get("jobs") or []}
        for job_id in job_ids:
            job = jobs_by_id.get(job_id)
            if not job:
                continue
            strategy_id = str(job.get("filter_strategy_id") or "")
            strategy = get_filter_strategy(self.db, strategy_id) if strategy_id else None
            history_id = str(job.get("history_record_id") or "")
            if history_id:
                assert_job_action_allowed(
                    self.db,
                    history_id,
                    strategy=strategy,
                    action="detail",
                    allow_manual_override=manual_override,
                )
        detail_kwargs = {"force": bool(arguments.get("force", False))}
        if manual_override:
            detail_kwargs["manual_override"] = True
        task = boss_capture_task_manager.start_details(task_id, job_ids, **detail_kwargs)
        return _result(
            result_type="task",
            status=str(task.get("status") or "queued"),
            data=task,
            resource=_resource("capture_task", task_id),
            terminal=False,
            message="岗位详情采集已启动。",
        )

    def get_job_jd(self, arguments: dict[str, Any]) -> dict[str, object]:
        history_job_id = str(arguments.get("job_id") or "").strip()
        job = get_capture_history_job(self.db, history_job_id)
        data = {
            "job_id": job["id"],
            "source_job_id": job.get("job_id"),
            "title": job.get("title"),
            "company_name": job.get("boss_name"),
            "company_id": job.get("company_id"),
            "company_type": job.get("company_type"),
            "is_outsourcing_company": job.get("is_outsourcing_company"),
            "is_blacklisted": job.get("is_blacklisted"),
            "application_status": job.get("application_status"),
            "applied_at": job.get("applied_at"),
            "salary": job.get("salary"),
            "location": job.get("location"),
            "job_link": job.get("job_link"),
            "search_keyword": job.get("search_keyword"),
            "filter_status": job.get("filter_status"),
            "filter_reasons": job.get("filter_reasons") or [],
            "filter_missing_fields": job.get("filter_missing_fields") or [],
            "filter_strategy_id": job.get("filter_strategy_id"),
            "detail": job.get("detail"),
            "detail_status": job.get("detail_status"),
            "detail_version": job.get("detail_version"),
            "delivery_evaluation": job.get("delivery_evaluation"),
        }
        return _result(
            result_type="data",
            status="succeeded",
            data=data,
            resource=_resource("job", history_job_id, int(job.get("detail_version") or 1)),
        )

    def get_job_context(self, arguments: dict[str, Any]) -> dict[str, object]:
        job_id = str(arguments.get("job_id") or "")
        resume_id = str(arguments.get("resume_id") or "").strip() or None
        with self.db.connect() as connection:
            job = connection.execute("SELECT * FROM fj_boss_jobs WHERE id = ?", (job_id,)).fetchone()
            if job is None:
                raise AppError(status_code=404, error_category="JOB_NOT_FOUND", error_message="岗位不存在。")
            evaluation = connection.execute(
                "SELECT * FROM fj_job_evaluations WHERE job_id = ? ORDER BY created_at DESC LIMIT 1",
                (job_id,),
            ).fetchone()
            previews = connection.execute(
                "SELECT * FROM fj_review_items WHERE job_id = ? ORDER BY created_at DESC LIMIT 10",
                (job_id,),
            ).fetchall()
            actions = connection.execute(
                "SELECT * FROM fj_automation_actions WHERE job_id = ? ORDER BY created_at DESC LIMIT 10",
                (job_id,),
            ).fetchall()
            resume = connection.execute(
                "SELECT * FROM fj_resumes WHERE id = ?", (resume_id,)
            ).fetchone() if resume_id else None
        resumes = list_resumes(self.db)
        facts = list_resume_facts(self.db, resume_id) if resume_id else []
        job_data = dict(job)
        job_data["detail"] = _loads(job_data.pop("detail_json", None), None)
        job_data["payload"] = _loads(job_data.pop("payload_json", None), {})
        data = {
            "job": job_data,
            "job_detail_version": int(job["detail_version"]),
            "available_resumes": resumes,
            "selected_resume": dict(resume) if resume else None,
            "resume_facts": facts,
            "resume_facts_version": int(resume["facts_version"]) if resume else None,
            "current_evaluation": self._evaluation_payload(evaluation),
            "previews": [self._review_payload(row) for row in previews],
            "actions": [self._automation_action_payload(row) for row in actions],
        }
        profile = profile_store.ensure_default_profile(self.db)
        data["candidate_profile_context"] = get_profile_context(
            self.db,
            str(profile["id"]),
            view="evaluation",
            job_id=job_id,
        )
        data["resume_versions"] = profile_store.list_resume_versions(self.db, str(profile["id"]))
        return _result(
            result_type="data",
            status="succeeded",
            resource=_resource("job", job_id, int(job["detail_version"])),
            data=data,
        )

    def collect_job_detail(self, arguments: dict[str, Any]) -> dict[str, object]:
        job_id = str(arguments.get("job_id") or "")
        manual_override = bool(arguments.get("manual_override", False))
        if not boss_scraper_service.get_browser_status().running:
            raise AppError(status_code=409, error_category="BROWSER_NOT_RUNNING", error_message="FineJob 专用 Chrome 未运行。")
        job = get_capture_history_job(self.db, job_id)
        strategy_id = str(job.get("filter_strategy_id") or "")
        strategy = get_filter_strategy(self.db, strategy_id) if strategy_id else None
        assert_job_action_allowed(
            self.db,
            job_id,
            strategy=strategy,
            action="detail",
            allow_manual_override=manual_override,
        )
        task = boss_capture_task_manager.start_history_detail(
            job,
            output_dir=self.config.output_root / "fine-job" / "boss-capture",
            db=self.db,
        )
        task_id = str(task["id"])
        return _result(
            result_type="task",
            status=str(task.get("status") or "queued"),
            resource=_resource("capture_task", task_id),
            data=task,
            terminal=False,
            message="岗位详情采集任务已创建。",
        )

    def list_resumes(self, _arguments: dict[str, Any]) -> dict[str, object]:
        return _result(result_type="data", status="succeeded", data={"items": list_resumes(self.db)})

    def get_resume_facts(self, arguments: dict[str, Any]) -> dict[str, object]:
        resume_id = str(arguments.get("resume_id") or "")
        with self.db.connect() as connection:
            resume = connection.execute("SELECT * FROM fj_resumes WHERE id = ?", (resume_id,)).fetchone()
        if resume is None:
            raise AppError(status_code=404, error_category="RESUME_NOT_FOUND", error_message="简历不存在。")
        version = int(resume["facts_version"])
        return _result(
            result_type="data",
            status="succeeded",
            resource=_resource("resume_facts", resume_id, version),
            data={"resume": dict(resume), "facts": list_resume_facts(self.db, resume_id), "facts_version": version},
        )

    def list_profiles(self, _arguments: dict[str, Any]) -> dict[str, object]:
        profiles = profile_store.list_profiles(self.db)
        return _result(result_type="data", status="succeeded", data={"items": profiles})

    def get_profile_analysis_input(self, arguments: dict[str, Any]) -> dict[str, object]:
        profile_id = str(arguments.get("profile_id") or profile_store.DEFAULT_PROFILE_ID)
        source_ids = [str(value) for value in arguments.get("source_ids") or []]
        if not source_ids:
            source_ids = [
                str(source["id"])
                for source in profile_store.list_sources(self.db, profile_id)
                if source["enabled"]
            ]
        if not source_ids:
            raise AppError(422, "PROFILE_SOURCE_REQUIRED", "当前档案没有可分析资料。")
        data = profile_analysis.prepare_profile_analysis_input(
            self.db,
            self.config,
            profile_id,
            source_ids,
        )
        return _result(
            result_type="data",
            status="succeeded",
            resource=_resource("candidate_profile", profile_id, int(data["input_versions"]["sources_version"])),  # type: ignore[index]
            data=data,
        )

    def save_profile_analysis_draft(self, arguments: dict[str, Any]) -> dict[str, object]:
        profile_id = str(arguments.get("profile_id") or profile_store.DEFAULT_PROFILE_ID)
        source_ids = [str(value) for value in arguments.get("source_ids") or []]
        expected_versions = arguments.get("input_versions")
        output = arguments.get("output")
        if not isinstance(expected_versions, dict) or not isinstance(output, dict):
            raise AppError(422, "VALIDATION_FAILED", "缺少分析输入版本或结构化输出。")
        run = profile_analysis.save_skill_analysis_draft(
            self.db,
            profile_id,
            source_ids,
            {str(key): int(value) for key, value in expected_versions.items()},
            output,
        )
        return _result(
            result_type="data",
            status="needs_confirmation",
            resource=_resource("profile_analysis_run", str(run["id"])),
            data={"analysis_run": run, "items": profile_store.list_analysis_items(self.db, str(run["id"]))},
            message="分析草稿已保存，请用户在求职资料页面逐项确认。",
        )

    def list_resume_families(self, arguments: dict[str, Any]) -> dict[str, object]:
        profile_id = str(arguments.get("profile_id") or profile_store.DEFAULT_PROFILE_ID)
        families = resume_analysis_v2.list_resume_families(self.db, profile_id)
        return _result(
            result_type="data",
            status="succeeded",
            resource=_resource("candidate_profile", profile_id),
            data={"items": families},
        )

    def get_resume_analysis_plan(self, arguments: dict[str, Any]) -> dict[str, object]:
        run_id = str(arguments.get("run_id") or "").strip()
        if run_id:
            run = resume_analysis_v2.get_analysis_run(self.db, run_id)
        else:
            profile_id = str(arguments.get("profile_id") or profile_store.DEFAULT_PROFILE_ID)
            resume_family_id = str(arguments.get("resume_family_id") or "").strip()
            if not resume_family_id:
                raise AppError(422, "RESUME_FAMILY_REQUIRED", "缺少要分析的简历组。")
            payload = ResumeAnalysisRunCreate(
                source_ids=[str(value) for value in arguments.get("source_ids") or []],
                operation_ids=arguments.get("operation_ids") or [],
                pipeline_mode="chained",
                execution_path="codex_workspace",
            )
            run = resume_analysis_v2.start_analysis_run(
                self.db,
                self.config,
                profile_id,
                resume_family_id,
                payload,
            )
        return _result(
            result_type="data",
            status=str(run["status"]),
            terminal=str(run["status"]) not in {"queued", "running"},
            resource=_resource("resume_analysis_run", str(run["id"])),
            data={
                "analysis_run": run,
                "execution_rule": "按 operations 的 sequence_no 顺序执行；每项保存后再读取下一项输入。",
            },
        )

    def get_resume_operation_input(self, arguments: dict[str, Any]) -> dict[str, object]:
        run_id = str(arguments.get("run_id") or "").strip()
        operation_id = str(arguments.get("operation_id") or "").strip()
        if not run_id or not operation_id:
            raise AppError(422, "VALIDATION_FAILED", "缺少分析任务或操作标识。")
        data = resume_analysis_v2.prepare_operation_input(self.db, run_id, operation_id)
        operation = data["operation"]
        return _result(
            result_type="data",
            status=str(operation["status"]),  # type: ignore[index]
            terminal=False,
            resource=_resource("resume_analysis_operation", str(operation["id"])),  # type: ignore[index]
            data=data,
        )

    def save_resume_operation_result(self, arguments: dict[str, Any]) -> dict[str, object]:
        run_id = str(arguments.get("run_id") or "").strip()
        operation_id = str(arguments.get("operation_id") or "").strip()
        output = arguments.get("output")
        if not run_id or not operation_id or not isinstance(output, dict):
            raise AppError(422, "VALIDATION_FAILED", "缺少分析任务、操作标识或结构化结果。")
        run = resume_analysis_v2.save_codex_operation_result(
            self.db,
            run_id,
            operation_id,
            output,
        )
        return _result(
            result_type="data",
            status=str(run["status"]),
            terminal=str(run["status"]) not in {"queued", "running"},
            resource=_resource("resume_analysis_run", run_id),
            data={"analysis_run": run},
            message="本项分析结果已保存；确定内容已直接入库，疑点已进入待处理清单。",
        )

    def get_resume_analysis_run(self, arguments: dict[str, Any]) -> dict[str, object]:
        run_id = str(arguments.get("run_id") or "").strip()
        run = resume_analysis_v2.get_analysis_run(self.db, run_id)
        return _result(
            result_type="data",
            status=str(run["status"]),
            terminal=str(run["status"]) not in {"queued", "running"},
            resource=_resource("resume_analysis_run", run_id),
            data={"analysis_run": run},
        )

    def list_resume_family_strategies(self, arguments: dict[str, Any]) -> dict[str, object]:
        resume_family_id = str(arguments.get("resume_family_id") or "").strip()
        data = {
            "strategies": resume_analysis_v2.list_strategies(self.db, resume_family_id),
            "search_keywords": resume_analysis_v2.list_search_keywords(self.db, resume_family_id),
            "issues": resume_analysis_v2.list_issues(self.db, resume_family_id),
        }
        return _result(
            result_type="data",
            status="succeeded",
            resource=_resource("resume_family", resume_family_id),
            data=data,
        )

    def get_profile_context(self, arguments: dict[str, Any]) -> dict[str, object]:
        profile_id = str(arguments.get("profile_id") or profile_store.DEFAULT_PROFILE_ID)
        view = str(arguments.get("view") or "full")
        data = get_profile_context(
            self.db,
            profile_id,
            view=view,  # type: ignore[arg-type]
            job_id=str(arguments.get("job_id") or "") or None,
            role_family=str(arguments.get("role_family") or "") or None,
            resume_family_id=str(arguments.get("resume_family_id") or "") or None,
        )
        return _result(
            result_type="data",
            status="succeeded",
            resource=_resource("candidate_context", profile_id, int(data["artifact_version"])),
            data=data,
        )

    def list_profile_questions(self, arguments: dict[str, Any]) -> dict[str, object]:
        profile_id = str(arguments.get("profile_id") or profile_store.DEFAULT_PROFILE_ID)
        questions, version = profile_store.list_questions(self.db, profile_id)
        for question in questions:
            question["answer_variants"] = profile_store.list_answer_variants(self.db, str(question["id"]))
        return _result(
            result_type="data",
            status="succeeded",
            resource=_resource("profile_questions", profile_id, version),
            data={"items": questions, "questions_version": version},
        )

    def list_chat_sessions(self, arguments: dict[str, Any]) -> dict[str, object]:
        status = str(arguments.get("status") or "").strip() or None
        page = max(1, int(arguments.get("page") or 1))
        page_size = min(100, max(1, int(arguments.get("page_size") or 20)))
        sessions = list_sessions(self.db, status=status)
        start = (page - 1) * page_size
        return _result(
            result_type="data",
            status="succeeded",
            data={"items": sessions[start:start + page_size], "total": len(sessions), "page": page, "page_size": page_size},
        )

    def get_chat_context(self, arguments: dict[str, Any]) -> dict[str, object]:
        session_id = str(arguments.get("session_id") or "")
        detail = get_session(self.db, session_id)
        session = detail["session"]
        with self.db.connect() as connection:
            facts = connection.execute(
                """
                SELECT f.*, r.facts_version FROM fj_resume_facts f
                JOIN fj_resumes r ON r.id = f.resume_id
                WHERE f.user_confirmed = 1 ORDER BY f.updated_at DESC LIMIT 100
                """
            ).fetchall()
        latest_task = detail["reply_tasks"][0] if detail["reply_tasks"] else None
        data = {
            **detail,
            "confirmed_resume_facts": [dict(row) for row in facts],
            "latest_inbound_message_id": session.get("latest_inbound_message_id"),
            "session_version": session.get("session_version"),
            "current_draft": latest_task,
            "content_categories": _loads((latest_task or {}).get("content_categories_json"), []),
        }
        profile = profile_store.ensure_default_profile(self.db)
        data["candidate_profile_context"] = get_profile_context(
            self.db,
            str(profile["id"]),
            view="chat",
            job_id=str(session.get("job_id") or "") or None,
        )
        return _result(
            result_type="data",
            status="succeeded",
            resource=_resource("chat_session", session_id, int(session["session_version"])),
            data=data,
        )

    def save_job_evaluation(self, arguments: dict[str, Any]) -> dict[str, object]:
        if str(arguments.get("recommendation_strategy_id") or "").strip():
            return self._save_v3_job_evaluation(arguments)
        job_id = str(arguments.get("job_id") or "")
        assert_job_action_allowed(
            self.db,
            job_id,
            strategy=None,
            action="evaluation",
            allow_manual_override=bool(arguments.get("manual_override", False)),
        )
        resume_id = str(arguments.get("resume_id") or "") or None
        profile_id = str(arguments.get("profile_id") or "") or None
        resume_version_id = str(arguments.get("resume_version_id") or "") or None
        expected_job_version = int(arguments.get("job_detail_version") or 0)
        expected_facts_version = int(arguments.get("resume_facts_version") or 0)
        expected_context_version = int(arguments.get("profile_context_version") or 0)
        decision = str(arguments.get("conclusion") or arguments.get("decision") or "review")
        if decision not in {"recommend", "review", "reject"}:
            raise AppError(status_code=422, error_category="VALIDATION_FAILED", error_message="评估结论无效。")
        if profile_id:
            profile = profile_store.get_profile(self.db, profile_id)
            if int(profile["versions"]["context_version"]) != expected_context_version:  # type: ignore[index]
                raise AppError(status_code=409, error_category="CONTEXT_VERSION_CHANGED", error_message="候选人上下文已经变化，请重新读取岗位上下文。")
            if resume_version_id:
                resume_version = profile_store.get_resume_version(self.db, resume_version_id)
                if resume_version["profile_id"] != profile_id or resume_version["status"] != "confirmed":
                    raise AppError(status_code=409, error_category="RESUME_VERSION_INVALID", error_message="简历版本未确认或不属于当前档案。")
        elif resume_id is None:
            raise AppError(status_code=422, error_category="PROFILE_CONTEXT_REQUIRED", error_message="需要候选人上下文或旧简历事实版本。")
        with self.db.connect() as connection:
            job = connection.execute("SELECT detail_version FROM fj_boss_jobs WHERE id = ?", (job_id,)).fetchone()
            resume = connection.execute("SELECT facts_version FROM fj_resumes WHERE id = ?", (resume_id,)).fetchone() if resume_id else None
            if job is None or (not profile_id and resume is None):
                raise AppError(status_code=404, error_category="RESOURCE_NOT_FOUND", error_message="岗位或候选人资料不存在。")
            legacy_version_changed = resume is not None and int(resume["facts_version"]) != expected_facts_version
            if int(job["detail_version"]) != expected_job_version or legacy_version_changed:
                raise AppError(status_code=409, error_category="CONTEXT_VERSION_CHANGED", error_message="岗位详情或候选人资料已经变化，请重新读取上下文。")
            evaluation_id = new_id()
            now = utc_now()
            payload = {
                "decision": decision,
                "reasons": arguments.get("reasons") or [],
                "risks": arguments.get("risks") or [],
                "matches": arguments.get("matches") or [],
                "gaps": arguments.get("gaps") or [],
                "suggestion": arguments.get("suggestion") or "",
            }
            connection.execute(
                """
                INSERT INTO fj_job_evaluations (
                  id, job_id, evaluation_version, resume_id, source, decision,
                  confidence, evaluation_json, created_at, job_detail_version,
                  resume_facts_version, structure_version, candidate_profile_id,
                  profile_context_version, resume_version_id
                ) VALUES (?, ?, 'codex-v2', ?, 'llm', ?, ?, ?, ?, ?, ?, 2, ?, ?, ?)
                """,
                (
                    evaluation_id,
                    job_id,
                    resume_id,
                    decision,
                    float(arguments.get("confidence") or 0),
                    json.dumps(payload, ensure_ascii=False),
                    now,
                    expected_job_version,
                    expected_facts_version if resume is not None else 1,
                    profile_id,
                    expected_context_version if profile_id else None,
                    resume_version_id,
                ),
            )
        record_job_event(self.db, "evaluation", job_id, now)
        return _result(
            result_type="evaluation",
            status="succeeded",
            resource=_resource("job_evaluation", evaluation_id),
            data={"evaluation_id": evaluation_id, "evaluation_version": 1, **payload},
            message="结构化岗位评估已保存。",
        )

    def _save_v3_job_evaluation(
        self, arguments: dict[str, Any]
    ) -> dict[str, object]:
        job_id = str(arguments.get("job_id") or "").strip()
        expected_job_version = int(arguments.get("job_detail_version") or 0)
        recommendation_strategy_id = str(
            arguments.get("recommendation_strategy_id") or ""
        ).strip()
        recommendation_strategy = _strategy_with_resolved_profile(
            self.db,
            get_recommendation_strategy(self.db, recommendation_strategy_id),
        )
        filter_strategy_id = str(arguments.get("filter_strategy_id") or "").strip()
        filter_strategy_id = filter_strategy_id or str(
            recommendation_strategy.get("filter_strategy_id") or ""
        )
        filter_strategy = (
            get_filter_strategy(self.db, filter_strategy_id)
            if filter_strategy_id
            else None
        )
        assert_job_action_allowed(
            self.db,
            job_id,
            strategy=filter_strategy,
            action="evaluation",
            allow_manual_override=bool(arguments.get("manual_override", False)),
        )
        profile_id = str(recommendation_strategy.get("candidate_profile_id") or "")
        resume_version_id = str(recommendation_strategy.get("resume_version_id") or "")
        context_revision_id = str(arguments.get("context_revision_id") or "").strip()
        if not profile_id or not resume_version_id or not context_revision_id:
            raise AppError(
                422,
                "V3_EVALUATION_CONTEXT_REQUIRED",
                "保存投递建议需要建议投递策略关联的具体简历和 V3 上下文修订。",
            )
        profile = profile_store.get_profile(self.db, profile_id)
        resume_version = profile_store.get_resume_version(self.db, resume_version_id)
        if str(resume_version.get("profile_id") or "") != profile_id:
            raise AppError(409, "RESUME_VERSION_INVALID", "具体简历不属于建议投递策略关联档案。")
        with self.db.connect() as connection:
            context_revision = connection.execute(
                """
                SELECT r.dependency_versions_json
                FROM fj_profile_context_revisions r
                JOIN fj_profile_context_heads h ON h.id = r.head_id
                WHERE r.id = ? AND h.profile_id = ? AND h.resume_version_id = ?
                  AND h.view_type = 'evaluation'
                """,
                (context_revision_id, profile_id, resume_version_id),
            ).fetchone()
        if context_revision is None:
            raise AppError(
                409,
                "CONTEXT_REVISION_INVALID",
                "岗位评估上下文修订不存在或不属于当前具体简历。",
            )
        job = get_capture_history_job(self.db, job_id)
        if job.get("detail_status") != "completed":
            raise AppError(409, "CAPTURE_NOT_READY", "请先完成该岗位的详情采集。")
        if int(job.get("detail_version") or 0) != expected_job_version:
            raise AppError(
                409,
                "JOB_CONTEXT_CHANGED",
                "岗位详情已经变化，请基于最新 JD 重新生成投递建议。",
            )
        decision = str(arguments.get("conclusion") or arguments.get("decision") or "review")
        if decision not in {"recommend", "review", "reject"}:
            raise AppError(422, "VALIDATION_FAILED", "评估结论无效。")
        evaluation = {
            "evaluation_version": "2.0",
            "job_id": str(job.get("job_id") or job_id),
            "decision": decision,
            "confidence": float(arguments.get("confidence") or 0),
            "summary": str(arguments.get("summary") or arguments.get("suggestion") or ""),
            "reasons": list(arguments.get("reasons") or []),
            "risks": list(arguments.get("risks") or []),
            "missing_fields": list(arguments.get("missing_fields") or []),
            "missing_information": list(arguments.get("missing_information") or []),
            "hard_requirements": list(arguments.get("hard_requirements") or []),
            "match_dimensions": dict(arguments.get("match_dimensions") or {}),
            "strengths": list(arguments.get("strengths") or arguments.get("matches") or []),
            "gaps": list(arguments.get("gaps") or []),
            "resume_suggestions": list(arguments.get("resume_suggestions") or []),
            "greeting_draft": dict(
                arguments.get("greeting_draft")
                or {"status": "not_generated", "text": "", "facts_used": []}
            ),
            "source": "llm",
        }
        # 复用历史岗位和审批路由已有写入，不在 Codex 侧维护第二套评估状态。
        update_capture_job_delivery_evaluation(self.db, job=job, evaluation=evaluation)
        route = record_evaluation_and_route(
            self.db,
            job=job,
            evaluation=evaluation,
            recommendation_strategy=recommendation_strategy,
            filter_strategy=filter_strategy,
            resume_id=str(recommendation_strategy.get("resume_id") or "") or None,
            delivery_strategy=get_delivery_strategy(self.db),
            candidate_profile=profile,
            resume_version_id=resume_version_id,
            context_revision_id=context_revision_id,
            context_dependency_versions=_loads(
                context_revision["dependency_versions_json"], {}
            ),
        )
        if route is None:
            raise AppError(409, "JOB_HISTORY_REQUIRED", "岗位尚未写入历史采集。")
        evaluation_id = str(route["evaluation_id"])
        return _result(
            result_type="evaluation",
            status="succeeded",
            resource=_resource("job_evaluation", evaluation_id),
            data={"evaluation": evaluation, "route": route, "history_job_id": job_id},
            message="投递建议已写入历史岗位、岗位评估记录和现有审批路由。",
        )

    def create_greeting_preview(self, arguments: dict[str, Any]) -> dict[str, object]:
        job_id = str(arguments.get("job_id") or "")
        evaluation_id = str(arguments.get("evaluation_id") or "")
        text = str(arguments.get("text") or "").strip()
        expected_job_version = int(arguments.get("job_detail_version") or 0)
        if not text:
            raise AppError(status_code=422, error_category="VALIDATION_FAILED", error_message="打招呼文本不能为空。")
        classification = classify_outbound_content(text, base_operation="send_greeting")
        now = utc_now()
        with self.db.connect() as connection:
            job = connection.execute("SELECT detail_version FROM fj_boss_jobs WHERE id = ?", (job_id,)).fetchone()
            evaluation = connection.execute("SELECT * FROM fj_job_evaluations WHERE id = ? AND job_id = ?", (evaluation_id, job_id)).fetchone()
            if job is None or evaluation is None:
                raise AppError(status_code=404, error_category="RESOURCE_NOT_FOUND", error_message="岗位或评估不存在。")
            if int(job["detail_version"]) != expected_job_version:
                raise AppError(status_code=409, error_category="JOB_CONTEXT_CHANGED", error_message="岗位详情已经变化，请重新生成预览。")
            connection.execute(
                "UPDATE fj_review_items SET status = 'dismissed', updated_at = ?, resolved_at = ? WHERE job_id = ? AND status = 'pending'",
                (now, now, job_id),
            )
            preview_id = new_id()
            connection.execute(
                """
                INSERT INTO fj_review_items (
                  id, job_id, evaluation_id, action_type, status, ai_decision,
                  draft_message, final_message, resolution_note, auto_approved,
                  created_at, updated_at, version, content_categories_json,
                  classification_version, authorization_mode, candidate_profile_id,
                  profile_context_version, resume_version_id
                ) VALUES (?, ?, ?, 'start_conversation', 'pending', ?, ?, ?, '', 0, ?, ?, 1, ?, ?, 'manual_confirmation', ?, ?, ?)
                """,
                (
                    preview_id,
                    job_id,
                    evaluation_id,
                    evaluation["decision"],
                    text,
                    text,
                    now,
                    now,
                    json.dumps(classification.categories, ensure_ascii=False),
                    classification.classification_version,
                    evaluation["candidate_profile_id"],
                    evaluation["profile_context_version"],
                    evaluation["resume_version_id"],
                ),
            )
        return _result(
            result_type="preview",
            status="draft",
            resource=_resource("greeting_preview", preview_id),
            data={"preview_id": preview_id, "text": text, "content_categories": classification.categories},
            message="打招呼预览已创建。",
        )

    def save_chat_reply_draft(self, arguments: dict[str, Any]) -> dict[str, object]:
        session_id = str(arguments.get("session_id") or "")
        task_id = str(arguments.get("reply_task_id") or "").strip()
        text = str(arguments.get("final_text") or arguments.get("text") or "").strip()
        expected_session_version = int(arguments.get("session_version") or 0)
        based_on_message_id = str(arguments.get("latest_message_id") or arguments.get("based_on_message_id") or "")
        profile_id = str(arguments.get("profile_id") or profile_store.DEFAULT_PROFILE_ID)
        profile = profile_store.get_profile(self.db, profile_id)
        current_context_version = int(profile["versions"]["context_version"])  # type: ignore[index]
        expected_context_version = int(arguments.get("profile_context_version") or current_context_version)
        if current_context_version != expected_context_version:
            raise AppError(status_code=409, error_category="CONTEXT_VERSION_CHANGED", error_message="候选人对话上下文已经变化，请重新生成回复。")
        if not text:
            raise AppError(status_code=422, error_category="VALIDATION_FAILED", error_message="回复正文不能为空。")
        classification = classify_outbound_content(text, base_operation="send_chat_reply")
        now = utc_now()
        with self.db.connect() as connection:
            session = connection.execute("SELECT * FROM fj_chat_sessions WHERE id = ?", (session_id,)).fetchone()
            if session is None:
                raise AppError(status_code=404, error_category="CHAT_SESSION_NOT_FOUND", error_message="聊天会话不存在。")
            if int(session["session_version"]) != expected_session_version or session["latest_inbound_message_id"] != based_on_message_id:
                raise AppError(status_code=409, error_category="CHAT_CONTEXT_CHANGED", error_message="聊天上下文已经变化，请重新生成回复。")
            if task_id:
                task = connection.execute("SELECT * FROM fj_chat_reply_tasks WHERE id = ? AND session_id = ?", (task_id, session_id)).fetchone()
                if task is None or task["status"] != "awaiting_review":
                    raise AppError(status_code=409, error_category="CHAT_REPLY_NOT_EDITABLE", error_message="当前回复任务不可编辑。")
                connection.execute(
                    """
                    UPDATE fj_chat_reply_tasks SET final_text = ?, text_version = text_version + 1,
                      content_categories_json = ?, classification_version = ?, candidate_profile_id = ?,
                      profile_context_version = ?, updated_at = ? WHERE id = ?
                    """,
                    (
                        text,
                        json.dumps(classification.categories, ensure_ascii=False),
                        classification.classification_version,
                        profile_id,
                        expected_context_version,
                        now,
                        task_id,
                    ),
                )
            else:
                task_id = f"chat_reply_{new_id()}"
                connection.execute(
                    """
                    INSERT INTO fj_chat_reply_tasks (
                      id, session_id, trigger_source, status, based_on_message_id,
                      based_on_session_version, draft_text, final_text, generation_model,
                      generated_at, created_at, updated_at, text_version,
                      content_categories_json, classification_version,
                      candidate_profile_id, profile_context_version
                    ) VALUES (?, ?, 'manual', 'awaiting_review', ?, ?, ?, ?, 'codex-tui', ?, ?, ?, 1, ?, ?, ?, ?)
                    """,
                    (
                        task_id,
                        session_id,
                        based_on_message_id,
                        expected_session_version,
                        text,
                        text,
                        now,
                        now,
                        now,
                        json.dumps(classification.categories, ensure_ascii=False),
                        classification.classification_version,
                        profile_id,
                        expected_context_version,
                    ),
                )
            task = connection.execute("SELECT * FROM fj_chat_reply_tasks WHERE id = ?", (task_id,)).fetchone()
        assert task is not None
        return _result(
            result_type="preview",
            status="draft",
            resource=_resource("chat_reply", task_id, int(task["text_version"])),
            data=dict(task),
            message="代聊草稿已保存。",
        )

    def request_greeting_execution(self, arguments: dict[str, Any]) -> dict[str, object]:
        preview_id = str(arguments.get("preview_id") or "")
        expected_version = int(arguments.get("expected_version") or 0)
        with self.db.connect() as connection:
            row = connection.execute("SELECT * FROM fj_review_items WHERE id = ?", (preview_id,)).fetchone()
        if row is None:
            raise AppError(status_code=404, error_category="PREVIEW_NOT_FOUND", error_message="打招呼预览不存在。")
        if int(row["version"]) != expected_version or row["status"] != "pending":
            raise AppError(status_code=409, error_category="PREVIEW_STALE", error_message="打招呼预览已经失效。")
        if row["candidate_profile_id"]:
            profile = profile_store.get_profile(self.db, str(row["candidate_profile_id"]))
            if int(profile["versions"]["context_version"]) != int(row["profile_context_version"]):  # type: ignore[index]
                raise AppError(status_code=409, error_category="CONTEXT_VERSION_CHANGED", error_message="候选人上下文已经变化，请重新生成打招呼预览。")
        text = str(row["final_message"] or row["draft_message"] or "")
        classification = classify_outbound_content(text, base_operation="send_greeting")
        authorization = resolve_codex_authorization(self.config, classification=classification)
        if authorization["requires_confirmation"]:
            return _result(
                result_type="confirmation",
                status="awaiting_confirmation",
                resource=_resource("greeting_preview", preview_id, expected_version),
                data={"confirmation": _resource("greeting_preview", preview_id, expected_version), "text": text},
                terminal=False,
                requires_confirmation=True,
                authorization=authorization,
                message="请在 FineJob 确认卡片中处理该打招呼动作。",
            )
        _review, action = approve_review_item(self.db, preview_id, message=text, allow_override=True)
        action_id = str(action["id"])
        with self.db.connect() as connection:
            connection.execute(
                """
                UPDATE fj_automation_actions SET authorization_mode = 'pre_authorized',
                  authorization_source = 'settings', content_categories_json = ?,
                  classification_version = ? WHERE id = ?
                """,
                (json.dumps(classification.categories, ensure_ascii=False), classification.classification_version, action_id),
            )
        return _result(
            result_type="action",
            status=str(action["status"]),
            resource=_resource("automation_action", action_id),
            data=action,
            terminal=False,
            authorization=authorization,
            message="打招呼动作已进入 FineJob 队列。",
        )

    def request_chat_send(self, arguments: dict[str, Any]) -> dict[str, object]:
        task_id = str(arguments.get("reply_task_id") or "")
        expected_text_version = int(arguments.get("text_version") or 0)
        with self.db.connect() as connection:
            task = connection.execute("SELECT * FROM fj_chat_reply_tasks WHERE id = ?", (task_id,)).fetchone()
            session = connection.execute(
                "SELECT * FROM fj_chat_sessions WHERE id = ?", (task["session_id"],)
            ).fetchone() if task else None
        if task is None or session is None:
            raise AppError(status_code=404, error_category="CHAT_REPLY_NOT_FOUND", error_message="回复任务不存在。")
        if int(task["text_version"]) != expected_text_version or task["status"] != "awaiting_review":
            raise AppError(status_code=409, error_category="CHAT_REPLY_STALE", error_message="回复文本版本已经变化。")
        if task["candidate_profile_id"]:
            profile = profile_store.get_profile(self.db, str(task["candidate_profile_id"]))
            if int(profile["versions"]["context_version"]) != int(task["profile_context_version"]):  # type: ignore[index]
                raise AppError(status_code=409, error_category="CONTEXT_VERSION_CHANGED", error_message="候选人对话上下文已经变化，请重新生成回复。")
        expected_message = str(arguments.get("latest_message_id") or "")
        expected_session_version = int(arguments.get("session_version") or 0)
        if expected_message != session["latest_inbound_message_id"] or expected_session_version != int(session["session_version"]):
            raise AppError(status_code=409, error_category="CHAT_CONTEXT_CHANGED", error_message="确认前收到新消息，请重新生成回复。")
        text = str(task["final_text"] or "")
        classification = classify_outbound_content(text, base_operation="send_chat_reply")
        authorization = resolve_codex_authorization(self.config, classification=classification)
        if authorization["requires_confirmation"]:
            return _result(
                result_type="confirmation",
                status="awaiting_confirmation",
                resource=_resource("chat_reply", task_id, expected_text_version),
                data={"confirmation": _resource("chat_reply", task_id, expected_text_version), "text": text},
                terminal=False,
                requires_confirmation=True,
                authorization=authorization,
                message="请在 FineJob 确认卡片中处理该代聊发送动作。",
            )
        action = confirm_reply(self.db, task_id, {
            "final_text": text,
            "based_on_message_id": expected_message,
            "based_on_session_version": expected_session_version,
        })
        action_id = str(action["id"])
        with self.db.connect() as connection:
            connection.execute(
                """
                UPDATE fj_chat_send_actions SET authorization_mode = 'pre_authorized',
                  authorization_source = 'settings', content_categories_json = ?,
                  classification_version = ? WHERE id = ?
                """,
                (json.dumps(classification.categories, ensure_ascii=False), classification.classification_version, action_id),
            )
        return _result(
            result_type="action",
            status=str(action["status"]),
            resource=_resource("chat_send_action", action_id),
            data=action,
            terminal=False,
            authorization=authorization,
            message="代聊发送动作已进入 FineJob 队列。",
        )

    def get_operation_status(self, arguments: dict[str, Any]) -> dict[str, object]:
        resource_type = str(arguments.get("resource_type") or "")
        resource_id = str(arguments.get("resource_id") or "")
        if resource_type == "chat_batch":
            task = job_hunt_refresh.boss_chat.boss_chat_batch_manager.get(resource_id)
            raw_status = str(task.get("status") or "queued")
            data = (
                job_hunt_refresh.settle_chat_batch_operation(
                    self.db,
                    resource_id,
                    task,
                )
                if raw_status not in {"queued", "running"}
                else task
            )
            status = (
                str(data["status"])
                if isinstance(data, dict) and "status" in data and data is not task
                else {"completed": "succeeded"}.get(raw_status, raw_status)
            )
            return _result(
                result_type="task",
                status=status,
                resource=_resource(resource_type, resource_id),
                data=data,
                terminal=status in {"succeeded", "failed", "cancelled"},
            )
        if resource_type == "capture_task":
            task = boss_capture_task_manager.get_task(resource_id)
            raw_status = str(task.get("status") or "queued")
            status = {"completed": "succeeded"}.get(raw_status, raw_status)
            return _result(result_type="task", status=status, resource=_resource(resource_type, resource_id), data=task, terminal=status in {"succeeded", "failed", "cancelled"})
        table_map = {
            "greeting_preview": ("fj_review_items", "confirmation"),
            "automation_action": ("fj_automation_actions", "action"),
            "chat_reply": ("fj_chat_reply_tasks", "confirmation"),
            "chat_send_action": ("fj_chat_send_actions", "action"),
            "job_evaluation": ("fj_job_evaluations", "evaluation"),
        }
        table_info = table_map.get(resource_type)
        if table_info is None:
            raise AppError(status_code=422, error_category="RESOURCE_TYPE_INVALID", error_message="不支持的操作资源类型。")
        table, result_type = table_info
        with self.db.connect() as connection:
            row = connection.execute(f"SELECT * FROM {table} WHERE id = ?", (resource_id,)).fetchone()
        if row is None:
            raise AppError(status_code=404, error_category="RESOURCE_NOT_FOUND", error_message="操作资源不存在。")
        data = dict(row)
        status = str(data.get("status") or "succeeded")
        terminal = status in {"succeeded", "failed", "blocked", "unknown", "cancelled", "rejected", "stale"}
        version = int(data.get("version") or data.get("text_version") or data.get("structure_version") or 1)
        return _result(result_type=result_type, status=status, resource=_resource(resource_type, resource_id, version), data=data, terminal=terminal, requires_confirmation=status in {"pending", "awaiting_review"})

    @staticmethod
    def _evaluation_payload(row: Any) -> dict[str, object] | None:
        if row is None:
            return None
        payload = dict(row)
        payload["evaluation"] = _loads(payload.pop("evaluation_json", None), {})
        return payload

    @staticmethod
    def _review_payload(row: Any) -> dict[str, object]:
        payload = dict(row)
        payload["content_categories"] = _loads(payload.pop("content_categories_json", None), [])
        return payload

    @staticmethod
    def _automation_action_payload(row: Any) -> dict[str, object]:
        payload = dict(row)
        payload["payload"] = _loads(payload.pop("payload_json", None), {})
        payload["result"] = _loads(payload.pop("result_json", None), {})
        payload["content_categories"] = _loads(payload.pop("content_categories_json", None), [])
        return payload


def _strategy_with_resolved_profile(
    db: Database,
    strategy: dict[str, object],
) -> dict[str, object]:
    """旧策略只要关联具体简历，即可由简历唯一反查候选人档案。"""
    item = dict(strategy)
    resume_version_id = str(item.get("resume_version_id") or "").strip()
    if not resume_version_id:
        return item
    resume_version = profile_store.get_resume_version(db, resume_version_id)
    resume_profile_id = str(resume_version.get("profile_id") or "").strip()
    strategy_profile_id = str(item.get("candidate_profile_id") or "").strip()
    if strategy_profile_id and strategy_profile_id != resume_profile_id:
        raise AppError(
            409,
            "STRATEGY_PROFILE_MISMATCH",
            "建议投递策略关联档案与具体简历所属档案不一致。",
        )
    item["candidate_profile_id"] = resume_profile_id
    return item


def list_pending_work(db: Database) -> dict[str, object]:
    with db.connect() as connection:
        greetings = connection.execute(
            "SELECT * FROM fj_review_items WHERE status = 'pending' ORDER BY created_at DESC"
        ).fetchall()
        chats = connection.execute(
            "SELECT * FROM fj_chat_reply_tasks WHERE status = 'awaiting_review' ORDER BY created_at DESC"
        ).fetchall()
        unknown_actions = connection.execute(
            "SELECT * FROM fj_automation_actions WHERE status IN ('blocked', 'unknown') ORDER BY created_at DESC"
        ).fetchall()
        unknown_chat_actions = connection.execute(
            "SELECT * FROM fj_chat_send_actions WHERE status IN ('failed', 'unknown') ORDER BY created_at DESC"
        ).fetchall()
    return {
        "greetings": [dict(row) for row in greetings],
        "chat_replies": [dict(row) for row in chats],
        "automation_actions": [dict(row) for row in unknown_actions],
        "chat_actions": [dict(row) for row in unknown_chat_actions],
    }


def approve_pending(
    db: Database,
    *,
    resource_type: str,
    resource_id: str,
    expected_version: int,
    final_text: str,
    allow_override: bool,
) -> dict[str, object]:
    if resource_type == "greeting_preview":
        with db.connect() as connection:
            row = connection.execute("SELECT * FROM fj_review_items WHERE id = ?", (resource_id,)).fetchone()
        if row is None or int(row["version"]) != expected_version:
            raise AppError(status_code=409, error_category="PREVIEW_STALE", error_message="打招呼预览已经失效。")
        _require_current_profile_context(db, row)
        review, action = approve_review_item(db, resource_id, message=final_text, allow_override=allow_override)
        return {"confirmation": review, "action": action}
    if resource_type == "chat_reply":
        with db.connect() as connection:
            task = connection.execute("SELECT * FROM fj_chat_reply_tasks WHERE id = ?", (resource_id,)).fetchone()
            session = connection.execute("SELECT * FROM fj_chat_sessions WHERE id = ?", (task["session_id"],)).fetchone() if task else None
        if task is None or session is None or int(task["text_version"]) != expected_version:
            raise AppError(status_code=409, error_category="CHAT_REPLY_STALE", error_message="回复草稿已经失效。")
        _require_current_profile_context(db, task)
        return confirm_reply(db, resource_id, {
            "final_text": final_text or task["final_text"],
            "based_on_message_id": task["based_on_message_id"],
            "based_on_session_version": int(task["based_on_session_version"]),
        })
    raise AppError(status_code=422, error_category="RESOURCE_TYPE_INVALID", error_message="待确认资源类型无效。")


def reject_pending(db: Database, *, resource_type: str, resource_id: str, note: str) -> dict[str, object]:
    if resource_type == "greeting_preview":
        return reject_review_item(db, resource_id, note=note)
    if resource_type == "chat_reply":
        return cancel_reply(db, resource_id)
    raise AppError(status_code=422, error_category="RESOURCE_TYPE_INVALID", error_message="待确认资源类型无效。")


def _require_current_profile_context(db: Database, row: Any) -> None:
    profile_id = row["candidate_profile_id"] if "candidate_profile_id" in row.keys() else None
    context_version = row["profile_context_version"] if "profile_context_version" in row.keys() else None
    if not profile_id or context_version is None:
        return
    profile = profile_store.get_profile(db, str(profile_id))
    if int(profile["versions"]["context_version"]) != int(context_version):  # type: ignore[index]
        raise AppError(status_code=409, error_category="CONTEXT_VERSION_CHANGED", error_message="候选人上下文已经变化，请重新生成确认内容。")
