from __future__ import annotations

import hashlib
import json
import secrets
import re
from datetime import datetime, timedelta, timezone
from typing import Callable
from urllib.parse import urlparse

from backend.app.db import Database
from backend.app.errors import AppError
from backend.app.services.fine_job.boss_scraper.service import boss_scraper_service
from backend.app.services.fine_job.delivery_strategies import get_delivery_strategy
from backend.app.utils import new_id, utc_now


PROTOCOL_VERSION = "1.1"
HEARTBEAT_TTL_SECONDS = 12
PAGE_STATUS_TIMEOUT_SECONDS = 30
DISPATCH_RESULT_TIMEOUT_SECONDS = 20
PAIRING_CODE_TTL_SECONDS = 300
VERIFICATION_DELAY_MIN_SECONDS = 10
VERIFICATION_DELAY_MAX_SECONDS = 30
ACTIVE_VERIFICATION_STATES = {"waiting_refresh", "refreshing", "waiting_snapshot"}
TERMINAL_EXECUTION_STATES = {
    "succeeded", "cancelled", "blocked", "failed_before_dispatch", "failed_after_dispatch",
    "unknown_after_dispatch",
}


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _parse_time(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _iso_after(seconds: int) -> str:
    return (
        datetime.now(timezone.utc) + timedelta(seconds=seconds)
    ).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def create_pairing_code(db: Database) -> dict[str, str]:
    code = f"{secrets.randbelow(1_000_000):06d}"
    now = utc_now()
    expires_at = _iso_after(PAIRING_CODE_TTL_SECONDS)
    with db.connect() as connection:
        connection.execute("DELETE FROM fj_boss_pairing_codes WHERE used_at IS NOT NULL OR expires_at <= ?", (now,))
        connection.execute(
            "INSERT INTO fj_boss_pairing_codes (id, code_hash, expires_at, created_at) VALUES (?, ?, ?, ?)",
            (new_id(), _hash(code), expires_at, now),
        )
    return {"code": code, "expires_at": expires_at}


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
        raise AppError(status_code=409, error_category="PROTOCOL_MISMATCH", error_message="插件通信协议版本不兼容。")
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
            raise AppError(status_code=401, error_category="INVALID_PAIRING_CODE", error_message="配对码无效或已经过期。")
        connection.execute("UPDATE fj_boss_pairing_codes SET used_at = ? WHERE id = ?", (now, row["id"]))
        # 当前版本明确只允许一个插件执行器，新的配对会暂停旧实例。
        connection.execute(
            "UPDATE fj_boss_executor_instances SET permission_state = 'not_authorized', queue_state = 'paused', updated_at = ?",
            (now,),
        )
        connection.execute(
            """
            INSERT INTO fj_boss_executor_instances (
              id, label, token_hash, protocol_version, plugin_version, capabilities_json,
              permission_state, queue_state, risk_state, browser_connected, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'not_authorized', 'paused', 'none', 0, ?, ?)
            """,
            (executor_id, label.strip(), _hash(token), protocol_version, plugin_version, json.dumps(capabilities), now, now),
        )
    return {"executor_id": executor_id, "token": token, "protocol_version": PROTOCOL_VERSION}


def authenticate_executor(db: Database, token: str) -> dict[str, object]:
    if not token:
        raise AppError(status_code=401, error_category="EXECUTOR_UNAUTHORIZED", error_message="缺少执行器令牌。")
    with db.connect() as connection:
        row = connection.execute(
            "SELECT * FROM fj_boss_executor_instances WHERE token_hash = ?", (_hash(token),)
        ).fetchone()
    if row is None:
        raise AppError(status_code=401, error_category="EXECUTOR_UNAUTHORIZED", error_message="执行器令牌无效。")
    return dict(row)


def heartbeat(db: Database, executor_id: str, payload: dict[str, object]) -> dict[str, object]:
    now = utc_now()
    if str(payload.get("protocol_version") or "") != PROTOCOL_VERSION:
        raise AppError(status_code=409, error_category="PROTOCOL_MISMATCH", error_message="插件通信协议版本不兼容。")
    risk_state = str(payload.get("risk_state") or "none")
    if risk_state not in {"none", "login", "captcha", "rate_limit", "unknown"}:
        risk_state = "unknown"
    with db.connect() as connection:
        connection.execute(
            """
            UPDATE fj_boss_executor_instances
            SET protocol_version = ?, plugin_version = ?, capabilities_json = ?,
                browser_connected = ?,
                risk_state = CASE
                  WHEN risk_state = 'consecutive_unknown_after_dispatch' THEN risk_state
                  ELSE ?
                END,
                last_heartbeat_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                PROTOCOL_VERSION, str(payload.get("plugin_version") or ""),
                json.dumps(payload.get("capabilities") or [], ensure_ascii=False),
                int(bool(payload.get("browser_connected"))), risk_state, now, now, executor_id,
            ),
        )
        if risk_state != "none":
            connection.execute(
                "UPDATE fj_boss_executor_instances SET permission_state = 'risk_paused', queue_state = 'risk_paused' WHERE id = ?",
                (executor_id,),
            )
    sweep_page_timeout(db, executor_id)
    return executor_snapshot(db, executor_id)


def set_control(db: Database, executor_id: str, command: str) -> dict[str, object]:
    now = utc_now()
    mapping = {
        "allow": ("allowed", "running"),
        "resume": ("allowed", "running"),
        "pause": ("paused", "paused"),
        "emergency_stop": ("paused", "emergency_stopped"),
    }
    permission, queue_state = mapping[command]
    if command in {"allow", "resume"} and _consecutive_unknown_count(db) >= 3:
        raise AppError(
            status_code=409,
            error_category="CONSECUTIVE_UNKNOWN_LIMIT",
            error_message="连续3个不同岗位出现未知错误，请先人工核验执行环境和未知岗位。",
        )
    with db.connect() as connection:
        current = connection.execute(
            "SELECT risk_state FROM fj_boss_executor_instances WHERE id = ?", (executor_id,)
        ).fetchone()
        if current is None:
            raise AppError(status_code=404, error_category="NOT_FOUND", error_message="执行器不存在。")
        current_risk = str(current["risk_state"])
        if command in {"allow", "resume"} and current_risk not in {"none", "unknown_after_dispatch"}:
            raise AppError(status_code=409, error_category="RISK_NOT_CLEARED", error_message="插件仍报告登录、验证码或频控风险，不能恢复。")
        connection.execute(
            "UPDATE fj_boss_executor_instances SET permission_state = ?, queue_state = ?, risk_state = CASE WHEN risk_state = 'unknown_after_dispatch' THEN 'none' ELSE risk_state END, updated_at = ? WHERE id = ?",
            (permission, queue_state, now, executor_id),
        )
    _audit(db, "executor_control", f"执行器控制状态更新为 {command}", {"executor_id": executor_id})
    return executor_snapshot(db, executor_id)


def executor_snapshot(db: Database, executor_id: str | None = None) -> dict[str, object]:
    with db.connect() as connection:
        if executor_id:
            executor = connection.execute("SELECT * FROM fj_boss_executor_instances WHERE id = ?", (executor_id,)).fetchone()
        else:
            executor = connection.execute(
                "SELECT * FROM fj_boss_executor_instances ORDER BY updated_at DESC LIMIT 1"
            ).fetchone()
    queue = list_queue(db)
    data = _serialize_executor(executor) if executor else None
    return {"executor": data, "queue": queue, "protocol_version": PROTOCOL_VERSION}


def list_queue(db: Database) -> dict[str, object]:
    with db.connect() as connection:
        rows = connection.execute(
            """
            SELECT a.*, j.title AS job_title, j.company_name, j.encrypt_job_id, j.job_link
            FROM fj_automation_actions a JOIN fj_boss_jobs j ON j.id = a.job_id
            WHERE a.action_type = 'BOSS_DEFAULT_GREETING'
              AND a.execution_state NOT IN ('succeeded', 'cancelled', 'failed_before_dispatch', 'failed_after_dispatch')
            ORDER BY CASE
              WHEN a.execution_state IN (
                'opening_page','waiting_page_ready','page_verified','ready_to_dispatch','dispatch_started','request_accepted'
              ) THEN 0
              WHEN a.execution_state = 'unknown_after_dispatch' THEN 2
              ELSE 1
            END,
              a.queue_position ASC, a.created_at ASC
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
    task_id = new_id()
    now = utc_now()
    with db.connect() as connection:
        connection.execute(
            """
            INSERT INTO fj_boss_navigation_tasks (
              id, action_id, job_id, source_context, target_url, target_encrypt_job_id,
              status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'queued', ?, ?)
            """,
            (task_id, action_id, job["id"], source_context, target_url, job["encrypt_job_id"], now, now),
        )
    try:
        target_id = (open_page or boss_scraper_service.open_job_page)(target_url)
    except (ValueError, RuntimeError, TimeoutError, OSError) as exc:
        with db.connect() as connection:
            connection.execute(
                "UPDATE fj_boss_navigation_tasks SET status = 'failed', error_code = 'PAGE_OPEN_FAILED', error_message = ?, updated_at = ? WHERE id = ?",
                (str(exc), utc_now(), task_id),
            )
        raise AppError(status_code=409, error_category="PAGE_OPEN_FAILED", error_message=str(exc)) from exc
    opened_at = utc_now()
    with db.connect() as connection:
        connection.execute(
            "UPDATE fj_boss_navigation_tasks SET status = 'opened', browser_target_id = ?, opened_at = ?, updated_at = ? WHERE id = ?",
            (target_id, opened_at, opened_at, task_id),
        )
    return get_navigation(db, task_id)


def get_navigation(db: Database, task_id: str) -> dict[str, object]:
    with db.connect() as connection:
        row = connection.execute("SELECT * FROM fj_boss_navigation_tasks WHERE id = ?", (task_id,)).fetchone()
    if row is None:
        raise AppError(status_code=404, error_category="NOT_FOUND", error_message="岗位页面打开任务不存在。")
    return dict(row)


def claim_next_action(
    db: Database,
    executor_id: str,
    *,
    open_page: Callable[[str], str] | None = None,
) -> dict[str, object] | None:
    sweep_page_timeout(db, executor_id)
    executor = _executor_row(db, executor_id)
    if executor["permission_state"] != "allowed" or executor["queue_state"] != "running":
        return None
    if executor["risk_state"] != "none":
        return None
    next_eligible = _parse_time(executor["next_eligible_at"])
    if next_eligible and next_eligible > datetime.now(timezone.utc):
        return None

    current_id = str(executor["current_action_id"] or "")
    if current_id:
        current = _action_row(db, current_id)
        if current and current["execution_state"] not in TERMINAL_EXECUTION_STATES:
            return _serialize_action(current)

    now = utc_now()
    with db.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            """
            SELECT a.id FROM fj_automation_actions a
            WHERE a.action_type = 'BOSS_DEFAULT_GREETING'
              AND a.status = 'queued' AND a.execution_state = 'queued'
            ORDER BY a.queue_position ASC, a.created_at ASC LIMIT 1
            """
        ).fetchone()
        if row is None:
            connection.execute(
                "UPDATE fj_boss_executor_instances SET current_action_id = NULL, current_epoch = NULL, updated_at = ? WHERE id = ?",
                (now, executor_id),
            )
            return None
        action_id = str(row["id"])
        connection.execute(
            """
            UPDATE fj_automation_actions
            SET status = 'leased', execution_state = 'opening_page', lease_owner = ?,
                lease_expires_at = NULL, execution_epoch = execution_epoch + 1,
                attempt_count = attempt_count + 1, updated_at = ?
            WHERE id = ?
            """,
            (executor_id, now, action_id),
        )
        epoch = int(connection.execute("SELECT execution_epoch FROM fj_automation_actions WHERE id = ?", (action_id,)).fetchone()[0])
        connection.execute(
            "UPDATE fj_boss_executor_instances SET current_action_id = ?, current_epoch = ?, cooldown_seconds = NULL, next_eligible_at = NULL, updated_at = ? WHERE id = ?",
            (action_id, epoch, now, executor_id),
        )

    action = _action_row(db, action_id)
    assert action is not None
    try:
        navigation = open_navigation(
            db, job_identifier=str(action["job_id"]), source_context="queue",
            action_id=action_id, open_page=open_page,
        )
    except AppError:
        with db.connect() as connection:
            connection.execute(
                "UPDATE fj_automation_actions SET status = 'blocked', execution_state = 'blocked', last_status_code = 'PAGE_OPEN_FAILED', updated_at = ? WHERE id = ?",
                (utc_now(), action_id),
            )
            connection.execute(
                "UPDATE fj_boss_executor_instances SET permission_state = 'risk_paused', queue_state = 'risk_paused', risk_state = 'browser', updated_at = ? WHERE id = ?",
                (utc_now(), executor_id),
            )
        raise

    deadline = _iso_after(PAGE_STATUS_TIMEOUT_SECONDS)
    with db.connect() as connection:
        connection.execute(
            """
            UPDATE fj_automation_actions
            SET execution_state = 'waiting_page_ready', page_open_attempts = page_open_attempts + 1,
                page_deadline_at = ?, navigation_task_id = ?, last_status_code = 'PAGE_OPENED', updated_at = ?
            WHERE id = ?
            """,
            (deadline, navigation["id"], utc_now(), action_id),
        )
    _audit(db, "boss_page_opened", "FineJob 已打开队首岗位详情页。", {"action_id": action_id, "execution_epoch": epoch})
    row = _action_row(db, action_id)
    assert row is not None
    return _serialize_action(row)


def report_page_status(
    db: Database,
    executor_id: str,
    action_id: str,
    payload: dict[str, object],
) -> dict[str, object]:
    action = _require_owned_action(db, executor_id, action_id, int(payload["execution_epoch"]))
    if action["execution_state"] == "request_accepted":
        return _report_verification_snapshot(db, executor_id, action, payload)
    if action["execution_state"] not in {"waiting_page_ready", "page_verified", "ready_to_dispatch"}:
        raise AppError(status_code=409, error_category="INVALID_STATE", error_message="当前动作不再等待页面状态。")
    state = str(payload.get("state") or "waiting")
    if state == "waiting":
        return _serialize_action(action)
    if state != "ready" or not bool(payload.get("logged_in")):
        reason = str(payload.get("reason") or "页面状态不可执行")
        _pause_for_risk(db, executor_id, action_id, "PAGE_NOT_EXECUTABLE", reason)
        return _serialize_action(_require_action(db, action_id))
    if str(payload.get("encrypt_job_id") or "") != str(action["encrypt_job_id"] or ""):
        _pause_for_risk(db, executor_id, action_id, "JOB_ID_MISMATCH", "插件识别岗位与队列目标不一致。")
        return _serialize_action(_require_action(db, action_id))

    if payload.get("contacted") is True:
        # 已沟通岗位不再发送，按安全跳过完成并进入随机冷却。
        return _finish_action(db, executor_id, action_id, "succeeded", "ALREADY_CONTACTED", {"already_contacted": True})

    with db.connect() as connection:
        connection.execute(
            "UPDATE fj_automation_actions SET execution_state = 'ready_to_dispatch', last_status_code = 'PAGE_VERIFIED', updated_at = ? WHERE id = ?",
            (utc_now(), action_id),
        )
    return _serialize_action(_require_action(db, action_id))


def mark_dispatch_started(db: Database, executor_id: str, action_id: str, execution_epoch: int) -> dict[str, object]:
    action = _require_owned_action(db, executor_id, action_id, execution_epoch)
    if action["execution_state"] != "ready_to_dispatch":
        raise AppError(status_code=409, error_category="INVALID_STATE", error_message="页面尚未通过验证，不能执行真实打招呼。")
    executor = _executor_row(db, executor_id)
    if executor["permission_state"] != "allowed" or executor["queue_state"] != "running" or executor["risk_state"] != "none":
        raise AppError(status_code=409, error_category="EXECUTION_NOT_ALLOWED", error_message="插件权限或队列状态不允许真实执行。")
    now = utc_now()
    with db.connect() as connection:
        connection.execute(
            "UPDATE fj_automation_actions SET execution_state = 'dispatch_started', dispatch_started_at = ?, last_status_code = 'DISPATCH_STARTED', updated_at = ? WHERE id = ?",
            (now, now, action_id),
        )
    _audit(db, "boss_dispatch_started", "插件已进入单次默认招呼请求。", {"action_id": action_id, "execution_epoch": execution_epoch})
    return _serialize_action(_require_action(db, action_id))


def complete_executor_action(
    db: Database,
    executor_id: str,
    action_id: str,
    payload: dict[str, object],
    *,
    verification_delay_provider: Callable[[], int] | None = None,
) -> dict[str, object]:
    epoch = int(payload["execution_epoch"])
    action = _require_action(db, action_id)
    if int(action["execution_epoch"]) != epoch:
        raise AppError(status_code=409, error_category="STALE_EXECUTION_EPOCH", error_message="该状态属于已经失效的执行轮次。")
    # accepted 回写允许幂等重试；只重试状态回写，绝不能再次调用平台请求。
    if payload.get("outcome") == "accepted" and action["request_accepted_at"]:
        return _serialize_action(action)
    action = _require_owned_action(db, executor_id, action_id, epoch)
    if action["execution_state"] != "dispatch_started":
        raise AppError(status_code=409, error_category="INVALID_STATE", error_message="动作尚未进入真实发送阶段。")
    evidence = _sanitize_execution_evidence(payload.get("evidence"))
    evidence.update({"message": str(payload.get("message") or ""), "contacted": payload.get("contacted")})
    if payload.get("outcome") == "accepted":
        return _accept_action(
            db,
            executor_id,
            action_id,
            str(payload.get("status_code") or "BOSS_REQUEST_ACCEPTED"),
            evidence,
            verification_delay_provider=verification_delay_provider,
        )
    if payload.get("outcome") == "succeeded" and payload.get("contacted") is True:
        return _finish_action(db, executor_id, action_id, "succeeded", str(payload.get("status_code") or "SUCCESS"), evidence)

    if payload.get("outcome") == "failed":
        status_code = str(payload.get("status_code") or "BOSS_REQUEST_REJECTED")
        message = str(payload.get("message") or "平台明确拒绝建立沟通请求")
        if status_code in {
            "BOSS_RATE_LIMIT", "BOSS_GREETING_LIMIT", "BOSS_TOKEN_MISSING",
            "PRE_DISPATCH_PAGE_MISMATCH",
        }:
            _pause_for_risk(db, executor_id, action_id, status_code, message)
            return _serialize_action(_require_action(db, action_id))
        return _finish_failed_action(db, executor_id, action_id, status_code, message, evidence)

    return _mark_unknown_after_dispatch(
        db,
        executor_id,
        action_id,
        str(payload.get("status_code") or "UNKNOWN_AFTER_DISPATCH"),
        str(payload.get("message") or "真实请求后结果未知"),
        evidence,
    )


def return_to_review(db: Database, action_id: str, *, reason: str, executor_id: str | None = None) -> dict[str, object]:
    action = _require_action(db, action_id)
    if action["execution_state"] in {"dispatch_started", "request_accepted", "succeeded", "failed_after_dispatch", "unknown_after_dispatch"}:
        raise AppError(status_code=409, error_category="RETURN_FORBIDDEN", error_message="真实请求已经发出或可能发出，不能退回待确认。")
    now = utc_now()
    with db.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "UPDATE fj_automation_actions SET status = 'cancelled', execution_state = 'cancelled', last_status_code = 'RETURNED_TO_REVIEW', last_error = ?, lease_owner = NULL, page_deadline_at = NULL, updated_at = ?, completed_at = ? WHERE id = ?",
            (reason.strip(), now, now, action_id),
        )
        connection.execute(
            "UPDATE fj_review_items SET status = 'pending', resolved_at = NULL, resolution_note = ?, updated_at = ? WHERE id = ?",
            (reason.strip(), now, action["review_item_id"]),
        )
        if executor_id:
            connection.execute(
                "UPDATE fj_boss_executor_instances SET current_action_id = NULL, current_epoch = NULL, updated_at = ? WHERE id = ? AND current_action_id = ?",
                (now, executor_id, action_id),
            )
    _audit(db, "boss_return_to_review", "未发送岗位已退回待确认。", {"action_id": action_id})
    return _serialize_action(_require_action(db, action_id))


def manual_verify_unknown_action(
    db: Database,
    action_id: str,
    *,
    contacted: bool,
    note: str,
) -> dict[str, object]:
    """只处理已经停止执行的未知动作，人工结论不会直接触发新的BOSS请求。"""
    action = _require_action(db, action_id)
    if action["execution_state"] != "unknown_after_dispatch":
        raise AppError(
            status_code=409,
            error_category="MANUAL_VERIFY_NOT_ALLOWED",
            error_message="只有未知错误岗位可以人工核验。",
        )
    now = utc_now()
    normalized_note = note.strip() or "用户人工核验BOSS岗位页面"
    with db.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        if contacted:
            connection.execute(
                """
                UPDATE fj_automation_actions
                SET status = 'succeeded', execution_state = 'succeeded',
                    verification_state = 'manual_confirmed', verification_method = 'manual',
                    verification_completed_at = ?, last_status_code = 'MANUAL_CONFIRMED_CONTACTED',
                    last_error = NULL, result_json = ?, updated_at = ?, completed_at = ?
                WHERE id = ?
                """,
                (
                    now,
                    json.dumps({"contacted": True, "verificationMethod": "manual", "note": normalized_note}, ensure_ascii=False),
                    now,
                    now,
                    action_id,
                ),
            )
        else:
            connection.execute(
                """
                UPDATE fj_automation_actions
                SET status = 'cancelled', execution_state = 'cancelled',
                    verification_state = 'manual_confirmed', verification_method = 'manual',
                    verification_completed_at = ?, last_status_code = 'MANUAL_CONFIRMED_NOT_CONTACTED',
                    last_error = ?, result_json = ?, updated_at = ?, completed_at = ?
                WHERE id = ?
                """,
                (
                    now,
                    normalized_note,
                    json.dumps({"contacted": False, "verificationMethod": "manual", "note": normalized_note}, ensure_ascii=False),
                    now,
                    now,
                    action_id,
                ),
            )
            connection.execute(
                """
                UPDATE fj_review_items
                SET status = 'pending', resolved_at = NULL,
                    resolution_note = '人工确认尚未建立沟通，等待重新批准', updated_at = ?
                WHERE id = ?
                """,
                (now, action["review_item_id"]),
            )
    _clear_unknown_limit_after_manual_verification(db)
    _audit(
        db,
        "boss_unknown_manually_verified",
        "未知错误岗位已人工确认已沟通。" if contacted else "未知错误岗位已人工确认未沟通并返回待确认。",
        {"action_id": action_id, "contacted": contacted},
    )
    return _serialize_action(_require_action(db, action_id))


def sweep_page_timeout(
    db: Database,
    executor_id: str,
    *,
    browser_status_provider: Callable[[], object] | None = None,
    random_seconds: Callable[[], int] | None = None,
    verification_reload_provider: Callable[[str, str], str] | None = None,
) -> None:
    executor = _executor_row(db, executor_id)
    action_id = str(executor["current_action_id"] or "")
    if not action_id:
        return
    action = _action_row(db, action_id)
    if not action:
        return
    if action["execution_state"] == "request_accepted":
        _sweep_contact_verification(
            db,
            executor_id,
            action,
            browser_status_provider=browser_status_provider,
            random_seconds=random_seconds,
            verification_reload_provider=verification_reload_provider,
        )
        return
    if action["execution_state"] == "dispatch_started":
        dispatch_started_at = _parse_time(action["dispatch_started_at"])
        if (
            dispatch_started_at
            and (datetime.now(timezone.utc) - dispatch_started_at).total_seconds()
            >= DISPATCH_RESULT_TIMEOUT_SECONDS
        ):
            _mark_unknown_after_dispatch(
                db,
                executor_id,
                action_id,
                "DISPATCH_RESULT_TIMEOUT",
                "真实请求开始后未收到明确结果",
                {},
            )
        return
    if action["execution_state"] != "waiting_page_ready":
        return
    deadline = _parse_time(action["page_deadline_at"])
    if not deadline or deadline > datetime.now(timezone.utc):
        return

    heartbeat_at = _parse_time(executor["last_heartbeat_at"])
    heartbeat_ok = bool(heartbeat_at and (datetime.now(timezone.utc) - heartbeat_at).total_seconds() <= HEARTBEAT_TTL_SECONDS)
    try:
        browser = (browser_status_provider or boss_scraper_service.get_browser_status)()
        browser_running = bool(getattr(browser, "running", False))
        current_url = str(getattr(browser, "current_url", "") or "")
        browser_ok = browser_running and _is_boss_url(current_url)
    except Exception:
        browser_ok = False

    normal = (
        heartbeat_ok and executor["permission_state"] == "allowed"
        and executor["queue_state"] == "running" and executor["risk_state"] == "none"
        and bool(executor["browser_connected"]) and browser_ok
    )
    if not normal:
        with db.connect() as connection:
            connection.execute(
                "UPDATE fj_boss_executor_instances SET permission_state = 'risk_paused', queue_state = 'risk_paused', risk_state = 'page_timeout_environment', updated_at = ? WHERE id = ?",
                (utc_now(), executor_id),
            )
        _audit(db, "boss_page_timeout_paused", "页面30秒无状态且插件或浏览器异常，已暂停队列。", {"action_id": action_id}, level="warning")
        return

    cooldown = _choose_cooldown(random_seconds)
    next_eligible = _iso_after(cooldown)
    now = utc_now()
    attempts = int(action["page_open_attempts"])
    with db.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        if attempts >= 2:
            connection.execute(
                "UPDATE fj_automation_actions SET status = 'blocked', execution_state = 'blocked', page_deadline_at = NULL, last_status_code = 'PAGE_STATUS_TIMEOUT_LIMIT', last_error = '第二次页面状态超时', cooldown_seconds = ?, next_eligible_at = ?, updated_at = ? WHERE id = ?",
                (cooldown, next_eligible, now, action_id),
            )
        else:
            max_position = int(connection.execute("SELECT COALESCE(MAX(queue_position), 0) FROM fj_automation_actions").fetchone()[0])
            connection.execute(
                "UPDATE fj_automation_actions SET status = 'queued', execution_state = 'queued', queue_position = ?, execution_epoch = execution_epoch + 1, page_deadline_at = NULL, lease_owner = NULL, last_status_code = 'PAGE_STATUS_TIMEOUT', cooldown_seconds = ?, next_eligible_at = ?, updated_at = ? WHERE id = ?",
                (max_position + 1, cooldown, next_eligible, now, action_id),
            )
        connection.execute(
            "UPDATE fj_boss_executor_instances SET current_action_id = NULL, current_epoch = NULL, cooldown_seconds = ?, next_eligible_at = ?, updated_at = ? WHERE id = ?",
            (cooldown, next_eligible, now, executor_id),
        )
    _audit(db, "boss_page_timeout", "页面30秒无状态，岗位已按规则处理。", {"action_id": action_id, "attempts": attempts, "cooldown_seconds": cooldown})


def _report_verification_snapshot(
    db: Database,
    executor_id: str,
    action,
    payload: dict[str, object],
) -> dict[str, object]:
    if str(action["verification_state"] or "") != "waiting_snapshot":
        return _serialize_action(action)
    started_at = _parse_time(action["verification_started_at"])
    observed_at = payload.get("observed_at")
    if started_at:
        if observed_at is None:
            return _serialize_action(action)
        observed = datetime.fromtimestamp(int(observed_at) / 1000, tz=timezone.utc)
        if observed < started_at:
            return _serialize_action(action)

    state = str(payload.get("state") or "waiting")
    if state == "waiting":
        return _serialize_action(action)
    if state != "ready" or not bool(payload.get("logged_in")):
        reason = str(payload.get("reason") or "刷新后的页面状态不可验证")
        _pause_for_risk(db, executor_id, str(action["id"]), "VERIFICATION_PAGE_NOT_EXECUTABLE", reason)
        return _serialize_action(_require_action(db, str(action["id"])))
    if str(payload.get("encrypt_job_id") or "") != str(action["encrypt_job_id"] or ""):
        _pause_for_risk(db, executor_id, str(action["id"]), "VERIFICATION_JOB_ID_MISMATCH", "刷新后识别到的岗位与原动作不一致。")
        return _serialize_action(_require_action(db, str(action["id"])))
    if payload.get("contacted") is True:
        return _finish_verified_action(
            db,
            executor_id,
            str(action["id"]),
            {"contacted": True, "verificationMethod": "page_refresh"},
        )
    return _release_accepted_action(
        db,
        executor_id,
        str(action["id"]),
        verification_state="pending",
        status_code="BOSS_REQUEST_ACCEPTED_PAGE_PENDING",
        evidence={"contacted": False, "verificationMethod": "page_refresh"},
    )


def _accept_action(
    db: Database,
    executor_id: str,
    action_id: str,
    status_code: str,
    evidence: dict[str, object],
    *,
    verification_delay_provider: Callable[[], int] | None = None,
) -> dict[str, object]:
    strategy = get_delivery_strategy(db) or {}
    force_verification = bool(strategy.get("force_contact_verification_enabled"))
    now = utc_now()
    if not force_verification:
        return _release_accepted_action(
            db,
            executor_id,
            action_id,
            verification_state="not_required",
            status_code=status_code,
            evidence=evidence,
        )

    delay = _choose_verification_delay(verification_delay_provider)
    due_at = _iso_after(delay)
    with db.connect() as connection:
        connection.execute(
            """
            UPDATE fj_automation_actions
            SET status = 'succeeded', execution_state = 'request_accepted',
                request_accepted_at = ?, verification_state = 'waiting_refresh',
                verification_method = 'page_refresh', verification_delay_seconds = ?,
                verification_due_at = ?, page_deadline_at = NULL,
                last_status_code = ?, last_error = NULL, result_json = ?, updated_at = ?
            WHERE id = ?
            """,
            (now, delay, due_at, status_code, json.dumps(evidence, ensure_ascii=False), now, action_id),
        )
    _audit(
        db,
        "boss_request_accepted",
        "平台已受理建立沟通请求，正在等待单次页面刷新验证。",
        {"action_id": action_id, "verification_delay_seconds": delay},
    )
    return _serialize_action(_require_action(db, action_id))


def _release_accepted_action(
    db: Database,
    executor_id: str,
    action_id: str,
    *,
    verification_state: str,
    status_code: str,
    evidence: dict[str, object],
    random_seconds: Callable[[], int] | None = None,
) -> dict[str, object]:
    cooldown = _choose_cooldown(random_seconds)
    next_eligible = _iso_after(cooldown)
    now = utc_now()
    current = _require_action(db, action_id)
    merged_evidence = _load_result_evidence(current["result_json"])
    merged_evidence.update(evidence)
    with db.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            UPDATE fj_automation_actions
            SET status = 'succeeded', execution_state = 'request_accepted',
                request_accepted_at = COALESCE(request_accepted_at, ?),
                verification_state = ?, verification_method = CASE
                  WHEN ? = 'not_required' THEN 'none' ELSE 'page_refresh' END,
                verification_due_at = NULL, verification_completed_at = ?,
                lease_owner = NULL, page_deadline_at = NULL, last_status_code = ?,
                last_error = NULL, result_json = ?, cooldown_seconds = ?,
                next_eligible_at = ?, updated_at = ?, completed_at = COALESCE(completed_at, ?)
            WHERE id = ?
            """,
            (
                now, verification_state, verification_state, now, status_code,
                json.dumps(merged_evidence, ensure_ascii=False), cooldown,
                next_eligible, now, now, action_id,
            ),
        )
        connection.execute(
            "UPDATE fj_boss_executor_instances SET current_action_id = NULL, current_epoch = NULL, cooldown_seconds = ?, next_eligible_at = ?, updated_at = ? WHERE id = ?",
            (cooldown, next_eligible, now, executor_id),
        )
    _audit(
        db,
        "boss_request_accepted_released",
        "建立沟通请求已受理，当前执行槽已释放。",
        {"action_id": action_id, "verification_state": verification_state, "cooldown_seconds": cooldown},
    )
    return _serialize_action(_require_action(db, action_id))


def _finish_verified_action(
    db: Database,
    executor_id: str,
    action_id: str,
    evidence: dict[str, object],
) -> dict[str, object]:
    cooldown = _choose_cooldown()
    next_eligible = _iso_after(cooldown)
    now = utc_now()
    current = _require_action(db, action_id)
    merged_evidence = _load_result_evidence(current["result_json"])
    merged_evidence.update(evidence)
    with db.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            UPDATE fj_automation_actions
            SET status = 'succeeded', execution_state = 'succeeded',
                verification_state = 'page_confirmed', verification_method = 'page_refresh',
                verification_due_at = NULL, verification_completed_at = ?, lease_owner = NULL,
                page_deadline_at = NULL, last_status_code = 'BOSS_CONTACT_PAGE_CONFIRMED',
                result_json = ?, cooldown_seconds = ?, next_eligible_at = ?,
                updated_at = ?, completed_at = ?
            WHERE id = ?
            """,
            (now, json.dumps(merged_evidence, ensure_ascii=False), cooldown, next_eligible, now, now, action_id),
        )
        connection.execute(
            "UPDATE fj_boss_executor_instances SET current_action_id = NULL, current_epoch = NULL, cooldown_seconds = ?, next_eligible_at = ?, updated_at = ? WHERE id = ?",
            (cooldown, next_eligible, now, executor_id),
        )
    _audit(db, "boss_contact_page_confirmed", "刷新岗位页面后已确认建立沟通。", {"action_id": action_id, "cooldown_seconds": cooldown})
    return _serialize_action(_require_action(db, action_id))


def _sweep_contact_verification(
    db: Database,
    executor_id: str,
    action,
    *,
    browser_status_provider: Callable[[], object] | None,
    random_seconds: Callable[[], int] | None,
    verification_reload_provider: Callable[[str, str], str] | None,
) -> None:
    verification_state = str(action["verification_state"] or "not_required")
    if verification_state == "waiting_refresh":
        due_at = _parse_time(action["verification_due_at"])
        if not due_at or due_at > datetime.now(timezone.utc):
            return
        if not _verification_environment_ok(db, executor_id, action, browser_status_provider):
            _pause_for_risk(db, executor_id, str(action["id"]), "VERIFICATION_ENVIRONMENT", "刷新验证前插件、浏览器或岗位页面状态异常。")
            return
        with db.connect() as connection:
            navigation = connection.execute(
                "SELECT browser_target_id FROM fj_boss_navigation_tasks WHERE id = ?",
                (action["navigation_task_id"],),
            ).fetchone()
        target_id = str(navigation["browser_target_id"] or "") if navigation else ""
        now = utc_now()
        deadline = _iso_after(PAGE_STATUS_TIMEOUT_SECONDS)
        with db.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE fj_automation_actions
                SET verification_state = 'refreshing', verification_started_at = ?,
                    verification_attempts = verification_attempts + 1,
                    page_deadline_at = ?, last_status_code = 'CONTACT_VERIFICATION_REFRESHING',
                    updated_at = ?
                WHERE id = ? AND verification_state = 'waiting_refresh'
                """,
                (now, deadline, now, action["id"]),
            )
        if cursor.rowcount != 1:
            return
        try:
            reload_page = verification_reload_provider or boss_scraper_service.reload_job_page
            reload_page(target_id, str(action["encrypt_job_id"] or ""))
        except (ValueError, RuntimeError, TimeoutError, OSError) as exc:
            _pause_for_risk(db, executor_id, str(action["id"]), "VERIFICATION_REFRESH_FAILED", str(exc))
            return
        with db.connect() as connection:
            connection.execute(
                "UPDATE fj_automation_actions SET verification_state = 'waiting_snapshot', last_status_code = 'CONTACT_VERIFICATION_WAITING_SNAPSHOT', updated_at = ? WHERE id = ?",
                (utc_now(), action["id"]),
            )
        _audit(db, "boss_contact_verification_refresh", "已刷新当前岗位页一次，等待插件重新识别。", {"action_id": action["id"], "target_id": target_id})
        return

    if verification_state not in {"refreshing", "waiting_snapshot"}:
        return
    deadline = _parse_time(action["page_deadline_at"])
    if not deadline or deadline > datetime.now(timezone.utc):
        return
    if not _verification_environment_ok(db, executor_id, action, browser_status_provider):
        _pause_for_risk(db, executor_id, str(action["id"]), "VERIFICATION_TIMEOUT_ENVIRONMENT", "刷新验证等待超时且插件或浏览器状态异常。")
        return
    _release_accepted_action(
        db,
        executor_id,
        str(action["id"]),
        verification_state="pending",
        status_code="BOSS_REQUEST_ACCEPTED_VERIFICATION_TIMEOUT",
        evidence={"contacted": None, "verificationMethod": "page_refresh"},
        random_seconds=random_seconds,
    )


def _verification_environment_ok(
    db: Database,
    executor_id: str,
    action,
    browser_status_provider: Callable[[], object] | None,
) -> bool:
    executor = _executor_row(db, executor_id)
    heartbeat_at = _parse_time(executor["last_heartbeat_at"])
    heartbeat_ok = bool(
        heartbeat_at and (datetime.now(timezone.utc) - heartbeat_at).total_seconds() <= HEARTBEAT_TTL_SECONDS
    )
    try:
        browser = (browser_status_provider or boss_scraper_service.get_browser_status)()
        current_url = str(getattr(browser, "current_url", "") or "")
        browser_ok = bool(getattr(browser, "running", False)) and _valid_job_url(
            current_url, str(action["encrypt_job_id"] or "")
        )
    except Exception:
        browser_ok = False
    return bool(
        heartbeat_ok
        and executor["permission_state"] == "allowed"
        and executor["queue_state"] == "running"
        and executor["risk_state"] == "none"
        and bool(executor["browser_connected"])
        and browser_ok
    )


def _finish_failed_action(
    db: Database,
    executor_id: str,
    action_id: str,
    status_code: str,
    message: str,
    evidence: dict[str, object],
) -> dict[str, object]:
    cooldown = _choose_cooldown()
    next_eligible = _iso_after(cooldown)
    now = utc_now()
    with db.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "UPDATE fj_automation_actions SET status = 'failed', execution_state = 'failed_after_dispatch', lease_owner = NULL, page_deadline_at = NULL, last_status_code = ?, last_error = ?, result_json = ?, cooldown_seconds = ?, next_eligible_at = ?, updated_at = ?, completed_at = ? WHERE id = ?",
            (status_code, message, json.dumps(evidence, ensure_ascii=False), cooldown, next_eligible, now, now, action_id),
        )
        connection.execute(
            "UPDATE fj_boss_executor_instances SET current_action_id = NULL, current_epoch = NULL, cooldown_seconds = ?, next_eligible_at = ?, updated_at = ? WHERE id = ?",
            (cooldown, next_eligible, now, executor_id),
        )
    _audit(db, "boss_request_rejected", "平台明确拒绝建立沟通请求。", {"action_id": action_id, "status_code": status_code}, level="warning")
    return _serialize_action(_require_action(db, action_id))


def _mark_unknown_after_dispatch(
    db: Database,
    executor_id: str,
    action_id: str,
    status_code: str,
    message: str,
    evidence: dict[str, object],
) -> dict[str, object]:
    cooldown = _choose_cooldown()
    next_eligible = _iso_after(cooldown)
    now = utc_now()
    with db.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        max_position = int(connection.execute(
            "SELECT COALESCE(MAX(queue_position), 0) FROM fj_automation_actions"
        ).fetchone()[0])
        connection.execute(
            """
            UPDATE fj_automation_actions
            SET status = 'unknown', execution_state = 'unknown_after_dispatch',
                queue_position = ?, lease_owner = NULL, page_deadline_at = NULL,
                last_status_code = ?, last_error = ?, result_json = ?,
                cooldown_seconds = ?, next_eligible_at = ?, updated_at = ?, completed_at = ?
            WHERE id = ?
            """,
            (
                max_position + 1, status_code, message,
                json.dumps(evidence, ensure_ascii=False), cooldown,
                next_eligible, now, now, action_id,
            ),
        )
        connection.execute(
            """
            UPDATE fj_boss_executor_instances
            SET current_action_id = NULL, current_epoch = NULL,
                cooldown_seconds = ?, next_eligible_at = ?,
                risk_state = CASE WHEN risk_state = 'unknown_after_dispatch' THEN 'none' ELSE risk_state END,
                updated_at = ?
            WHERE id = ?
            """,
            (cooldown, next_eligible, now, executor_id),
        )
    consecutive_unknowns = _consecutive_unknown_count(db)
    if consecutive_unknowns >= 3:
        with db.connect() as connection:
            connection.execute(
                """
                UPDATE fj_boss_executor_instances
                SET permission_state = 'risk_paused', queue_state = 'risk_paused',
                    risk_state = 'consecutive_unknown_after_dispatch', updated_at = ?
                WHERE id = ?
                """,
                (utc_now(), executor_id),
            )
    _audit(
        db,
        "boss_unknown_after_dispatch",
        "真实请求后出现未知错误，已移到队尾。" if consecutive_unknowns < 3 else "连续3个不同岗位出现未知错误，已暂停队列。",
        {"action_id": action_id, "consecutive_unknowns": consecutive_unknowns, "cooldown_seconds": cooldown},
        level="warning",
    )
    return _serialize_action(_require_action(db, action_id))


def _sanitize_execution_evidence(value: object) -> dict[str, object]:
    source = value if isinstance(value, dict) else {}
    return {
        key: source[key]
        for key in ("responseCode", "httpStatus")
        if key in source and isinstance(source[key], (str, int, float, bool, type(None)))
    }


def _load_result_evidence(value: object) -> dict[str, object]:
    try:
        parsed = json.loads(str(value or "{}"))
    except json.JSONDecodeError:
        return {}
    return dict(parsed) if isinstance(parsed, dict) else {}


def _finish_action(db: Database, executor_id: str, action_id: str, state: str, status_code: str, evidence: dict[str, object]) -> dict[str, object]:
    cooldown = _choose_cooldown()
    next_eligible = _iso_after(cooldown)
    now = utc_now()
    with db.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "UPDATE fj_automation_actions SET status = 'succeeded', execution_state = ?, lease_owner = NULL, page_deadline_at = NULL, last_status_code = ?, result_json = ?, cooldown_seconds = ?, next_eligible_at = ?, updated_at = ?, completed_at = ? WHERE id = ?",
            (state, status_code, json.dumps(evidence, ensure_ascii=False), cooldown, next_eligible, now, now, action_id),
        )
        connection.execute(
            "UPDATE fj_boss_executor_instances SET current_action_id = NULL, current_epoch = NULL, cooldown_seconds = ?, next_eligible_at = ?, updated_at = ? WHERE id = ?",
            (cooldown, next_eligible, now, executor_id),
        )
    _audit(db, "boss_action_finished", "默认招呼动作已完成。", {"action_id": action_id, "status_code": status_code, "cooldown_seconds": cooldown})
    return _serialize_action(_require_action(db, action_id))


def _pause_for_risk(db: Database, executor_id: str, action_id: str, code: str, reason: str) -> None:
    now = utc_now()
    with db.connect() as connection:
        connection.execute(
            "UPDATE fj_automation_actions SET status = 'blocked', execution_state = 'blocked', last_status_code = ?, last_error = ?, updated_at = ? WHERE id = ?",
            (code, reason, now, action_id),
        )
        connection.execute(
            "UPDATE fj_boss_executor_instances SET permission_state = 'risk_paused', queue_state = 'risk_paused', risk_state = ?, updated_at = ? WHERE id = ?",
            (code.lower(), now, executor_id),
        )
    _audit(db, "boss_executor_risk", reason, {"action_id": action_id, "code": code}, level="warning")


def _choose_cooldown(provider: Callable[[], int] | None = None) -> int:
    value = int(provider() if provider else secrets.randbelow(3) + 1)
    if value not in {1, 2, 3}:
        raise ValueError("随机等待只能是1、2或3秒")
    return value


def _choose_verification_delay(provider: Callable[[], int] | None = None) -> int:
    value = int(
        provider()
        if provider
        else secrets.randbelow(VERIFICATION_DELAY_MAX_SECONDS - VERIFICATION_DELAY_MIN_SECONDS + 1)
        + VERIFICATION_DELAY_MIN_SECONDS
    )
    if not VERIFICATION_DELAY_MIN_SECONDS <= value <= VERIFICATION_DELAY_MAX_SECONDS:
        raise ValueError("刷新验证随机等待只能在10～30秒之间")
    return value


def _resolve_job(db: Database, identifier: str, source_context: str) -> dict[str, object]:
    identifier = identifier.strip()
    with db.connect() as connection:
        if source_context == "review":
            row = connection.execute(
                """
                SELECT j.* FROM fj_review_items r JOIN fj_boss_jobs j ON j.id = r.job_id
                WHERE r.id = ? OR j.id = ? LIMIT 1
                """, (identifier, identifier),
            ).fetchone()
        else:
            row = connection.execute(
                "SELECT * FROM fj_boss_jobs WHERE id = ? OR source_job_id = ? OR encrypt_job_id = ? LIMIT 1",
                (identifier, identifier, identifier),
            ).fetchone()
    if row is None:
        raise AppError(status_code=404, error_category="JOB_NOT_FOUND", error_message="找不到要打开的岗位记录。")
    return dict(row)


def _target_job_url(job: dict[str, object]) -> str:
    stored = str(job.get("job_link") or "").strip()
    stored_job_id = _job_id_from_url(stored)
    expected = str(job.get("encrypt_job_id") or stored_job_id or job.get("source_job_id") or "").strip()
    if stored and _valid_job_url(stored, expected):
        return stored
    if expected:
        return f"https://www.zhipin.com/job_detail/{expected}.html"
    raise AppError(status_code=409, error_category="JOB_ID_MISSING", error_message="岗位缺少可验证的BOSS岗位标识，不能打开详情页。")


def _job_id_from_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in {"www.zhipin.com", "zhipin.com"}:
        return ""
    matched = re.search(r"/job_detail/([^/]+?)\.html(?:/|$)", parsed.path)
    return matched.group(1) if matched else ""


def _valid_job_url(url: str, expected: str) -> bool:
    parsed = urlparse(url)
    return (
        parsed.scheme == "https" and parsed.hostname in {"www.zhipin.com", "zhipin.com"}
        and "/job_detail/" in parsed.path
        and (not expected or expected in parsed.path)
    )


def _is_boss_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme in {"http", "https"} and parsed.hostname in {"www.zhipin.com", "zhipin.com"}


def _executor_row(db: Database, executor_id: str):
    with db.connect() as connection:
        row = connection.execute("SELECT * FROM fj_boss_executor_instances WHERE id = ?", (executor_id,)).fetchone()
    if row is None:
        raise AppError(status_code=404, error_category="NOT_FOUND", error_message="执行器不存在。")
    return row


def _consecutive_unknown_count(db: Database) -> int:
    """统计最近连续的不同未知岗位；任一明确执行结果都会把计数清零。"""
    with db.connect() as connection:
        rows = connection.execute(
            """
            SELECT execution_state, job_id
            FROM fj_automation_actions
            WHERE action_type = 'BOSS_DEFAULT_GREETING'
              AND dispatch_started_at IS NOT NULL
              AND execution_state IN (
                'request_accepted', 'succeeded', 'failed_after_dispatch',
                'unknown_after_dispatch', 'cancelled'
              )
            ORDER BY COALESCE(completed_at, updated_at) DESC, updated_at DESC, id DESC
            """
        ).fetchall()
    unknown_jobs: set[str] = set()
    for row in rows:
        if row["execution_state"] != "unknown_after_dispatch":
            break
        unknown_jobs.add(str(row["job_id"]))
    return len(unknown_jobs)


def _clear_unknown_limit_after_manual_verification(db: Database) -> None:
    if _consecutive_unknown_count(db) >= 3:
        return
    now = utc_now()
    with db.connect() as connection:
        connection.execute(
            """
            UPDATE fj_boss_executor_instances
            SET permission_state = CASE
                  WHEN risk_state = 'consecutive_unknown_after_dispatch' THEN 'paused'
                  ELSE permission_state END,
                queue_state = CASE
                  WHEN risk_state = 'consecutive_unknown_after_dispatch' THEN 'paused'
                  ELSE queue_state END,
                risk_state = CASE
                  WHEN risk_state IN ('unknown_after_dispatch', 'consecutive_unknown_after_dispatch') THEN 'none'
                  ELSE risk_state END,
                updated_at = ?
            """,
            (now,),
        )


def _action_row(db: Database, action_id: str):
    with db.connect() as connection:
        return connection.execute(
            "SELECT a.*, j.title AS job_title, j.company_name, j.encrypt_job_id, j.job_link FROM fj_automation_actions a JOIN fj_boss_jobs j ON j.id = a.job_id WHERE a.id = ?",
            (action_id,),
        ).fetchone()


def _require_action(db: Database, action_id: str):
    row = _action_row(db, action_id)
    if row is None:
        raise AppError(status_code=404, error_category="NOT_FOUND", error_message="打招呼动作不存在。")
    return row


def _require_owned_action(db: Database, executor_id: str, action_id: str, epoch: int):
    row = _require_action(db, action_id)
    if str(row["lease_owner"] or "") != executor_id:
        raise AppError(status_code=409, error_category="INVALID_LEASE", error_message="动作不属于当前执行器。")
    if int(row["execution_epoch"]) != epoch:
        raise AppError(status_code=409, error_category="STALE_EXECUTION_EPOCH", error_message="该状态属于已经失效的执行轮次。")
    return row


def _serialize_action(row, *, include_payload: bool = True) -> dict[str, object]:
    data = {
        "id": row["id"], "job_id": row["job_id"], "review_item_id": row["review_item_id"],
        "action_type": row["action_type"], "status": row["status"],
        "execution_state": row["execution_state"], "execution_epoch": row["execution_epoch"],
        "queue_position": row["queue_position"], "page_open_attempts": row["page_open_attempts"],
        "page_deadline_at": row["page_deadline_at"], "dispatch_started_at": row["dispatch_started_at"],
        "request_accepted_at": row["request_accepted_at"],
        "verification_state": row["verification_state"],
        "verification_method": row["verification_method"],
        "verification_delay_seconds": row["verification_delay_seconds"],
        "verification_due_at": row["verification_due_at"],
        "verification_started_at": row["verification_started_at"],
        "verification_completed_at": row["verification_completed_at"],
        "verification_attempts": row["verification_attempts"],
        "cooldown_seconds": row["cooldown_seconds"], "next_eligible_at": row["next_eligible_at"],
        "last_status_code": row["last_status_code"], "last_error": row["last_error"],
        "job_title": row["job_title"], "company_name": row["company_name"],
        "encrypt_job_id": row["encrypt_job_id"], "job_link": row["job_link"],
        "created_at": row["created_at"], "updated_at": row["updated_at"],
    }
    if include_payload:
        payload = json.loads(row["payload_json"] or "{}")
        payload.pop("message", None)
        data["payload"] = payload
    return data


def _serialize_executor(row) -> dict[str, object]:
    return {
        "id": row["id"], "label": row["label"], "protocol_version": row["protocol_version"],
        "plugin_version": row["plugin_version"], "capabilities": json.loads(row["capabilities_json"] or "[]"),
        "permission_state": row["permission_state"], "queue_state": row["queue_state"],
        "risk_state": row["risk_state"], "browser_connected": bool(row["browser_connected"]),
        "current_action_id": row["current_action_id"], "current_epoch": row["current_epoch"],
        "cooldown_seconds": row["cooldown_seconds"], "next_eligible_at": row["next_eligible_at"],
        "last_heartbeat_at": row["last_heartbeat_at"], "updated_at": row["updated_at"],
    }


def _audit(db: Database, action_type: str, message: str, detail: dict[str, object], *, level: str = "info") -> None:
    with db.connect() as connection:
        connection.execute(
            "INSERT INTO fj_action_logs (id, run_id, level, action_type, message, detail_json, created_at) VALUES (?, NULL, ?, ?, ?, ?, ?)",
            (new_id(), level, action_type, message, json.dumps(detail, ensure_ascii=False), utc_now()),
        )
