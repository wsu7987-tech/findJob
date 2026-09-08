from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


_RECOVERABLE_REPLY_STATUSES = {"failed", "cancelled", "stale", "superseded"}
_TERMINAL_STATUSES = {"succeeded", "failed", "cancelled", "blocked", "unknown", "stale", "superseded"}


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _id() -> str:
    return f"fj_action_{uuid4().hex}"


def _loads(value: str | None, fallback: Any) -> Any:
    try:
        return json.loads(value or "")
    except (TypeError, ValueError):
        return fallback


def _unified_status(source_table: str, row: sqlite3.Row) -> str:
    status = str(row["status"] or "queued")
    if source_table == "fj_automation_actions":
        if status in {"running", "leased"}:
            return "dispatching" if row["dispatch_started_at"] else "claimed"
        return status if status in {"queued", "succeeded", "failed", "blocked", "unknown", "cancelled"} else "unknown"
    return {"leased": "claimed", "dispatching": "dispatching", "accepted": "accepted", "queued": "queued", "failed": "failed", "unknown": "unknown", "cancelled": "cancelled"}.get(status, "unknown")


def _greeting_account_uid(connection: sqlite3.Connection, job_id: str) -> str:
    """兼容展示已有局部账号信息，不将其作为 greeting 的调度前置条件。"""
    binding = connection.execute(
        "SELECT account_uid FROM fj_greeting_account_bindings WHERE job_id = ?",
        (job_id,),
    ).fetchone()
    if binding is not None:
        return str(binding["account_uid"] or "")
    rows = connection.execute(
        "SELECT DISTINCT account_uid FROM fj_chat_sessions WHERE job_id = ? AND account_uid <> '' ORDER BY account_uid",
        (job_id,),
    ).fetchall()
    return str(rows[0]["account_uid"] or "") if len(rows) == 1 else ""


def bind_greeting_account(
    connection: sqlite3.Connection,
    *,
    job_id: str,
    account_uid: str,
) -> None:
    """由已验证的账号上下文补全 greeting，统一 Action 才可恢复排队资格。"""
    if not job_id or not account_uid:
        raise ValueError("greeting 账号绑定需要完整岗位与账号身份")
    now = _now()
    connection.execute(
        """INSERT INTO fj_greeting_account_bindings
           (job_id, account_uid, binding_source, bound_at, updated_at)
           VALUES (?, ?, 'explicit_binding', ?, ?)
           ON CONFLICT(job_id) DO UPDATE SET
             account_uid=excluded.account_uid, binding_source=excluded.binding_source,
             updated_at=excluded.updated_at""",
        (job_id, account_uid, now, now),
    )
    rows = connection.execute(
        "SELECT id FROM fj_automation_actions WHERE job_id=? AND action_type='BOSS_DEFAULT_GREETING'",
        (job_id,),
    ).fetchall()
    for row in rows:
        sync_legacy_action(connection, source_table="fj_automation_actions", source_id=str(row["id"]))
    job = connection.execute("SELECT encrypt_job_id FROM fj_boss_jobs WHERE id=?", (job_id,)).fetchone()
    encrypt_job_id = str(job["encrypt_job_id"] or "") if job else ""
    connection.execute(
        """UPDATE fj_actions SET account_uid=?, target_page_key=?, target_context_key='',
             waiting_reason_code='', waiting_reason_detail='', waiting_since_at=NULL, updated_at=?
           WHERE job_id=? AND action_type='greeting' AND source_table='fj_actions_direct'""",
        (account_uid, f"boss:{account_uid}:job:{encrypt_job_id}" if encrypt_job_id else "", now, job_id),
    )


def _authorization_mode(value: Any) -> str:
    return "preauthorized" if str(value or "") == "pre_authorized" else "manual"


def _snapshot(connection: sqlite3.Connection, source_table: str, source_id: str) -> dict[str, Any] | None:
    if source_table == "fj_automation_actions":
        row = connection.execute(
            """SELECT a.*, j.encrypt_job_id FROM fj_automation_actions a
               JOIN fj_boss_jobs j ON j.id = a.job_id
               WHERE a.id = ? AND a.action_type = 'BOSS_DEFAULT_GREETING'""",
            (source_id,),
        ).fetchone()
        if row is None:
            return None
        payload = _loads(str(row["payload_json"] or "{}"), {})
        payload = payload if isinstance(payload, dict) else {}
        account_uid = _greeting_account_uid(connection, str(row["job_id"]))
        status = _unified_status(source_table, row)
        return {
            "action_type": "greeting", "account_uid": account_uid, "job_id": str(row["job_id"]), "session_id": None,
            "target_page_kind": "job", "target_page_key": f"boss:{account_uid}:job:{row['encrypt_job_id']}" if account_uid else "",
            "target_context_key": "" if account_uid else f"greeting_account_binding:{row['job_id']}",
            "text": str(payload.get("message") or ""), "resume_id": "", "resume_filename": "", "payload": payload,
            "base_id": None, "base_mid": "", "base_version": None, "priority": 100,
            "status": status, "available_at": str(row["created_at"]),
            "authorization": _authorization_mode(row["authorization_mode"]),
            "waiting_code": "",
            "waiting_detail": "",
            "waiting_since": None,
            "lease_owner": str(row["executor_id"] or "") or None, "lease_expires_at": None,
            "epoch": int(row["execution_epoch"] or 0), "attempts": int(row["attempt_count"] or 0),
            "dispatch_deadline": None, "dispatched_at": row["dispatch_started_at"],
            "canonical": str(row["canonical_status"] or "pending"),
            "outcome": (_loads(str(row["result_json"] or "{}"), {}) or {}).get("outcome"),
            "status_code": str(row["last_status_code"] or ""), "completed_at": row["completed_at"],
            "created_at": str(row["created_at"]), "updated_at": str(row["updated_at"]), "coverage": [],
        }
    row = connection.execute(
        """SELECT a.*, s.account_uid, s.job_id, s.session_version FROM fj_chat_send_actions a
           JOIN fj_chat_sessions s ON s.id = a.session_id
           WHERE a.id = ? AND a.operation_kind IN ('text', 'resume')""",
        (source_id,),
    ).fetchone()
    if row is None:
        return None
    task = connection.execute(
        "SELECT based_on_message_id, based_on_session_version, input_message_ids_json FROM fj_chat_reply_tasks WHERE id = ?",
        (row["reply_task_id"],),
    ).fetchone()
    coverage = _loads(str(task["input_message_ids_json"] or "[]"), []) if task else []
    coverage = [str(item) for item in coverage if isinstance(item, str) and item]
    base_id = str(task["based_on_message_id"] or "") if task else ""
    if base_id and base_id not in coverage:
        coverage.append(base_id)
    base = connection.execute("SELECT platform_message_id FROM fj_chat_messages WHERE id = ?", (base_id,)).fetchone() if base_id else None
    action_type = "chat_message" if row["operation_kind"] == "text" else "resume_send"
    status = _unified_status(source_table, row)
    return {
        "action_type": action_type, "account_uid": str(row["account_uid"] or ""), "job_id": str(row["job_id"]) if row["job_id"] else None,
        "session_id": str(row["session_id"]), "target_page_kind": "chat", "target_page_key": f"boss:{row['account_uid']}:chat" if row["account_uid"] else "",
        "target_context_key": "",
        "text": str(row["text"] or ""), "resume_id": str(row["encrypt_resume_id"] or ""), "resume_filename": str(row["resume_filename"] or ""),
        "payload": {"reply_task_id": str(row["reply_task_id"]), "operation_kind": str(row["operation_kind"])},
        "base_id": base_id or None, "base_mid": str(base["platform_message_id"] or ""), "base_version": int(task["based_on_session_version"] or 0) if task else None,
        "priority": 300 if action_type == "chat_message" else 200, "status": status, "available_at": str(row["created_at"]),
        "authorization": _authorization_mode(row["authorization_mode"]),
        "waiting_code": "legacy_executor_queue" if status == "queued" else "", "waiting_detail": "由旧聊天执行队列实际执行" if status == "queued" else "", "waiting_since": str(row["created_at"]) if status == "queued" else None,
        "lease_owner": str(row["lease_owner"] or "") or None, "lease_expires_at": row["lease_expires_at"], "epoch": int(row["execution_epoch"] or 0), "attempts": int(row["attempt_count"] or 0),
        "dispatch_deadline": row["dispatch_deadline_at"], "dispatched_at": row["dispatched_at"], "canonical": str(row["canonical_status"] or "pending"), "outcome": row["outcome"],
        "status_code": str(row["status_code"] or ""), "completed_at": row["completed_at"], "created_at": str(row["created_at"]), "updated_at": str(row["updated_at"]), "coverage": coverage,
    }


def sync_legacy_action(connection: sqlite3.Connection, *, source_table: str, source_id: str) -> str | None:
    """以旧 executor 的真实记录更新统一 shadow Action，重复调用不会生成副本。"""
    data = _snapshot(connection, source_table, source_id)
    if data is None:
        return None
    if str(data["canonical"]) == "succeeded":
        data["status"] = "succeeded"
        data["outcome"] = data["outcome"] or "accepted"
        data["completed_at"] = data["completed_at"] or data["updated_at"]
    elif str(data["canonical"]) == "unknown" and str(data["status"]) == "succeeded":
        data["status"] = "unknown"
    existing = connection.execute(
        "SELECT id, status, canonical_status, outcome, completed_at FROM fj_actions WHERE source_table = ? AND source_id = ?",
        (source_table, source_id),
    ).fetchone()
    if existing is None:
        action_id = _id()
        sequence = None
        if data["session_id"]:
            row = connection.execute("SELECT COALESCE(MAX(session_sequence), 0) AS value FROM fj_actions WHERE session_id = ?", (data["session_id"],)).fetchone()
            sequence = int(row["value"] or 0) + 1
        connection.execute(
            """INSERT INTO fj_actions (
              id, action_type, account_uid, job_id, session_id, target_page_kind, target_page_key, target_context_key, session_sequence,
              priority, priority_source, authorization_mode, text, encrypt_resume_id, resume_filename, payload_json,
              base_raw_message_id, base_message_mid, base_conversation_revision, planned_at, status, available_at,
              waiting_reason_code, waiting_reason_detail, waiting_since_at, lease_owner, lease_expires_at,
              execution_epoch, attempt_count, dispatch_deadline_at, dispatched_at, canonical_status, outcome, status_code,
              completed_at, created_at, updated_at, source_table, source_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'stage1_default', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (action_id, data["action_type"], data["account_uid"], data["job_id"], data["session_id"], data["target_page_kind"], data["target_page_key"], data["target_context_key"], sequence,
             data["priority"], data["authorization"], data["text"], data["resume_id"], data["resume_filename"], json.dumps(data["payload"], ensure_ascii=False), data["base_id"], data["base_mid"], data["base_version"], data["created_at"], data["status"], data["available_at"], data["waiting_code"], data["waiting_detail"], data["waiting_since"], data["lease_owner"], data["lease_expires_at"], data["epoch"], data["attempts"], data["dispatch_deadline"], data["dispatched_at"], data["canonical"], data["outcome"], data["status_code"], data["completed_at"], data["created_at"], data["updated_at"], source_table, source_id),
        )
    else:
        action_id = str(existing["id"])
        status = str(data["status"])
        canonical = str(data["canonical"])
        outcome = data["outcome"]
        completed_at = data["completed_at"]
        # 已观察或直接证据确认的成功状态为最终权威，旧 executor 的后到终态不能覆盖。
        if str(existing["status"]) == "succeeded":
            status = "succeeded"
            canonical = "succeeded"
            outcome = existing["outcome"]
            completed_at = existing["completed_at"]
        elif str(existing["canonical_status"]) == "unknown" and canonical not in {"unknown", "succeeded"}:
            canonical = "unknown"
            if status == "succeeded":
                status = "unknown"
        if canonical == "unknown" and status == "succeeded":
            status = "unknown"
        connection.execute(
            """UPDATE fj_actions SET action_type=?, account_uid=?, job_id=?, session_id=?, target_page_kind=?, target_page_key=?, target_context_key=?,
              priority=?, authorization_mode=?, text=?, encrypt_resume_id=?, resume_filename=?, payload_json=?, base_raw_message_id=?, base_message_mid=?,
              base_conversation_revision=?, planned_at=?, status=?, available_at=?, waiting_reason_code=?, waiting_reason_detail=?,
              waiting_since_at=?, lease_owner=?, lease_expires_at=?, execution_epoch=?, attempt_count=?, dispatch_deadline_at=?,
              dispatched_at=?, canonical_status=?, outcome=?, status_code=?, completed_at=?, created_at=?, updated_at=? WHERE id=?""",
            (data["action_type"], data["account_uid"], data["job_id"], data["session_id"], data["target_page_kind"], data["target_page_key"], data["target_context_key"], data["priority"], data["authorization"], data["text"], data["resume_id"], data["resume_filename"], json.dumps(data["payload"], ensure_ascii=False), data["base_id"], data["base_mid"], data["base_version"], data["created_at"], status, data["available_at"], data["waiting_code"], data["waiting_detail"], data["waiting_since"], data["lease_owner"], data["lease_expires_at"], data["epoch"], data["attempts"], data["dispatch_deadline"], data["dispatched_at"], canonical, outcome, data["status_code"], completed_at, data["created_at"], data["updated_at"], action_id),
        )
    if data["base_id"]:
        link_action_messages(connection, action_id=action_id, message_ids=[data["base_id"]], role="trigger")
    link_action_messages(connection, action_id=action_id, message_ids=list(data["coverage"]), role="covered")
    reconcile_action_coverage_state(connection, action_id=action_id)
    return action_id


def create_legacy_action(connection: sqlite3.Connection, **kwargs: Any) -> str:
    action_id = sync_legacy_action(connection, source_table=str(kwargs["source_table"]), source_id=str(kwargs["source_id"]))
    if action_id is None:
        raise ValueError("旧 Action 不存在，无法创建统一 Action")
    return action_id


def sync_legacy_action_authorization(
    connection: sqlite3.Connection,
    *,
    source_table: str,
    source_id: str,
) -> str | None:
    """旧队列授权变化沿用同一生命周期投影，避免独立字段漂移。"""
    return sync_legacy_action(connection, source_table=source_table, source_id=source_id)


def backfill_legacy_actions(connection: sqlite3.Connection) -> None:
    """初始化时按创建顺序回填两套旧队列，source 映射保证幂等。"""
    for source_table, sql in (
        ("fj_automation_actions", "SELECT id FROM fj_automation_actions WHERE action_type='BOSS_DEFAULT_GREETING' ORDER BY created_at, id"),
        ("fj_chat_send_actions", "SELECT id FROM fj_chat_send_actions WHERE operation_kind IN ('text','resume') ORDER BY created_at, id"),
    ):
        for row in connection.execute(sql).fetchall():
            sync_legacy_action(connection, source_table=source_table, source_id=str(row["id"]))


def link_action_messages(connection: sqlite3.Connection, *, action_id: str, message_ids: list[str], role: str) -> None:
    now = _now()
    for ordinal, message_id in enumerate(dict.fromkeys(message_ids)):
        if not message_id:
            continue
        connection.execute("INSERT OR IGNORE INTO fj_action_message_links (action_id, raw_message_id, role, ordinal, created_at) VALUES (?, ?, ?, ?, ?)", (action_id, message_id, role, ordinal, now))
        if role in {"trigger", "covered"}:
            connection.execute("UPDATE fj_chat_message_states SET reply_state=CASE WHEN reply_state IN ('unanswered','reply_planned') THEN 'queued' ELSE reply_state END, reply_planned_at=COALESCE(reply_planned_at, ?), updated_at=? WHERE raw_message_id=? AND reply_state<>'not_required'", (now, now, message_id))


def mark_messages_reply_planned(connection: sqlite3.Connection, message_ids: list[str]) -> None:
    """一个回复任务覆盖多条 inbound 时，将全部消息推进到已规划状态。"""
    now = _now()
    for message_id in dict.fromkeys(message_ids):
        connection.execute("UPDATE fj_chat_message_states SET reply_state=CASE WHEN reply_state='unanswered' THEN 'reply_planned' ELSE reply_state END, reply_planned_at=COALESCE(reply_planned_at, ?), updated_at=? WHERE raw_message_id=? AND reply_state<>'not_required'", (now, now, message_id))


def reconcile_action_coverage_state(connection: sqlite3.Connection, *, action_id: str) -> None:
    action = connection.execute("SELECT status FROM fj_actions WHERE id=?", (action_id,)).fetchone()
    if action is None or str(action["status"]) not in _RECOVERABLE_REPLY_STATUSES:
        return
    replacement = connection.execute("SELECT 1 FROM fj_actions WHERE supersedes_action_id=? AND status NOT IN ('failed','cancelled','stale','superseded') LIMIT 1", (action_id,)).fetchone()
    if replacement is not None:
        return
    now = _now()
    connection.execute("UPDATE fj_chat_message_states SET reply_state='unanswered', reply_planned_at=NULL, updated_at=? WHERE raw_message_id IN (SELECT raw_message_id FROM fj_action_message_links WHERE action_id=? AND role IN ('trigger','covered')) AND reply_state<>'not_required'", (now, action_id))


def reconcile_reply_task_coverage(connection: sqlite3.Connection, *, reply_task_id: str) -> None:
    """草稿在未产生有效替代发送动作时失效，覆盖消息回到待回复状态。"""
    task = connection.execute(
        "SELECT status, based_on_message_id, input_message_ids_json FROM fj_chat_reply_tasks WHERE id=?",
        (reply_task_id,),
    ).fetchone()
    if task is None or str(task["status"]) not in {"failed", "cancelled", "stale"}:
        return
    message_ids = _loads(str(task["input_message_ids_json"] or "[]"), [])
    message_ids = [str(item) for item in message_ids if isinstance(item, str) and item]
    base_id = str(task["based_on_message_id"] or "")
    if base_id and base_id not in message_ids:
        message_ids.append(base_id)
    if not message_ids:
        return
    placeholders = ",".join("?" for _ in message_ids)
    replacement = connection.execute(
        f"""SELECT 1 FROM fj_actions action
            JOIN fj_action_message_links link ON link.action_id=action.id
            WHERE link.raw_message_id IN ({placeholders}) AND link.role IN ('trigger','covered')
              AND action.action_type='chat_message'
              AND action.status NOT IN ('failed','cancelled','stale','superseded')
            LIMIT 1""",
        message_ids,
    ).fetchone()
    if replacement is not None:
        return
    now = _now()
    connection.execute(
        f"""UPDATE fj_chat_message_states
            SET reply_state='unanswered', reply_planned_at=NULL, updated_at=?
            WHERE raw_message_id IN ({placeholders}) AND reply_state<>'not_required'""",
        [now, *message_ids],
    )


def transfer_action_coverage(connection: sqlite3.Connection, *, previous_action_id: str, replacement_action_id: str) -> None:
    """替代 Action 继承完整 coverage，重规划期间不会错误回退消息状态。"""
    now = _now()
    rows = connection.execute("SELECT raw_message_id, role, ordinal FROM fj_action_message_links WHERE action_id=? AND role IN ('trigger','covered','new_context')", (previous_action_id,)).fetchall()
    for row in rows:
        connection.execute("INSERT OR IGNORE INTO fj_action_message_links (action_id, raw_message_id, role, ordinal, created_at) VALUES (?, ?, ?, ?, ?)", (replacement_action_id, row["raw_message_id"], row["role"], row["ordinal"], now))
    message_ids = list(dict.fromkeys(str(row["raw_message_id"]) for row in rows))
    if message_ids:
        placeholders = ",".join("?" for _ in message_ids)
        connection.execute(
            f"""UPDATE fj_chat_message_states
                SET reply_state=CASE WHEN reply_state IN ('unanswered','reply_planned') THEN 'queued' ELSE reply_state END,
                    reply_planned_at=COALESCE(reply_planned_at, ?), updated_at=?
                WHERE raw_message_id IN ({placeholders}) AND reply_state<>'not_required'""",
            [now, now, *message_ids],
        )
    connection.execute(
        "UPDATE fj_actions SET supersedes_action_id=?, updated_at=? WHERE id=?",
        (previous_action_id, now, replacement_action_id),
    )
    connection.execute("UPDATE fj_actions SET status='superseded', updated_at=? WHERE id=?", (now, previous_action_id))


def observe_action_outbound_result(connection: sqlite3.Connection, *, source_table: str, source_id: str, raw_message_id: str) -> None:
    action = connection.execute("SELECT id FROM fj_actions WHERE source_table=? AND source_id=?", (source_table, source_id)).fetchone()
    if action is None:
        return
    now = _now()
    action_id = str(action["id"])
    link_action_messages(connection, action_id=action_id, message_ids=[raw_message_id], role="outbound_result")
    connection.execute("UPDATE fj_actions SET status='succeeded', canonical_status='succeeded', outcome='accepted', completed_at=COALESCE(completed_at, ?), updated_at=? WHERE id=?", (now, now, action_id))
    connection.execute("UPDATE fj_chat_message_states SET reply_state='replied', replied_at=?, updated_at=? WHERE raw_message_id IN (SELECT raw_message_id FROM fj_action_message_links WHERE action_id=? AND role IN ('trigger','covered')) AND reply_state<>'not_required'", (now, now, action_id))
