from __future__ import annotations

import inspect
import json
from contextvars import ContextVar
from functools import wraps
from uuid import UUID, uuid4

from backend.app.db import Database
from backend.app.errors import AppError
from backend.app.utils import utc_now


EXECUTOR_EPOCH = str(uuid4())
_current: ContextVar[dict | None] = ContextVar("collection_start_operation", default=None)


def current_operation() -> dict | None:
    return _current.get()


def initialize_schema(connection) -> None:
    # 新表按现有初始化入口增量创建，已有业务记录保持原样。
    connection.execute("""
        CREATE TABLE IF NOT EXISTS fj_collection_start_operations (
          operation_id TEXT PRIMARY KEY, action TEXT NOT NULL, owner_kind TEXT NOT NULL,
          owner_id TEXT, request_identity_json TEXT NOT NULL DEFAULT '{}',
          status TEXT NOT NULL, phase TEXT NOT NULL, result_kind TEXT,
          result_task_id TEXT, result_phase_ref TEXT, result_summary_json TEXT NOT NULL DEFAULT '{}',
          error_category TEXT, message TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL, executor_epoch TEXT NOT NULL,
          closed_without_dispatch INTEGER NOT NULL DEFAULT 0
        )
    """)


def _receipt(row) -> dict:
    result = dict(row)
    result.pop("request_identity_json", None)
    result["result_summary"] = json.loads(result.pop("result_summary_json", "{}"))
    result["closed_without_dispatch"] = bool(result["closed_without_dispatch"])
    result["response_type"] = "operation_receipt"
    return result


def get_operation(db: Database, operation_id: str) -> dict:
    with db.connect() as connection:
        row = connection.execute("SELECT * FROM fj_collection_start_operations WHERE operation_id = ?", (operation_id,)).fetchone()
        if row and row["status"] == "unknown":
            summary = json.loads(row["result_summary_json"] or "{}")
            if summary.get("status") in {"completed", "failed"}:
                # 执行器真实终态提供确认事实，可以结束响应丢失留下的未知窗口。
                connection.execute("UPDATE fj_collection_start_operations SET status = 'started', phase = 'finished', updated_at = ? WHERE operation_id = ? AND status = 'unknown'", (utc_now(), operation_id))
                row = connection.execute("SELECT * FROM fj_collection_start_operations WHERE operation_id = ?", (operation_id,)).fetchone()
    if row is None:
        raise AppError(404, "START_OPERATION_NOT_FOUND", "尚未查到启动结果，请保留本次请求身份。")
    return _receipt(row)


def resolve_operation(db: Database, operation_id: str) -> dict:
    _validate_id(operation_id)
    now = utc_now()
    with db.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        # 持久化关闭标记与启动登记使用同一个唯一键，迟到 POST 无法再取得执行权。
        connection.execute("""
            INSERT OR IGNORE INTO fj_collection_start_operations
              (operation_id, action, owner_kind, status, phase, created_at, updated_at,
               executor_epoch, closed_without_dispatch, message)
            VALUES (?, '', '', 'rejected', 'finished', ?, ?, ?, 1, '已确认结束未开始的请求。')
        """, (operation_id, now, now, EXECUTOR_EPOCH))
    return get_operation(db, operation_id)


def _validate_id(operation_id: str) -> None:
    try:
        UUID(operation_id)
    except (ValueError, TypeError):
        raise AppError(422, "VALIDATION_FAILED", "operation_id 必须为 UUID。") from None


def set_phase(phase: str) -> None:
    operation = current_operation()
    if operation is None:
        return
    with operation["db"].connect() as connection:
        connection.execute("UPDATE fj_collection_start_operations SET phase = ?, updated_at = ? WHERE operation_id = ? AND status = 'pending'",
                           (phase, utc_now(), operation["id"]))


def bind_in_connection(connection, kind: str, task_id: str, phase_ref: str | None = None) -> None:
    operation = current_operation()
    if operation is None:
        return
    phase_ref = phase_ref or task_id
    if operation.get("owner_kind") == "workflow" and operation.get("owner_id"):
        kind, task_id = "workflow", operation["owner_id"]
    # 同一事务保存真实身份和回执；后续子执行器只补充阶段引用。
    connection.execute("""
        UPDATE fj_collection_start_operations SET
          result_kind = COALESCE(result_kind, ?), result_task_id = COALESCE(result_task_id, ?),
          result_phase_ref = ?, phase = 'dispatching', updated_at = ?
        WHERE operation_id = ? AND status = 'pending'
    """, (kind, task_id, phase_ref, utc_now(), operation["id"]))


def bind_existing(db: Database, kind: str, task_id: str, phase_ref: str | None = None) -> None:
    with db.connect() as connection:
        bind_in_connection(connection, kind, task_id, phase_ref)


def record_task_summary(db: Database, task: dict) -> None:
    summary = {key: task.get(key) for key in ("id", "status", "stage", "message", "jobs_collected", "details_completed", "details_failed", "error_message")}
    with db.connect() as connection:
        connection.execute("""UPDATE fj_collection_start_operations SET result_summary_json = ?, updated_at = ?
            WHERE result_phase_ref = ? OR result_task_id = ?""",
            (json.dumps(summary, ensure_ascii=False), utc_now(), task["id"], task["id"]))


def recover_operations(db: Database) -> None:
    with db.connect() as connection:
        # 已绑定身份保留不确定窗口；从未绑定且旧执行进程已退出的请求可以明确关闭。
        connection.execute("""UPDATE fj_collection_start_operations
            SET status = CASE WHEN result_task_id IS NULL THEN 'rejected' ELSE 'unknown' END,
                closed_without_dispatch = CASE WHEN result_task_id IS NULL THEN 1 ELSE 0 END,
                phase = 'finished', message = '应用重启，原启动流程已中断，请查看已保存结果。', updated_at = ?
            WHERE status = 'pending' AND executor_epoch <> ?""", (utc_now(), EXECUTOR_EPOCH))


def assert_no_other_start(connection) -> None:
    operation = current_operation()
    row = connection.execute("""SELECT operation_id FROM fj_collection_start_operations
        WHERE status IN ('pending', 'unknown') AND operation_id <> ? LIMIT 1""",
        (operation["id"] if operation else "",)).fetchone()
    if row:
        raise AppError(409, "START_OPERATION_PENDING", "已有启动请求等待结果确认，请先检查原请求。")


def collection_start(action: str, owner_kind: str, owner_arg: str = ""):
    def decorate(function):
        signature = inspect.signature(function, eval_str=True)

        @wraps(function)
        def wrapped(*args, **kwargs):
            if current_operation() is not None:
                return function(*args, **kwargs)
            bound = signature.bind(*args, **kwargs)
            bound.apply_defaults()
            values = bound.arguments
            db = values["db"] if "db" in values else values["self"].db
            payload = values.get("payload") or values.get("arguments") or {}
            if hasattr(payload, "model_dump"):
                payload = payload.model_dump(mode="json")
            operation_id = str(values.get("operation_id") or (payload.get("operation_id") if isinstance(payload, dict) else "") or uuid4())
            _validate_id(operation_id)
            owner_id = str(values.get(owner_arg) or payload.get(owner_arg) or "") or None
            # 规范化业务参数后直接比较同一意图的动作和目标。
            identity = {key: value for key, value in values.items() if key not in {"self", "db", "config", "operation_id", "transition_id"}}
            if "arguments" in identity:
                identity["arguments"] = {key: value for key, value in payload.items() if key != "operation_id"}
            if "payload" in identity:
                identity["payload"] = {key: value for key, value in payload.items() if key != "operation_id"}
            canonical = json.dumps(identity, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))
            now = utc_now()
            with db.connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                old = connection.execute("SELECT * FROM fj_collection_start_operations WHERE operation_id = ?", (operation_id,)).fetchone()
                if old:
                    if not old["closed_without_dispatch"] and (old["action"] != action or old["owner_id"] != owner_id or old["request_identity_json"] != canonical):
                        raise AppError(409, "START_OPERATION_CONFLICT", "同一启动请求身份不能用于不同参数或目标。")
                    return _receipt(old)
                connection.execute("""INSERT INTO fj_collection_start_operations
                    (operation_id, action, owner_kind, owner_id, request_identity_json, status, phase, created_at, updated_at, executor_epoch)
                    VALUES (?, ?, ?, ?, ?, 'pending', 'validating', ?, ?, ?)""",
                    (operation_id, action, owner_kind, owner_id, canonical, now, now, EXECUTOR_EPOCH))
                other = connection.execute("SELECT 1 FROM fj_collection_start_operations WHERE operation_id <> ? AND status IN ('pending', 'unknown') LIMIT 1", (operation_id,)).fetchone()
                if other:
                    connection.execute("UPDATE fj_collection_start_operations SET status = 'rejected', phase = 'finished', error_category = 'START_OPERATION_PENDING', message = '已有启动请求等待结果确认。' WHERE operation_id = ?", (operation_id,))
            if other:
                return get_operation(db, operation_id)
            token = _current.set({"db": db, "id": operation_id, "ready": False, "owner_kind": owner_kind, "owner_id": owner_id})
            try:
                result = function(*args, **kwargs)
                if hasattr(result, "model_dump"):
                    result = result.model_dump(mode="json")
                task_result = result.get("data") if isinstance(result.get("data"), dict) else result
                task_id = str(task_result.get("smart_capture_id") or task_result.get("workflow_run_id") or task_result.get("id") or owner_id or "")
                with db.connect() as connection:
                    row = connection.execute("SELECT result_task_id FROM fj_collection_start_operations WHERE operation_id = ?", (operation_id,)).fetchone()
                    if not row["result_task_id"]:
                        bind_in_connection(connection, owner_kind, task_id)
                    connection.execute("UPDATE fj_collection_start_operations SET status = 'started', phase = 'finished', updated_at = ? WHERE operation_id = ?", (utc_now(), operation_id))
                return {**result, "operation_id": operation_id, "response_type": "task"}
            except Exception as exc:
                with db.connect() as connection:
                    connection.execute("""UPDATE fj_collection_start_operations
                        SET status = CASE WHEN result_task_id IS NULL THEN 'rejected' ELSE 'unknown' END,
                            phase = 'finished', error_category = ?, message = ?, updated_at = ? WHERE operation_id = ?""",
                        (getattr(exc, "error_category", "START_FAILED"), str(exc), utc_now(), operation_id))
                if isinstance(exc, AppError):
                    exc.operation_id = operation_id
                raise
            finally:
                _current.reset(token)
        wrapped.__signature__ = signature
        return wrapped
    return decorate


def start_http_response(function):
    signature = inspect.signature(function, eval_str=True)

    @wraps(function)
    def wrapped(*args, **kwargs):
        result = function(*args, **kwargs)
        if isinstance(result, dict) and result.get("response_type") == "operation_receipt" and result.get("status") == "pending":
            from fastapi.responses import JSONResponse
            return JSONResponse(result, status_code=202)
        return result

    wrapped.__signature__ = signature
    return wrapped
