from __future__ import annotations

import asyncio
import json
import re
import secrets
from typing import Any, Callable
from urllib.parse import urlparse

from backend.app.db import Database
from backend.app.errors import AppError
from backend.app.services.fine_job.boss_scraper.service import boss_scraper_service
from backend.app.utils import new_id, utc_now


PROTOCOL_VERSION = "1.1"
PAIRING_CODE_TTL_SECONDS = 300
TEST_JOB_COUNT = 5
TEST_JOB_LINK_DEFAULT = "https://www.zhipin.com/"

# 控制通道只保存当前进程中的连接和一次性心跳请求。
_executor_channels: dict[str, Any] = {}
_heartbeat_requests: dict[str, tuple[str, asyncio.Future[dict[str, object]]]] = {}
_control_requests: dict[str, tuple[str, asyncio.Future[dict[str, object]]]] = {}
_desktop_channels: set[Any] = set()


def _hash(value: str) -> str:
    return value


def _iso_after(seconds: int) -> str:
    from datetime import datetime, timedelta, timezone

    return (
        datetime.now(timezone.utc) + timedelta(seconds=seconds)
    ).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def create_pairing_code(db: Database) -> dict[str, str]:
    code = f"{secrets.randbelow(1_000_000):06d}"
    now = utc_now()
    expires_at = _iso_after(PAIRING_CODE_TTL_SECONDS)
    with db.connect() as connection:
        connection.execute(
            "DELETE FROM fj_boss_pairing_codes WHERE used_at IS NOT NULL OR expires_at <= ?",
            (now,),
        )
        connection.execute(
            "INSERT INTO fj_boss_pairing_codes (id, code_hash, expires_at, created_at) VALUES (?, ?, ?, ?)",
            (new_id(), _hash(code), expires_at, now),
        )
    return {"code": code, "expires_at": expires_at}


def reset_executor_connections(db: Database) -> None:
    _finish_active_tasks_as_unknown(db, "服务启动时发现遗留执行中任务，已结束为结果未知。", "EXECUTOR_STARTUP_RESET")
    with db.connect() as connection:
        connection.execute(
            """
            UPDATE fj_boss_executor_instances
            SET browser_connected = 0, runtime_phase = 'idle', runtime_detail = '',
                runtime_until_at = NULL, updated_at = ?
            """,
            (utc_now(),),
        )


def pair_executor(
    db: Database,
    *,
    code: str,
    label: str,
    protocol_version: str,
    plugin_version: str,
    capabilities: list[str],
) -> dict[str, str]:
    if protocol_version != PROTOCOL_VERSION:
        raise AppError(409, "PROTOCOL_MISMATCH", "插件通信协议版本不兼容。")
    now = utc_now()
    token = secrets.token_urlsafe(32)
    executor_id = new_id()
    with db.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT id, expires_at, used_at FROM fj_boss_pairing_codes WHERE code_hash = ?",
            (_hash(code.strip()),),
        ).fetchone()
        if row is None or row["used_at"] is not None or str(row["expires_at"]) <= now:
            raise AppError(401, "INVALID_PAIRING_CODE", "配对码无效或已经过期。")
        connection.execute(
            "UPDATE fj_boss_pairing_codes SET used_at = ? WHERE id = ?",
            (now, row["id"]),
        )
        connection.execute(
            "UPDATE fj_boss_executor_instances SET queue_state = 'paused', updated_at = ?",
            (now,),
        )
        connection.execute(
            """
            INSERT INTO fj_boss_executor_instances (
              id, label, token_hash, protocol_version, plugin_version, capabilities_json,
              queue_state, risk_state, browser_connected, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'paused', 'none', 0, ?, ?)
            """,
            (
                executor_id,
                label.strip(),
                _hash(token),
                protocol_version,
                plugin_version,
                json.dumps(capabilities, ensure_ascii=False),
                now,
                now,
            ),
        )
    return {"executor_id": executor_id, "token": token, "protocol_version": PROTOCOL_VERSION}


def authenticate_executor(db: Database, token: str) -> dict[str, object]:
    if not token:
        raise AppError(401, "EXECUTOR_UNAUTHORIZED", "缺少执行器令牌。")
    with db.connect() as connection:
        row = connection.execute(
            "SELECT * FROM fj_boss_executor_instances WHERE token_hash = ?",
            (_hash(token),),
        ).fetchone()
    if row is None:
        raise AppError(401, "EXECUTOR_UNAUTHORIZED", "执行器令牌无效。")
    return dict(row)


async def register_executor_channel(db: Database, executor_id: str, websocket: Any) -> None:
    previous = _executor_channels.get(executor_id)
    if previous is not None and previous is not websocket:
        await previous.close(code=1000)
    _executor_channels[executor_id] = websocket
    _finish_active_tasks_as_unknown(db, "插件重新连接后清理遗留执行中任务，已结束为结果未知。", "EXECUTOR_RECONNECTED_RESET")
    _record_channel_heartbeat(db, executor_id)
    await _send_queue(db, executor_id)
    await _broadcast_executor_state(db)


async def unregister_executor_channel(db: Database, executor_id: str, websocket: Any) -> None:
    if _executor_channels.get(executor_id) is websocket:
        _executor_channels.pop(executor_id, None)
        mark_executor_disconnected(db, executor_id)
        await _broadcast_executor_state(db)
    for request_id, (owner_id, future) in list(_heartbeat_requests.items()):
        if owner_id != executor_id:
            continue
        _heartbeat_requests.pop(request_id, None)
        if not future.done():
            future.set_result({"ok": False, "message": "插件控制通道已断开。"})
    for request_id, (owner_id, future) in list(_control_requests.items()):
        if owner_id != executor_id:
            continue
        _control_requests.pop(request_id, None)
        if not future.done():
            future.set_exception(AppError(409, "EXECUTOR_NOT_CONNECTED", "插件控制通道已断开。"))


async def register_desktop_channel(db: Database, websocket: Any) -> None:
    _desktop_channels.add(websocket)
    await _send_desktop_state(db, websocket)


async def unregister_desktop_channel(websocket: Any) -> None:
    _desktop_channels.discard(websocket)


async def close_executor_channel(executor_id: str) -> None:
    websocket = _executor_channels.pop(executor_id, None)
    if websocket is None:
        return
    try:
        await websocket.close(code=4001, reason="executor_disconnected")
    except Exception:
        pass


async def handle_executor_channel_message(db: Database, executor_id: str, message: object) -> None:
    if not isinstance(message, dict):
        return
    try:
        await _handle_executor_channel_message(db, executor_id, message)
    except AppError as exc:
        await _report_executor_channel_error(
            db,
            executor_id,
            message,
            code=exc.error_category,
            error_message=exc.error_message,
        )
    except Exception as exc:
        await _report_executor_channel_error(
            db,
            executor_id,
            message,
            code="EXECUTOR_MESSAGE_FAILED",
            error_message=str(exc) or "插件运行消息处理失败。",
        )


async def _handle_executor_channel_message(db: Database, executor_id: str, message: dict[str, object]) -> None:
    message_type = str(message.get("type") or "")
    if message_type == "heartbeat_test_result":
        request_id = str(message.get("request_id") or "")
        pending = _heartbeat_requests.pop(request_id, None)
        if pending is None or pending[0] != executor_id:
            return
        future = pending[1]
        if not future.done():
            future.set_result({"ok": bool(message.get("ok")), "message": str(message.get("message") or "")})
        return
    if message_type == "heartbeat":
        _record_channel_heartbeat(db, executor_id)
        await _broadcast_executor_state(db)
        return
    if message_type == "runtime_state":
        phase = str(message.get("phase") or "idle")
        if phase not in {"idle", "task_cooldown"}:
            return
        detail = str(message.get("detail") or "")
        until_at = str(message.get("until_at") or "") or None
        _update_runtime_state(db, executor_id, phase, detail=detail, until_at=until_at)
        await _broadcast_executor_state(db)
        return
    if message_type == "executor_state_changed":
        queue_state = str(message.get("queue_state") or "")
        if queue_state not in {"running", "paused"}:
            return
        _update_queue_state(db, executor_id, queue_state)
        request_id = str(message.get("request_id") or "")
        pending = _control_requests.pop(request_id, None)
        if pending is not None and pending[0] == executor_id and not pending[1].done():
            pending[1].set_result(executor_status(db, executor_id))
        await notify_queue_changed(db)
        return
    if message_type == "open_task_page":
        await _open_and_notify_task_page(db, executor_id)
        return
    if message_type == "match_task":
        task_id = str(message.get("task_id") or "")
        if not task_id:
            return
        task = _require_action(db, task_id)
        execution_epoch = int(
            message.get("execution_epoch")
            if message.get("execution_epoch") is not None
            else task["execution_epoch"] or 0
        )
        result = match_task(db, executor_id, task_id, execution_epoch)
        await _send_executor_message(executor_id, {
            "type": "task_match_synced",
            "task_id": task_id,
            "execution_epoch": execution_epoch,
            "task": result["task"],
            "queue": result["queue"],
        })
        await notify_queue_changed(db)
        return
    if message_type == "task_succeeded":
        await _handle_task_completion_message(db, executor_id, message, succeeded=True)
        return
    if message_type == "task_failed":
        await _handle_task_completion_message(db, executor_id, message, succeeded=False)
        return
    if message_type == "execution_error":
        task_id = str(message.get("task_id") or "")
        failure_kind = str(message.get("failure_kind") or "")
        if task_id and failure_kind == "page_match_failed":
            _record_task_match_failure(db, task_id, str(message.get("error_message") or "页面匹配失败"))
            await notify_queue_changed(db)
            if bool(message.get("disconnect")):
                await close_executor_channel(executor_id)
                mark_executor_disconnected(db, executor_id)
                await _broadcast_executor_state(db)
            return
        if task_id:
            _record_execution_error(db, task_id, str(message.get("error_message") or "页面检查失败"))
            await notify_queue_changed(db)
        await close_executor_channel(executor_id)
        mark_executor_disconnected(db, executor_id)
        await _broadcast_executor_state(db)


async def request_heartbeat_test(db: Database, executor_id: str) -> dict[str, object]:
    websocket = _executor_channels.get(executor_id)
    if websocket is None:
        mark_executor_disconnected(db, executor_id)
        raise AppError(409, "EXECUTOR_NOT_CONNECTED", "插件未连接，无法进行心跳测试。")
    request_id = new_id()
    future = asyncio.get_running_loop().create_future()
    _heartbeat_requests[request_id] = (executor_id, future)
    try:
        await websocket.send_json({"type": "heartbeat_test", "request_id": request_id})
        result = await asyncio.wait_for(future, timeout=6)
    except asyncio.TimeoutError as exc:
        _heartbeat_requests.pop(request_id, None)
        mark_executor_disconnected(db, executor_id)
        raise AppError(504, "HEARTBEAT_TIMEOUT", "插件未在规定时间内返回心跳。") from exc
    except Exception as exc:
        _heartbeat_requests.pop(request_id, None)
        mark_executor_disconnected(db, executor_id)
        raise AppError(409, "EXECUTOR_NOT_CONNECTED", "插件控制通道不可用。") from exc
    if not result.get("ok"):
        mark_executor_disconnected(db, executor_id)
        raise AppError(502, "HEARTBEAT_FAILED", str(result.get("message") or "插件心跳失败。"))
    return executor_status(db, executor_id)


def mark_executor_disconnected(db: Database, executor_id: str) -> None:
    _finish_active_tasks_as_unknown(db, "插件连接已断开，执行中任务已结束为结果未知。", "EXECUTOR_DISCONNECTED")
    with db.connect() as connection:
        connection.execute(
            """
            UPDATE fj_boss_executor_instances
            SET browser_connected = 0, runtime_phase = 'idle', runtime_detail = '',
                runtime_until_at = NULL, updated_at = ?
            WHERE id = ?
            """,
            (utc_now(), executor_id),
        )


async def disconnect_executor(db: Database, executor_id: str) -> dict[str, object]:
    _finish_active_tasks_as_unknown(db, "执行器断开连接，执行中任务已结束为结果未知。", "EXECUTOR_DISCONNECTED")
    with db.connect() as connection:
        connection.execute(
            """
            UPDATE fj_boss_executor_instances
            SET token_hash = 'revoked:' || id, browser_connected = 0,
                queue_state = 'paused', runtime_phase = 'idle', runtime_detail = '',
                runtime_until_at = NULL, updated_at = ?
            WHERE id = ?
            """,
            (utc_now(), executor_id),
        )
    await close_executor_channel(executor_id)
    await _broadcast_executor_state(db)
    return executor_status(db, executor_id)


def heartbeat(db: Database, executor_id: str, payload: dict[str, object]) -> dict[str, object]:
    if str(payload.get("protocol_version") or "") != PROTOCOL_VERSION:
        raise AppError(409, "PROTOCOL_MISMATCH", "插件通信协议版本不兼容。")
    now = utc_now()
    risk_state = str(payload.get("risk_state") or "none")
    if risk_state not in {"none", "login", "captcha", "rate_limit", "unknown"}:
        risk_state = "unknown"
    with db.connect() as connection:
        connection.execute(
            """
            UPDATE fj_boss_executor_instances
            SET protocol_version = ?, plugin_version = ?, capabilities_json = ?,
                browser_connected = ?, risk_state = ?, last_heartbeat_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                PROTOCOL_VERSION,
                str(payload.get("plugin_version") or ""),
                json.dumps(payload.get("capabilities") or [], ensure_ascii=False),
                int(bool(payload.get("browser_connected"))),
                risk_state,
                now,
                now,
                executor_id,
            ),
        )
        if risk_state != "none":
            connection.execute(
                "UPDATE fj_boss_executor_instances SET queue_state = 'risk_paused' WHERE id = ?",
                (executor_id,),
            )
    return executor_status(db, executor_id)


def update_executor_settings(db: Database, payload: dict[str, object]) -> dict[str, object]:
    runtime = executor_status(db)
    executor = runtime.get("executor")
    if not isinstance(executor, dict):
        raise AppError(409, "EXECUTOR_NOT_PAIRED", "尚未配对BOSS执行器。")
    task_cooldown = min(600, max(4, int(payload.get("task_cooldown_max_seconds") or 4)))
    page_load_wait = min(600, max(3, int(payload.get("page_load_wait_max_seconds") or 3)))
    with db.connect() as connection:
        connection.execute(
            """
            UPDATE fj_boss_executor_instances
            SET task_cooldown_max_seconds = ?, page_load_wait_max_seconds = ?, updated_at = ?
            WHERE id = ?
            """,
            (task_cooldown, page_load_wait, utc_now(), executor["id"]),
        )
    return executor_status(db, str(executor["id"]))


def _update_queue_state(db: Database, executor_id: str, queue_state: str) -> None:
    if queue_state == "running":
        _finish_active_tasks_as_unknown(db, "开始执行前清理遗留执行中任务，已结束为结果未知。", "EXECUTOR_START_RESET")
    with db.connect() as connection:
        current = connection.execute(
            "SELECT risk_state FROM fj_boss_executor_instances WHERE id = ?",
            (executor_id,),
        ).fetchone()
        if current is None:
            raise AppError(404, "NOT_FOUND", "执行器不存在。")
        if queue_state == "running" and current["risk_state"] != "none":
            raise AppError(409, "RISK_NOT_CLEARED", "插件仍报告风险，不能恢复。")
        connection.execute(
            "UPDATE fj_boss_executor_instances SET queue_state = ?, updated_at = ? WHERE id = ?",
            (queue_state, utc_now(), executor_id),
        )


def _update_runtime_state(
    db: Database,
    executor_id: str,
    phase: str,
    *,
    detail: str = "",
    until_at: str | None = None,
) -> None:
    with db.connect() as connection:
        connection.execute(
            """
            UPDATE fj_boss_executor_instances
            SET runtime_phase = ?, runtime_detail = ?, runtime_until_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (phase, detail[:300], until_at, utc_now(), executor_id),
        )


async def request_control(db: Database, executor_id: str, command: str) -> dict[str, object]:
    websocket = _executor_channels.get(executor_id)
    if websocket is None:
        mark_executor_disconnected(db, executor_id)
        await _broadcast_executor_state(db)
        raise AppError(409, "EXECUTOR_NOT_CONNECTED", "插件未连接，无法同步执行状态。")
    request_id = new_id()
    future = asyncio.get_running_loop().create_future()
    _control_requests[request_id] = (executor_id, future)
    try:
        await websocket.send_json({"type": "executor_control", "command": command, "request_id": request_id})
        result = await asyncio.wait_for(future, timeout=6)
    except asyncio.TimeoutError as exc:
        _control_requests.pop(request_id, None)
        raise AppError(504, "CONTROL_SYNC_TIMEOUT", "插件未确认执行状态变更。") from exc
    except Exception:
        _control_requests.pop(request_id, None)
        raise
    _audit(db, "executor_control", f"插件已同步执行状态：{command}", {"executor_id": executor_id})
    return result


async def set_plugin_control(db: Database, executor_id: str, command: str) -> dict[str, object]:
    queue_state = "running" if command == "start" else "paused"
    _update_queue_state(db, executor_id, queue_state)
    await _send_queue(db, executor_id)
    await _broadcast_executor_state(db)
    return executor_status(db, executor_id)


def executor_status(db: Database, executor_id: str | None = None) -> dict[str, object]:
    with db.connect() as connection:
        executor = (
            connection.execute("SELECT * FROM fj_boss_executor_instances WHERE id = ?", (executor_id,)).fetchone()
            if executor_id
            else connection.execute("SELECT * FROM fj_boss_executor_instances ORDER BY updated_at DESC LIMIT 1").fetchone()
        )
    return {
        "executor": _serialize_executor(executor) if executor else None,
        "current_task": _current_task(db),
        "queue": list_queue(db),
        "protocol_version": PROTOCOL_VERSION,
    }


def list_queue(db: Database) -> dict[str, object]:
    # 插件只保留待执行与执行中的任务；终态任务通过桌面端结果展示。
    with db.connect() as connection:
        rows = connection.execute(
            """
            SELECT a.*, j.title AS job_title, j.company_name, j.encrypt_job_id, j.job_link
            FROM fj_automation_actions a
            JOIN fj_boss_jobs j ON j.id = a.job_id
            WHERE a.task_type IN ('BOSS_DEFAULT_GREETING', 'TEST_DELAY')
              AND a.status IN ('queued', 'running', 'leased')
              AND a.execution_state IN ('queued', 'running')
            ORDER BY a.created_at ASC, a.id ASC
            """
        ).fetchall()
    actions = [_serialize_action(row, include_payload=False) for row in rows]
    return {"actions": actions, "total": len(actions)}


def open_navigation(
    db: Database,
    *,
    job_identifier: str,
    source_context: str,
    action_id: str | None = None,
    open_page: Callable[[str], str] | None = None,
) -> dict[str, object]:
    job = _resolve_job(db, job_identifier, source_context)
    target_url = _target_job_url(job)
    navigation_id = new_id()
    now = utc_now()
    with db.connect() as connection:
        connection.execute(
            """
            INSERT INTO fj_boss_navigation_tasks (
              id, action_id, job_id, source_context, target_url, target_encrypt_job_id,
              status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'queued', ?, ?)
            """,
            (
                navigation_id,
                action_id,
                job["id"],
                source_context,
                target_url,
                job["encrypt_job_id"],
                now,
                now,
            ),
        )
    try:
        target_id = (open_page or boss_scraper_service.open_job_page)(target_url)
    except (ValueError, RuntimeError, TimeoutError, OSError) as exc:
        with db.connect() as connection:
            connection.execute(
                "UPDATE fj_boss_navigation_tasks SET status = 'failed', error_code = 'PAGE_OPEN_FAILED', error_message = ?, updated_at = ? WHERE id = ?",
                (str(exc), utc_now(), navigation_id),
            )
        raise AppError(409, "PAGE_OPEN_FAILED", str(exc)) from exc
    opened_at = utc_now()
    with db.connect() as connection:
        connection.execute(
            "UPDATE fj_boss_navigation_tasks SET status = 'opened', browser_target_id = ?, opened_at = ?, updated_at = ? WHERE id = ?",
            (target_id, opened_at, opened_at, navigation_id),
        )
    return get_navigation(db, navigation_id)


def open_task_page(
    db: Database,
    executor_id: str,
    *,
    open_page: Callable[[str], str] | None = None,
) -> dict[str, object]:
    executor = _executor_row(db, executor_id)
    if executor["queue_state"] != "running":
        return {"task": None, "navigation": None, "queue": list_queue(db)}
    with db.connect() as connection:
        active = connection.execute(
            """
            SELECT id FROM fj_automation_actions
            WHERE task_type IN ('BOSS_DEFAULT_GREETING', 'TEST_DELAY')
              AND status IN ('running', 'leased')
              AND execution_state = 'running'
            ORDER BY updated_at DESC, id DESC
            LIMIT 1
            """
        ).fetchone()
        queued_total = int(connection.execute(
            """
            SELECT COUNT(*) AS total FROM fj_automation_actions
            WHERE task_type IN ('BOSS_DEFAULT_GREETING', 'TEST_DELAY') AND status = 'queued'
            """
        ).fetchone()["total"])
    if active is not None:
        active_task_id = str(active["id"])
        return {
            "task": _serialize_action(_require_action(db, active_task_id), include_payload=False),
            "navigation": None,
            "busy": True,
            "queue": list_queue(db),
        }
    if queued_total <= 0:
        return {"task": None, "navigation": None, "queue": list_queue(db)}
    last_error: dict[str, str] | None = None
    queue_changed = False
    for _index in range(max(queued_total, 0)):
        with db.connect() as connection:
            row = connection.execute(
                """
                SELECT id, job_id, task_type FROM fj_automation_actions
                WHERE task_type IN ('BOSS_DEFAULT_GREETING', 'TEST_DELAY') AND status = 'queued'
                ORDER BY created_at ASC, id ASC
                LIMIT 1
                """
            ).fetchone()
        if row is None:
            return {"task": None, "navigation": None, "queue": list_queue(db)}
        task_id = str(row["id"])
        try:
            if row["task_type"] == "TEST_DELAY":
                navigation = _open_test_task_page(db, task_id, open_page=open_page)
            else:
                navigation = open_navigation(
                    db,
                    job_identifier=str(row["job_id"]),
                    source_context="queue",
                    action_id=task_id,
                    open_page=open_page,
                )
        except AppError as exc:
            last_error = {"code": exc.error_category, "message": exc.error_message}
            _record_task_open_failure(db, task_id, exc.error_message)
            queue_changed = True
            continue
        return {
            "task": _serialize_action(_require_action(db, task_id), include_payload=False),
            "navigation": navigation,
            "queue": list_queue(db),
            "queue_changed": queue_changed,
        }
    return {
        "task": None,
        "navigation": None,
        "error": last_error or {"code": "PAGE_OPEN_FAILED", "message": "任务页面打开失败。"},
        "queue": list_queue(db),
        "queue_changed": queue_changed,
    }


def match_task(
    db: Database,
    executor_id: str,
    task_id: str,
    execution_epoch: int,
) -> dict[str, object]:
    _executor_row(db, executor_id)
    task = _require_action(db, task_id)
    if task["task_type"] not in {"BOSS_DEFAULT_GREETING", "TEST_DELAY"}:
        raise AppError(409, "INVALID_TASK", "任务类型不支持当前执行器。")
    if int(task["execution_epoch"] or 0) != execution_epoch:
        raise AppError(409, "STALE_EXECUTION_EPOCH", "该任务执行轮次已经失效。")
    if task["status"] in {"running", "leased", "succeeded"}:
        return {"task": _serialize_action(task, include_payload=False), "queue": list_queue(db)}
    if task["status"] != "queued" or task["execution_state"] != "queued":
        raise AppError(409, "INVALID_TASK_STATE", "任务当前状态不能匹配执行。")
    now = utc_now()
    running_status = _running_action_status(db)
    already_locked = False
    with db.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        active = connection.execute(
            """
            SELECT id FROM fj_automation_actions
            WHERE task_type IN ('BOSS_DEFAULT_GREETING', 'TEST_DELAY')
              AND status IN ('running', 'leased')
              AND execution_state = 'running'
              AND id != ?
            ORDER BY updated_at DESC, id DESC
            LIMIT 1
            """,
            (task_id,),
        ).fetchone()
        if active is not None:
            raise AppError(409, "EXECUTOR_BUSY", "已有任务正在执行，等待当前任务完成后再匹配。")
        current = connection.execute(
            """
            SELECT status, execution_state, execution_epoch FROM fj_automation_actions
            WHERE id = ?
            """,
            (task_id,),
        ).fetchone()
        if current is None:
            raise AppError(404, "NOT_FOUND", "执行任务不存在。")
        if int(current["execution_epoch"] or 0) != execution_epoch:
            raise AppError(409, "STALE_EXECUTION_EPOCH", "该任务执行轮次已经失效。")
        if current["status"] in {"running", "leased", "succeeded"}:
            already_locked = True
        elif current["status"] != "queued" or current["execution_state"] != "queued":
            raise AppError(409, "INVALID_TASK_STATE", "任务当前状态不能匹配执行。")
        if not already_locked:
            connection.execute(
                """
                UPDATE fj_automation_actions
                SET status = ?, execution_state = 'running', updated_at = ?
                WHERE id = ?
                """,
                (running_status, now, task_id),
            )
    if not already_locked:
        _update_runtime_state(db, executor_id, "idle")
        _audit(db, "boss_task_matched", "岗位页面已匹配执行任务。", {"task_id": task_id})
    return {"task": _serialize_action(_require_action(db, task_id), include_payload=False), "queue": list_queue(db)}


def complete_task(
    db: Database,
    executor_id: str,
    task_id: str,
    payload: dict[str, object],
) -> dict[str, object]:
    execution_epoch = int(payload["execution_epoch"])
    _executor_row(db, executor_id)
    task = _require_action(db, task_id)
    if int(task["execution_epoch"] or 0) != execution_epoch:
        raise AppError(409, "STALE_EXECUTION_EPOCH", "该任务执行轮次已经失效。")
    outcome = str(payload.get("outcome") or "unknown")
    status = "succeeded" if outcome in {"accepted", "succeeded"} else outcome
    if status not in {"succeeded", "failed", "unknown"}:
        status = "unknown"
    if task["status"] not in {"queued", "running", "leased"}:
        if task["status"] == status:
            return {
                "task": _serialize_action(task, include_payload=False),
                "queue": list_queue(db),
            }
        raise AppError(409, "INVALID_TASK_ASSIGNMENT", "任务当前状态不能回写执行结果。")
    now = utc_now()
    message = str(payload.get("message") or "")
    result = {
        "outcome": outcome,
        "contacted": payload.get("contacted"),
        "statusCode": str(payload.get("status_code") or ""),
        "message": message,
        "evidence": payload.get("evidence") if isinstance(payload.get("evidence"), dict) else {},
    }
    with db.connect() as connection:
        connection.execute(
            """
            UPDATE fj_automation_actions
            SET status = ?, execution_state = ?,
                last_status_code = ?, last_error = ?, result_json = ?, updated_at = ?, completed_at = ?
            WHERE id = ?
            """,
            (
                status,
                status,
                str(payload.get("status_code") or "") or None,
                None if status == "succeeded" else (message or None),
                json.dumps(result, ensure_ascii=False),
                now,
                now,
                task_id,
            ),
        )
    _audit(
        db,
        "boss_task_completed",
        "默认招呼任务已回写执行结果。",
        {"task_id": task_id, "status": status},
        level="info" if status == "succeeded" else "warning",
    )
    return {
        "task": _serialize_action(_require_action(db, task_id), include_payload=False),
        "queue": list_queue(db),
    }


def list_test_jobs(db: Database) -> dict[str, object]:
    _ensure_test_jobs(db)
    with db.connect() as connection:
        rows = connection.execute(
            """
            SELECT id, encrypt_job_id, title, company_name, job_link,
                   first_collected_at AS created_at, last_collected_at AS updated_at
            FROM fj_boss_jobs WHERE is_test = 1 ORDER BY first_collected_at ASC, id ASC
            """
        ).fetchall()
    return {"jobs": [dict(row) for row in rows]}


def update_test_job(db: Database, job_id: str, *, encrypt_job_id: str, job_link: str) -> dict[str, object]:
    _ensure_test_jobs(db)
    _validate_test_job_link(job_link)
    with db.connect() as connection:
        row = connection.execute(
            "SELECT id FROM fj_boss_jobs WHERE id = ? AND is_test = 1", (job_id,)
        ).fetchone()
        if row is None:
            raise AppError(404, "TEST_JOB_NOT_FOUND", "测试岗位不存在。")
        connection.execute(
            "UPDATE fj_boss_jobs SET encrypt_job_id = ?, job_link = ?, last_collected_at = ? WHERE id = ?",
            (encrypt_job_id.strip(), job_link.strip(), utc_now(), job_id),
        )
        updated = connection.execute(
            """
            SELECT id, encrypt_job_id, title, company_name, job_link,
                   first_collected_at AS created_at, last_collected_at AS updated_at
            FROM fj_boss_jobs WHERE id = ?
            """,
            (job_id,),
        ).fetchone()
    return dict(updated)


def create_test_task(
    db: Database,
    *,
    job_id: str,
    close_page_after_completion: bool,
    delay_seconds: int = 3,
) -> dict[str, object]:
    _ensure_test_jobs(db)
    delay_seconds = min(600, max(1, int(delay_seconds)))
    now = utc_now()
    with db.connect() as connection:
        job = connection.execute(
            "SELECT id, title, company_name, job_link FROM fj_boss_jobs WHERE id = ? AND is_test = 1",
            (job_id,),
        ).fetchone()
        if job is None:
            raise AppError(404, "TEST_JOB_NOT_FOUND", "请选择一个测试岗位。")
        evaluation = connection.execute(
            "SELECT id FROM fj_job_evaluations WHERE job_id = ? ORDER BY created_at ASC LIMIT 1", (job_id,)
        ).fetchone()
        review = connection.execute(
            "SELECT id FROM fj_review_items WHERE job_id = ? ORDER BY created_at ASC LIMIT 1", (job_id,)
        ).fetchone()
        if evaluation is None or review is None:
            raise AppError(409, "TEST_JOB_INCOMPLETE", "测试岗位关联数据不完整。")
        task_id = new_id()
        payload = {
            "task_type": "TEST_DELAY",
            "delay_seconds": delay_seconds,
            "close_page_after_completion": close_page_after_completion,
            "job_link": job["job_link"],
        }
        connection.execute(
            """
            INSERT INTO fj_automation_actions (
              id, job_id, evaluation_id, review_item_id, action_type, task_type,
              status, idempotency_key, payload_json, execution_state, created_at, updated_at
            ) VALUES (?, ?, ?, ?, 'start_conversation', 'TEST_DELAY', 'queued', ?, ?, 'queued', ?, ?)
            """,
            (
                task_id,
                job_id,
                evaluation["id"],
                review["id"],
                f"test-delay:{task_id}",
                json.dumps(payload, ensure_ascii=False),
                now,
                now,
            ),
        )
    return _serialize_action(_require_action(db, task_id), include_payload=False)


def _ensure_test_jobs(db: Database) -> None:
    now = utc_now()
    with db.connect() as connection:
        capture_id = "system-test-capture-batch"
        connection.execute(
            """
            INSERT OR IGNORE INTO fj_boss_capture_batches (
              id, keyword, city, pages, auto_details, status, jobs_collected, details_completed,
              details_failed, created_at, updated_at, finished_at
            ) VALUES (?, '系统测试', '系统测试', 1, 0, 'completed', ?, 0, 0, ?, ?, ?)
            """,
            (capture_id, TEST_JOB_COUNT, now, now, now),
        )
        for number in range(1, TEST_JOB_COUNT + 1):
            job_id = f"system-test-job-{number}"
            evaluation_id = f"system-test-evaluation-{number}"
            review_id = f"system-test-review-{number}"
            connection.execute(
                """
                INSERT OR IGNORE INTO fj_boss_jobs (
                  id, dedupe_key, source_job_id, encrypt_job_id, title, company_name, job_link,
                  is_test, payload_json, detail_status, first_collected_at, last_collected_at,
                  collect_count, latest_batch_id
                ) VALUES (?, ?, ?, ?, ?, 'FineJob 系统测试', ?, 1, '{}', 'completed', ?, ?, 1, ?)
                """,
                (
                    job_id,
                    job_id,
                    f"test-job-{number}",
                    f"test-encrypt-job-{number}",
                    f"测试岗位 {number}",
                    TEST_JOB_LINK_DEFAULT,
                    now,
                    now,
                    capture_id,
                ),
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO fj_job_evaluations (
                  id, job_id, source, decision, confidence, evaluation_json, created_at
                ) VALUES (?, ?, 'rules', 'recommend', 1, '{}', ?)
                """,
                (evaluation_id, job_id, now),
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO fj_review_items (
                  id, job_id, evaluation_id, action_type, status, ai_decision, created_at, updated_at, resolved_at
                ) VALUES (?, ?, ?, 'start_conversation', 'approved', 'recommend', ?, ?, ?)
                """,
                (review_id, job_id, evaluation_id, now, now, now),
            )


def _validate_test_job_link(job_link: str) -> None:
    parsed = urlparse(job_link.strip())
    if parsed.scheme != "https" or not parsed.netloc:
        raise AppError(422, "INVALID_TEST_JOB_LINK", "测试岗位链接必须是 HTTPS 页面地址。")


def _open_test_task_page(
    db: Database,
    task_id: str,
    *,
    open_page: Callable[[str], str] | None = None,
) -> dict[str, object]:
    task = _require_action(db, task_id)
    job = _resolve_job(db, str(task["job_id"]), "queue")
    target_url = str(job.get("job_link") or "").strip()
    _validate_test_job_link(target_url)
    navigation_id = new_id()
    now = utc_now()
    with db.connect() as connection:
        connection.execute(
            """
            INSERT INTO fj_boss_navigation_tasks (
              id, action_id, job_id, source_context, target_url, target_encrypt_job_id,
              status, created_at, updated_at
            ) VALUES (?, ?, ?, 'queue', ?, ?, 'queued', ?, ?)
            """,
            (navigation_id, task_id, job["id"], target_url, str(job.get("encrypt_job_id") or ""), now, now),
        )
    try:
        target_id = (open_page or boss_scraper_service.open_test_page)(target_url)
    except (ValueError, RuntimeError, TimeoutError, OSError) as exc:
        with db.connect() as connection:
            connection.execute(
                "UPDATE fj_boss_navigation_tasks SET status = 'failed', error_code = 'PAGE_OPEN_FAILED', error_message = ?, updated_at = ? WHERE id = ?",
                (str(exc), utc_now(), navigation_id),
            )
        raise AppError(409, "PAGE_OPEN_FAILED", str(exc)) from exc
    opened_at = utc_now()
    with db.connect() as connection:
        connection.execute(
            "UPDATE fj_boss_navigation_tasks SET status = 'opened', browser_target_id = ?, opened_at = ?, updated_at = ? WHERE id = ?",
            (target_id, opened_at, opened_at, navigation_id),
        )
    return get_navigation(db, navigation_id)


def _should_close_task(task) -> bool:
    if task["task_type"] != "TEST_DELAY":
        return True
    try:
        payload = json.loads(task["payload_json"] or "{}")
    except json.JSONDecodeError:
        return False
    return bool(payload.get("close_page_after_completion"))


async def _send_executor_message(executor_id: str, message: dict[str, object]) -> bool:
    websocket = _executor_channels.get(executor_id)
    if websocket is None:
        return False
    try:
        await websocket.send_json(message)
    except Exception:
        return False
    return True


async def _report_executor_channel_error(
    db: Database,
    executor_id: str,
    message: dict[str, object],
    *,
    code: str,
    error_message: str,
) -> None:
    message_type = str(message.get("type") or "")
    task_id = str(message.get("task_id") or "")
    execution_epoch = message.get("execution_epoch")
    # 单条消息失败只回传错误并保留控制通道，避免状态类消息中断后续结果回写。
    try:
        _audit(
            db,
            "boss_executor_message_failed",
            "插件运行消息处理失败。",
            {
                "executor_id": executor_id,
                "message_type": message_type,
                "task_id": task_id,
                "execution_epoch": execution_epoch,
                "code": code,
                "message": error_message,
            },
            level="error",
        )
    except Exception:
        pass
    await _send_executor_message(executor_id, {
        "type": "task_sync_failed",
        "message_type": message_type,
        "task_id": task_id,
        "execution_epoch": execution_epoch,
        "code": code,
        "message": error_message,
    })
    await _broadcast_executor_state(db)


async def _send_queue(db: Database, executor_id: str) -> None:
    queue = list_queue(db)
    executor = _serialize_executor(_executor_row(db, executor_id))
    await _send_executor_message(executor_id, {
        "type": "task_queue",
        "tasks": queue["actions"],
        "executor": executor,
    })


async def _send_queue_to_connected_executors(db: Database) -> None:
    for executor_id in list(_executor_channels):
        await _send_queue(db, executor_id)


async def notify_queue_changed(db: Database) -> None:
    # 队列变更统一从这里推送，保证已连接插件无需等下一次心跳或页面刷新。
    await _send_queue_to_connected_executors(db)
    await _broadcast_executor_state(db)


async def _send_desktop_state(db: Database, websocket: Any) -> bool:
    try:
        await websocket.send_json({"type": "executor_state", "runtime": executor_status(db)})
    except Exception:
        return False
    return True


async def _broadcast_executor_state(db: Database) -> None:
    for websocket in list(_desktop_channels):
        if not await _send_desktop_state(db, websocket):
            _desktop_channels.discard(websocket)


async def broadcast_executor_state(db: Database) -> None:
    await _broadcast_executor_state(db)


async def _open_and_notify_task_page(db: Database, executor_id: str) -> None:
    opened = open_task_page(db, executor_id)
    await _broadcast_executor_state(db)
    if opened.get("queue_changed") is True:
        await _send_queue(db, executor_id)
    if opened.get("busy") is True:
        task = opened.get("task")
        await _send_executor_message(executor_id, {
            "type": "page_opened",
            "task_id": task.get("id") if isinstance(task, dict) else "",
            "success": False,
            "busy": True,
            "message": "已有任务正在执行，等待当前任务完成。",
        })
        return
    task = opened.get("task")
    error = opened.get("error")
    if not isinstance(task, dict):
        await _send_executor_message(executor_id, {
            "type": "page_opened",
            "task_id": "",
            "success": False,
            "queue_empty": not isinstance(error, dict),
            "message": error.get("message") if isinstance(error, dict) else "当前没有待执行任务。",
        })
        return
    navigation = opened.get("navigation")
    if isinstance(navigation, dict):
        await _send_executor_message(executor_id, {
            "type": "page_opened",
            "task_id": task["id"],
            "success": True,
            "page": navigation,
        })
        return
    await _send_executor_message(executor_id, {
        "type": "page_opened",
        "task_id": task["id"],
        "success": False,
        "message": error.get("message") if isinstance(error, dict) else "任务页面打开失败。",
    })


async def _handle_task_completion_message(
    db: Database,
    executor_id: str,
    message: dict[str, object],
    *,
    succeeded: bool,
) -> None:
    task_id = str(message.get("task_id") or "")
    if not task_id:
        return
    task = _require_action(db, task_id)
    execution_epoch = int(
        message.get("execution_epoch")
        if message.get("execution_epoch") is not None
        else task["execution_epoch"] or 0
    )
    outcome = str(message.get("outcome") or ("succeeded" if succeeded else "failed"))
    if succeeded and outcome not in {"accepted", "succeeded"}:
        outcome = "succeeded"
    if not succeeded and outcome not in {"failed", "unknown"}:
        outcome = "failed"
    payload = {
        "execution_epoch": execution_epoch,
        "outcome": outcome,
        "contacted": message.get("contacted"),
        "status_code": str(message.get("status_code") or ""),
        "message": str(message.get("execution_result") or message.get("error_message") or message.get("message") or ""),
        "evidence": {
            "platform_result": message.get("platform_result"),
            "completed_at": message.get("completed_at") or message.get("failed_at"),
        },
    }
    result = complete_task(db, executor_id, task_id, payload)
    if succeeded and _should_close_task(task):
        # 页面关闭只发起浏览器动作，后续不读取或记录页面是否已关闭。
        _close_task_page(db, task_id)
    await _send_executor_message(executor_id, {
        "type": "task_result_synced",
        "task_id": task_id,
        "execution_epoch": payload["execution_epoch"],
        "succeeded": succeeded,
        "task": result["task"],
        "queue": result["queue"],
    })
    await notify_queue_changed(db)


def _record_channel_heartbeat(db: Database, executor_id: str) -> None:
    now = utc_now()
    with db.connect() as connection:
        connection.execute(
            "UPDATE fj_boss_executor_instances SET browser_connected = 1, last_heartbeat_at = ?, updated_at = ? WHERE id = ?",
            (now, now, executor_id),
        )


def _running_action_status(db: Database) -> str:
    with db.connect() as connection:
        row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'fj_automation_actions'"
        ).fetchone()
    table_sql = str(row["sql"] or "") if row is not None else ""
    # 旧库和新库对执行中状态的 CHECK 枚举不完全一致，写入时按当前库支持值选择。
    return "leased" if "'leased'" in table_sql else "running"


def _close_task_page(db: Database, task_id: str) -> None:
    with db.connect() as connection:
        navigation = connection.execute(
            """
            SELECT browser_target_id FROM fj_boss_navigation_tasks
            WHERE action_id = ? AND status = 'opened' AND browser_target_id IS NOT NULL
            ORDER BY opened_at DESC, id DESC LIMIT 1
            """,
            (task_id,),
        ).fetchone()
    if navigation is None:
        return
    try:
        boss_scraper_service.close_job_page(str(navigation["browser_target_id"]))
    except (ValueError, RuntimeError, TimeoutError, OSError):
        return


def _record_task_open_failure(db: Database, task_id: str, message: str) -> None:
    _record_retriable_task_failure(
        db,
        task_id,
        message,
        counter_key="page_open_failures",
        status_code="PAGE_OPEN_FAILED",
        final_status_code="PAGE_OPEN_FAILED_THREE_TIMES",
        move_to_tail=True,
    )


def _record_task_match_failure(db: Database, task_id: str, message: str) -> None:
    _record_retriable_task_failure(
        db,
        task_id,
        message,
        counter_key="page_match_failures",
        status_code="PAGE_MATCH_FAILED",
        final_status_code="PAGE_MATCH_FAILED_THREE_TIMES",
        move_to_tail=False,
    )


def _record_retriable_task_failure(
    db: Database,
    task_id: str,
    message: str,
    *,
    counter_key: str,
    status_code: str,
    final_status_code: str,
    move_to_tail: bool,
) -> None:
    task = _require_action(db, task_id)
    if task["status"] not in {"queued", "running", "leased"}:
        return
    try:
        payload = json.loads(task["payload_json"] or "{}")
    except json.JSONDecodeError:
        payload = {}
    failure_count = int(payload.get(counter_key) or 0) + 1
    payload[counter_key] = failure_count
    now = utc_now()
    if failure_count >= 3:
        result = {"outcome": "failed", "statusCode": final_status_code, "message": message, "evidence": payload}
        with db.connect() as connection:
            connection.execute(
                """
                UPDATE fj_automation_actions
                SET status = 'failed', execution_state = 'failed', last_status_code = ?,
                    last_error = ?, payload_json = ?, result_json = ?, updated_at = ?, completed_at = ?
                WHERE id = ?
                """,
                (
                    final_status_code,
                    message,
                    json.dumps(payload, ensure_ascii=False),
                    json.dumps(result, ensure_ascii=False),
                    now,
                    now,
                    task_id,
                ),
            )
        _audit(db, "boss_task_retry_failed", "任务页面连续失败，已标记任务失败。", {"task_id": task_id, "status_code": final_status_code}, level="warning")
        return
    created_at_sql = ", created_at = ?" if move_to_tail else ""
    params: tuple[object, ...]
    if move_to_tail:
        params = (status_code, message, json.dumps(payload, ensure_ascii=False), now, now, task_id)
    else:
        params = (status_code, message, json.dumps(payload, ensure_ascii=False), now, task_id)
    with db.connect() as connection:
        connection.execute(
            f"""
            UPDATE fj_automation_actions
            SET status = 'queued', execution_state = 'queued', execution_epoch = execution_epoch + 1,
                last_status_code = ?, last_error = ?, payload_json = ?, updated_at = ?{created_at_sql}
            WHERE id = ?
            """,
            params,
        )


def _finish_active_tasks_as_unknown(db: Database, message: str, status_code: str) -> int:
    now = utc_now()
    result = {
        "outcome": "unknown",
        "statusCode": status_code,
        "message": message,
        "evidence": {},
    }
    with db.connect() as connection:
        rows = connection.execute(
            """
            SELECT id FROM fj_automation_actions
            WHERE task_type IN ('BOSS_DEFAULT_GREETING', 'TEST_DELAY')
              AND status IN ('running', 'leased')
              AND execution_state = 'running'
            """
        ).fetchall()
        if not rows:
            return 0
        connection.execute(
            """
            UPDATE fj_automation_actions
            SET status = 'unknown', execution_state = 'unknown',
                last_status_code = ?, last_error = ?, result_json = ?,
                updated_at = ?, completed_at = ?
            WHERE task_type IN ('BOSS_DEFAULT_GREETING', 'TEST_DELAY')
              AND status IN ('running', 'leased')
              AND execution_state = 'running'
            """,
            (status_code, message, json.dumps(result, ensure_ascii=False), now, now),
        )
    _audit(
        db,
        "boss_active_tasks_finished_unknown",
        "遗留执行中任务已结束为结果未知。",
        {"count": len(rows), "status_code": status_code},
        level="warning",
    )
    return len(rows)


def _record_execution_error(db: Database, task_id: str, message: str) -> None:
    task = _require_action(db, task_id)
    if task["status"] not in {"queued", "running", "leased"}:
        return
    now = utc_now()
    with db.connect() as connection:
        connection.execute(
            """
            UPDATE fj_automation_actions
            SET status = 'failed', execution_state = 'failed', last_error = ?, updated_at = ?, completed_at = ?
            WHERE id = ?
            """,
            (message, now, now, task_id),
        )


def _current_task(db: Database) -> dict[str, object] | None:
    with db.connect() as connection:
        row = connection.execute(
            """
            SELECT a.*, j.title AS job_title, j.company_name, j.encrypt_job_id, j.job_link
            FROM fj_automation_actions a JOIN fj_boss_jobs j ON j.id = a.job_id
            WHERE a.task_type IN ('BOSS_DEFAULT_GREETING', 'TEST_DELAY')
              AND a.status IN ('running', 'leased')
              AND a.execution_state = 'running'
            ORDER BY a.updated_at DESC, a.id DESC LIMIT 1
            """
        ).fetchone()
    return _serialize_action(row, include_payload=False) if row is not None else None


def return_to_review(db: Database, action_id: str, *, reason: str, executor_id: str | None = None) -> dict[str, object]:
    task = _require_action(db, action_id)
    if task["status"] in {"running", "leased", "succeeded"}:
        raise AppError(409, "RETURN_FORBIDDEN", "任务已经开始执行或已完成，不能退回待确认。")
    now = utc_now()
    with db.connect() as connection:
        connection.execute(
            "UPDATE fj_automation_actions SET status = 'cancelled', execution_state = 'cancelled', last_status_code = 'RETURNED_TO_REVIEW', last_error = ?, updated_at = ?, completed_at = ? WHERE id = ?",
            (reason.strip(), now, now, action_id),
        )
        connection.execute(
            "UPDATE fj_review_items SET status = 'pending', resolved_at = NULL, resolution_note = ?, updated_at = ? WHERE id = ?",
            (reason.strip(), now, task["review_item_id"]),
        )
    _audit(db, "boss_return_to_review", "未执行岗位已退回待确认。", {"task_id": action_id})
    return _serialize_action(_require_action(db, action_id))


def get_navigation(db: Database, task_id: str) -> dict[str, object]:
    with db.connect() as connection:
        row = connection.execute("SELECT * FROM fj_boss_navigation_tasks WHERE id = ?", (task_id,)).fetchone()
    if row is None:
        raise AppError(404, "NOT_FOUND", "岗位页面打开任务不存在。")
    return dict(row)


def _resolve_job(db: Database, identifier: str, source_context: str) -> dict[str, object]:
    identifier = identifier.strip()
    with db.connect() as connection:
        if source_context == "review":
            row = connection.execute(
                """
                SELECT j.* FROM fj_review_items r JOIN fj_boss_jobs j ON j.id = r.job_id
                WHERE r.id = ? OR j.id = ? LIMIT 1
                """,
                (identifier, identifier),
            ).fetchone()
        else:
            row = connection.execute(
                "SELECT * FROM fj_boss_jobs WHERE id = ? OR source_job_id = ? OR encrypt_job_id = ? LIMIT 1",
                (identifier, identifier, identifier),
            ).fetchone()
    if row is None:
        raise AppError(404, "JOB_NOT_FOUND", "找不到要打开的岗位记录。")
    return dict(row)


def _target_job_url(job: dict[str, object]) -> str:
    stored = str(job.get("job_link") or "").strip()
    stored_job_id = _job_id_from_url(stored)
    expected = str(job.get("encrypt_job_id") or stored_job_id or job.get("source_job_id") or "").strip()
    if stored and _valid_job_url(stored, expected):
        return stored
    if expected:
        return f"https://www.zhipin.com/job_detail/{expected}.html"
    raise AppError(409, "JOB_ID_MISSING", "岗位缺少可验证的BOSS岗位标识，不能打开详情页。")


def _job_id_from_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in {"www.zhipin.com", "zhipin.com"}:
        return ""
    matched = re.search(r"/job_detail/([^/]+?)\.html(?:/|$)", parsed.path)
    return matched.group(1) if matched else ""


def _valid_job_url(url: str, expected: str) -> bool:
    parsed = urlparse(url)
    return (
        parsed.scheme == "https"
        and parsed.hostname in {"www.zhipin.com", "zhipin.com"}
        and "/job_detail/" in parsed.path
        and (not expected or expected in parsed.path)
    )


def _executor_row(db: Database, executor_id: str):
    with db.connect() as connection:
        row = connection.execute("SELECT * FROM fj_boss_executor_instances WHERE id = ?", (executor_id,)).fetchone()
    if row is None:
        raise AppError(404, "NOT_FOUND", "执行器不存在。")
    return row


def _action_row(db: Database, action_id: str):
    with db.connect() as connection:
        return connection.execute(
            "SELECT a.*, j.title AS job_title, j.company_name, j.encrypt_job_id, j.job_link FROM fj_automation_actions a JOIN fj_boss_jobs j ON j.id = a.job_id WHERE a.id = ?",
            (action_id,),
        ).fetchone()


def _require_action(db: Database, action_id: str):
    row = _action_row(db, action_id)
    if row is None:
        raise AppError(404, "NOT_FOUND", "执行任务不存在。")
    return row


def _serialize_action(row, *, include_payload: bool = True) -> dict[str, object]:
    payload = json.loads(row["payload_json"] or "{}")
    delay_seconds = int(payload.get("delay_seconds") or (3 if row["task_type"] == "TEST_DELAY" else 0))
    data = {
        "id": row["id"],
        "job_id": row["job_id"],
        "evaluation_id": row["evaluation_id"],
        "review_item_id": row["review_item_id"],
        "action_type": row["action_type"],
        "task_type": row["task_type"],
        "status": row["status"],
        "execution_state": row["execution_state"],
        "execution_epoch": row["execution_epoch"],
        "last_error": row["last_error"],
        "last_status_code": row["last_status_code"],
        "job_title": row["job_title"],
        "company_name": row["company_name"],
        "encrypt_job_id": row["encrypt_job_id"],
        "job_link": row["job_link"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "completed_at": row["completed_at"],
        "close_page_after_completion": bool(payload.get("close_page_after_completion", row["task_type"] != "TEST_DELAY")),
        "delay_seconds": delay_seconds,
    }
    if include_payload:
        payload.pop("message", None)
        data["payload"] = payload
    return data


def _serialize_executor(row) -> dict[str, object]:
    keys = set(row.keys())
    return {
        "id": row["id"],
        "label": row["label"],
        "protocol_version": row["protocol_version"],
        "plugin_version": row["plugin_version"],
        "capabilities": json.loads(row["capabilities_json"] or "[]"),
        "queue_state": row["queue_state"],
        "risk_state": row["risk_state"],
        "browser_connected": bool(row["browser_connected"]),
        "last_heartbeat_at": row["last_heartbeat_at"],
        "task_cooldown_max_seconds": int(row["task_cooldown_max_seconds"]) if "task_cooldown_max_seconds" in keys else 4,
        "page_load_wait_max_seconds": int(row["page_load_wait_max_seconds"]) if "page_load_wait_max_seconds" in keys else 3,
        "runtime_phase": row["runtime_phase"] if "runtime_phase" in keys else "idle",
        "runtime_detail": row["runtime_detail"] if "runtime_detail" in keys else "",
        "runtime_until_at": row["runtime_until_at"] if "runtime_until_at" in keys else None,
        "updated_at": row["updated_at"],
    }


def _audit(
    db: Database,
    action_type: str,
    message: str,
    detail: dict[str, object],
    *,
    level: str = "info",
) -> None:
    with db.connect() as connection:
        connection.execute(
            "INSERT INTO fj_action_logs (id, run_id, level, action_type, message, detail_json, created_at) VALUES (?, NULL, ?, ?, ?, ?, ?)",
            (new_id(), level, action_type, message, json.dumps(detail, ensure_ascii=False), utc_now()),
        )
