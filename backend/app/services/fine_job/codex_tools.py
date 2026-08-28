from __future__ import annotations

import json
from typing import Any, Callable

from backend.app.config import AppConfig
from backend.app.db import Database
from backend.app.errors import AppError
from backend.app.services.fine_job.boss_capture_history import get_capture_history_job
from backend.app.services.fine_job.boss_capture_tasks import boss_capture_task_manager
from backend.app.services.fine_job.boss_chat import (
    cancel_reply,
    confirm_reply,
    get_session,
    list_sessions,
)
from backend.app.services.fine_job.boss_scraper.service import boss_scraper_service
from backend.app.services.fine_job.codex_authorization import (
    classify_outbound_content,
    resolve_codex_authorization,
)
from backend.app.services.fine_job.resumes import list_resume_facts, list_resumes
from backend.app.services.fine_job.workflow import (
    approve_review_item,
    reject_review_item,
)
from backend.app.utils import new_id, utc_now


CORE_TOOLS = (
    "finejob.get_capabilities",
    "finejob.search_jobs",
    "finejob.get_job_context",
    "finejob.collect_job_detail",
    "finejob.list_resumes",
    "finejob.get_resume_facts",
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
            "finejob.search_jobs": self.search_jobs,
            "finejob.get_job_context": self.get_job_context,
            "finejob.collect_job_detail": self.collect_job_detail,
            "finejob.list_resumes": self.list_resumes,
            "finejob.get_resume_facts": self.get_resume_facts,
            "finejob.list_chat_sessions": self.list_chat_sessions,
            "finejob.get_chat_context": self.get_chat_context,
            "finejob.save_job_evaluation": self.save_job_evaluation,
            "finejob.create_greeting_preview": self.create_greeting_preview,
            "finejob.save_chat_reply_draft": self.save_chat_reply_draft,
            "finejob.request_greeting_execution": self.request_greeting_execution,
            "finejob.request_chat_send": self.request_chat_send,
            "finejob.get_operation_status": self.get_operation_status,
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
                "SELECT browser_connected, permission_state, queue_state FROM fj_boss_executor_instances ORDER BY updated_at DESC LIMIT 1"
            ).fetchone()
            chat_runtime = connection.execute(
                "SELECT send_enabled FROM fj_chat_runtime ORDER BY updated_at DESC LIMIT 1"
            ).fetchone()
        data = {
            "mcp_contract_version": "v1",
            "finejob_internal_api_version": "v1",
            "finejob_capabilities_version": "v1",
            "registered_tools": list(CORE_TOOLS),
            "runtime": {
                "boss_browser_running": bool(browser.running),
                "boss_executor_online": bool(executor and executor["browser_connected"]),
                "boss_executor_permission": executor["permission_state"] if executor else "not_authorized",
                "boss_executor_queue": executor["queue_state"] if executor else "paused",
                "chat_send_enabled": bool(chat_runtime and chat_runtime["send_enabled"]),
            },
            "future_capabilities": {
                "application_preview": False,
                "application_execution": False,
                "delivery_run_request": False,
            },
        }
        return _result(result_type="data", status="succeeded", data=data)

    def search_jobs(self, arguments: dict[str, Any]) -> dict[str, object]:
        query = str(arguments.get("query") or "").strip()
        city = str(arguments.get("city") or "").strip()
        detail_status = str(arguments.get("status") or "").strip()
        page = max(1, int(arguments.get("page") or 1))
        page_size = min(100, max(1, int(arguments.get("page_size") or 20)))
        clauses: list[str] = []
        values: list[object] = []
        if query:
            clauses.append("(j.title LIKE ? OR j.company_name LIKE ? OR j.skills LIKE ?)")
            values.extend([f"%{query}%"] * 3)
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
                SELECT j.id, j.title, j.company_name, j.salary, j.location,
                       j.detail_status, j.detail_version, j.last_collected_at,
                       e.id AS evaluation_id, e.decision AS evaluation_decision
                FROM fj_boss_jobs j
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
        return _result(
            result_type="data",
            status="succeeded",
            resource=_resource("job", job_id, int(job["detail_version"])),
            data=data,
        )

    def collect_job_detail(self, arguments: dict[str, Any]) -> dict[str, object]:
        job_id = str(arguments.get("job_id") or "")
        if not boss_scraper_service.get_browser_status().running:
            raise AppError(status_code=409, error_category="BROWSER_NOT_RUNNING", error_message="FineJob 专用 Chrome 未运行。")
        job = get_capture_history_job(self.db, job_id)
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
        return _result(
            result_type="data",
            status="succeeded",
            resource=_resource("chat_session", session_id, int(session["session_version"])),
            data=data,
        )

    def save_job_evaluation(self, arguments: dict[str, Any]) -> dict[str, object]:
        job_id = str(arguments.get("job_id") or "")
        resume_id = str(arguments.get("resume_id") or "")
        expected_job_version = int(arguments.get("job_detail_version") or 0)
        expected_facts_version = int(arguments.get("resume_facts_version") or 0)
        decision = str(arguments.get("conclusion") or arguments.get("decision") or "review")
        if decision not in {"recommend", "review", "reject"}:
            raise AppError(status_code=422, error_category="VALIDATION_FAILED", error_message="评估结论无效。")
        with self.db.connect() as connection:
            job = connection.execute("SELECT detail_version FROM fj_boss_jobs WHERE id = ?", (job_id,)).fetchone()
            resume = connection.execute("SELECT facts_version FROM fj_resumes WHERE id = ?", (resume_id,)).fetchone()
            if job is None or resume is None:
                raise AppError(status_code=404, error_category="RESOURCE_NOT_FOUND", error_message="岗位或简历不存在。")
            if int(job["detail_version"]) != expected_job_version or int(resume["facts_version"]) != expected_facts_version:
                raise AppError(status_code=409, error_category="CONTEXT_VERSION_CHANGED", error_message="岗位详情或简历事实已经变化，请重新读取上下文。")
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
                  resume_facts_version, structure_version
                ) VALUES (?, ?, 'codex-v1', ?, 'llm', ?, ?, ?, ?, ?, ?, 1)
                """,
                (evaluation_id, job_id, resume_id, decision, float(arguments.get("confidence") or 0), json.dumps(payload, ensure_ascii=False), now, expected_job_version, expected_facts_version),
            )
        return _result(
            result_type="evaluation",
            status="succeeded",
            resource=_resource("job_evaluation", evaluation_id),
            data={"evaluation_id": evaluation_id, "evaluation_version": 1, **payload},
            message="结构化岗位评估已保存。",
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
            evaluation = connection.execute("SELECT id, decision FROM fj_job_evaluations WHERE id = ? AND job_id = ?", (evaluation_id, job_id)).fetchone()
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
                  classification_version, authorization_mode
                ) VALUES (?, ?, ?, 'start_conversation', 'pending', ?, ?, ?, '', 0, ?, ?, 1, ?, ?, 'manual_confirmation')
                """,
                (preview_id, job_id, evaluation_id, evaluation["decision"], text, text, now, now, json.dumps(classification.categories, ensure_ascii=False), classification.classification_version),
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
                      content_categories_json = ?, classification_version = ?, updated_at = ? WHERE id = ?
                    """,
                    (text, json.dumps(classification.categories, ensure_ascii=False), classification.classification_version, now, task_id),
                )
            else:
                task_id = f"chat_reply_{new_id()}"
                connection.execute(
                    """
                    INSERT INTO fj_chat_reply_tasks (
                      id, session_id, trigger_source, status, based_on_message_id,
                      based_on_session_version, draft_text, final_text, generation_model,
                      generated_at, created_at, updated_at, text_version,
                      content_categories_json, classification_version
                    ) VALUES (?, ?, 'manual', 'awaiting_review', ?, ?, ?, ?, 'codex-tui', ?, ?, ?, 1, ?, ?)
                    """,
                    (task_id, session_id, based_on_message_id, expected_session_version, text, text, now, now, now, json.dumps(classification.categories, ensure_ascii=False), classification.classification_version),
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
        review, action = approve_review_item(db, resource_id, message=final_text, allow_override=allow_override)
        return {"confirmation": review, "action": action}
    if resource_type == "chat_reply":
        with db.connect() as connection:
            task = connection.execute("SELECT * FROM fj_chat_reply_tasks WHERE id = ?", (resource_id,)).fetchone()
            session = connection.execute("SELECT * FROM fj_chat_sessions WHERE id = ?", (task["session_id"],)).fetchone() if task else None
        if task is None or session is None or int(task["text_version"]) != expected_version:
            raise AppError(status_code=409, error_category="CHAT_REPLY_STALE", error_message="回复草稿已经失效。")
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

