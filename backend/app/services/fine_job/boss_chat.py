from __future__ import annotations

import json
import random
import re
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from backend.app.config import AppConfig
from backend.app.db import Database
from backend.app.errors import AppError
from backend.app.services.ai import _post_json
from backend.app.services.fine_job import profile_store
from backend.app.services.fine_job.boss_capture_history import (
    get_capture_history_job,
    record_chat_job,
)
from backend.app.services.fine_job.boss_capture_tasks import boss_capture_task_manager
from backend.app.services.fine_job.boss_scraper.service import boss_scraper_service
from backend.app.services.fine_job.codex_authorization import classify_outbound_content
from backend.app.services.fine_job.profile_context import get_profile_context
from backend.app.services.fine_job.job_applications import set_job_application_status
from backend.app.services.fine_job.job_progress import build_job_progress_with_connection
from backend.app.services.fine_job.execution_reconciliation import (
    observe_outbound_chat_message,
    record_execution_evidence_with_connection,
    set_canonical_from_raw,
)
from backend.app.services.fine_job.job_activity import append_job_activity_with_connection
from backend.app.services.reasoning.codex_exec import run_codex_exec


RUNTIME_ID = "boss"
SEND_LEASE_SECONDS = 30
SEND_DISPATCH_TIMEOUT_SECONDS = 45
REPLY_DEBOUNCE_SECONDS = 3

MESSAGE_TRANSFORM_CONFIG_ID = "boss"
DEFAULT_MESSAGE_TRANSFORM_RULES = [
    {
        "id": "job-context",
        "label": "岗位沟通卡片",
        "enabled": True,
        "direction": "inbound",
        "match_mode": "regex",
        "pattern": r"^\s*岗位：\s*(.+?)\s*·\s*(.+?)\s*$",
        "output_kind": "action",
        "display_content": "沟通岗位",
        "action_type": "job_context",
        "requires_resume_sent": False,
        "condition_rule_id": "",
        "condition_branch": "always",
    },
    {
        "id": "competitor-pk",
        "label": "竞争者 PK 推广",
        "enabled": True,
        "direction": "inbound",
        "match_mode": "contains",
        "pattern": "你与该职位竞争者PK情况",
        "output_kind": "discard",
        "display_content": "",
        "action_type": "",
        "requires_resume_sent": False,
        "condition_rule_id": "",
        "condition_branch": "always",
    },
    {
        "id": "resume-withdraw-requested",
        "label": "条件线：附件简历请求已撤回",
        "enabled": True,
        "direction": "inbound",
        "match_mode": "exact",
        "pattern": "附件简历请求已撤回",
        "output_kind": "action",
        "display_content": "我已执行简历撤回操作",
        "action_type": "resume_withdraw_requested",
        "requires_resume_sent": False,
        "condition_rule_id": "",
        "condition_branch": "always",
    },
    {
        "id": "resume-withdrawn",
        "label": "简历已撤回",
        "enabled": True,
        "direction": "outbound",
        "match_mode": "exact",
        "pattern": "附件状态更新",
        "output_kind": "action",
        "display_content": "简历已撤回",
        "action_type": "resume_withdrawn",
        "requires_resume_sent": False,
        "condition_rule_id": "resume-withdraw-requested",
        "condition_branch": "if",
    },
    {
        "id": "resume-sent",
        "label": "我发送附件简历",
        "enabled": True,
        "direction": "outbound",
        "match_mode": "exact",
        "pattern": "附件状态更新",
        "output_kind": "action",
        "display_content": "我已发送附件简历",
        "action_type": "resume_sent",
        "requires_resume_sent": False,
        "condition_rule_id": "resume-withdraw-requested",
        "condition_branch": "else",
    },
    {
        "id": "resume-withdrawn-by-hr",
        "label": "简历已于 HR 端成功撤回",
        "enabled": True,
        "direction": "inbound",
        "match_mode": "exact",
        "pattern": "附件状态更新",
        "output_kind": "action",
        "display_content": "简历已于HR端成功撤回",
        "action_type": "resume_withdrawn_by_hr",
        "requires_resume_sent": False,
        "condition_rule_id": "resume-withdraw-requested",
        "condition_branch": "if",
    },
    {
        "id": "resume-viewed",
        "label": "HR 已读发送简历的消息",
        "enabled": True,
        "direction": "inbound",
        "match_mode": "exact",
        "pattern": "附件状态更新",
        "output_kind": "action",
        "display_content": "HR已读我发送简历的消息",
        "action_type": "resume_message_read",
        "requires_resume_sent": False,
        "condition_rule_id": "resume-withdraw-requested",
        "condition_branch": "else",
    },
    {
        "id": "resume-read",
        "label": "简历已成功发送",
        "enabled": True,
        "direction": "inbound",
        "match_mode": "regex",
        "pattern": r"^\s*.+\.pdf\s*$",
        "output_kind": "action",
        "display_content": "我的简历已成功发送出去",
        "action_type": "resume_sent_confirmed",
        "requires_resume_sent": False,
        "condition_rule_id": "",
        "condition_branch": "always",
    },
    {
        "id": "resume-viewed-by-hr",
        "label": "HR 查看附件简历",
        "enabled": True,
        "direction": "inbound",
        "match_mode": "exact",
        "pattern": "对方已查看了您的附件简历",
        "output_kind": "action",
        "display_content": "HR已查看我的简历",
        "action_type": "resume_viewed",
        "requires_resume_sent": False,
        "condition_rule_id": "",
        "condition_branch": "always",
    },
    {
        "id": "resume-received",
        "label": "HR 接收附件简历",
        "enabled": True,
        "direction": "inbound",
        "match_mode": "exact",
        "pattern": "对方已同意，您的附件简历已发送给对方",
        "output_kind": "action",
        "display_content": "HR已接收附件简历",
        "action_type": "resume_received",
        "requires_resume_sent": False,
        "condition_rule_id": "",
        "condition_branch": "always",
    },
]

_generation_timers: dict[str, threading.Timer] = {}
_generation_timers_lock = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _after(seconds: int) -> str:
    return (
        datetime.now(timezone.utc) + timedelta(seconds=seconds)
    ).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _record_resume_claim_log(
    connection: sqlite3.Connection,
    *,
    level: str,
    message: str,
    detail: dict[str, object],
) -> None:
    """记录自动代聊简历任务领取过程，供动作日志页面直接展示。"""
    connection.execute(
        """
        INSERT INTO fj_action_logs (id, level, action_type, message, detail_json, created_at)
        VALUES (?, ?, 'boss_chat_resume_claim', ?, ?, ?)
        """,
        (uuid4().hex, level, message, json.dumps(detail, ensure_ascii=False), _now()),
    )


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def _new_client_mid(connection: sqlite3.Connection) -> str:
    """生成并保存为字符串，避免 JavaScript number 精度影响 int64 clientMid。"""
    while True:
        candidate = str(random.SystemRandom().randint(1_000_000_000_000_000_000, 9_000_000_000_000_000_000))
        exists = connection.execute(
            "SELECT 1 FROM fj_chat_send_actions WHERE client_mid = ? LIMIT 1", (candidate,)
        ).fetchone()
        if exists is None:
            return candidate


def _loads(value: str | None, fallback: Any) -> Any:
    try:
        return json.loads(value or "")
    except (TypeError, ValueError):
        return fallback


def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    result = dict(row)
    for key in ("listen_enabled", "generation_enabled", "send_enabled", "direct_execution_enabled", "is_draft"):
        if key in result:
            result[key] = bool(result[key])
    for key in ("message_update_required", "history_has_more", "has_local_messages"):
        if key in result:
            result[key] = bool(result[key])
    for key in (
        "context_json",
        "raw_meta_json",
        "evidence_json",
        "payload_json",
        "input_message_ids_json",
        "facts_used_json",
        "warnings_json",
        "content_categories_json",
        "insight_json",
        "recommendation_json",
        "deterministic_facts_json",
        "skipped_reasons_json",
    ):
        if key in result:
            fallback: Any = [] if key in {
                "input_message_ids_json",
                "facts_used_json",
                "warnings_json",
                "content_categories_json",
            } else {}
            result[key.removesuffix("_json")] = _loads(result.pop(key), fallback)
    if "requires_user_input" in result:
        result["requires_user_input"] = bool(result["requires_user_input"])
    return result


def _default_message_transform_rules() -> list[dict[str, Any]]:
    return [dict(rule) for rule in DEFAULT_MESSAGE_TRANSFORM_RULES]


def _upgrade_legacy_default_transform_rules(rules: list[dict[str, Any]]) -> bool:
    """升级未改动的旧内置规则，保留用户自定义过的规则内容。"""
    changed = False
    defaults_by_id = {str(rule["id"]): rule for rule in _default_message_transform_rules()}
    legacy_contents = {
        "job-context": {r"沟通岗位：\1 · \2"},
        "resume-sent": {"已发送附件简历"},
        "resume-viewed": {"HR 已查看附件简历", "HR已查看附件简历"},
        "resume-read": {"HR 已阅读附件简历", "HR已读附件简历"},
        "resume-received": {"HR 已接收附件简历", "HR已接收附件简历"},
    }
    for rule in rules:
        rule_id = str(rule.get("id") or "")
        default = defaults_by_id.get(rule_id)
        if default is None:
            continue
        if str(rule.get("display_content") or "") in legacy_contents.get(rule_id, set()):
            for key in ("label", "direction", "match_mode", "pattern", "output_kind", "display_content", "action_type", "requires_resume_sent"):
                if rule.get(key) != default[key]:
                    rule[key] = default[key]
                    changed = True
        if rule_id == "resume-withdraw-requested" and str(rule.get("label") or "") == "我执行简历撤回操作":
            rule["label"] = str(default["label"])
            changed = True
        legacy_condition_rule_ids = {
            "resume_withdraw_requested": "resume-withdraw-requested",
        }
        legacy_condition_action_type = str(rule.pop("condition_action_type", "") or "")
        if "condition_rule_id" not in rule:
            rule["condition_rule_id"] = (
                legacy_condition_rule_ids.get(legacy_condition_action_type)
                or str(default["condition_rule_id"] or "")
            )
            changed = True
        elif legacy_condition_action_type:
            changed = True
        for key in ("condition_branch",):
            if key not in rule:
                rule[key] = default[key]
                changed = True
    existing_rule_ids = {str(rule.get("id") or "") for rule in rules}
    for rule_id in ("resume-viewed-by-hr", "resume-withdraw-requested", "resume-withdrawn", "resume-withdrawn-by-hr"):
        if rule_id not in existing_rule_ids:
            rules.append(dict(defaults_by_id[rule_id]))
            changed = True
    return changed


def _message_transform_config(connection: sqlite3.Connection) -> dict[str, Any]:
    row = connection.execute(
        "SELECT rules_json, updated_at FROM fj_chat_message_transform_settings WHERE id = ?",
        (MESSAGE_TRANSFORM_CONFIG_ID,),
    ).fetchone()
    if row is not None:
        rules = _loads(str(row["rules_json"] or "[]"), [])
        if isinstance(rules, list) and rules:
            normalized_rules = [dict(rule) for rule in rules if isinstance(rule, dict)]
            if _upgrade_legacy_default_transform_rules(normalized_rules):
                now = _now()
                connection.execute(
                    "UPDATE fj_chat_message_transform_settings SET rules_json = ?, updated_at = ? WHERE id = ?",
                    (json.dumps(normalized_rules, ensure_ascii=False), now, MESSAGE_TRANSFORM_CONFIG_ID),
                )
                return {"rules": normalized_rules, "updated_at": now}
            return {"rules": normalized_rules, "updated_at": row["updated_at"]}
    now = _now()
    rules = _default_message_transform_rules()
    connection.execute(
        """
        INSERT INTO fj_chat_message_transform_settings (id, rules_json, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET rules_json = excluded.rules_json, updated_at = excluded.updated_at
        """,
        (MESSAGE_TRANSFORM_CONFIG_ID, json.dumps(rules, ensure_ascii=False), now),
    )
    return {"rules": rules, "updated_at": now}


def get_message_transform_config(db: Database) -> dict[str, Any]:
    with db.connect() as connection:
        return _message_transform_config(connection)


def save_message_transform_config(db: Database, rules: list[dict[str, Any]]) -> dict[str, Any]:
    rules_by_id = {str(rule.get("id") or ""): rule for rule in rules}
    for rule in rules:
        condition_branch = str(rule.get("condition_branch") or "always")
        condition_rule_id = str(rule.get("condition_rule_id") or "").strip()
        if condition_branch not in {"always", "if", "else"}:
            raise AppError(
                status_code=400,
                error_category="CHAT_MESSAGE_TRANSFORM_CONDITION_INVALID",
                error_message=f"规则“{rule.get('label') or rule.get('id')}”的条件分支无效。",
            )
        condition_rule = rules_by_id.get(condition_rule_id)
        if condition_branch != "always" and not condition_rule_id:
            raise AppError(
                status_code=400,
                error_category="CHAT_MESSAGE_TRANSFORM_CONDITION_RULE_REQUIRED",
                error_message=f"规则“{rule.get('label') or rule.get('id')}”需要选择条件线。",
            )
        if condition_branch != "always" and (
            condition_rule is None
            or condition_rule.get("output_kind") != "action"
            or not str(condition_rule.get("action_type") or "").strip()
        ):
            raise AppError(
                status_code=400,
                error_category="CHAT_MESSAGE_TRANSFORM_CONDITION_RULE_INVALID",
                error_message=f"规则“{rule.get('label') or rule.get('id')}”选择的条件线无效。",
            )
        if str(rule.get("match_mode") or "") == "regex":
            try:
                re.compile(str(rule.get("pattern") or ""))
            except re.error as exc:
                raise AppError(
                    status_code=400,
                    error_category="CHAT_MESSAGE_TRANSFORM_REGEX_INVALID",
                    error_message=f"规则“{rule.get('label') or rule.get('id')}”的正则表达式无效：{exc}",
                ) from exc
    now = _now()
    with db.connect() as connection:
        connection.execute(
            """
            INSERT INTO fj_chat_message_transform_settings (id, rules_json, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET rules_json = excluded.rules_json, updated_at = excluded.updated_at
            """,
            (MESSAGE_TRANSFORM_CONFIG_ID, json.dumps(rules, ensure_ascii=False), now),
        )
    return {"rules": rules, "updated_at": now}


def reset_message_transform_config(db: Database) -> dict[str, Any]:
    return save_message_transform_config(db, _default_message_transform_rules())


def _has_sent_resume(connection: sqlite3.Connection, session_id: str) -> bool:
    return connection.execute(
        """
        SELECT 1 FROM fj_chat_messages
        WHERE session_id = ? AND direction = 'outbound'
          AND (action_type = 'resume_sent' OR content = '附件状态更新')
        LIMIT 1
        """,
        (session_id,),
    ).fetchone() is not None


def _has_prior_condition(
    connection: sqlite3.Connection,
    *,
    session_id: str,
    condition_rule_id: str,
    rules: list[dict[str, Any]],
    occurred_at: str | None,
) -> bool:
    """判断当前消息之前是否已命中配置的条件线。"""
    condition_rule = next(
        (rule for rule in rules if str(rule.get("id") or "") == condition_rule_id),
        None,
    )
    action_type = str(condition_rule.get("action_type") or "").strip() if condition_rule else ""
    if not action_type:
        return False
    params: list[Any] = [session_id, action_type]
    time_clause = ""
    if occurred_at:
        time_clause = " AND sent_at < ?"
        params.append(occurred_at)
    return connection.execute(
        f"""
        SELECT 1 FROM fj_chat_messages
        WHERE session_id = ? AND display_kind = 'action' AND action_type = ?
        {time_clause}
        LIMIT 1
        """,
        tuple(params),
    ).fetchone() is not None


def _transform_message(
    connection: sqlite3.Connection,
    *,
    session_id: str,
    direction: str,
    message_type: str,
    content: str,
    rules: list[dict[str, Any]],
    occurred_at: str | None = None,
) -> dict[str, str] | None:
    """将平台提示转为中立动作，避免它们被视作 HR 文本消息。"""
    clean_content = content.strip()
    for rule in rules:
        if not rule.get("enabled") or rule.get("direction") != direction:
            continue
        pattern = str(rule.get("pattern") or "")
        match_mode = str(rule.get("match_mode") or "")
        matched = False
        match: re.Match[str] | None = None
        if match_mode == "exact":
            matched = clean_content == pattern.strip()
        elif match_mode == "contains":
            matched = bool(pattern and pattern in clean_content)
        elif match_mode == "regex":
            try:
                match = re.search(pattern, clean_content)
            except re.error:
                continue
            matched = match is not None
        if not matched:
            continue
        if rule.get("requires_resume_sent") and not _has_sent_resume(connection, session_id):
            continue
        condition_branch = str(rule.get("condition_branch") or "always")
        condition_rule_id = str(rule.get("condition_rule_id") or "").strip()
        has_prior_action = _has_prior_condition(
            connection,
            session_id=session_id,
            condition_rule_id=condition_rule_id,
            rules=rules,
            occurred_at=occurred_at,
        )
        if condition_branch == "if" and not has_prior_action:
            continue
        if condition_branch == "else" and has_prior_action:
            continue
        if rule.get("output_kind") == "discard":
            return None
        display_content = str(rule.get("display_content") or clean_content)
        if match is not None:
            try:
                display_content = match.expand(display_content)
            except re.error:
                display_content = str(rule.get("display_content") or clean_content)
        return {
            "message_type": "system",
            "content": display_content.strip() or clean_content,
            "display_kind": "action",
            "action_type": str(rule.get("action_type") or ""),
        }
    return {
        "message_type": message_type,
        "content": clean_content,
        "display_kind": "chat",
        "action_type": "",
    }


def save_resume_attachment_snapshot(db: Database, captured: dict[str, Any]) -> dict[str, Any]:
    """保存当前账号的附件简历快照，并返回可直接给页面使用的结果。"""
    account_uid = str(captured.get("account_uid") or "").strip()
    if not account_uid:
        raise ValueError("附件简历结果缺少当前 BOSS 账号。")
    attachments = captured.get("attachments")
    normalized_attachments = [item for item in attachments if isinstance(item, dict)] if isinstance(attachments, list) else []
    source_url = str(captured.get("url") or "")
    now = _now()
    with db.connect() as connection:
        connection.execute(
            """
            INSERT INTO fj_boss_resume_attachment_snapshots (
              account_uid, attachments_json, source_url, captured_at
            ) VALUES (?, ?, ?, ?)
            ON CONFLICT(account_uid) DO UPDATE SET
              attachments_json = excluded.attachments_json,
              source_url = excluded.source_url,
              captured_at = excluded.captured_at
            """,
            (account_uid, json.dumps(normalized_attachments, ensure_ascii=False), source_url, now),
        )
    return {
        "account_uid": account_uid,
        "attachments": normalized_attachments,
        "url": source_url,
        "target_id": str(captured.get("target_id") or ""),
        "captured_at": now,
        "saved": True,
    }


def get_latest_resume_attachment_snapshot(db: Database) -> dict[str, Any]:
    """读取最近一次成功保存的附件简历快照。"""
    with db.connect() as connection:
        snapshot = connection.execute(
            """
            SELECT account_uid, attachments_json, source_url, captured_at
            FROM fj_boss_resume_attachment_snapshots
            ORDER BY captured_at DESC
            LIMIT 1
            """
        ).fetchone()
    if snapshot is None:
        return {
            "account_uid": "",
            "attachments": [],
            "url": "",
            "target_id": "",
            "captured_at": None,
            "saved": False,
        }
    attachments = _loads(str(snapshot["attachments_json"] or "[]"), [])
    return {
        "account_uid": str(snapshot["account_uid"] or ""),
        "attachments": [item for item in attachments if isinstance(item, dict)] if isinstance(attachments, list) else [],
        "url": str(snapshot["source_url"] or ""),
        "target_id": "",
        "captured_at": str(snapshot["captured_at"] or ""),
        "saved": True,
    }


def _ensure_runtime(connection: sqlite3.Connection) -> sqlite3.Row:
    row = connection.execute(
        "SELECT * FROM fj_chat_runtime WHERE id = ?", (RUNTIME_ID,)
    ).fetchone()
    if row is not None:
        return row
    now = _now()
    connection.execute(
        """
        INSERT INTO fj_chat_runtime (
          id, listen_enabled, generation_enabled, send_enabled, direct_execution_enabled,
          trigger_mode, interval_minutes, leader_epoch, created_at, updated_at
        ) VALUES (?, 0, 0, 0, 0, 'interval', 30, 0, ?, ?)
        """,
        (RUNTIME_ID, now, now),
    )
    return connection.execute(
        "SELECT * FROM fj_chat_runtime WHERE id = ?", (RUNTIME_ID,)
    ).fetchone()


def get_runtime(db: Database) -> dict[str, Any]:
    with db.connect() as connection:
        runtime = _row(_ensure_runtime(connection)) or {}
        runtime["leaders"] = [
            _row(item) or {}
            for item in connection.execute(
                "SELECT * FROM fj_chat_leaders ORDER BY account_uid"
            ).fetchall()
        ]
        return runtime


def update_runtime(db: Database, changes: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "listen_enabled",
        "generation_enabled",
        "send_enabled",
        "direct_execution_enabled",
        "trigger_mode",
        "interval_minutes",
    }
    updates = {key: value for key, value in changes.items() if key in allowed and value is not None}
    if not updates:
        return get_runtime(db)
    for key in ("listen_enabled", "generation_enabled", "send_enabled", "direct_execution_enabled"):
        if key in updates:
            updates[key] = int(bool(updates[key]))
    updates["updated_at"] = _now()
    with db.connect() as connection:
        runtime = _ensure_runtime(connection)
        effective_mode = str(updates.get("trigger_mode", runtime["trigger_mode"]))
        effective_interval = int(updates.get("interval_minutes", runtime["interval_minutes"]))
        if effective_mode in {"immediate", "manual"}:
            updates["interval_minutes"] = 0
        elif effective_interval not in {5, 10, 30, 60}:
            raise AppError(
                status_code=422,
                error_category="INVALID_CHAT_RUNTIME",
                error_message="定时模式必须选择 5、10、30 或 60 分钟。",
            )
        assignments = ", ".join(f"{key} = ?" for key in updates)
        connection.execute(
            f"UPDATE fj_chat_runtime SET {assignments} WHERE id = ?",
            (*updates.values(), RUNTIME_ID),
        )
    return get_runtime(db)


def report_heartbeat(
    db: Database,
    executor_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    now = _now()
    with db.connect() as connection:
        runtime = _ensure_runtime(connection)
        current = connection.execute(
            "SELECT * FROM fj_chat_leaders WHERE account_uid = ?",
            (payload["account_uid"],),
        ).fetchone()
        current_expiry = _parse_time(current["lease_expires_at"]) if current else None
        current_epoch = int(current["leader_epoch"] or 0) if current else 0
        same_leader = (
            current is not None
            and current["executor_id"] == executor_id
            and current["tab_id"] == payload["tab_id"]
        )
        may_lead = (
            not current_expiry
            or current_expiry <= datetime.now(timezone.utc)
            or same_leader
            or int(payload["leader_epoch"]) > current_epoch
        )
        accepted = bool(payload["is_leader"] and may_lead)
        if accepted:
            lease = payload.get("lease_expires_at") or _after(20)
            connection.execute(
                """
                INSERT INTO fj_chat_leaders (
                  account_uid, executor_id, tab_id, leader_epoch, lease_expires_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(account_uid) DO UPDATE SET
                  executor_id = excluded.executor_id,
                  tab_id = excluded.tab_id,
                  leader_epoch = excluded.leader_epoch,
                  lease_expires_at = excluded.lease_expires_at,
                  updated_at = excluded.updated_at
                """,
                (
                    payload["account_uid"], executor_id, payload["tab_id"],
                    int(payload["leader_epoch"]), lease, now,
                ),
            )
            connection.execute(
                """
                UPDATE fj_chat_runtime
                SET leader_executor_id = ?, leader_tab_id = ?, leader_epoch = ?,
                    leader_lease_expires_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    executor_id,
                    payload["tab_id"],
                    int(payload["leader_epoch"]),
                    lease,
                    now,
                    RUNTIME_ID,
                ),
            )
        return {"accepted": accepted, "runtime": _row(connection.execute(
            "SELECT * FROM fj_chat_runtime WHERE id = ?", (RUNTIME_ID,)
        ).fetchone())}


def _resolve_job_id(connection: sqlite3.Connection, message: dict[str, Any]) -> str | None:
    # 聊天列表的 encryptJobId 与岗位表的 encrypt_job_id 是稳定的岗位关联键，优先使用它匹配。
    candidates = [message.get("encrypt_job_id"), message.get("job_id")]
    for candidate in candidates:
        if not candidate:
            continue
        row = connection.execute(
            """
            SELECT id FROM fj_boss_jobs
            WHERE id = ? OR source_job_id = ? OR encrypt_job_id = ?
            ORDER BY last_collected_at DESC LIMIT 1
            """,
            (candidate, candidate, candidate),
        ).fetchone()
        if row:
            return str(row["id"])
    return None


def _local_job_title(connection: sqlite3.Connection, job_id: str | None) -> str:
    """只返回本地岗位库中的岗位标题。"""
    if not job_id:
        return ""
    row = connection.execute(
        "SELECT title FROM fj_boss_jobs WHERE id = ?", (job_id,)
    ).fetchone()
    return str(row["title"] or "") if row is not None else ""


def _epoch_ms_to_iso(value: Any) -> str | None:
    """把 BOSS 列表中的毫秒时间戳转换为统一的 UTC 时间。"""
    try:
        milliseconds = int(value or 0)
    except (TypeError, ValueError):
        return None
    if milliseconds <= 0:
        return None
    return datetime.fromtimestamp(milliseconds / 1000, tz=timezone.utc).isoformat()


def _optional_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _friend_session_row(
    connection: sqlite3.Connection,
    *,
    account_uid: str,
    peer_uid: str,
    encrypt_job_id: str,
) -> sqlite3.Row | None:
    """按联系人和岗位查找列表同步对应的聊天会话。"""
    row = connection.execute(
        """
        SELECT * FROM fj_chat_sessions
        WHERE platform = 'boss' AND account_uid = ? AND peer_uid = ? AND encrypt_job_id = ?
        """,
        (account_uid, peer_uid, encrypt_job_id),
    ).fetchone()
    if row is not None or encrypt_job_id:
        return row
    related = connection.execute(
        """
        SELECT * FROM fj_chat_sessions
        WHERE platform = 'boss' AND account_uid = ? AND peer_uid = ?
        ORDER BY updated_at DESC
        """,
        (account_uid, peer_uid),
    ).fetchall()
    return related[0] if len(related) == 1 else None


def sync_friend_list(
    db: Database,
    *,
    account_uid: str,
    response: dict[str, Any],
    source_url: str,
) -> dict[str, Any]:
    """保存 BOSS 聊天列表，并比较本次与上次保存的最新消息编号。"""
    zp_data = response.get("zpData") if isinstance(response, dict) else None
    items = zp_data.get("result") if isinstance(zp_data, dict) else None
    if not isinstance(items, list):
        raise AppError(
            status_code=502,
            error_category="BOSS_CHAT_LIST_INVALID",
            error_message="BOSS 聊天列表响应中没有可用的联系人数据。",
        )
    account_uid = account_uid.strip()
    if not account_uid:
        raise AppError(
            status_code=502,
            error_category="BOSS_CHAT_ACCOUNT_MISSING",
            error_message="未能识别当前 BOSS 账号。",
        )

    created_count = 0
    changed_count = 0
    synced_count = 0
    synced_session_ids: list[str] = []
    created_session_ids: list[str] = []
    synced_at = _now()
    with db.connect() as connection:
        transform_rules = _message_transform_config(connection)["rules"]
        # 保存 BOSS 原始数组位置，列表展示和批量队列共用这个顺序。
        for platform_list_index, raw in enumerate(items):
            if not isinstance(raw, dict) or not raw.get("uid"):
                continue
            synced_count += 1
            peer_uid = str(raw.get("uid"))
            info = raw.get("lastMessageInfo")
            info = info if isinstance(info, dict) else {}
            encrypt_job_id = str(raw.get("encryptJobId") or "")
            encrypt_peer_uid = str(
                raw.get("encryptFriendId")
                or raw.get("encryptUid")
                or raw.get("encryptBossId")
                or ""
            )
            security_id = str(raw.get("securityId") or "")
            job_id = _resolve_job_id(
                connection,
                {
                    "job_id": raw.get("jobId"),
                    "encrypt_job_id": encrypt_job_id,
                },
            )
            existing = _friend_session_row(
                connection,
                account_uid=account_uid,
                peer_uid=peer_uid,
                encrypt_job_id=encrypt_job_id,
            )
            effective_job_id = job_id or (str(existing["job_id"] or "") if existing else "")
            job_title = _local_job_title(connection, effective_job_id)
            latest_msg_id = str(info.get("msgId") or "")
            previous_msg_id = str(existing["platform_latest_msg_id"] or "") if existing else ""
            message_changed = bool(previous_msg_id and latest_msg_id and previous_msg_id != latest_msg_id)
            if message_changed:
                changed_count += 1
            latest_message_at = _epoch_ms_to_iso(info.get("msgTime") or raw.get("lastTS"))
            latest_message_text = str(
                raw.get("lastMsg") or info.get("showText") or ""
            )
            session_id = str(existing["id"]) if existing else _id("chat_session")
            latest_direction = "outbound" if str(info.get("fromId") or "") == account_uid else "inbound"
            transformed = _transform_message(
                connection,
                session_id=session_id,
                direction=latest_direction,
                message_type="text",
                content=latest_message_text,
                rules=transform_rules,
                occurred_at=latest_message_at,
            )
            latest_display_kind = transformed["display_kind"] if transformed is not None else "chat"
            latest_message_text = transformed["content"] if transformed is not None else ""
            identity_complete = bool(encrypt_peer_uid and security_id and encrypt_job_id)
            status = "active" if identity_complete else "unsupported"
            if existing is None:
                connection.execute(
                    """
                    INSERT INTO fj_chat_sessions (
                      id, platform, account_uid, peer_uid, encrypt_peer_uid, security_id,
                      job_id, encrypt_job_id, job_title, peer_name, company_name,
                      peer_title,
                      platform_latest_msg_id, platform_latest_message_status,
                      platform_relation_type, platform_chat_status, platform_latest_message_text,
                      platform_latest_display_kind,
                      platform_latest_message_at, platform_latest_from_id, platform_latest_to_id,
                      platform_synced_at, platform_list_index, message_update_required, status, session_version,
                      created_at, updated_at
                    ) VALUES (?, 'boss', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
                    """,
                    (
                        session_id,
                        account_uid,
                        peer_uid,
                        encrypt_peer_uid,
                        security_id,
                        job_id,
                        encrypt_job_id,
                        job_title,
                        str(raw.get("name") or ""),
                        str(raw.get("brandName") or ""),
                        str(raw.get("title") or ""),
                        latest_msg_id,
                        _optional_int(info.get("status")),
                        _optional_int(raw.get("relationType")),
                        _optional_int(raw.get("chatStatus")),
                        latest_message_text,
                        latest_display_kind,
                        latest_message_at,
                        str(info.get("fromId") or ""),
                        str(info.get("toId") or ""),
                        synced_at,
                        platform_list_index,
                        int(message_changed),
                        status,
                        synced_at,
                        synced_at,
                    ),
                )
                created_count += 1
                synced_session_ids.append(session_id)
                created_session_ids.append(session_id)
                continue

            next_status = (
                "active"
                if existing["status"] == "unsupported" and identity_complete
                else existing["status"]
            )
            connection.execute(
                """
                UPDATE fj_chat_sessions SET
                  encrypt_peer_uid = CASE WHEN ? <> '' THEN ? ELSE encrypt_peer_uid END,
                  security_id = CASE WHEN ? <> '' THEN ? ELSE security_id END,
                  job_id = COALESCE(?, job_id),
                  encrypt_job_id = CASE WHEN encrypt_job_id = '' AND ? <> '' THEN ? ELSE encrypt_job_id END,
                  job_title = ?,
                  peer_name = CASE WHEN ? <> '' THEN ? ELSE peer_name END,
                  peer_title = CASE WHEN ? <> '' THEN ? ELSE peer_title END,
                  company_name = CASE WHEN ? <> '' THEN ? ELSE company_name END,
                  platform_latest_msg_id = ?,
                  platform_latest_message_status = ?,
                  platform_relation_type = ?,
                  platform_chat_status = ?,
                  platform_latest_message_text = ?,
                  platform_latest_display_kind = ?,
                  platform_latest_message_at = ?,
                  platform_latest_from_id = ?,
                  platform_latest_to_id = ?,
                  platform_synced_at = ?,
                  platform_list_index = ?,
                  message_update_required = ?,
                  status = ?,
                  updated_at = ?
                WHERE id = ?
                """,
                (
                    encrypt_peer_uid,
                    encrypt_peer_uid,
                    security_id,
                    security_id,
                    job_id,
                    encrypt_job_id,
                    encrypt_job_id,
                    job_title,
                    str(raw.get("name") or ""),
                    str(raw.get("name") or ""),
                    str(raw.get("title") or ""),
                    str(raw.get("title") or ""),
                    str(raw.get("brandName") or ""),
                    str(raw.get("brandName") or ""),
                    latest_msg_id,
                    _optional_int(info.get("status")),
                    _optional_int(raw.get("relationType")),
                    _optional_int(raw.get("chatStatus")),
                    latest_message_text,
                    latest_display_kind,
                    latest_message_at,
                    str(info.get("fromId") or ""),
                    str(info.get("toId") or ""),
                    synced_at,
                    platform_list_index,
                    int(message_changed),
                    next_status,
                    synced_at,
                    existing["id"],
                ),
            )
            synced_session_ids.append(str(existing["id"]))
        return {
            "account_uid": account_uid,
            "count": synced_count,
            "created_count": created_count,
            "changed_count": changed_count,
            "source_url": source_url,
            "synced_at": synced_at,
            # Scope Discovery 使用本次同步的稳定标识识别新会话和本批会话。
            "session_ids": synced_session_ids,
            "created_session_ids": created_session_ids,
        }


def _history_message_content(message: dict[str, Any]) -> str:
    """从历史消息的 body 中提取文本或系统消息摘要。"""
    body = message.get("body") if isinstance(message.get("body"), dict) else {}
    body_type = _optional_int(body.get("type"))
    text = str(body.get("text") or message.get("pushText") or "").strip()
    if text:
        return text
    if body_type == 8:
        job_desc = body.get("jobDesc") if isinstance(body.get("jobDesc"), dict) else {}
        title = str(job_desc.get("title") or "").strip()
        company = str(job_desc.get("company") or "").strip()
        if title and company:
            return f"岗位：{title} · {company}"
        if title:
            return f"岗位：{title}"
        return str(body.get("headTitle") or "岗位沟通卡片")
    return {
        4: "附件状态更新",
        7: "附件请求",
        12: "附件已发送",
    }.get(body_type, str(body.get("headTitle") or "系统消息"))


def _record_message_activity(
    connection: sqlite3.Connection,
    *,
    session_id: str,
    message_id: str,
    direction: str,
    occurred_at: str,
    platform_message_id: str,
    message_type: str = "text",
    content: str = "",
    display_kind: str = "chat",
    action_type: str = "",
) -> None:
    session = connection.execute(
        "SELECT job_id, company_name FROM fj_chat_sessions WHERE id = ?",
        (session_id,),
    ).fetchone()
    if session is None or not session["job_id"]:
        return
    job_id = str(session["job_id"])
    if display_kind == "chat":
        event_type = "recruiter_replied" if direction == "inbound" else "candidate_replied"
        append_job_activity_with_connection(
            connection, job_id=job_id, chat_session_id=session_id, event_type=event_type,
            occurred_at=occurred_at, source="chat", source_ref_type="chat_message",
            source_ref_id=message_id, evidence_level="direct",
            payload={"direction": direction, "platform_message_id": platform_message_id},
            dedupe_key=f"chat_message:{message_id}:{event_type}",
        )

    # 聊天动作和 HR 的明确文案直接形成事实，不依赖 AI 推断。
    rule_event = {
        "resume_sent": "resume_submitted",
        "resume_sent_confirmed": "resume_submitted",
        "resume_received": "resume_accepted",
        "resume_viewed": "resume_viewed",
    }.get(action_type)
    compact = "".join(content.split())
    if not rule_event and direction == "inbound" and display_kind == "chat":
        if any(phrase in compact for phrase in ("请发简历", "发下简历", "发一份简历", "简历发一下", "投递简历", "附件简历")):
            rule_event = "resume_requested"
        elif any(phrase in compact for phrase in ("发给业务部门看看", "发给用人部门看看", "用人部门评估", "内部再评估")):
            rule_event = "under_review"
        elif any(phrase in compact for phrase in ("约面", "安排面试", "面试时间", "面试安排")):
            rule_event = "interview_invited"
    if rule_event:
        append_job_activity_with_connection(
            connection, job_id=job_id, chat_session_id=session_id, event_type=rule_event,
            occurred_at=occurred_at, source="rule", source_ref_type="chat_message",
            source_ref_id=message_id, evidence_level="direct",
            payload={"derived_by": "chat_rule", "action_type": action_type, "message_type": message_type},
            dedupe_key=f"chat_message:{message_id}:{rule_event}:chat-rule-v2",
        )


def analyze_rule_progress(db: Database, session_id: str) -> dict[str, Any]:
    """按当前本地聊天记录重放固定规则，不调用 AI 或外部页面。"""
    with db.connect() as connection:
        session = _session_or_404(connection, session_id)
        if not session["job_id"]:
            raise AppError(409, "CHAT_JOB_UNLINKED", "请先关联本地岗位，再分析聊天规则。")
        messages = connection.execute(
            """
            SELECT id, direction, message_type, content, display_kind, action_type, sent_at, platform_message_id
            FROM fj_chat_messages WHERE session_id = ? AND display_kind <> 'discard'
            ORDER BY sent_at, rowid
            """,
            (session_id,),
        ).fetchall()
        for message in messages:
            _record_message_activity(
                connection, session_id=session_id, message_id=str(message["id"]),
                direction=str(message["direction"]), occurred_at=str(message["sent_at"]),
                platform_message_id=str(message["platform_message_id"]),
                message_type=str(message["message_type"]), content=str(message["content"]),
                display_kind=str(message["display_kind"]), action_type=str(message["action_type"]),
            )
        return {"progress": build_job_progress_with_connection(connection, str(session["job_id"]), session_id=session_id)}


def mark_manual_progress(db: Database, session_id: str, action: str) -> dict[str, Any]:
    """记录用户在会话页确认的约面或拒绝结果。"""
    with db.connect() as connection:
        session = _session_or_404(connection, session_id)
        if not session["job_id"]:
            raise AppError(409, "CHAT_JOB_UNLINKED", "请先关联本地岗位，再更新求职进展。")
        event_type = "interview_scheduled" if action == "interview_scheduled" else "rejected"
        party = "candidate" if action == "candidate_rejected" else "recruiter"
        append_job_activity_with_connection(
            connection, job_id=str(session["job_id"]), chat_session_id=session_id,
            event_type=event_type, occurred_at=_now(), source="manual",
            source_ref_type="chat_session", source_ref_id=session_id,
            evidence_level="direct",
            payload={
                "waiting_on": "none",
                **({"rejection_party": party, "rejection_reason_source": "unknown", "rejection_reason_category": "unknown"} if event_type == "rejected" else {}),
            },
            dedupe_key=f"chat_session:{session_id}:manual:{action}",
        )
        return {"progress": build_job_progress_with_connection(connection, str(session["job_id"]), session_id=session_id)}


def refresh_session_history(db: Database, session_id: str) -> dict[str, Any]:
    """复用自动代聊批量任务的单会话历史获取与保存顺序。"""
    with db.connect() as connection:
        session = _session_or_404(connection, session_id)
    captured = boss_scraper_service.capture_chat_history(
        boss_id=str(session["encrypt_peer_uid"] or ""),
        security_id=str(session["security_id"] or ""),
    )
    return sync_history_messages(
        db,
        session_id=session_id,
        messages=list(captured.get("messages") or []),
        history_has_more=bool(captured.get("has_more")),
        history_next_cursor=str(captured.get("next_cursor") or ""),
    )


def force_refresh_session_history(db: Database, session_id: str) -> dict[str, Any]:
    """绕过本地更新判断，直接读取页面消息并按当前规则刷新展示。"""
    result = refresh_session_history(db, session_id)
    retransformed = retransform_session_messages(db, session_id)
    return {
        **result,
        "retransformed_count": retransformed["updated_count"],
        "discarded_count": retransformed["discarded_count"],
    }


def _history_client_mid(raw: dict[str, Any]) -> str:
    """读取历史记录可能携带的客户端消息 ID。"""
    for key in ("cmid", "clientMid", "client_mid"):
        value = raw.get(key)
        if value not in (None, ""):
            return str(value)
    return ""


def _history_message_status(raw: dict[str, Any]) -> int | None:
    """读取历史消息的送达和已读状态。"""
    status = _optional_int(raw.get("status"))
    return status if status in {0, 1, 2} else None


def _find_provisional_message_for_history(
    connection: sqlite3.Connection,
    *,
    session_id: str,
    platform_message_id: str,
    direction: str,
    client_mid: str,
) -> sqlite3.Row | None:
    """根据同一 client_mid 将临时消息关联到 BOSS 官方 mid。"""
    candidate_client_mids = [client_mid] if client_mid else []
    if direction == "outbound" and not candidate_client_mids:
        action = connection.execute(
            """
            SELECT client_mid FROM fj_chat_send_actions
            WHERE session_id = ? AND platform_message_id = ? AND client_mid <> ''
            ORDER BY updated_at DESC LIMIT 1
            """,
            (session_id, platform_message_id),
        ).fetchone()
        if action is not None:
            candidate_client_mids.append(str(action["client_mid"]))

    for candidate_client_mid in candidate_client_mids:
        row = connection.execute(
            """
            SELECT * FROM fj_chat_messages
            WHERE session_id = ?
              AND client_mid = ?
              AND platform_message_id <> ?
              AND (platform_message_id = client_mid OR platform_message_id LIKE 'assistant:%')
            ORDER BY rowid DESC LIMIT 1
            """,
            (session_id, candidate_client_mid, platform_message_id),
        ).fetchone()
        if row is not None:
            return row
    return None


def sync_history_messages(
    db: Database,
    *,
    session_id: str,
    messages: list[dict[str, Any]],
    history_has_more: bool | None = None,
    history_next_cursor: str | None = None,
) -> dict[str, Any]:
    """保存一页历史消息，按平台 mid 去重并更新会话分页状态。"""
    inserted_count = 0
    reconciled_count = 0
    now = _now()
    with db.connect() as connection:
        session = _session_or_404(connection, session_id)
        account_uid = str(session["account_uid"] or "")
        transform_rules = _message_transform_config(connection)["rules"]
        processed_platform_message_ids: set[str] = set()
        ordered_messages = sorted(
            (raw for raw in messages if isinstance(raw, dict) and raw.get("mid")),
            key=lambda raw: (str(raw.get("time") or ""), str(raw.get("mid") or "")),
        )
        for raw in ordered_messages:
            message_id = str(raw["mid"])
            processed_platform_message_ids.add(message_id)
            sender = raw.get("from") if isinstance(raw.get("from"), dict) else {}
            receiver = raw.get("to") if isinstance(raw.get("to"), dict) else {}
            sender_uid = str(sender.get("uid") or "")
            receiver_uid = str(receiver.get("uid") or "")
            direction = "outbound" if sender_uid == account_uid else "inbound"
            body = raw.get("body") if isinstance(raw.get("body"), dict) else {}
            body_type = _optional_int(body.get("type"))
            message_type = "text" if body_type == 1 else "system"
            sent_at = _epoch_ms_to_iso(raw.get("time")) or now
            message_status = _history_message_status(raw)
            transformed = _transform_message(
                connection,
                session_id=session_id,
                direction=direction,
                message_type=message_type,
                content=_history_message_content(raw),
                rules=transform_rules,
                occurred_at=sent_at,
            )
            if transformed is None:
                # 命中过滤规则的历史消息需同步隐藏已保存的旧展示记录。
                connection.execute(
                    """
                    UPDATE fj_chat_messages
                    SET message_type = 'system', content = '', display_kind = 'discard', action_type = '',
                        status = COALESCE(?, status), observed_at = ?
                    WHERE session_id = ? AND platform_message_id = ?
                    """,
                    (message_status, now, session_id, message_id),
                )
                continue
            client_mid = _history_client_mid(raw)
            existing = connection.execute(
                """
                SELECT id FROM fj_chat_messages
                WHERE session_id = ? AND platform_message_id = ?
                LIMIT 1
                """,
                (session_id, message_id),
            ).fetchone()
            provisional = _find_provisional_message_for_history(
                connection,
                session_id=session_id,
                platform_message_id=message_id,
                direction=direction,
                client_mid=client_mid,
            )
            if existing is not None and provisional is None:
                # 历史消息按当前规则重写展示语义，并补充平台返回的状态。
                connection.execute(
                    """
                    UPDATE fj_chat_messages
                    SET message_type = ?, content = ?, display_kind = ?, action_type = ?,
                        status = COALESCE(?, status), observed_at = ?
                    WHERE id = ?
                    """,
                    (
                        transformed["message_type"],
                        transformed["content"],
                        transformed["display_kind"],
                        transformed["action_type"],
                        message_status,
                        now,
                        existing["id"],
                    ),
                )
                continue
            if provisional is not None:
                if existing is not None:
                    # 旧版本已插入两行时，保留临时行的本地引用并移除官方 mid 的重复行。
                    connection.execute(
                        """
                        UPDATE fj_chat_reply_tasks SET based_on_message_id = ?
                        WHERE based_on_message_id = ?
                        """,
                        (provisional["id"], existing["id"]),
                    )
                    connection.execute(
                        """
                        UPDATE fj_chat_sessions
                        SET latest_message_id = CASE WHEN latest_message_id = ? THEN ? ELSE latest_message_id END,
                            latest_inbound_message_id = CASE WHEN latest_inbound_message_id = ? THEN ? ELSE latest_inbound_message_id END
                        WHERE id = ?
                        """,
                        (existing["id"], provisional["id"], existing["id"], provisional["id"], session_id),
                    )
                    connection.execute(
                        """
                        UPDATE fj_job_activity_events SET source_ref_id = ?
                        WHERE source_ref_type = 'chat_message' AND source_ref_id = ?
                        """,
                        (provisional["id"], existing["id"]),
                    )
                    connection.execute(
                        "DELETE FROM fj_chat_messages WHERE id = ?",
                        (existing["id"],),
                    )
                # 官方 mid 到达时只回填同一条临时消息，保留本地消息和活动记录的关联。
                connection.execute(
                    """
                    UPDATE fj_chat_messages
                    SET platform_message_id = ?, direction = ?, message_type = ?, content = ?,
                        display_kind = ?, action_type = ?, status = ?, sender_uid = ?, receiver_uid = ?,
                        client_mid = CASE WHEN ? <> '' THEN ? ELSE client_mid END,
                        sent_at = ?, observed_at = ?, raw_meta_json = ?
                    WHERE id = ?
                    """,
                    (
                        message_id,
                        direction,
                        transformed["message_type"],
                        transformed["content"],
                        transformed["display_kind"],
                        transformed["action_type"],
                        message_status,
                        sender_uid,
                        receiver_uid,
                        client_mid,
                        client_mid,
                        sent_at,
                        now,
                        json.dumps({"history": True, "evidence_source": "history_record", "platform_type": raw.get("type")}, ensure_ascii=False),
                        provisional["id"],
                    ),
                )
                reconciled_count += 1
                continue
            local_message_id = _id("chat_message")
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO fj_chat_messages (
                  id, session_id, platform_message_id, direction, message_type,
                  content, display_kind, action_type, status, sender_uid, receiver_uid, client_mid, source,
                  sent_at, observed_at, raw_meta_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'websocket', ?, ?, ?, ?)
                """,
                (
                    local_message_id,
                    session_id,
                    message_id,
                    direction,
                    transformed["message_type"],
                    transformed["content"],
                    transformed["display_kind"],
                    transformed["action_type"],
                    message_status,
                    sender_uid,
                    receiver_uid,
                    client_mid,
                    sent_at,
                    now,
                    json.dumps({"history": True, "evidence_source": "history_record", "platform_type": raw.get("type")}, ensure_ascii=False),
                    now,
                ),
            )
            inserted_count += int(cursor.rowcount > 0)
            if cursor.rowcount > 0:
                _record_message_activity(
                    connection, session_id=session_id, message_id=local_message_id,
                    direction=direction, occurred_at=sent_at, platform_message_id=message_id,
                    message_type=transformed["message_type"], content=transformed["content"],
                    display_kind=transformed["display_kind"], action_type=transformed["action_type"],
                )
                if direction == "outbound" and transformed["display_kind"] == "chat":
                    observe_outbound_chat_message(connection, message_id=local_message_id)

        latest = connection.execute(
            """
            SELECT * FROM fj_chat_messages
            WHERE session_id = ?
            ORDER BY sent_at DESC, rowid DESC LIMIT 1
            """,
            (session_id,),
        ).fetchone()
        latest_inbound = connection.execute(
            """
            SELECT id FROM fj_chat_messages
            WHERE session_id = ? AND direction = 'inbound' AND display_kind = 'chat'
            ORDER BY sent_at DESC, rowid DESC LIMIT 1
            """,
            (session_id,),
        ).fetchone()
        current_platform_msg_id = str(session["platform_latest_msg_id"] or "")
        current_message_loaded = bool(
            current_platform_msg_id in processed_platform_message_ids
            or (current_platform_msg_id and connection.execute(
                "SELECT 1 FROM fj_chat_messages WHERE session_id = ? AND platform_message_id = ? LIMIT 1",
                (session_id, current_platform_msg_id),
            ).fetchone())
        )
        next_version = int(session["session_version"] or 0) + inserted_count
        assignments = [
            "session_version = ?",
            "latest_message_id = ?",
            "latest_inbound_message_id = ?",
            "last_message_at = ?",
            "message_update_required = ?",
            "updated_at = ?",
        ]
        params: list[Any] = [
            next_version,
            latest["id"] if latest else session["latest_message_id"],
            latest_inbound["id"] if latest_inbound else session["latest_inbound_message_id"],
            latest["sent_at"] if latest else session["last_message_at"],
            int(bool(current_platform_msg_id and not current_message_loaded)),
            now,
        ]
        if history_has_more is not None:
            assignments.extend([
                "history_has_more = ?",
                "history_next_cursor = ?",
            ])
            params.extend([
                int(history_has_more),
                history_next_cursor if history_has_more and history_next_cursor else "",
            ])
        connection.execute(
            f"UPDATE fj_chat_sessions SET {', '.join(assignments)} WHERE id = ?",
            (*params, session_id),
        )
        updated_session = connection.execute(
            "SELECT history_has_more FROM fj_chat_sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
        return {
            "session_id": session_id,
            "fetched_count": len(messages),
            "inserted_count": inserted_count,
            "reconciled_count": reconciled_count,
            "message_update_required": bool(current_platform_msg_id and not current_message_loaded),
            "has_more": bool(updated_session["history_has_more"]),
        }


def _find_or_create_session(
    connection: sqlite3.Connection,
    *,
    account_uid: str,
    message: dict[str, Any],
) -> sqlite3.Row:
    encrypt_job_id = str(message.get("encrypt_job_id") or "")
    row = connection.execute(
        """
        SELECT * FROM fj_chat_sessions
        WHERE platform = 'boss' AND account_uid = ? AND peer_uid = ? AND encrypt_job_id = ?
        """,
        (account_uid, message["peer_uid"], encrypt_job_id),
    ).fetchone()
    if row is None:
        related = connection.execute(
            """
            SELECT * FROM fj_chat_sessions
            WHERE platform = 'boss' AND account_uid = ? AND peer_uid = ?
            ORDER BY updated_at DESC
            """,
            (account_uid, message["peer_uid"]),
        ).fetchall()
        if encrypt_job_id:
            unresolved = [item for item in related if not item["encrypt_job_id"]]
            if len(unresolved) == 1:
                row = unresolved[0]
        elif len(related) == 1:
            # 只有一个已知岗位会话时，可用后续消息补齐同一会话，避免先后创建两个记录。
            row = related[0]
    now = _now()
    job_id = _resolve_job_id(connection, message)
    job_title = _local_job_title(connection, job_id)
    identity_complete = bool(
        encrypt_job_id
        and message.get("encrypt_peer_uid")
        and message.get("security_id")
    )
    if row is None:
        session_id = _id("chat_session")
        connection.execute(
            """
            INSERT INTO fj_chat_sessions (
              id, platform, account_uid, peer_uid, encrypt_peer_uid, security_id,
              job_id, encrypt_job_id, job_title, peer_name, company_name,
              status, session_version, created_at, updated_at
            ) VALUES (?, 'boss', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
            """,
            (
                session_id,
                account_uid,
                message["peer_uid"],
                message.get("encrypt_peer_uid") or "",
                message.get("security_id") or "",
                job_id,
                encrypt_job_id,
                job_title,
                message.get("peer_name") or "",
                message.get("company_name") or "",
                "active" if identity_complete else "unsupported",
                now,
                now,
            ),
        )
        return connection.execute(
            "SELECT * FROM fj_chat_sessions WHERE id = ?", (session_id,)
        ).fetchone()

    # 观察到更完整的会话资料时补齐，不覆盖已有的有效字段。
    connection.execute(
        """
        UPDATE fj_chat_sessions SET
          encrypt_peer_uid = CASE WHEN ? <> '' THEN ? ELSE encrypt_peer_uid END,
          security_id = CASE WHEN ? <> '' THEN ? ELSE security_id END,
          encrypt_job_id = CASE WHEN encrypt_job_id = '' AND ? <> '' THEN ? ELSE encrypt_job_id END,
          job_id = COALESCE(?, job_id),
          job_title = CASE WHEN ? IS NOT NULL THEN ? ELSE job_title END,
          peer_name = CASE WHEN ? <> '' THEN ? ELSE peer_name END,
          company_name = CASE WHEN ? <> '' THEN ? ELSE company_name END,
          status = CASE
            WHEN status = 'unsupported' AND ? <> '' AND ? <> '' AND ? <> '' THEN 'active'
            ELSE status
          END,
          updated_at = ?
        WHERE id = ?
        """,
        (
            message.get("encrypt_peer_uid") or "",
            message.get("encrypt_peer_uid") or "",
            message.get("security_id") or "",
            message.get("security_id") or "",
            encrypt_job_id,
            encrypt_job_id,
            job_id,
            job_id,
            job_title,
            message.get("peer_name") or "",
            message.get("peer_name") or "",
            message.get("company_name") or "",
            message.get("company_name") or "",
            encrypt_job_id,
            message.get("encrypt_peer_uid") or "",
            message.get("security_id") or "",
            now,
            row["id"],
        ),
    )
    return connection.execute(
        "SELECT * FROM fj_chat_sessions WHERE id = ?", (row["id"],)
    ).fetchone()


def _queue_reply_task(
    connection: sqlite3.Connection,
    session: sqlite3.Row,
    message_id: str,
    trigger_source: str,
) -> str | None:
    if session["status"] != "active":
        return None
    now = _now()
    in_flight = connection.execute(
        """
        SELECT 1 FROM fj_chat_send_actions a
        JOIN fj_chat_reply_tasks t ON t.id = a.reply_task_id
        WHERE t.session_id = ? AND a.status = 'dispatching'
        LIMIT 1
        """,
        (session["id"],),
    ).fetchone()
    if in_flight:
        connection.execute(
            "UPDATE fj_chat_sessions SET status = 'active', updated_at = ? WHERE id = ?",
            (now, session["id"]),
        )
        return None
    pending = connection.execute(
        """
        SELECT * FROM fj_chat_reply_tasks
        WHERE session_id = ? AND is_draft = 1 AND status = 'pending_generation'
        ORDER BY created_at DESC LIMIT 1
        """,
        (session["id"],),
    ).fetchone()
    # 已开始生成或等待确认的旧草稿立即失效；防抖窗口内的待生成任务直接延后。
    connection.execute(
        """
        UPDATE fj_chat_reply_tasks
        SET status = 'stale', cancelled_at = ?, updated_at = ?
        WHERE session_id = ?
          AND is_draft = 1
          AND status IN ('generating', 'awaiting_review', 'confirmed')
        """,
        (now, now, session["id"]),
    )
    generation_due_at = _after(REPLY_DEBOUNCE_SECONDS)
    if pending is not None:
        input_message_ids = _loads(pending["input_message_ids_json"], [])
        if message_id not in input_message_ids:
            input_message_ids.append(message_id)
        connection.execute(
            """
            UPDATE fj_chat_reply_tasks
            SET trigger_source = ?, based_on_message_id = ?, based_on_session_version = ?,
                generation_due_at = ?, input_message_ids_json = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                trigger_source,
                message_id,
                int(session["session_version"]),
                generation_due_at,
                json.dumps(input_message_ids, ensure_ascii=False),
                now,
                pending["id"],
            ),
        )
        return str(pending["id"])
    task_id = _id("chat_reply")
    connection.execute(
        """
        INSERT INTO fj_chat_reply_tasks (
          id, session_id, trigger_source, is_draft, status, based_on_message_id,
          based_on_session_version, generation_due_at, input_message_ids_json,
          created_at, updated_at
        ) VALUES (?, ?, ?, 1, 'pending_generation', ?, ?, ?, ?, ?, ?)
        """,
        (
            task_id,
            session["id"],
            trigger_source,
            message_id,
            int(session["session_version"]),
            generation_due_at,
            json.dumps([message_id], ensure_ascii=False),
            now,
            now,
        ),
    )
    return task_id


def _cancel_session_send_actions(
    connection: sqlite3.Connection,
    session_id: str,
    status_code: str,
) -> None:
    now = _now()
    connection.execute(
        """
        UPDATE fj_chat_send_actions
        SET status = 'cancelled', outcome = NULL, status_code = ?,
            error_message = '会话已暂停或由用户接管', completed_at = ?, updated_at = ?,
            lease_expires_at = NULL, canonical_status = 'cancelled',
            canonical_updated_at = ?, canonical_reason = '会话已暂停或由用户接管'
        WHERE session_id = ? AND status IN ('queued', 'leased')
        """,
        (status_code, now, now, now, session_id),
    )
    # 已进入页面发送边界的动作只收口为未知，禁止再次领取和自动重发。
    connection.execute(
        """
        UPDATE fj_chat_send_actions
        SET status = 'unknown', outcome = 'unknown', status_code = ?,
            error_message = '接管发生在发送边界内，请人工核对 BOSS 会话',
            completed_at = ?, updated_at = ?, lease_expires_at = NULL,
            canonical_status = 'unknown', canonical_updated_at = ?,
            canonical_reason = '接管发生在发送边界内'
        WHERE session_id = ? AND status = 'dispatching'
        """,
        (status_code, now, now, now, session_id),
    )


def ingest_events(
    db: Database,
    executor_id: str,
    events: list[dict[str, Any]],
) -> dict[str, Any]:
    accepted = 0
    duplicates = 0
    ignored = 0
    queued_task_ids: list[str] = []
    with db.connect() as connection:
        runtime = _ensure_runtime(connection)
        transform_rules = _message_transform_config(connection)["rules"]
        trigger = {
            "immediate": "realtime",
            "interval": "interval",
            "manual": "manual",
        }.get(str(runtime["trigger_mode"]), "interval")
        for event in events:
            if event["event_type"] == "message" and not runtime["listen_enabled"]:
                ignored += 1
                continue
            try:
                event_payload = event.get("payload") or {}
                if event["event_type"] == "message":
                    event_message = event.get("message") or {}
                    event_payload = {
                        "platform_message_id": event_message.get("platform_message_id") or "",
                        "direction": event_message.get("direction") or "",
                        "message_type": event_message.get("message_type") or "unknown",
                        "frame_origin": event_message.get("frame_origin") or "remote_message",
                        "evidence_source": event_message.get("evidence_source") or "remote_message",
                    }
                connection.execute(
                    """
                    INSERT INTO fj_chat_events (
                      id, executor_id, event_id, event_type, account_uid,
                      leader_epoch, payload_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        _id("chat_event"),
                        executor_id,
                        event["event_id"],
                        event["event_type"],
                        event["account_uid"],
                        int(event.get("leader_epoch") or 0),
                        json.dumps(event_payload, ensure_ascii=False),
                        _now(),
                    ),
                )
            except sqlite3.IntegrityError:
                duplicates += 1
                continue
            accepted += 1
            if event["event_type"] != "message":
                continue
            message = event.get("message") or {}
            evidence_source = str(message.get("evidence_source") or "remote_message")
            if evidence_source == "local_transport_write":
                # 本机 WebSocket.send 仅能证明写入尝试；不创建聊天消息、更不参与成功校正。
                action = connection.execute(
                    """
                    SELECT a.id FROM fj_chat_send_actions a
                    JOIN fj_chat_sessions s ON s.id = a.session_id
                    WHERE s.account_uid = ? AND a.client_mid = ?
                    ORDER BY a.updated_at DESC LIMIT 1
                    """,
                    (event["account_uid"], message.get("client_mid") or ""),
                ).fetchone()
                if action is not None:
                    record_execution_evidence_with_connection(
                        connection,
                        action_ref_type="chat_send_action",
                        action_ref_id=str(action["id"]),
                        evidence_type="transport_write_observed",
                        source="local_websocket",
                        source_ref_type="chat_event",
                        source_ref_id=str(event["event_id"]),
                        observed_at=str(message.get("observed_at") or _now()),
                        confidence=0.5,
                        evidence_level="weak_inferred",
                        payload={"confirmed": False, "client_mid": str(message.get("client_mid") or "")},
                        dedupe_key=f"chat_event:{event['event_id']}:transport_write",
                    )
                continue
            if evidence_source == "message_sync":
                # 字段号和确认语义仍待 native trace；只保存 clientMid/serverMid 关联，不提升成功。
                action = connection.execute(
                    """
                    SELECT a.id FROM fj_chat_send_actions a
                    JOIN fj_chat_sessions s ON s.id = a.session_id
                    WHERE s.account_uid = ? AND a.client_mid = ?
                    ORDER BY a.updated_at DESC LIMIT 1
                    """,
                    (event["account_uid"], message.get("client_mid") or ""),
                ).fetchone()
                if action is not None:
                    record_execution_evidence_with_connection(
                        connection,
                        action_ref_type="chat_send_action",
                        action_ref_id=str(action["id"]),
                        evidence_type="message_sync_observed",
                        source="remote_websocket",
                        source_ref_type="chat_event",
                        source_ref_id=str(event["event_id"]),
                        observed_at=str(message.get("observed_at") or _now()),
                        confidence=0.7,
                        evidence_level="strong_inferred",
                        payload={
                            "confirmed": False,
                            "reference_only": True,
                            "client_mid": str(message.get("client_mid") or ""),
                            "server_mid": str(message.get("server_mid") or ""),
                        },
                        dedupe_key=f"chat_event:{event['event_id']}:message_sync",
                    )
                    connection.execute(
                        """
                        UPDATE fj_chat_send_actions SET platform_message_id = ?, updated_at = ?
                        WHERE id = ? AND (platform_message_id IS NULL OR platform_message_id = '')
                        """,
                        (message.get("server_mid") or "", _now(), action["id"]),
                    )
                continue
            session = _find_or_create_session(
                connection,
                account_uid=event["account_uid"],
                message=message,
            )
            transformed = _transform_message(
                connection,
                session_id=str(session["id"]),
                direction=str(message.get("direction") or "inbound"),
                message_type=str(message.get("message_type") or "text"),
                content=str(message.get("content") or ""),
                rules=transform_rules,
                occurred_at=str(message.get("sent_at") or "") or None,
            )
            if transformed is None:
                ignored += 1
                continue
            message = {
                **message,
                **transformed,
            }
            stored_raw_meta = dict(message.get("raw_meta") or {})
            stored_raw_meta.update({
                "frame_origin": message.get("frame_origin") or "remote_message",
                "evidence_source": evidence_source,
                "server_mid": message.get("server_mid") or "",
            })
            assistant_echo = None
            if message.get("direction") == "outbound" and message.get("client_mid"):
                assistant_echo = connection.execute(
                    """
                    SELECT id FROM fj_chat_messages
                    WHERE session_id = ? AND client_mid = ? AND source = 'assistant'
                    LIMIT 1
                    """,
                    (session["id"], message.get("client_mid")),
                ).fetchone()
            message_id = str(assistant_echo["id"]) if assistant_echo is not None else _id("chat_message")
            try:
                if assistant_echo is not None:
                    # 将发送完成时的占位消息升级为平台实际回显，保留稳定的本地消息 ID。
                    connection.execute(
                        """
                        UPDATE fj_chat_messages
                        SET platform_message_id = ?, direction = ?, message_type = ?, content = ?,
                            display_kind = ?, action_type = ?, sender_uid = ?, receiver_uid = ?, source = 'assistant', sent_at = ?,
                            observed_at = ?, raw_meta_json = ?
                        WHERE id = ?
                        """,
                        (
                            message["platform_message_id"],
                            message["direction"],
                            message.get("message_type") or "text",
                            message.get("content") or "",
                            message.get("display_kind") or "chat",
                            message.get("action_type") or "",
                            message.get("sender_uid") or "",
                            message.get("receiver_uid") or "",
                            message["sent_at"],
                            message["observed_at"],
                            json.dumps(stored_raw_meta, ensure_ascii=False),
                            message_id,
                        ),
                    )
                else:
                    connection.execute(
                        """
                        INSERT INTO fj_chat_messages (
                          id, session_id, platform_message_id, direction, message_type,
                          content, display_kind, action_type, sender_uid, receiver_uid, client_mid, source,
                          sent_at, observed_at, raw_meta_json, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            message_id,
                            session["id"],
                            message["platform_message_id"],
                            message["direction"],
                            message.get("message_type") or "text",
                            message.get("content") or "",
                            message.get("display_kind") or "chat",
                            message.get("action_type") or "",
                            message.get("sender_uid") or "",
                            message.get("receiver_uid") or "",
                            message.get("client_mid") or "",
                            message.get("source") or "websocket",
                            message["sent_at"],
                            message["observed_at"],
                            json.dumps(stored_raw_meta, ensure_ascii=False),
                            _now(),
                        ),
                    )
            except sqlite3.IntegrityError:
                duplicates += 1
                continue
            _record_message_activity(
                connection, session_id=str(session["id"]), message_id=message_id,
                direction=str(message["direction"]), occurred_at=str(message["sent_at"]),
                platform_message_id=str(message["platform_message_id"]),
                message_type=str(message.get("message_type") or "text"),
                content=str(message.get("content") or ""),
                display_kind=str(message.get("display_kind") or "chat"),
                action_type=str(message.get("action_type") or ""),
            )
            if message["direction"] == "outbound" and message.get("display_kind") == "chat":
                observe_outbound_chat_message(
                    connection,
                    message_id=message_id,
                    observed_account_uid=str(event["account_uid"]),
                )
            next_version = int(session["session_version"]) + 1
            inbound_id = (
                message_id
                if message["direction"] == "inbound" and message.get("display_kind") == "chat"
                else session["latest_inbound_message_id"]
            )
            next_status = session["status"]
            connection.execute(
                """
                UPDATE fj_chat_sessions
                SET status = ?, session_version = ?, latest_message_id = ?,
                    latest_inbound_message_id = ?, last_message_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    next_status,
                    next_version,
                    message_id,
                    inbound_id,
                    message["sent_at"],
                    _now(),
                    session["id"],
                ),
            )
            if (
                message["direction"] == "inbound"
                and message.get("display_kind") == "chat"
                and message.get("message_type", "text") == "text"
            ):
                refreshed = connection.execute(
                    "SELECT * FROM fj_chat_sessions WHERE id = ?", (session["id"],)
                ).fetchone()
                task_id = _queue_reply_task(connection, refreshed, message_id, trigger)
                if task_id:
                    queued_task_ids.append(task_id)
    return {
        "accepted": accepted,
        "duplicates": duplicates,
        "ignored": ignored,
        "queued_task_ids": list(dict.fromkeys(queued_task_ids)),
    }


def _session_or_404(connection: sqlite3.Connection, session_id: str) -> sqlite3.Row:
    row = connection.execute(
        "SELECT * FROM fj_chat_sessions WHERE id = ?", (session_id,)
    ).fetchone()
    if row is None:
        raise AppError(status_code=404, error_category="CHAT_SESSION_NOT_FOUND", error_message="聊天会话不存在。")
    return row


def _task_or_404(connection: sqlite3.Connection, task_id: str) -> sqlite3.Row:
    row = connection.execute(
        "SELECT * FROM fj_chat_reply_tasks WHERE id = ?", (task_id,)
    ).fetchone()
    if row is None:
        raise AppError(status_code=404, error_category="CHAT_REPLY_NOT_FOUND", error_message="回复任务不存在。")
    return row


def _session_payload(
    row: sqlite3.Row,
    connection: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    payload = _row(row) or {}
    if connection is not None:
        # 聊天侧岗位标题不参与页面展示，始终从本地岗位关联重新计算。
        payload["job_title"] = ""
        history = None
        if payload.get("job_id"):
            history = connection.execute(
                "SELECT id, title FROM fj_boss_jobs WHERE id = ?", (payload["job_id"],)
            ).fetchone()
        if history is None and payload.get("encrypt_job_id"):
            history = connection.execute(
                "SELECT id, title FROM fj_boss_jobs WHERE encrypt_job_id = ? LIMIT 1",
                (payload["encrypt_job_id"],),
            ).fetchone()
        if history is not None:
            payload["job_id"] = str(history["id"])
            payload["job_title"] = str(history["title"] or "")
        if "attention_status" not in payload:
            attention = connection.execute(
                """
                SELECT attention_status, display_label, recommended_action, reason, priority, updated_at
                FROM fj_chat_attention_states
                WHERE session_id = ?
                """,
                (payload.get("id"),),
            ).fetchone()
            if attention is not None:
                payload["attention_status"] = str(attention["attention_status"] or "")
                payload["attention_label"] = str(attention["display_label"] or "")
                payload["attention_action"] = str(attention["recommended_action"] or "")
                payload["attention_reason"] = str(attention["reason"] or "")
                payload["attention_priority"] = int(attention["priority"] or 0)
                payload["attention_updated_at"] = str(attention["updated_at"] or "")
        if payload.get("job_id"):
            payload["progress"] = build_job_progress_with_connection(
                connection,
                str(payload["job_id"]),
                session_id=str(payload.get("id") or ""),
            )
            attention_status, attention_label = _effective_attention_from_progress(
                payload["progress"], str(payload.get("attention_status") or "")
            )
            if attention_status:
                payload["attention_status"] = attention_status
                payload["attention_label"] = attention_label
    payload["identity_state"] = (
        "ready"
        if payload.get("encrypt_peer_uid") and payload.get("security_id") and payload.get("encrypt_job_id")
        else "incomplete"
    )
    payload["job_context_state"] = "linked" if payload.get("job_id") else "unlinked"
    return payload


def _effective_attention_from_progress(
    progress: dict[str, Any] | None, fallback_status: str
) -> tuple[str, str]:
    """聊天事实优先于旧分析待办，避免简历状态与左侧提示冲突。"""
    if not progress:
        return fallback_status, ""
    stage = str(progress.get("stage") or "")
    outcome = progress.get("outcome") or {}
    if stage == "rejected":
        party = str(outcome.get("rejection_party") or "")
        if party == "candidate":
            return "no_action", "无需处理"
        reason_source = str(outcome.get("rejection_reason_source") or "unknown")
        reason_category = str(outcome.get("rejection_reason_category") or "unknown")
        if reason_source == "unknown" or reason_category in {"unknown", "fit"}:
            return "needs_rejection_reason", "建议询问"
        return "no_action", "无需处理"
    delivery = str((progress.get("resume_delivery") or {}).get("status") or "not_started")
    if stage == "resume_requested" and delivery in {"not_started", "pending_confirmation", "queued"}:
        return "needs_resume", "待发简历"
    if delivery == "sending":
        return "waiting", "简历发送中"
    if delivery in {"awaiting_observation", "sent", "received", "viewed"}:
        return "waiting", "等待 HR"
    if delivery == "withdrawn":
        waiting_on = str(progress.get("waiting_on") or "unknown")
        if waiting_on == "candidate":
            return "needs_reply", "待回复"
        if waiting_on == "recruiter":
            return "waiting", "等待 HR"
        return "no_action", "无需处理"
    return fallback_status, ""


def list_sessions(
    db: Database,
    *,
    status: str | None = None,
    account_uid: str | None = None,
    attention: str | None = None,
    waiting_on: str | None = None,
    query: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    with db.connect() as connection:
        _ensure_runtime(connection)
        params: list[Any] = []
        conditions: list[str] = []
        if status:
            conditions.append("s.status = ?")
            params.append(status)
        if account_uid:
            conditions.append("s.account_uid = ?")
            params.append(account_uid)
        if attention == "actionable":
            conditions.append(
                "att.attention_status IN ("
                "'needs_reply', 'needs_resume', 'needs_followup', "
                "'needs_rejection_reason', 'needs_interview_confirm', 'needs_info')"
            )
        elif attention:
            conditions.append("att.attention_status = ?")
            params.append(attention)
        if waiting_on:
            conditions.append("pipeline.waiting_on = ?")
            params.append(waiting_on)
        if query:
            conditions.append(
                "(s.peer_name LIKE ? OR s.company_name LIKE ? OR s.job_title LIKE ? "
                "OR m.content LIKE ? OR s.platform_latest_message_text LIKE ?)"
            )
            like = f"%{query.strip()}%"
            params.extend([like, like, like, like, like])
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        params.extend([limit, offset])
        rows = connection.execute(
            f"""
            SELECT s.*,
              CASE
                WHEN m.platform_message_id = s.platform_latest_msg_id THEN m.content
                WHEN s.platform_latest_msg_id <> '' THEN s.platform_latest_message_text
                ELSE m.content
              END AS latest_message_content,
              CASE
                WHEN s.platform_latest_msg_id <> '' AND s.platform_latest_from_id = s.account_uid THEN 'outbound'
                WHEN s.platform_latest_msg_id <> '' AND s.platform_latest_to_id = s.account_uid THEN 'inbound'
                WHEN m.id IS NOT NULL THEN m.direction
                ELSE NULL
              END AS latest_message_direction,
              CASE
                WHEN m.platform_message_id = s.platform_latest_msg_id THEN m.display_kind
                WHEN s.platform_latest_msg_id <> '' THEN s.platform_latest_display_kind
                ELSE 'chat'
              END AS latest_message_display_kind,
              s.platform_latest_msg_id AS latest_platform_msg_id,
              s.platform_latest_message_status,
              s.platform_relation_type,
              s.platform_chat_status,
              s.platform_latest_message_at,
              s.platform_synced_at,
              s.message_update_required,
              EXISTS(
                SELECT 1 FROM fj_chat_messages local_message WHERE local_message.session_id = s.id
              ) AS has_local_messages,
              t.id AS reply_task_id,
              t.status AS reply_task_status,
              t.draft_text AS reply_draft_text,
              t.final_text AS reply_final_text,
              att.attention_status,
              att.display_label AS attention_label,
              att.recommended_action AS attention_action,
              att.reason AS attention_reason,
              att.priority AS attention_priority,
              att.updated_at AS attention_updated_at,
              (
                SELECT COUNT(*) FROM fj_chat_reply_tasks pending
                WHERE pending.session_id = s.id
                  AND pending.status IN ('pending_generation', 'generating', 'awaiting_review')
                  AND (pending.is_draft = 0 OR TRIM(COALESCE(pending.final_text, pending.draft_text, '')) <> '')
              ) AS unhandled_count
            FROM fj_chat_sessions s
            LEFT JOIN fj_chat_messages m ON m.id = s.latest_message_id
            LEFT JOIN fj_chat_reply_tasks t ON t.id = (
              SELECT id FROM fj_chat_reply_tasks
              WHERE session_id = s.id
              ORDER BY created_at DESC LIMIT 1
            )
            LEFT JOIN fj_chat_attention_states att ON att.session_id = s.id
            LEFT JOIN fj_job_pipeline_snapshots pipeline ON pipeline.job_id = s.job_id
            {where}
            -- 严格沿用 BOSS 好友列表顺序，聊天消息同步不会改变会话位置。
            ORDER BY s.platform_synced_at DESC, s.platform_list_index ASC, s.id ASC
            LIMIT ? OFFSET ?
            """,
            params,
        ).fetchall()
        return [_session_payload(row, connection) for row in rows]


def get_session(db: Database, session_id: str) -> dict[str, Any]:
    with db.connect() as connection:
        session = _session_or_404(connection, session_id)
        messages = connection.execute(
            """
            SELECT * FROM fj_chat_messages
            WHERE session_id = ? AND display_kind <> 'discard'
              AND (message_type <> 'text' OR TRIM(content) <> '')
            ORDER BY sent_at ASC, rowid ASC
            """,
            (session_id,),
        ).fetchall()
        draft = connection.execute(
            "SELECT * FROM fj_chat_reply_tasks WHERE session_id = ? AND is_draft = 1 ORDER BY updated_at DESC LIMIT 1",
            (session_id,),
        ).fetchone()
        tasks = connection.execute(
            "SELECT * FROM fj_chat_reply_tasks WHERE session_id = ? AND is_draft = 0 ORDER BY created_at DESC",
            (session_id,),
        ).fetchall()
        actions = connection.execute(
            "SELECT * FROM fj_chat_send_actions WHERE session_id = ? ORDER BY created_at DESC LIMIT 20",
            (session_id,),
        ).fetchall()
        resume_attachments: list[dict[str, Any]] = []
        for action in actions:
            if str(action["operation_kind"] or "text") != "resume_list":
                continue
            evidence = _loads(str(action["evidence_json"] or "{}"), {})
            attachments = evidence.get("attachments") if isinstance(evidence, dict) else None
            if isinstance(attachments, list):
                resume_attachments = [item for item in attachments if isinstance(item, dict)]
                break
        message_count = int(connection.execute(
            """
            SELECT COUNT(*) FROM fj_chat_messages
            WHERE session_id = ? AND display_kind <> 'discard'
              AND (message_type <> 'text' OR TRIM(content) <> '')
            """,
            (session_id,),
        ).fetchone()[0])
        insight = connection.execute(
            """
            SELECT *
            FROM fj_conversation_insights
            WHERE session_id = ?
            ORDER BY updated_at DESC, created_at DESC
            LIMIT 1
            """,
            (session_id,),
        ).fetchone()
        return {
            "session": _session_payload(session, connection),
            "messages": [_row(item) for item in messages],
            "draft": _row(draft),
            "reply_tasks": [_row(item) for item in tasks],
            "send_actions": [_row(item) for item in actions],
            "resume_attachments": resume_attachments,
            "latest_conversation_insight": _row(insight),
            "messages_truncated": False,
            "message_count": message_count,
        }


def retransform_session_messages(db: Database, session_id: str) -> dict[str, Any]:
    """按当前全局规则重写当前会话已保存消息的展示语义。"""
    with db.connect() as connection:
        session = _session_or_404(connection, session_id)
        rules = _message_transform_config(connection)["rules"]
        rows = connection.execute(
            """
            SELECT * FROM fj_chat_messages
            WHERE session_id = ?
            ORDER BY sent_at ASC, rowid ASC
            """,
            (session_id,),
        ).fetchall()
        updated_count = 0
        discarded_count = 0
        reclassified_message_ids: list[str] = []
        latest_platform_content: str | None = None
        latest_platform_display_kind: str | None = None
        for row in rows:
            # 已完成转义或已过滤的记录没有保留原始正文，保持当前展示语义。
            current_display_kind = str(row["display_kind"] or "chat")
            if current_display_kind == "action":
                current_action_type = str(row["action_type"] or "")
                current_content = str(row["content"] or "")
                if current_action_type == "resume_read" and current_content in {"HR 已阅读附件简历", "HR已读附件简历"}:
                    current_action_type = "resume_sent_confirmed"
                elif current_action_type == "resume_viewed" and current_content in {"HR 已查看附件简历", "HR已查看附件简历"}:
                    current_action_type = "resume_message_read"
                action_rule = next((
                    rule for rule in rules
                    if rule.get("enabled")
                    and rule.get("output_kind") == "action"
                    and str(rule.get("action_type") or "") == current_action_type
                ), None)
                if action_rule is not None:
                    next_content = str(action_rule.get("display_content") or row["content"])
                    if str(row["content"]) != next_content or str(row["action_type"] or "") != current_action_type:
                        connection.execute(
                            "UPDATE fj_chat_messages SET content = ?, action_type = ? WHERE id = ?",
                            (next_content, current_action_type, row["id"]),
                        )
                        updated_count += 1
                        if str(row["platform_message_id"]) == str(session["platform_latest_msg_id"] or ""):
                            latest_platform_content = next_content
                            latest_platform_display_kind = "action"
                continue
            if current_display_kind != "chat":
                continue
            transformed = _transform_message(
                connection,
                session_id=session_id,
                direction=str(row["direction"]),
                message_type=str(row["message_type"]),
                content=str(row["content"]),
                rules=rules,
                occurred_at=str(row["sent_at"] or "") or None,
            )
            if transformed is None:
                next_message_type = "system"
                next_content = ""
                next_display_kind = "discard"
                next_action_type = ""
                discarded_count += 1
            else:
                next_message_type = transformed["message_type"]
                next_content = transformed["content"]
                next_display_kind = transformed["display_kind"]
                next_action_type = transformed["action_type"]
            if (
                str(row["message_type"]) == next_message_type
                and str(row["content"]) == next_content
                and str(row["display_kind"] or "chat") == next_display_kind
                and str(row["action_type"] or "") == next_action_type
            ):
                continue
            connection.execute(
                """
                UPDATE fj_chat_messages
                SET message_type = ?, content = ?, display_kind = ?, action_type = ?
                WHERE id = ?
                """,
                (next_message_type, next_content, next_display_kind, next_action_type, row["id"]),
            )
            updated_count += 1
            if next_display_kind != "chat":
                reclassified_message_ids.append(str(row["id"]))
            if str(row["platform_message_id"]) == str(session["platform_latest_msg_id"] or ""):
                latest_platform_content = next_content
                latest_platform_display_kind = next_display_kind

        latest = connection.execute(
            """
            SELECT * FROM fj_chat_messages
            WHERE session_id = ? AND display_kind <> 'discard'
            ORDER BY sent_at DESC, rowid DESC LIMIT 1
            """,
            (session_id,),
        ).fetchone()
        latest_inbound = connection.execute(
            """
            SELECT id FROM fj_chat_messages
            WHERE session_id = ? AND direction = 'inbound' AND display_kind = 'chat'
            ORDER BY sent_at DESC, rowid DESC LIMIT 1
            """,
            (session_id,),
        ).fetchone()
        if reclassified_message_ids:
            placeholders = ", ".join("?" for _ in reclassified_message_ids)
            connection.execute(
                f"""
                UPDATE fj_chat_reply_tasks
                SET status = 'stale', cancelled_at = ?, updated_at = ?
                WHERE based_on_message_id IN ({placeholders})
                  AND is_draft = 1
                  AND status IN ('pending_generation', 'generating', 'awaiting_review')
                """,
                (_now(), _now(), *reclassified_message_ids),
            )
        assignments = [
            "latest_message_id = ?",
            "latest_inbound_message_id = ?",
            "last_message_at = ?",
            "session_version = ?",
            "updated_at = ?",
        ]
        params: list[Any] = [
            latest["id"] if latest else None,
            latest_inbound["id"] if latest_inbound else None,
            latest["sent_at"] if latest else None,
            int(session["session_version"] or 0) + int(updated_count > 0),
            _now(),
        ]
        if latest_platform_content is not None:
            assignments.extend([
                "platform_latest_message_text = ?",
                "platform_latest_display_kind = ?",
            ])
            params.extend([latest_platform_content, latest_platform_display_kind])
        connection.execute(
            f"UPDATE fj_chat_sessions SET {', '.join(assignments)} WHERE id = ?",
            (*params, session_id),
        )
        return {
            "session_id": session_id,
            "updated_count": updated_count,
            "discarded_count": discarded_count,
        }


def prepare_chat_job(
    db: Database,
    session_id: str,
    *,
    can_fetch_details: bool,
) -> dict[str, Any]:
    """为聊天岗位准备历史记录，并返回查看或详情采集动作。"""
    with db.connect() as connection:
        session = _session_or_404(connection, session_id)
        session_data = dict(session)
        history = None
        if session["job_id"]:
            history = connection.execute(
                "SELECT id FROM fj_boss_jobs WHERE id = ?",
                (session["job_id"],),
            ).fetchone()
        if history is None and session["encrypt_job_id"]:
            history = connection.execute(
                "SELECT id FROM fj_boss_jobs WHERE encrypt_job_id = ? LIMIT 1",
                (session["encrypt_job_id"],),
            ).fetchone()

    if history is not None:
        history_id = str(history["id"])
        if str(session_data.get("job_id") or "") != history_id:
            with db.connect() as connection:
                connection.execute(
                    "UPDATE fj_chat_sessions SET job_id = ?, updated_at = ? WHERE id = ?",
                    (history_id, _now(), session_id),
                )
        job = get_capture_history_job(db, history_id)
        if str(job.get("detail_status") or "") != "completed":
            if not can_fetch_details:
                raise AppError(
                    409,
                    "BROWSER_NOT_RUNNING",
                    "FineJob 专用 Chrome 未启动，请先打开并完成 BOSS 登录。",
                )
            return {
                "action": "update",
                "history_job_id": history_id,
                "job": job,
                "task": None,
            }
        return {
            "action": "view",
            "history_job_id": history_id,
            "job": job,
            "task": None,
        }

    if not can_fetch_details:
        raise AppError(
            409,
            "BROWSER_NOT_RUNNING",
            "FineJob 专用 Chrome 未启动，请先打开并完成 BOSS 登录。",
        )

    application_status = _derive_chat_application_status(
        db,
        session_id=session_id,
        job_id=str(session_data.get("job_id") or ""),
    )
    recorded = record_chat_job(
        db,
        session=session_data,
        application_status=application_status,
    )
    history_id = str(recorded["history_record_id"])
    set_job_application_status(
        db,
        history_id,
        status=application_status,
        source="manual",
        note="自动代聊页面补录岗位状态",
    )
    return {
        "action": "update",
        "history_job_id": history_id,
        "job": get_capture_history_job(db, history_id),
        "task": None,
    }


def _derive_chat_application_status(
    db: Database,
    *,
    session_id: str,
    job_id: str,
) -> str | None:
    """根据当前聊天消息和自动打招呼动作计算投递阶段。"""
    with db.connect() as connection:
        attachment_update = connection.execute(
            """
            SELECT 1 FROM fj_chat_messages
            WHERE session_id = ? AND direction = 'outbound'
              AND (action_type = 'resume_sent' OR content = '附件状态更新')
            LIMIT 1
            """,
            (session_id,),
        ).fetchone()
        if attachment_update:
            return "communicating"

        greeting_rows = connection.execute(
            """
            SELECT status FROM fj_automation_actions
            WHERE job_id = ? AND action_type = 'BOSS_DEFAULT_GREETING'
            """,
            (job_id,),
        ).fetchall() if job_id else []

        if greeting_rows and all(row["status"] == "succeeded" for row in greeting_rows):
            return "pending_application"
        if greeting_rows:
            return "pending_greeting"

        recommendation = connection.execute(
            """
            SELECT decision FROM fj_job_evaluations
            WHERE job_id = ? ORDER BY created_at DESC LIMIT 1
            """,
            (job_id,),
        ).fetchone() if job_id else None
        if recommendation and recommendation["decision"] == "recommend":
            return "pending_greeting"
    return None


def _build_context(db: Database, connection: sqlite3.Connection, session: sqlite3.Row) -> dict[str, Any]:
    messages = [dict(row) for row in connection.execute(
        """
        SELECT direction, message_type, display_kind, action_type, content, sent_at
        FROM fj_chat_messages
        WHERE session_id = ? AND display_kind <> 'discard'
          AND (message_type <> 'text' OR TRIM(content) <> '')
        ORDER BY sent_at DESC, rowid DESC LIMIT 20
        """,
        (session["id"],),
    ).fetchall()][::-1]
    resume_facts = [dict(row) for row in connection.execute(
        """
        SELECT fact_type, fact_key, fact_value
        FROM fj_resume_facts WHERE user_confirmed = 1 AND sensitive = 0
        ORDER BY updated_at DESC LIMIT 40
        """
    ).fetchall()]
    intent_row = connection.execute(
        "SELECT * FROM fj_job_intents ORDER BY updated_at DESC LIMIT 1"
    ).fetchone()
    profile = profile_store.ensure_default_profile(db)
    candidate_context = get_profile_context(
        db,
        str(profile["id"]),
        view="chat",
        job_id=str(session["job_id"] or "") or None,
        persist_artifact=False,
    )
    confirmed_profile_count = int(
        connection.execute(
            "SELECT COUNT(*) FROM fj_profile_facts WHERE profile_id = ? AND status = 'confirmed'",
            (profile["id"],),
        ).fetchone()[0]
    )
    evaluation_row = None
    if session["job_id"]:
        evaluation_row = connection.execute(
            """
            SELECT decision, evaluation_json FROM fj_job_evaluations
            WHERE job_id = ? ORDER BY created_at DESC LIMIT 1
            """,
            (session["job_id"],),
        ).fetchone()
    progress = (
        build_job_progress_with_connection(
            connection, str(session["job_id"]), session_id=str(session["id"])
        )
        if session["job_id"] else None
    )
    waiting_since = None
    if progress and progress.get("waiting_since_at"):
        try:
            waiting_since = datetime.fromisoformat(
                str(progress["waiting_since_at"]).replace("Z", "+00:00")
            )
            if waiting_since.tzinfo is None:
                waiting_since = waiting_since.replace(tzinfo=timezone.utc)
        except ValueError:
            waiting_since = None
    waiting_days = (
        max(0, int((datetime.now(timezone.utc) - waiting_since).total_seconds() // 86_400))
        if waiting_since else 0
    )
    return {
        "conversation": messages,
        "job": {
            "job_id": session["job_id"],
            "encrypt_job_id": session["encrypt_job_id"],
            "title": session["job_title"],
            "company": session["company_name"],
        },
        "evaluation": {
            "decision": evaluation_row["decision"],
            "detail": _loads(evaluation_row["evaluation_json"], {}),
        } if evaluation_row else None,
        "job_progress": {
            "stage": progress.get("stage"),
            "waiting_on": progress.get("waiting_on"),
            "waiting_since_at": progress.get("waiting_since_at"),
            "waiting_duration_days": waiting_days,
            "followup": progress.get("followup"),
            "outcome": progress.get("outcome"),
        } if progress else None,
        "candidate_profile_context": candidate_context,
        "resume_facts": resume_facts if confirmed_profile_count == 0 else [],
        "job_intent": _row(intent_row) if intent_row else None,
    }


def _chat_reply_json_schema() -> dict[str, Any]:
    properties = {
        "decision": {"type": "string", "enum": ["reply", "manual", "ignore"]},
        "reply_text": {"type": "string"},
        "facts_used": {"type": "array", "items": {"type": "string"}},
        "warnings": {"type": "array", "items": {"type": "string"}},
        "requires_user_input": {"type": "boolean"},
        "reason": {"type": "string"},
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
        "required": list(properties),
    }


def _normalize_chat_completion(result: Any) -> dict[str, Any]:
    if not isinstance(result, dict):
        raise AppError(status_code=502, error_category="INVALID_LLM_RESPONSE", error_message="智能执行器回复结构无效。")
    decision = str(result.get("decision") or "")
    if decision not in {"reply", "manual", "ignore"}:
        raise AppError(status_code=502, error_category="INVALID_LLM_RESPONSE", error_message="智能执行器回复决定无效。")
    text = str(result.get("reply_text") or "").strip()
    if decision == "reply" and not text:
        raise AppError(status_code=502, error_category="INVALID_LLM_RESPONSE", error_message="智能执行器返回了空回复。")
    facts_used = result.get("facts_used") if isinstance(result.get("facts_used"), list) else []
    warnings = result.get("warnings") if isinstance(result.get("warnings"), list) else []
    return {
        "decision": decision,
        "reply_text": text[:5000],
        "facts_used": [str(item)[:300] for item in facts_used if str(item).strip()][:30],
        "warnings": [str(item)[:80] for item in warnings if str(item).strip()][:20],
        "requires_user_input": bool(result.get("requires_user_input")),
        "reason": str(result.get("reason") or "")[:500],
    }


def _chat_completion(
    config: AppConfig,
    context: dict[str, Any],
    instruction: str,
    action_kind: str = "reply",
) -> tuple[dict[str, Any], str]:
    action_instruction = {
        "reply": "回复招聘方最新消息",
        "followup": "礼貌跟进当前求职进展",
        "ask_rejection_reason": "礼貌询问本次未继续推进的具体原因",
    }.get(action_kind, "回复招聘方最新消息")
    system_prompt = (
        "你是求职者的沟通助手。只根据给定的本地对话、岗位信息、已确认候选人上下文和求职意向，"
        "生成一条简洁、自然、诚实的中文回复。不得虚构经历、薪资、到岗时间或联系方式；"
        "资料不足时应明确表示需要确认。只输出 JSON 对象，字段为 decision、reply_text、facts_used、"
        "warnings、requires_user_input、reason；decision 只能是 reply、manual、ignore。"
        "主动跟进时结合岗位匹配结论、已确认候选人优势和等待天数，只使用上下文中可证实的信息；"
        "简历已提交或已查看后，优先询问匹配情况、评估进度或需要补充的材料。"
        f"本次目标：{action_instruction}。"
    )
    user_prompt = json.dumps(
        {"context": context, "temporary_instruction": instruction},
        ensure_ascii=False,
    )
    executor = (config.reasoning_executor or "llm").strip().lower()
    # 草稿生成跟随统一智能执行器配置，让 Codex 模式直接复用本机已登录的执行器。
    if executor == "codex-cli":
        codex_result = run_codex_exec(
            cli_path=config.codex_cli_path,
            prompt=f"{system_prompt}\n输入：{user_prompt}",
            output_schema=_chat_reply_json_schema(),
            model=config.codex_model,
            reasoning_effort=config.codex_reasoning_effort,
            timeout_seconds=config.codex_timeout_seconds,
        )
        return _normalize_chat_completion(codex_result.output), str(
            codex_result.model or config.codex_model or "codex-cli"
        )
    if executor != "llm":
        raise AppError(
            status_code=422,
            error_category="UNSUPPORTED_REASONING_EXECUTOR",
            error_message=f"不支持的智能执行器：{config.reasoning_executor}",
        )
    if not config.llm_provider or not config.llm_model:
        raise AppError(
            status_code=422,
            error_category="LLM_NOT_CONFIGURED",
            error_message="请先在系统配置中完成 LLM 提供方和模型配置。",
        )
    provider = config.llm_provider.strip().lower()
    model = config.llm_model
    latest = ""
    for message in reversed(context["conversation"]):
        if message["direction"] == "inbound" and message["message_type"] == "text":
            latest = message["content"].strip()
            break
    if provider == "stub-llm":
        subject = latest[:60] or "您的问题"
        if action_kind == "followup":
            reply_text = "您好，想礼貌跟进一下目前的评估进展。如需我补充材料，请随时告诉我，谢谢。"
            reason = "根据当前等待状态生成礼貌跟进"
        elif action_kind == "ask_rejection_reason":
            reply_text = "感谢您的回复。方便的话，想请教一下这次未能继续推进的主要原因，便于我后续改进，谢谢。"
            reason = "根据已拒绝但原因未知的状态生成询问草稿"
        else:
            reply_text = f"您好，感谢您的消息。关于“{subject}”，我确认一下相关情况后回复您。"
            reason = "根据最新招聘方消息生成保守回复"
        return {
            "decision": "reply",
            "reply_text": reply_text,
            "facts_used": [],
            "warnings": [],
            "requires_user_input": False,
            "reason": reason,
        }, model
    if provider not in {"openai", "openai-compatible"}:
        raise AppError(status_code=422, error_category="UNSUPPORTED_LLM_PROVIDER", error_message=f"不支持的 LLM 提供方：{config.llm_provider}")
    if not config.llm_api_key:
        raise AppError(status_code=422, error_category="LLM_NOT_CONFIGURED", error_message="请先配置 LLM API Key。")
    payload = _post_json(
        url=f"{(config.llm_base_url or 'https://api.openai.com/v1').rstrip('/')}/chat/completions",
        api_key=config.llm_api_key,
        timeout_seconds=config.llm_timeout_seconds,
        payload={
            "model": model,
            "temperature": 0.2,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        },
    )
    try:
        raw_text = str(payload["choices"][0]["message"]["content"]).strip()
    except (KeyError, IndexError, TypeError):
        raise AppError(status_code=502, error_category="INVALID_LLM_RESPONSE", error_message="LLM 未返回有效回复正文。")
    if raw_text.startswith("```"):
        raw_text = raw_text.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        result = json.loads(raw_text)
    except (TypeError, json.JSONDecodeError):
        raise AppError(status_code=502, error_category="INVALID_LLM_RESPONSE", error_message="LLM 未返回有效的结构化回复。")
    return _normalize_chat_completion(result), model


def _generate_reply(
    db: Database,
    config: AppConfig,
    session_id: str,
    *,
    instruction: str = "",
    action_kind: str = "reply",
    regenerate: bool = False,
    job_action_key: str | None = None,
) -> tuple[dict[str, Any], bool]:
    if action_kind not in {"reply", "followup", "ask_rejection_reason"}:
        raise AppError(422, "CHAT_ACTION_KIND_INVALID", "消息草稿类型无效。")
    with db.connect() as connection:
        session = _session_or_404(connection, session_id)
        if session["status"] not in {"active", "unsupported", "paused"}:
            raise AppError(status_code=409, error_category="CHAT_SESSION_UNAVAILABLE", error_message="当前会话状态不支持生成消息草稿。")
        based_on_message_id = (
            session["latest_inbound_message_id"]
            if action_kind == "reply"
            else session["latest_message_id"]
        )
        if not based_on_message_id:
            raise AppError(status_code=409, error_category="NO_CHAT_MESSAGE", error_message="会话没有可作为草稿依据的消息。")
        current = connection.execute(
            """
            SELECT * FROM fj_chat_reply_tasks WHERE session_id = ?
              AND is_draft = 1
              AND status IN ('pending_generation', 'generating', 'awaiting_review', 'confirmed')
            ORDER BY created_at DESC LIMIT 1
            """,
            (session_id,),
        ).fetchone()
        now = _now()
        same_business_trigger = bool(
            current is not None
            and str(current["action_kind"] or "reply") == action_kind
            and str(current["based_on_message_id"]) == str(based_on_message_id)
            and int(current["based_on_session_version"]) == int(session["session_version"])
            and (
                not job_action_key
                or not current["job_action_key"]
                or str(current["job_action_key"]) == job_action_key
            )
        )
        should_reuse_current = bool(
            same_business_trigger
            and not regenerate
            and (
                job_action_key
                or str(current["status"]) in {"generating", "awaiting_review"}
            )
        )
        if should_reuse_current:
            # 单条与批量并发命中同一业务触发时，复用已经存在的有效任务。
            if job_action_key and not current["job_action_key"]:
                try:
                    connection.execute(
                        "UPDATE fj_chat_reply_tasks SET job_action_key = ? WHERE id = ?",
                        (job_action_key, current["id"]),
                    )
                except sqlite3.IntegrityError:
                    pass
            refreshed = connection.execute(
                "SELECT * FROM fj_chat_reply_tasks WHERE id = ?", (current["id"],)
            ).fetchone()
            return _row(refreshed) or {}, False
        if current is not None and current["status"] == "generating":
            raise AppError(
                status_code=409,
                error_category="CHAT_REPLY_GENERATING",
                error_message="该会话正在生成回复，请稍后查看结果。",
            )
        replace_current = (
            current is None
            or regenerate
            or str(current["action_kind"] or "reply") != action_kind
            or str(current["based_on_message_id"]) != str(based_on_message_id)
            or int(current["based_on_session_version"]) != int(session["session_version"])
            or str(current["status"]) == "confirmed"
            or bool(
                job_action_key
                and current["job_action_key"]
                and str(current["job_action_key"]) != job_action_key
            )
        )
        if replace_current:
            if current is not None:
                connection.execute(
                    "UPDATE fj_chat_reply_tasks SET status = 'stale', cancelled_at = ?, updated_at = ? WHERE id = ?",
                    (now, now, current["id"]),
                )
            task_id = _id("chat_reply")
            try:
                connection.execute(
                    """
                    INSERT INTO fj_chat_reply_tasks (
                      id, session_id, trigger_source, job_action_key, action_kind, is_draft,
                      status, based_on_message_id, based_on_session_version,
                      generation_due_at, input_message_ids_json, created_at, updated_at
                    ) VALUES (?, ?, 'manual', ?, ?, 1, 'generating', ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        task_id,
                        session_id,
                        job_action_key,
                        action_kind,
                        based_on_message_id,
                        session["session_version"],
                        now,
                        json.dumps([based_on_message_id], ensure_ascii=False),
                        now,
                        now,
                    ),
                )
            except sqlite3.IntegrityError:
                competing = connection.execute(
                    """
                    SELECT * FROM fj_chat_reply_tasks
                    WHERE session_id = ?
                      AND is_draft = 1
                      AND status IN ('pending_generation', 'generating', 'awaiting_review', 'confirmed')
                    ORDER BY updated_at DESC, created_at DESC LIMIT 1
                    """,
                    (session_id,),
                ).fetchone()
                if (
                    job_action_key
                    and competing is not None
                    and str(competing["action_kind"] or "reply") == action_kind
                    and str(competing["based_on_message_id"]) == str(based_on_message_id)
                    and int(competing["based_on_session_version"] or 0)
                    == int(session["session_version"])
                    and (
                        not competing["job_action_key"]
                        or str(competing["job_action_key"]) == job_action_key
                    )
                ):
                    return _row(competing) or {}, False
                raise
        else:
            task_id = str(current["id"])
            connection.execute(
                """
                UPDATE fj_chat_reply_tasks
                SET status = 'generating', action_kind = ?, job_action_key = ?,
                    generation_error = NULL, updated_at = ?
                WHERE id = ?
                """,
                (action_kind, job_action_key, now, task_id),
            )
        context = _build_context(db, connection, session)
        candidate_context = context["candidate_profile_context"]
        connection.execute(
            """
            UPDATE fj_chat_reply_tasks
            SET context_json = ?, candidate_profile_id = ?, profile_context_version = ?
            WHERE id = ?
            """,
            (
                json.dumps(context, ensure_ascii=False),
                candidate_context["profile_id"],
                candidate_context["artifact_version"],
                task_id,
            ),
        )
    try:
        completion, model = _chat_completion(config, context, instruction, action_kind)
    except Exception as exc:
        with db.connect() as connection:
            connection.execute(
                "UPDATE fj_chat_reply_tasks SET status = 'failed', generation_error = ?, updated_at = ? WHERE id = ?",
                (str(exc)[:500], _now(), task_id),
            )
        raise
    with db.connect() as connection:
        task = _task_or_404(connection, task_id)
        session = _session_or_404(connection, session_id)
        if (
            task["based_on_message_id"] != (
                session["latest_inbound_message_id"]
                if task["action_kind"] == "reply"
                else session["latest_message_id"]
            )
            or int(task["based_on_session_version"]) != int(session["session_version"])
        ):
            connection.execute(
                "UPDATE fj_chat_reply_tasks SET status = 'stale', cancelled_at = ?, updated_at = ? WHERE id = ?",
                (_now(), _now(), task_id),
            )
            raise AppError(status_code=409, error_category="CHAT_CONTEXT_CHANGED", error_message="生成期间收到新消息，请基于最新对话重新生成。")
        now = _now()
        text = str(completion["reply_text"])
        classification = classify_outbound_content(text, base_operation="send_chat_reply")
        warnings = list(dict.fromkeys([
            *completion["warnings"],
            *[item for item in classification.categories if item != "send_chat_reply"],
        ]))
        connection.execute(
            """
            UPDATE fj_chat_reply_tasks
            SET status = 'awaiting_review', draft_text = ?, final_text = ?,
                generation_model = ?, decision = ?, facts_used_json = ?, warnings_json = ?,
                requires_user_input = ?, decision_reason = ?, content_categories_json = ?,
                classification_version = ?, generated_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                text,
                text,
                model,
                completion["decision"],
                json.dumps(completion["facts_used"], ensure_ascii=False),
                json.dumps(warnings, ensure_ascii=False),
                int(bool(completion["requires_user_input"])),
                completion["reason"],
                json.dumps(classification.categories, ensure_ascii=False),
                classification.classification_version,
                now,
                now,
                task_id,
            ),
        )
        task_result = _row(connection.execute(
            "SELECT * FROM fj_chat_reply_tasks WHERE id = ?", (task_id,)
        ).fetchone()) or {}
        return task_result, True


def generate_reply(
    db: Database,
    config: AppConfig,
    session_id: str,
    *,
    instruction: str = "",
    action_kind: str = "reply",
    regenerate: bool = False,
    job_action_key: str | None = None,
) -> dict[str, Any]:
    task, _ = _generate_reply(
        db,
        config,
        session_id,
        instruction=instruction,
        action_kind=action_kind,
        regenerate=regenerate,
        job_action_key=job_action_key,
    )
    return task


def generate_reply_for_action(
    db: Database,
    config: AppConfig,
    session_id: str,
    *,
    action_kind: str,
    job_action_key: str,
) -> tuple[dict[str, Any], bool]:
    return _generate_reply(
        db,
        config,
        session_id,
        action_kind=action_kind,
        job_action_key=job_action_key,
    )


def edit_reply(db: Database, task_id: str, final_text: str) -> dict[str, Any]:
    with db.connect() as connection:
        task = _task_or_404(connection, task_id)
        if not task["is_draft"] or task["status"] != "awaiting_review":
            raise AppError(status_code=409, error_category="CHAT_REPLY_NOT_EDITABLE", error_message="当前回复任务不可编辑。")
        classification = classify_outbound_content(final_text, base_operation="send_chat_reply")
        warnings = [item for item in classification.categories if item != "send_chat_reply"]
        connection.execute(
            """
            UPDATE fj_chat_reply_tasks
            SET final_text = ?, text_version = text_version + 1, decision = 'reply',
                requires_user_input = 0, warnings_json = ?, content_categories_json = ?,
                classification_version = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                final_text.strip(),
                json.dumps(warnings, ensure_ascii=False),
                json.dumps(classification.categories, ensure_ascii=False),
                classification.classification_version,
                _now(),
                task_id,
            ),
        )
        return _row(connection.execute("SELECT * FROM fj_chat_reply_tasks WHERE id = ?", (task_id,)).fetchone()) or {}


def create_manual_reply(db: Database, session_id: str, final_text: str) -> dict[str, Any]:
    """将唯一草稿复制为独立的待确认代聊任务。"""
    text = final_text.strip()
    if not text:
        raise AppError(status_code=422, error_category="CHAT_REPLY_EMPTY", error_message="回复正文不能为空。")
    with db.connect() as connection:
        session = _session_or_404(connection, session_id)
        draft = connection.execute(
            "SELECT * FROM fj_chat_reply_tasks WHERE session_id = ? AND is_draft = 1 ORDER BY updated_at DESC LIMIT 1",
            (session_id,),
        ).fetchone()
        based_on_message_id = str(
            (draft["based_on_message_id"] if draft is not None else session["latest_inbound_message_id"]) or ""
        )
        if not based_on_message_id:
            raise AppError(status_code=409, error_category="NO_CHAT_MESSAGE", error_message="会话没有可作为回复依据的消息。")
        now = _now()
        classification = classify_outbound_content(text, base_operation="send_chat_reply")
        warnings = [item for item in classification.categories if item != "send_chat_reply"]
        task_id = _id("chat_reply")
        connection.execute(
            """
            INSERT INTO fj_chat_reply_tasks (
              id, session_id, trigger_source, job_action_key, action_kind, insight_id, is_draft, status,
              based_on_message_id, based_on_session_version, input_message_ids_json,
              draft_text, final_text, generation_model, generated_at,
              decision, warnings_json, requires_user_input,
              decision_reason, context_json, facts_used_json, content_categories_json, classification_version,
              created_at, updated_at
            ) VALUES (?, ?, 'manual', ?, ?, ?, 0, 'awaiting_review', ?, ?, ?, ?, ?, ?, ?,
                      'reply', ?, 0, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task_id,
                session_id,
                draft["job_action_key"] if draft is not None else None,
                draft["action_kind"] if draft is not None else "reply",
                draft["insight_id"] if draft is not None else None,
                based_on_message_id,
                int(draft["based_on_session_version"] if draft is not None else session["session_version"]),
                draft["input_message_ids_json"] if draft is not None else json.dumps([based_on_message_id], ensure_ascii=False),
                text,
                text,
                draft["generation_model"] if draft is not None else "manual",
                draft["generated_at"] if draft is not None else now,
                json.dumps(warnings, ensure_ascii=False),
                draft["decision_reason"] if draft is not None else "手动创建待确认任务",
                draft["context_json"] if draft is not None else "{}",
                draft["facts_used_json"] if draft is not None else "[]",
                json.dumps(classification.categories, ensure_ascii=False),
                classification.classification_version,
                now,
                now,
            ),
        )
        if draft is not None:
            # 任务入待确认后清空唯一草稿，后续可继续编辑新的消息。
            connection.execute(
                """
                UPDATE fj_chat_reply_tasks
                SET status = 'awaiting_review', draft_text = '', final_text = '', generation_error = NULL,
                    updated_at = ?
                WHERE id = ?
                """,
                (now, draft["id"]),
            )
        return _row(connection.execute(
            "SELECT * FROM fj_chat_reply_tasks WHERE id = ?", (task_id,)
        ).fetchone()) or {}


def set_session_status(db: Database, session_id: str, status: str) -> dict[str, Any]:
    with db.connect() as connection:
        _session_or_404(connection, session_id)
        now = _now()
        connection.execute(
            "UPDATE fj_chat_sessions SET status = ?, updated_at = ? WHERE id = ?",
            (status, now, session_id),
        )
        if status != "active":
            connection.execute(
                """
                UPDATE fj_chat_reply_tasks SET status = 'cancelled', cancelled_at = ?, updated_at = ?
                WHERE session_id = ? AND status IN ('pending_generation', 'generating', 'awaiting_review')
                """,
                (now, now, session_id),
            )
            connection.execute(
                """
                UPDATE fj_chat_reply_tasks SET status = 'cancelled', cancelled_at = ?, updated_at = ?
                WHERE session_id = ? AND status = 'confirmed'
                """,
                (now, now, session_id),
            )
            _cancel_session_send_actions(
                connection,
                session_id,
                "session_paused",
            )
        return _row(connection.execute("SELECT * FROM fj_chat_sessions WHERE id = ?", (session_id,)).fetchone()) or {}


def _restore_task_to_draft(
    connection: sqlite3.Connection,
    task: sqlite3.Row,
    *,
    draft_resolution: str | None,
) -> None:
    """把取消的待确认任务回填到会话唯一草稿。"""
    draft = connection.execute(
        "SELECT * FROM fj_chat_reply_tasks WHERE session_id = ? AND is_draft = 1 ORDER BY updated_at DESC LIMIT 1",
        (task["session_id"],),
    ).fetchone()
    draft_text = str((draft["final_text"] or draft["draft_text"] or "") if draft is not None else "").strip()
    if draft_text and draft_resolution not in {"overwrite", "discard"}:
        raise AppError(
            status_code=409,
            error_category="CHAT_REPLY_DRAFT_EXISTS",
            error_message="当前会话草稿已有内容，请选择覆盖草稿或丢弃本轮信息。",
        )
    if draft_resolution not in {None, "overwrite", "discard"}:
        raise AppError(status_code=422, error_category="CHAT_REPLY_DRAFT_RESOLUTION_INVALID", error_message="草稿处理方式无效。")
    if draft_resolution == "discard":
        return
    now = _now()
    if draft is not None:
        connection.execute(
            """
            UPDATE fj_chat_reply_tasks
            SET trigger_source = 'manual', job_action_key = ?, action_kind = ?, insight_id = ?,
                status = 'awaiting_review', based_on_message_id = ?, based_on_session_version = ?,
                input_message_ids_json = ?, decision = 'reply', facts_used_json = ?, warnings_json = ?,
                requires_user_input = 0, decision_reason = ?, context_json = ?, draft_text = ?, final_text = ?,
                generation_model = ?, generated_at = ?, generation_error = NULL, cancelled_at = NULL, updated_at = ?
            WHERE id = ?
            """,
            (
                task["job_action_key"], task["action_kind"], task["insight_id"],
                task["based_on_message_id"], task["based_on_session_version"], task["input_message_ids_json"],
                task["facts_used_json"], task["warnings_json"], task["decision_reason"], task["context_json"],
                task["draft_text"], task["final_text"], task["generation_model"], task["generated_at"], now,
                draft["id"],
            ),
        )
        return
    draft_id = _id("chat_draft")
    connection.execute(
        """
        INSERT INTO fj_chat_reply_tasks (
          id, session_id, trigger_source, job_action_key, action_kind, insight_id, is_draft, status,
          based_on_message_id, based_on_session_version, input_message_ids_json, decision,
          facts_used_json, warnings_json, requires_user_input, decision_reason, context_json,
          draft_text, final_text, generation_model, generated_at, created_at, updated_at
        ) VALUES (?, ?, 'manual', ?, ?, ?, 1, 'awaiting_review', ?, ?, ?, 'reply', ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            draft_id, task["session_id"], task["job_action_key"], task["action_kind"], task["insight_id"],
            task["based_on_message_id"], task["based_on_session_version"], task["input_message_ids_json"],
            task["facts_used_json"], task["warnings_json"], task["decision_reason"], task["context_json"],
            task["draft_text"], task["final_text"], task["generation_model"], task["generated_at"], now, now,
        ),
    )


def cancel_reply(
    db: Database,
    task_id: str,
    *,
    draft_resolution: str | None = None,
) -> dict[str, Any]:
    with db.connect() as connection:
        task = _task_or_404(connection, task_id)
        if task["is_draft"]:
            now = _now()
            connection.execute(
                """
                UPDATE fj_chat_reply_tasks
                SET status = 'awaiting_review', draft_text = '', final_text = '', generation_error = NULL,
                    updated_at = ?
                WHERE id = ?
                """,
                (now, task_id),
            )
            return _row(connection.execute("SELECT * FROM fj_chat_reply_tasks WHERE id = ?", (task_id,)).fetchone()) or {}
        if task["status"] in {"confirmed", "cancelled", "stale"}:
            return _row(task) or {}
        _restore_task_to_draft(connection, task, draft_resolution=draft_resolution)
        now = _now()
        connection.execute(
            "UPDATE fj_chat_reply_tasks SET status = 'cancelled', cancelled_at = ?, updated_at = ? WHERE id = ?",
            (now, now, task_id),
        )
        return _row(connection.execute("SELECT * FROM fj_chat_reply_tasks WHERE id = ?", (task_id,)).fetchone()) or {}


def confirm_reply(db: Database, task_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    with db.connect() as connection:
        runtime = _ensure_runtime(connection)
        if not runtime["send_enabled"]:
            raise AppError(status_code=409, error_category="CHAT_SEND_DISABLED", error_message="请先在自动代聊设置中启用发送。")
        task = _task_or_404(connection, task_id)
        session = _session_or_404(connection, str(task["session_id"]))
        if task["is_draft"] or task["status"] != "awaiting_review":
            raise AppError(status_code=409, error_category="CHAT_REPLY_NOT_CONFIRMABLE", error_message="当前回复任务不可确认发送。")
        if session["status"] == "unsupported":
            raise AppError(
                status_code=409,
                error_category="CHAT_IDENTITY_INCOMPLETE",
                error_message="聊天对象身份不完整，请先在 BOSS 打开对应会话后重试。",
            )
        if not session["encrypt_peer_uid"] or not session["security_id"] or not session["encrypt_job_id"]:
            raise AppError(
                status_code=409,
                error_category="CHAT_IDENTITY_INCOMPLETE",
                error_message="聊天对象身份不完整，请先在 BOSS 打开对应会话后重试。",
            )
        expected_message = payload["based_on_message_id"]
        expected_version = int(payload["based_on_session_version"])
        current_basis_message = (
            session["latest_inbound_message_id"]
            if task["action_kind"] == "reply"
            else session["latest_message_id"]
        )
        if (
            expected_message != task["based_on_message_id"]
            or expected_version != int(task["based_on_session_version"])
            or expected_message != current_basis_message
            or expected_version != int(session["session_version"])
        ):
            connection.execute(
                "UPDATE fj_chat_reply_tasks SET status = 'stale', cancelled_at = ?, updated_at = ? WHERE id = ?",
                (_now(), _now(), task_id),
            )
            raise AppError(status_code=409, error_category="CHAT_CONTEXT_CHANGED", error_message="确认前收到新消息，请重新生成回复。")
        now = _now()
        action_id = _id("chat_send")
        final_text = str(payload["final_text"]).strip()
        classification = classify_outbound_content(final_text, base_operation="send_chat_reply")
        warnings = [item for item in classification.categories if item != "send_chat_reply"]
        connection.execute(
            """
            UPDATE fj_chat_reply_tasks
            SET status = 'confirmed', final_text = ?, decision = 'reply', requires_user_input = 0,
                warnings_json = ?, content_categories_json = ?, classification_version = ?,
                confirmed_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                final_text,
                json.dumps(warnings, ensure_ascii=False),
                json.dumps(classification.categories, ensure_ascii=False),
                classification.classification_version,
                now,
                now,
                task_id,
            ),
        )
        connection.execute(
            """
            INSERT INTO fj_chat_send_actions (
              id, reply_task_id, session_id, status, text,
              content_categories_json, classification_version,
              canonical_status, canonical_updated_at, canonical_reason,
              created_at, updated_at
            ) VALUES (?, ?, ?, 'queued', ?, ?, ?, 'pending', ?, '等待执行', ?, ?)
            """,
            (
                action_id,
                task_id,
                session["id"],
                final_text,
                json.dumps(classification.categories, ensure_ascii=False),
                classification.classification_version,
                now,
                now,
                now,
            ),
        )
        return _action_payload(connection, action_id)


def _create_resume_support_task(connection: sqlite3.Connection, session: sqlite3.Row) -> str:
    """复用既有动作外键，为非文本聊天动作保存最小关联记录。"""
    based_on_message_id = str(session["latest_message_id"] or session["latest_inbound_message_id"] or "")
    if not based_on_message_id:
        raise AppError(status_code=409, error_category="CHAT_RESUME_CONTEXT_INVALID", error_message="当前会话缺少可关联的聊天消息。")
    now = _now()
    task_id = _id("chat_resume_support")
    connection.execute(
        """
        INSERT INTO fj_chat_reply_tasks (
          id, session_id, trigger_source, status, based_on_message_id,
          based_on_session_version, cancelled_at, created_at, updated_at
        ) VALUES (?, ?, 'manual', 'cancelled', ?, ?, ?, ?, ?)
        """,
        (task_id, session["id"], based_on_message_id, int(session["session_version"]), now, now, now),
    )
    return task_id


def refresh_resume_attachments(db: Database, session_id: str) -> dict[str, Any]:
    """直接读取当前 BOSS 账号附件，并把结果保存到当前页面会话。"""
    captured = boss_scraper_service.capture_resume_attachments()
    attachments = captured.get("attachments")
    if not isinstance(attachments, list):
        attachments = []
    account_uid = str(captured.get("account_uid") or "")
    source_url = str(captured.get("url") or "")

    with db.connect() as connection:
        # 会话只作为页面结果的保存容器，当前 BOSS 账号直接以 CDP 页面登录态为准。
        session = _session_or_404(connection, session_id)
        task_id = _create_resume_support_task(connection, session)
        now = _now()
        action_id = _id("chat_resume")
        evidence = {
            "attachments": [item for item in attachments if isinstance(item, dict)],
            "account_uid": account_uid,
            "source_url": source_url,
        }
        connection.execute(
            """
            INSERT INTO fj_chat_send_actions (
              id, reply_task_id, session_id, operation_kind, encrypt_resume_id, resume_filename,
              status, outcome, text, status_code, evidence_json,
              canonical_status, canonical_updated_at, canonical_reason,
              created_at, updated_at, completed_at
            ) VALUES (?, ?, ?, 'resume_list', '', '', 'accepted', 'accepted', '', ?, ?,
                      'accepted', ?, '附件简历列表已读取', ?, ?, ?)
            """,
            (
                action_id,
                task_id,
                session_id,
                "resume_list_loaded",
                json.dumps(evidence, ensure_ascii=False),
                now,
                now,
                now,
                now,
            ),
        )
        return _action_payload(connection, action_id)


def _create_resume_action(
    db: Database,
    session_id: str,
    operation_kind: str,
    encrypt_resume_id: str = "",
    resume_filename: str = "",
) -> dict[str, Any]:
    with db.connect() as connection:
        runtime = _ensure_runtime(connection)
        session = _session_or_404(connection, session_id)
        if not all(session[key] for key in ("account_uid", "peer_uid", "encrypt_peer_uid", "security_id", "encrypt_job_id")):
            raise AppError(status_code=409, error_category="CHAT_IDENTITY_INCOMPLETE", error_message="聊天对象身份不完整，请先在 BOSS 打开对应会话后重试。")
        direct_execution = operation_kind == "resume" and bool(runtime["direct_execution_enabled"])
        if direct_execution and not runtime["send_enabled"]:
            raise AppError(status_code=409, error_category="CHAT_SEND_DISABLED", error_message="请先在自动代聊设置中启用发送。")
        task_id = _create_resume_support_task(connection, session)
        now = _now()
        action_id = _id("chat_resume")
        # 简历任务沿用统一开关决定进入待确认或执行队列。
        confirmation_status = "confirmed" if direct_execution or operation_kind != "resume" else "pending"
        canonical_reason = "等待执行" if confirmation_status == "confirmed" else "等待人工确认"
        connection.execute(
            """
            INSERT INTO fj_chat_send_actions (
              id, reply_task_id, session_id, operation_kind, encrypt_resume_id, resume_filename,
              confirmation_status, status, text, canonical_status, canonical_updated_at, canonical_reason,
              created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'queued', '', 'pending', ?, ?, ?, ?)
            """,
            (
                action_id, task_id, session_id, operation_kind, encrypt_resume_id, resume_filename,
                confirmation_status, now, canonical_reason, now, now,
            ),
        )
        return _action_payload(connection, action_id)


def create_resume_list_action(db: Database, session_id: str) -> dict[str, Any]:
    return _create_resume_action(db, session_id, "resume_list")


def create_resume_send_action(db: Database, session_id: str, encrypt_resume_id: str, resume_filename: str) -> dict[str, Any]:
    return _create_resume_action(db, session_id, "resume", encrypt_resume_id, resume_filename)


def list_review_tasks(db: Database) -> dict[str, list[dict[str, Any]]]:
    """汇总需要人工确认的聊天消息与附件简历动作。"""
    with db.connect() as connection:
        reply_rows = connection.execute(
            """
            SELECT t.*, s.job_id, s.encrypt_job_id, s.peer_name, s.company_name, s.job_title,
                   s.latest_inbound_message_id, s.session_version,
                   COALESCE(latest_inbound.content, '') AS latest_message
            FROM fj_chat_reply_tasks t
            JOIN fj_chat_sessions s ON s.id = t.session_id
            LEFT JOIN fj_chat_messages latest_inbound ON latest_inbound.id = s.latest_inbound_message_id
            WHERE t.is_draft = 0 AND t.status = 'awaiting_review'
            ORDER BY t.created_at DESC, t.id DESC
            """
        ).fetchall()
        resume_rows = connection.execute(
            """
            SELECT a.*, s.job_id, s.encrypt_job_id, s.peer_name, s.company_name, s.job_title,
                   EXISTS(
                     SELECT 1 FROM fj_chat_send_actions sent
                     WHERE sent.session_id = a.session_id
                       AND sent.operation_kind = 'resume'
                       AND sent.status = 'accepted'
                   ) AS resume_already_sent
            FROM fj_chat_send_actions a
            JOIN fj_chat_sessions s ON s.id = a.session_id
            WHERE a.operation_kind = 'resume'
              AND a.status = 'queued'
              AND a.confirmation_status = 'pending'
            ORDER BY a.created_at DESC, a.id DESC
            """
        ).fetchall()
        # 会话尚未写入本地岗位 ID 时，使用加密岗位标识补齐历史岗位关联。
        reply_job_ids = {
            str(row["id"]): _resolve_job_id(
                connection,
                {
                    "job_id": str(row["job_id"] or ""),
                    "encrypt_job_id": str(row["encrypt_job_id"] or ""),
                },
            ) or ""
            for row in reply_rows
        }
        resume_job_ids = {
            str(row["id"]): _resolve_job_id(
                connection,
                {
                    "job_id": str(row["job_id"] or ""),
                    "encrypt_job_id": str(row["encrypt_job_id"] or ""),
                },
            ) or ""
            for row in resume_rows
        }
    items = [
        {
            "id": str(row["id"]),
            "source": "chat_reply",
            "session_id": str(row["session_id"]),
            "job_id": reply_job_ids[str(row["id"])],
            "task_type": "代聊",
            # 待确认列表展示用户将要发送的完整消息。
            "task_detail": str(row["final_text"] or row["draft_text"] or ""),
            "peer_name": str(row["peer_name"] or ""),
            "company_name": str(row["company_name"] or ""),
            "job_title": str(row["job_title"] or ""),
            "created_at": str(row["created_at"]),
            "based_on_message_id": str(row["based_on_message_id"]),
            "based_on_session_version": int(row["based_on_session_version"]),
            "has_new_message": (
                str(row["based_on_message_id"]) != str(row["latest_inbound_message_id"] or "")
                or int(row["based_on_session_version"]) != int(row["session_version"])
            ),
            "latest_message": str(row["latest_message"] or ""),
        }
        for row in reply_rows
    ]
    items.extend(
        {
            "id": str(row["id"]),
            "source": "chat_resume",
            "session_id": str(row["session_id"]),
            "job_id": resume_job_ids[str(row["id"])],
            "task_type": "发送简历",
            "task_detail": str(row["resume_filename"] or "未命名简历"),
            "peer_name": str(row["peer_name"] or ""),
            "company_name": str(row["company_name"] or ""),
            "job_title": str(row["job_title"] or ""),
            "created_at": str(row["created_at"]),
            "resume_already_sent": bool(row["resume_already_sent"]),
        }
        for row in resume_rows
    )
    items.sort(key=lambda item: (str(item["created_at"]), str(item["id"])), reverse=True)
    return {"items": items}


def list_executed_review_tasks(db: Database) -> dict[str, list[dict[str, Any]]]:
    """返回已完成执行的代聊和简历发送任务，供待确认页复用同一列表展示。"""
    with db.connect() as connection:
        rows = connection.execute(
            """
            SELECT a.*, s.job_id, s.encrypt_job_id, s.peer_name, s.company_name, s.job_title
            FROM fj_chat_send_actions a
            JOIN fj_chat_sessions s ON s.id = a.session_id
            WHERE a.operation_kind IN ('text', 'resume')
              AND a.status IN ('accepted', 'failed', 'unknown')
            ORDER BY COALESCE(a.completed_at, a.created_at) DESC, a.id DESC
            """
        ).fetchall()
        job_ids = {
            str(row["id"]): _resolve_job_id(
                connection,
                {
                    "job_id": str(row["job_id"] or ""),
                    "encrypt_job_id": str(row["encrypt_job_id"] or ""),
                },
            ) or ""
            for row in rows
        }
    items = [
        {
            "id": str(row["id"]),
            "source": "chat_reply" if row["operation_kind"] == "text" else "chat_resume",
            "session_id": str(row["session_id"]),
            "job_id": job_ids[str(row["id"])],
            "task_type": "代聊" if row["operation_kind"] == "text" else "发送简历",
            "task_detail": str(row["text"] or "") if row["operation_kind"] == "text" else str(row["resume_filename"] or "未命名简历"),
            "peer_name": str(row["peer_name"] or ""),
            "company_name": str(row["company_name"] or ""),
            "job_title": str(row["job_title"] or ""),
            "created_at": str(row["completed_at"] or row["created_at"]),
            "execution_state": str(row["status"]),
            "execution_error": str(row["error_message"] or ""),
        }
        for row in rows
    ]
    return {"items": items}


def link_review_task_context(db: Database, task_id: str, source: str) -> dict[str, Any]:
    """检查待确认任务关联会话的最新状态，并取消重复简历任务。"""
    with db.connect() as connection:
        if source == "chat_reply":
            row = connection.execute(
                """
                SELECT t.based_on_message_id, t.based_on_session_version,
                       s.latest_inbound_message_id, s.session_version,
                       COALESCE(m.content, '') AS latest_message
                FROM fj_chat_reply_tasks t
                JOIN fj_chat_sessions s ON s.id = t.session_id
                LEFT JOIN fj_chat_messages m ON m.id = s.latest_inbound_message_id
                WHERE t.id = ? AND t.is_draft = 0 AND t.status = 'awaiting_review'
                """,
                (task_id,),
            ).fetchone()
            if row is None:
                raise AppError(status_code=409, error_category="CHAT_REPLY_NOT_CONFIRMABLE", error_message="当前回复任务不可确认发送。")
            has_new_message = (
                str(row["based_on_message_id"]) != str(row["latest_inbound_message_id"] or "")
                or int(row["based_on_session_version"]) != int(row["session_version"])
            )
            return {
                "status": "new_message" if has_new_message else "up_to_date",
                "has_new_message": has_new_message,
                "latest_message": str(row["latest_message"] or ""),
                "cancelled": False,
            }

        if source == "chat_resume":
            action = _action_payload(connection, task_id)
            if action["operation_kind"] != "resume" or action["confirmation_status"] != "pending":
                raise AppError(status_code=409, error_category="CHAT_RESUME_NOT_CONFIRMABLE", error_message="当前简历动作不可确认。")
            sent = connection.execute(
                """
                SELECT 1 FROM fj_chat_send_actions
                WHERE session_id = ? AND operation_kind = 'resume' AND status = 'accepted'
                LIMIT 1
                """,
                (action["session_id"],),
            ).fetchone()
            if sent is not None:
                now = _now()
                connection.execute(
                    """
                    UPDATE fj_chat_send_actions
                    SET status = 'cancelled', confirmation_status = 'confirmed', completed_at = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (now, now, task_id),
                )
                return {
                    "status": "resume_already_sent",
                    "has_new_message": False,
                    "latest_message": "",
                    "cancelled": True,
                }
            return {
                "status": "resume_not_sent",
                "has_new_message": False,
                "latest_message": "",
                "cancelled": False,
            }

    raise AppError(status_code=422, error_category="CHAT_REVIEW_SOURCE_INVALID", error_message="待确认任务类型无效。")


def confirm_resume_action(db: Database, action_id: str) -> dict[str, Any]:
    """确认附件简历动作后，允许聊天执行器领取该动作。"""
    with db.connect() as connection:
        runtime = _ensure_runtime(connection)
        action = _action_payload(connection, action_id)
        if action["operation_kind"] != "resume" or action["confirmation_status"] != "pending":
            raise AppError(status_code=409, error_category="CHAT_RESUME_NOT_CONFIRMABLE", error_message="当前简历动作不可确认。")
        if not runtime["send_enabled"]:
            raise AppError(status_code=409, error_category="CHAT_SEND_DISABLED", error_message="请先在自动代聊设置中启用发送。")
        connection.execute(
            "UPDATE fj_chat_send_actions SET confirmation_status = 'confirmed', updated_at = ? WHERE id = ?",
            (_now(), action_id),
        )
        return _action_payload(connection, action_id)


def cancel_resume_action(db: Database, action_id: str) -> dict[str, Any]:
    """取消尚未确认的附件简历动作。"""
    with db.connect() as connection:
        action = _action_payload(connection, action_id)
        if action["operation_kind"] != "resume" or action["confirmation_status"] != "pending":
            raise AppError(status_code=409, error_category="CHAT_RESUME_NOT_CANCELLABLE", error_message="当前简历动作不可取消。")
        now = _now()
        connection.execute(
            """
            UPDATE fj_chat_send_actions
            SET status = 'cancelled', confirmation_status = 'confirmed', completed_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (now, now, action_id),
        )
        return _action_payload(connection, action_id)


def return_send_action_to_review(
    db: Database,
    action_id: str,
    *,
    draft_resolution: str | None = None,
) -> dict[str, Any]:
    """取消尚未开始发送的聊天动作，并将代聊消息恢复为草稿。"""
    with db.connect() as connection:
        action = _action_payload(connection, action_id)
        if action["status"] not in {"queued", "leased"}:
            raise AppError(
                status_code=409,
                error_category="CHAT_ACTION_NOT_RETURNABLE",
                error_message="任务已经开始发送，请到聊天信息确认发送结果。",
            )
        now = _now()
        if action["operation_kind"] == "resume":
            if action["confirmation_status"] != "confirmed":
                raise AppError(status_code=409, error_category="CHAT_RESUME_NOT_RETURNABLE", error_message="当前简历任务已经处于待确认状态。")
            connection.execute(
                """
                UPDATE fj_chat_send_actions
                SET confirmation_status = 'pending', canonical_status = 'pending',
                    lease_owner = NULL, lease_expires_at = NULL, dispatch_deadline_at = NULL,
                    canonical_updated_at = ?, canonical_reason = '已退回待确认', updated_at = ?
                WHERE id = ?
                """,
                (now, now, action_id),
            )
            return _action_payload(connection, action_id)
        if action["operation_kind"] != "text":
            raise AppError(status_code=409, error_category="CHAT_ACTION_NOT_RETURNABLE", error_message="当前任务不支持退回待确认。")
        task = _task_or_404(connection, str(action["reply_task_id"]))
        if task["is_draft"] or task["status"] != "confirmed":
            raise AppError(status_code=409, error_category="CHAT_REPLY_NOT_RETURNABLE", error_message="当前代聊任务不能取消发送。")
        _restore_task_to_draft(connection, task, draft_resolution=draft_resolution)
        # 取消执行动作后，保留任务记录并将内容回填到唯一草稿。
        connection.execute(
            """
            UPDATE fj_chat_send_actions
            SET status = 'cancelled', outcome = NULL, status_code = '', error_message = '',
                lease_owner = NULL, lease_expires_at = NULL, dispatch_deadline_at = NULL,
                execution_epoch = execution_epoch + 1,
                canonical_status = 'cancelled', canonical_updated_at = ?, canonical_reason = '已退回待确认',
                completed_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (now, now, now, action_id),
        )
        connection.execute(
            "UPDATE fj_chat_reply_tasks SET status = 'cancelled', cancelled_at = ?, updated_at = ? WHERE id = ?",
            (now, now, task["id"]),
        )
        return _action_payload(connection, action_id)


def handle_executor_disconnected(db: Database, executor_id: str) -> dict[str, int]:
    """在插件断开时结束自动代聊中的执行态动作。"""
    now = _now()
    with db.connect() as connection:
        requeued = connection.execute(
            """
            UPDATE fj_chat_send_actions
            SET status = 'queued', lease_owner = NULL, lease_expires_at = NULL,
                dispatch_deadline_at = NULL, canonical_status = 'pending',
                canonical_updated_at = ?, canonical_reason = '插件断开，已退回执行队列', updated_at = ?
            WHERE lease_owner = ? AND status = 'leased'
            """,
            (now, now, executor_id),
        ).rowcount
        unknown = connection.execute(
            """
            UPDATE fj_chat_send_actions
            SET status = 'unknown', outcome = 'unknown', status_code = 'executor_disconnected',
                error_message = '插件连接已断开，请人工核对 BOSS 会话', completed_at = ?,
                lease_expires_at = NULL, dispatch_deadline_at = NULL,
                canonical_status = 'unknown', canonical_updated_at = ?,
                canonical_reason = '插件在发送边界断开', updated_at = ?
            WHERE lease_owner = ? AND status = 'dispatching'
            """,
            (now, now, now, executor_id),
        ).rowcount
    return {"requeued": int(requeued), "unknown": int(unknown)}


def _action_payload(connection: sqlite3.Connection, action_id: str) -> dict[str, Any]:
    row = connection.execute(
        """
        SELECT a.*, s.account_uid, s.status AS session_status, s.peer_uid, s.encrypt_peer_uid,
          s.security_id, s.encrypt_job_id, s.job_title, s.peer_name, s.company_name
        FROM fj_chat_send_actions a
        JOIN fj_chat_sessions s ON s.id = a.session_id
        WHERE a.id = ?
        """,
        (action_id,),
    ).fetchone()
    if row is None:
        raise AppError(status_code=404, error_category="CHAT_SEND_ACTION_NOT_FOUND", error_message="发送动作不存在。")
    return _row(row) or {}


def claim_send_action(
    db: Database,
    executor_id: str,
    *,
    account_uid: str,
    tab_id: str,
    leader_epoch: int,
) -> dict[str, Any] | None:
    now_dt = datetime.now(timezone.utc)
    now = _now()
    with db.connect() as connection:
        runtime = _ensure_runtime(connection)
        _sweep_stale_send_actions(connection)
        action = connection.execute(
            """
            SELECT a.id, a.client_mid FROM fj_chat_send_actions a
            WHERE a.confirmation_status = 'confirmed'
              AND a.status IN ('queued', 'leased')
            ORDER BY a.created_at ASC LIMIT 1
            """,
            (),
        ).fetchone()
        if action is None:
            # 领取接口返回空任务时记录明确原因，便于区分待确认、租约占用和发送开关拦截。
            queue_stats = connection.execute(
                """
                SELECT
                  SUM(CASE WHEN confirmation_status = 'pending' AND status = 'queued' THEN 1 ELSE 0 END) AS pending_confirmation_count,
                  SUM(CASE WHEN confirmation_status = 'confirmed' AND status = 'queued' THEN 1 ELSE 0 END) AS queued_confirmed_count,
                  SUM(CASE WHEN confirmation_status = 'confirmed' AND status = 'leased' AND lease_expires_at > ? THEN 1 ELSE 0 END) AS active_lease_count
                FROM fj_chat_send_actions
                WHERE operation_kind IN ('resume_list', 'resume')
                """,
                (now,),
            ).fetchone()
            pending_confirmation_count = int(queue_stats["pending_confirmation_count"] or 0)
            queued_confirmed_count = int(queue_stats["queued_confirmed_count"] or 0)
            active_lease_count = int(queue_stats["active_lease_count"] or 0)
            if pending_confirmation_count:
                reason = "任务仍在待确认"
            elif active_lease_count:
                reason = "任务已由其他标签页领取"
            else:
                reason = "没有可领取的发送任务"
            _record_resume_claim_log(
                connection,
                level="info",
                message=f"自动代聊未领取简历任务：{reason}",
                detail={
                    "executor_id": executor_id,
                    "account_uid": account_uid,
                    "tab_id": tab_id,
                    "pending_confirmation_count": pending_confirmation_count,
                    "queued_confirmed_count": queued_confirmed_count,
                    "active_lease_count": active_lease_count,
                },
            )
            return None
        # 领取只返回动作内容；插件确认开始执行后才写入执行状态，响应未送达时任务仍可领取。
        payload = _action_payload(connection, str(action["id"]))
        _record_resume_claim_log(
            connection,
            level="info",
            message="自动代聊已返回简历发送参数给插件",
            detail={
                "chat_action_id": str(payload["id"]),
                "session_id": str(payload["session_id"]),
                "resume_filename": str(payload.get("resume_filename") or ""),
                "executor_id": executor_id,
                "tab_id": tab_id,
            },
        )
        return payload


def mark_dispatch_started(
    db: Database,
    executor_id: str,
    action_id: str,
    execution_epoch: int,
    *,
    tab_id: str,
    leader_epoch: int,
) -> dict[str, Any]:
    with db.connect() as connection:
        runtime = _ensure_runtime(connection)
        executor = connection.execute(
            "SELECT queue_state FROM fj_boss_executor_instances WHERE id = ?",
            (executor_id,),
        ).fetchone()
        # 派发前以服务端保存的开始状态复核，避免插件状态尚未同步时直接发送。
        if executor is None or executor["queue_state"] != "running":
            _record_resume_claim_log(
                connection,
                level="info",
                message="自动代聊未开始执行：插件尚未点击开始",
                detail={"chat_action_id": action_id, "executor_id": executor_id},
            )
            connection.commit()
            raise AppError(status_code=409, error_category="CHAT_EXECUTOR_PAUSED", error_message="插件尚未点击开始。")
        action = _action_payload(connection, action_id)
        if int(action["execution_epoch"]) != execution_epoch or action["status"] not in {"queued", "leased"}:
            _record_resume_claim_log(
                connection,
                level="warning",
                message="自动代聊未开始发送简历：动作状态已变化",
                detail={
                    "chat_action_id": action_id,
                    "requested_execution_epoch": execution_epoch,
                    "current_execution_epoch": int(action["execution_epoch"]),
                    "current_status": str(action["status"]),
                },
            )
            connection.commit()
            raise AppError(status_code=409, error_category="CHAT_ACTION_LEASE_LOST", error_message="发送动作已被其他执行请求接管。")
        client_mid = str(action["client_mid"] or "") or _new_client_mid(connection)
        if action["operation_kind"] != "resume_list" and not runtime["send_enabled"]:
            now = _now()
            connection.execute(
                """
                UPDATE fj_chat_send_actions SET status = 'queued', lease_owner = NULL,
                  lease_expires_at = NULL, dispatch_deadline_at = NULL,
                  canonical_status = 'pending', canonical_updated_at = ?,
                  canonical_reason = '发送开关已关闭', updated_at = ? WHERE id = ?
                """,
                (now, now, action_id),
            )
            connection.commit()
            _record_resume_claim_log(
                connection,
                level="warning",
                message="自动代聊未开始发送简历：发送开关关闭",
                detail={"chat_action_id": action_id},
            )
            connection.commit()
            raise AppError(status_code=409, error_category="CHAT_SEND_DISABLED", error_message="自动代聊发送开关已关闭。")
        if not all(
            action.get(key) for key in ("account_uid", "peer_uid", "encrypt_peer_uid", "security_id", "encrypt_job_id")
        ):
            _record_resume_claim_log(
                connection,
                level="warning",
                message="自动代聊未开始发送简历：发送上下文不完整",
                detail={"chat_action_id": action_id, "session_status": str(action["session_status"])},
            )
            connection.commit()
            raise AppError(status_code=409, error_category="CHAT_SEND_CONTEXT_INVALID", error_message="发送上下文或身份已失效。")
        now = _now()
        next_epoch = execution_epoch + 1
        connection.execute(
            """
            UPDATE fj_chat_send_actions
            SET status = 'dispatching', lease_owner = ?, lease_expires_at = ?,
                execution_epoch = ?, attempt_count = attempt_count + 1,
                leader_tab_id = ?, leader_epoch = ?, client_mid = ?,
                dispatched_at = ?, dispatch_deadline_at = ?,
                canonical_status = 'dispatching', canonical_updated_at = ?,
                canonical_reason = '页面发送已开始', updated_at = ?
            WHERE id = ?
            """,
            (
                executor_id,
                _after(SEND_LEASE_SECONDS),
                next_epoch,
                tab_id,
                leader_epoch,
                client_mid,
                now,
                _after(SEND_DISPATCH_TIMEOUT_SECONDS),
                now,
                now,
                action_id,
            ),
        )
        return _action_payload(connection, action_id)


def complete_send_action(
    db: Database,
    executor_id: str,
    action_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    with db.connect() as connection:
        action = _action_payload(connection, action_id)
        if action["lease_owner"] != executor_id or int(action["execution_epoch"]) != int(payload["execution_epoch"]):
            raise AppError(status_code=409, error_category="CHAT_ACTION_LEASE_LOST", error_message="发送动作租约已失效。")
        if str(payload.get("client_mid") or "") != str(action["client_mid"] or ""):
            raise AppError(status_code=409, error_category="CHAT_CLIENT_MID_MISMATCH", error_message="发送结果 clientMid 与领取动作不一致。")
        late_unknown_result = action["status"] == "unknown"
        if action["status"] not in {"leased", "dispatching"} and not late_unknown_result:
            return action
        outcome = payload["outcome"]
        now = _now()
        exchange_evidence = payload.get("evidence", {}).get("exchange") if isinstance(payload.get("evidence"), dict) else None
        if action["operation_kind"] == "resume":
            _record_resume_claim_log(
                connection,
                level="info" if outcome in {"accepted", "unknown"} else "warning",
                message="自动代聊简历交换请求结果",
                detail={
                    "chat_action_id": action_id,
                    "outcome": str(outcome),
                    "status_code": str(payload.get("status_code") or ""),
                    "exchange": exchange_evidence if isinstance(exchange_evidence, dict) else {"request_started": False},
                },
            )
        connection.execute(
            """
            UPDATE fj_chat_send_actions SET status = ?, outcome = ?, status_code = ?,
              error_message = ?, evidence_json = ?, platform_message_id = ?, client_mid = ?,
              completed_at = ?, updated_at = ?, lease_expires_at = NULL,
              dispatch_deadline_at = NULL
            WHERE id = ?
            """,
            (
                outcome,
                outcome,
                payload.get("status_code") or "",
                payload.get("message") or "",
                json.dumps(payload.get("evidence") or {}, ensure_ascii=False),
                payload.get("platform_message_id") or "",
                payload.get("client_mid") or "",
                now,
                now,
                action_id,
            ),
        )
        set_canonical_from_raw(
            connection,
            action_ref_type="chat_send_action",
            action_ref_id=action_id,
            raw_status=str(outcome),
            updated_at=now,
            reason=(
                "发送协议已接受，等待平台 outbound observation"
                if outcome == "accepted"
                else str(payload.get("message") or payload.get("status_code") or outcome)
            ),
        )
        if outcome == "accepted" and action["operation_kind"] == "text":
            record_execution_evidence_with_connection(
                connection,
                action_ref_type="chat_send_action",
                action_ref_id=action_id,
                evidence_type="protocol_acknowledged",
                source="executor",
                source_ref_type="chat_send_result",
                source_ref_id=f"{action_id}:{payload['execution_epoch']}",
                observed_at=now,
                confidence=0.9,
                evidence_level="strong_inferred",
                payload={
                    "confirmed": True,
                    "status_code": str(payload.get("status_code") or ""),
                    "client_mid": str(payload.get("client_mid") or ""),
                },
                dedupe_key=f"chat_send:{action_id}:epoch:{payload['execution_epoch']}:protocol_ack",
            )
        elif outcome == "failed":
            record_execution_evidence_with_connection(
                connection,
                action_ref_type="chat_send_action",
                action_ref_id=action_id,
                evidence_type="protocol_acknowledged",
                source="executor",
                source_ref_type="chat_send_result",
                source_ref_id=f"{action_id}:{payload['execution_epoch']}:failed",
                observed_at=now,
                confidence=1.0,
                evidence_level="direct",
                payload={
                    "confirmed": False,
                    "status_code": str(payload.get("status_code") or ""),
                },
                dedupe_key=f"chat_send:{action_id}:epoch:{payload['execution_epoch']}:failed_ack",
            )
        else:
            record_execution_evidence_with_connection(
                connection,
                action_ref_type="chat_send_action",
                action_ref_id=action_id,
                evidence_type="page_state_confirmed",
                source="executor",
                source_ref_type="chat_send_result",
                source_ref_id=f"{action_id}:{payload['execution_epoch']}:unknown",
                observed_at=now,
                confidence=0.5,
                evidence_level="weak_inferred",
                payload={"confirmed": False, "status_code": str(payload.get("status_code") or "")},
                dedupe_key=f"chat_send:{action_id}:epoch:{payload['execution_epoch']}:unknown_observation",
            )
        if outcome == "accepted":
            session = _session_or_404(connection, str(action["session_id"]))
            existing_assistant = connection.execute(
                """
                SELECT id FROM fj_chat_messages
                WHERE session_id = ? AND client_mid = ? AND source = 'assistant'
                LIMIT 1
                """,
                (session["id"], payload.get("client_mid") or ""),
            ).fetchone()
            # 远端回显可能早于发送回执；已有 assistant 记录时不再插入第二条占位消息。
            if existing_assistant is not None:
                return _action_payload(connection, action_id)
            platform_message_id = payload.get("platform_message_id") or f"assistant:{action_id}"
            message_id = _id("chat_message")
            try:
                connection.execute(
                    """
                    INSERT INTO fj_chat_messages (
                      id, session_id, platform_message_id, direction, message_type,
                      content, sender_uid, receiver_uid, client_mid, source,
                      sent_at, observed_at, raw_meta_json, created_at
                    ) VALUES (?, ?, ?, 'outbound', 'text', ?, ?, ?, ?, 'assistant', ?, ?, ?, ?)
                    """,
                    (
                        message_id,
                        session["id"],
                        platform_message_id,
                        action["text"],
                        session["account_uid"],
                        session["peer_uid"],
                        payload.get("client_mid") or "",
                        now,
                        now,
                        json.dumps(payload.get("evidence") or {}, ensure_ascii=False),
                        now,
                    ),
                )
                connection.execute(
                    """
                    UPDATE fj_chat_sessions SET session_version = session_version + 1,
                      latest_message_id = ?, last_message_at = ?, updated_at = ? WHERE id = ?
                    """,
                    (message_id, now, now, session["id"]),
                )
            except sqlite3.IntegrityError:
                pass
        return _action_payload(connection, action_id)


def _sweep_stale_send_actions(connection: sqlite3.Connection) -> int:
    now = _now()
    cursor = connection.execute(
        """
        UPDATE fj_chat_send_actions
        SET status = 'unknown', outcome = 'unknown', status_code = 'dispatch_result_timeout',
            error_message = '页面发送结果超过截止时间，请人工核对',
            completed_at = ?, updated_at = ?, lease_expires_at = NULL,
            canonical_status = 'unknown', canonical_updated_at = ?,
            canonical_reason = 'dispatch result timeout'
        WHERE status = 'dispatching' AND dispatch_deadline_at IS NOT NULL
          AND dispatch_deadline_at <= ?
        """,
        (now, now, now, now),
    )
    return int(cursor.rowcount)


def sweep_stale_send_actions(db: Database) -> int:
    with db.connect() as connection:
        return _sweep_stale_send_actions(connection)


def process_due_tasks(
    db: Database,
    config: AppConfig,
    *,
    limit: int = 10,
    force: bool = False,
) -> int:
    runtime = get_runtime(db)
    if not runtime.get("generation_enabled"):
        return 0
    if runtime.get("trigger_mode") == "manual" and not force:
        return 0
    now_dt = datetime.now(timezone.utc)
    trigger_mode = runtime.get("trigger_mode")
    due = force or trigger_mode == "immediate"
    if trigger_mode == "interval" and not force:
        last = _parse_time(runtime.get("last_scheduled_at"))
        due = last is None or now_dt - last >= timedelta(minutes=int(runtime.get("interval_minutes") or 30))
    if not due:
        return 0
    now = _now()
    with db.connect() as connection:
        due_clause = "" if force else "AND (t.generation_due_at IS NULL OR t.generation_due_at <= ?)"
        params: list[Any] = [] if force else [now]
        params.append(limit)
        task_rows = connection.execute(
            f"""
            SELECT t.id, t.session_id FROM fj_chat_reply_tasks t
            JOIN fj_chat_sessions s ON s.id = t.session_id
            WHERE t.is_draft = 1 AND t.status = 'pending_generation' AND s.status = 'active'
              {due_clause}
            ORDER BY t.created_at ASC LIMIT ?
            """,
            params,
        ).fetchall()
        if trigger_mode == "interval" and not force:
            connection.execute(
                "UPDATE fj_chat_runtime SET last_scheduled_at = ?, updated_at = ? WHERE id = ?",
                (now, now, RUNTIME_ID),
            )
    completed = 0
    for task in task_rows:
        try:
            generate_reply(db, config, str(task["session_id"]))
            completed += 1
        except Exception:
            # 单条生成失败已写入任务，不能阻断其它会话。
            continue
    return completed


def _run_generation_timer(db: Database, config: AppConfig, task_id: str) -> None:
    with _generation_timers_lock:
        _generation_timers.pop(task_id, None)
    try:
        process_due_tasks(db, config)
    finally:
        # 生成失败或间隔尚未到达时，按数据库中的最新时间重新安排一次性任务。
        schedule_pending_generation(db, config)


def schedule_pending_generation(db: Database, config: AppConfig) -> None:
    """根据待生成任务的到期时间安排一次性回调，不启动常驻扫描线程。"""
    runtime = get_runtime(db)
    if not runtime.get("generation_enabled") or runtime.get("trigger_mode") == "manual":
        with _generation_timers_lock:
            for timer in _generation_timers.values():
                timer.cancel()
            _generation_timers.clear()
        return

    now = datetime.now(timezone.utc)
    interval_due = None
    if runtime.get("trigger_mode") == "interval":
        last_scheduled = _parse_time(runtime.get("last_scheduled_at"))
        if last_scheduled is not None:
            interval_due = last_scheduled + timedelta(
                minutes=int(runtime.get("interval_minutes") or 30)
            )

    with db.connect() as connection:
        rows = connection.execute(
            """
            SELECT t.id, t.generation_due_at
            FROM fj_chat_reply_tasks t
            JOIN fj_chat_sessions s ON s.id = t.session_id
            WHERE t.is_draft = 1 AND t.status = 'pending_generation' AND s.status = 'active'
            """
        ).fetchall()

    active_ids = {str(row["id"]) for row in rows}
    with _generation_timers_lock:
        for task_id in set(_generation_timers) - active_ids:
            _generation_timers.pop(task_id).cancel()

    for row in rows:
        due = _parse_time(row["generation_due_at"]) or now
        if interval_due is not None and interval_due > due:
            due = interval_due
        delay = max(0.0, (due - datetime.now(timezone.utc)).total_seconds())
        timer = threading.Timer(
            delay,
            _run_generation_timer,
            args=(db, config, str(row["id"])),
        )
        timer.daemon = True
        with _generation_timers_lock:
            previous = _generation_timers.get(str(row["id"]))
            if previous is not None:
                previous.cancel()
            _generation_timers[str(row["id"])] = timer
        timer.start()


CHAT_BATCH_LIMIT = 20


def get_batch_candidates(
    db: Database,
    *,
    session_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    """返回需要同步最新消息的会话，并计算岗位详情状态。"""
    session_filter = ""
    parameters: tuple[Any, ...] = ()
    if session_ids is not None:
        normalized_ids = [str(value).strip() for value in session_ids if str(value).strip()]
        if not normalized_ids:
            return []
        placeholders = ",".join("?" for _ in normalized_ids)
        session_filter = f"AND s.id IN ({placeholders})"
        parameters = tuple(normalized_ids)
    with db.connect() as connection:
        rows = connection.execute(
            f"""
            SELECT s.id, s.peer_name, s.company_name, s.job_title,
                   s.job_id, s.encrypt_job_id,
                   s.message_update_required,
                   EXISTS(
                     SELECT 1 FROM fj_chat_messages m WHERE m.session_id = s.id
                   ) AS has_local_messages,
                   COALESCE(
                     (SELECT j.detail_status FROM fj_boss_jobs j WHERE j.id = s.job_id LIMIT 1),
                     (SELECT j.detail_status FROM fj_boss_jobs j
                      WHERE s.encrypt_job_id <> '' AND j.encrypt_job_id = s.encrypt_job_id LIMIT 1),
                     'not_collected'
                   ) AS job_detail_status
            FROM fj_chat_sessions s
            WHERE (
              NOT EXISTS(
                SELECT 1 FROM fj_chat_messages m WHERE m.session_id = s.id
              ) OR s.message_update_required = 1
            )
            {session_filter}
            -- 批量队列与左侧会话列表保持同一套 BOSS 顺序。
            ORDER BY s.platform_synced_at DESC, s.platform_list_index ASC, s.id ASC
            """,
            parameters,
        ).fetchall()
    return [_row(row) or {} for row in rows]


def get_batch_summary(db: Database) -> dict[str, int]:
    candidates = get_batch_candidates(db)
    queued = candidates[:CHAT_BATCH_LIMIT]
    return {
        "pending_chat_count": len(candidates),
        "pending_job_count": sum(
            1 for item in candidates if item.get("job_detail_status") != "completed"
        ),
        "queued_chat_count": len(queued),
        "batch_limit": CHAT_BATCH_LIMIT,
    }


def get_job_batch_candidates(
    db: Database,
    *,
    session_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    """返回已关联岗位且仍缺少详情的会话，供仅岗位批量采集使用。"""
    session_filter = ""
    parameters: tuple[Any, ...] = ()
    if session_ids is not None:
        normalized_ids = [str(value).strip() for value in session_ids if str(value).strip()]
        if not normalized_ids:
            return []
        placeholders = ",".join("?" for _ in normalized_ids)
        session_filter = f"AND s.id IN ({placeholders})"
        parameters = tuple(normalized_ids)
    with db.connect() as connection:
        rows = connection.execute(
            f"""
            SELECT s.id, s.peer_name, s.company_name, s.job_title,
                   s.job_id, s.encrypt_job_id,
                   COALESCE(
                     (SELECT j.detail_status FROM fj_boss_jobs j WHERE j.id = s.job_id LIMIT 1),
                     (SELECT j.detail_status FROM fj_boss_jobs j
                      WHERE s.encrypt_job_id <> '' AND j.encrypt_job_id = s.encrypt_job_id LIMIT 1),
                     'not_collected'
                   ) AS job_detail_status
            FROM fj_chat_sessions s
            WHERE (COALESCE(s.job_id, '') <> '' OR s.encrypt_job_id <> '')
              AND COALESCE(
                (SELECT j.detail_status FROM fj_boss_jobs j WHERE j.id = s.job_id LIMIT 1),
                (SELECT j.detail_status FROM fj_boss_jobs j
                 WHERE s.encrypt_job_id <> '' AND j.encrypt_job_id = s.encrypt_job_id LIMIT 1),
                'not_collected'
              ) <> 'completed'
            {session_filter}
            ORDER BY s.platform_synced_at DESC, s.platform_list_index ASC, s.id ASC
            """,
            parameters,
        ).fetchall()
    return [_row(row) or {} for row in rows]


class BossChatBatchManager:
    """按会话顺序同步最新聊天，并按需补齐岗位详情。"""

    def __init__(self) -> None:
        self._tasks: dict[str, dict[str, Any]] = {}
        self._active_task_id = ""

    def start(
        self,
        db: Database,
        config: AppConfig,
        *,
        batch_size: int = CHAT_BATCH_LIMIT,
        session_ids: list[str] | None = None,
        mode: str = "chat_and_job",
    ) -> dict[str, Any]:
        if mode not in {"chat_and_job", "job_only"}:
            raise AppError(422, "CHAT_BATCH_MODE_INVALID", "批量更新模式无效。")
        scoped_ids = (
            [str(value).strip() for value in session_ids if str(value).strip()]
            if session_ids is not None
            else None
        )
        if self._active_task_id:
            active = self._tasks.get(self._active_task_id)
            if active and active["status"] in {"queued", "running"}:
                if active.get("mode") != mode or (
                    scoped_ids is not None and active.get("scope_session_ids") != scoped_ids
                ):
                    raise AppError(
                        409,
                        "CHAT_BATCH_ACTIVE",
                        "已有批量更新任务正在执行，请等待完成后重试。",
                    )
                return self.get(self._active_task_id)
        candidate_source = get_batch_candidates if mode == "chat_and_job" else get_job_batch_candidates
        candidates = candidate_source(db, session_ids=scoped_ids)[
            :max(1, min(batch_size, CHAT_BATCH_LIMIT))
        ]
        if not candidates:
            message = "当前没有需要更新的聊天记录。" if mode == "chat_and_job" else "当前没有需要补采的岗位。"
            raise AppError(409, "CHAT_BATCH_EMPTY", message)
        now = _now()
        task_id = _id("chat_batch")
        self._tasks[task_id] = {
            "id": task_id,
            "mode": mode,
            "status": "queued",
            "total": len(candidates),
            "current": 0,
            "chat_completed": 0,
            "job_completed": 0,
            "job_skipped": 0,
            "failed": 0,
            "current_session_name": "",
            "current_job_title": "",
            "stage": "queued",
            "message": "批量更新任务已创建。" if mode == "chat_and_job" else "批量岗位采集任务已创建。",
            "created_at": now,
            "finished_at": None,
            "candidates": candidates,
            "scope_session_ids": scoped_ids,
        }
        self._active_task_id = task_id
        threading.Thread(target=self._run, args=(task_id, db, config), daemon=True).start()
        return self.get(task_id)

    def get(self, task_id: str) -> dict[str, Any]:
        task = self._tasks.get(task_id)
        if task is None:
            raise AppError(404, "CHAT_BATCH_NOT_FOUND", "批量更新任务不存在。")
        return {
            key: value
            for key, value in task.items()
            if key not in {"candidates", "scope_session_ids"}
        }

    def _run(self, task_id: str, db: Database, config: AppConfig) -> None:
        task = self._tasks[task_id]
        is_job_only = task["mode"] == "job_only"
        task.update(
            status="running",
            stage="collecting_job" if is_job_only else "syncing_chat",
            message="开始采集岗位详情。" if is_job_only else "开始同步聊天记录。",
        )
        try:
            for index, candidate in enumerate(task["candidates"], start=1):
                task.update(
                    current=index - 1,
                    current_session_name=str(candidate.get("peer_name") or candidate.get("company_name") or "当前会话"),
                    current_job_title=str(candidate.get("job_title") or ""),
                    stage="collecting_job" if is_job_only else "syncing_chat",
                    message="正在采集岗位详情。" if is_job_only else "正在获取最新 20 条聊天消息。",
                )
                try:
                    # 仅岗位模式复用会话关联信息，不触发聊天消息同步。
                    if not is_job_only:
                        refresh_session_history(db, str(candidate["id"]))
                        task["chat_completed"] += 1
                    job_action = prepare_chat_job(
                        db,
                        str(candidate["id"]),
                        can_fetch_details=True,
                    )
                    if job_action["action"] == "update":
                        task.update(stage="waiting_for_job", message="等待后开始采集岗位详情。")
                        time.sleep(random.randint(1, 3))
                        task.update(stage="collecting_job", message="正在采集岗位详情。")
                        job_task = boss_capture_task_manager.start_history_detail(
                            job_action["job"],
                            output_dir=config.output_root / "fine-job" / "boss-capture",
                            db=db,
                        )
                        while str(job_task.get("status") or "") in {"queued", "running"}:
                            time.sleep(1)
                            job_task = boss_capture_task_manager.get_task(str(job_task["id"]))
                        if str(job_task.get("status") or "") == "completed":
                            task["job_completed"] += 1
                        else:
                            task["failed"] += 1
                    else:
                        task["job_skipped"] += 1
                except Exception as exc:
                    task["failed"] += 1
                    task["message"] = f"当前会话处理失败：{str(exc)[:120]}"
                task["current"] = index
                if index < task["total"]:
                    task.update(
                        stage="waiting_next",
                        message="等待后处理下一条岗位记录。" if is_job_only else "等待后处理下一条聊天记录。",
                    )
                    time.sleep(random.randint(2, 5))
            task.update(
                status="completed",
                stage="completed",
                message="批量岗位采集已完成。" if is_job_only else "批量更新已完成。",
                finished_at=_now(),
            )
        except Exception as exc:
            task.update(status="failed", stage="failed", message=str(exc)[:300], finished_at=_now())
        finally:
            if self._active_task_id == task_id:
                self._active_task_id = ""


boss_chat_batch_manager = BossChatBatchManager()
