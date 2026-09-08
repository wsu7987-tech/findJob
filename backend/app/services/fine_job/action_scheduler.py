"""BOSS 统一 Action 调度、领取与执行前复核。"""
from __future__ import annotations

import json
import random
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from backend.app.db import Database
from backend.app.errors import AppError
from backend.app.services.fine_job.action_store import (
    link_action_messages,
    transfer_action_coverage,
)


LEASE_SECONDS = 45
DISPATCH_SECONDS = 30
MAX_SAME_PAGE_BATCH = 8
_TERMINAL_PREDECESSORS = {"succeeded", "superseded", "stale", "cancelled", "failed", "blocked"}


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _after(seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _loads(value: str | None, fallback: Any) -> Any:
    try:
        return json.loads(value or "")
    except (TypeError, ValueError):
        return fallback


def _id() -> str:
    return f"fj_action_{uuid4().hex}"


def _token() -> str:
    return f"fj_dispatch_{uuid4().hex}"


def _client_mid() -> str:
    """为既有 MQTT sender 固化安全整数范围内的 clientMid 字符串。"""
    return str(random.SystemRandom().randint(1_000_000_000_000_000_000, 9_000_000_000_000_000_000))


def _log(connection: sqlite3.Connection, action: sqlite3.Row | dict[str, Any], event: str, **detail: Any) -> None:
    """记录结构化生命周期事件，供后续 UI 与审计读取。"""
    action_id = str(action["id"])
    connection.execute(
        """INSERT INTO fj_action_logs (id, level, action_type, message, detail_json, created_at)
           VALUES (?, 'info', ?, ?, ?, ?)""",
        (f"action_log_{uuid4().hex}", str(action["action_type"]), event,
         json.dumps({"event": event, "action_id": action_id, **detail}, ensure_ascii=False), _now()),
    )


def _runtime(connection: sqlite3.Connection) -> sqlite3.Row:
    row = connection.execute("SELECT * FROM fj_chat_runtime WHERE id='boss'").fetchone()
    if row is not None:
        return row
    now = _now()
    connection.execute(
        """INSERT INTO fj_chat_runtime
           (id, listen_enabled, generation_enabled, send_enabled, trigger_mode, interval_minutes, leader_epoch, created_at, updated_at)
           VALUES ('boss', 0, 0, 0, 'interval', 30, 0, ?, ?)""",
        (now, now),
    )
    return connection.execute("SELECT * FROM fj_chat_runtime WHERE id='boss'").fetchone()


def _serialize(connection: sqlite3.Connection, action_id: str) -> dict[str, Any]:
    row = connection.execute(
        """SELECT a.*, s.peer_uid, s.encrypt_peer_uid, s.security_id, s.encrypt_job_id,
                  s.status AS session_status, j.encrypt_job_id AS job_encrypt_job_id
           FROM fj_actions a
           LEFT JOIN fj_chat_sessions s ON s.id=a.session_id
           LEFT JOIN fj_boss_jobs j ON j.id=a.job_id
           WHERE a.id=?""",
        (action_id,),
    ).fetchone()
    if row is None:
        raise AppError(404, "UNIFIED_ACTION_NOT_FOUND", "统一动作不存在。")
    result = dict(row)
    result["payload"] = _loads(result.pop("payload_json", "{}"), {})
    # 扩展沿用现有 chat sender 所需字段，业务决定仍只由统一 Action 给出。
    result["operation_kind"] = {
        "chat_message": "text", "resume_send": "resume", "greeting": "greeting"
    }[str(row["action_type"])]
    result["client_mid"] = str(result["payload"].get("client_mid") or "")
    result["encrypt_job_id"] = str(row["encrypt_job_id"] or row["job_encrypt_job_id"] or "")
    return result


def create_action(
    connection: sqlite3.Connection,
    *,
    action_type: str,
    account_uid: str,
    job_id: str | None = None,
    session_id: str | None = None,
    text: str = "",
    encrypt_resume_id: str = "",
    resume_filename: str = "",
    payload: dict[str, Any] | None = None,
    base_raw_message_id: str | None = None,
    base_message_mid: str = "",
    base_conversation_revision: int | None = None,
    authorization_mode: str = "manual",
    status: str = "queued",
    session_sequence: int | None = None,
    revision_no: int = 1,
    supersedes_action_id: str | None = None,
    source_table: str = "fj_actions_direct",
    source_id: str | None = None,
) -> str:
    """直接创建统一 Action，新的业务动作不再写入旧领取队列。"""
    if action_type not in {"greeting", "chat_message", "resume_send"}:
        raise ValueError("不支持的统一动作类型")
    now = _now()
    if session_id and session_sequence is None:
        row = connection.execute(
            "SELECT COALESCE(MAX(session_sequence), 0) AS value FROM fj_actions WHERE session_id=?", (session_id,)
        ).fetchone()
        session_sequence = int(row["value"] or 0) + 1
    action_payload = dict(payload or {})
    if action_type in {"chat_message", "resume_send"}:
        action_payload.setdefault("client_mid", _client_mid())
    if action_type == "greeting":
        job = connection.execute("SELECT encrypt_job_id FROM fj_boss_jobs WHERE id=?", (job_id,)).fetchone()
        encrypt_job_id = str(job["encrypt_job_id"] or "") if job else ""
        page_kind, page_key = "job", f"boss:{account_uid}:job:{encrypt_job_id}" if account_uid and encrypt_job_id else ""
    else:
        page_kind, page_key = "chat", f"boss:{account_uid}:chat" if account_uid else ""
    priority = {"chat_message": 300, "resume_send": 200, "greeting": 100}[action_type]
    action_id = _id()
    connection.execute(
        """INSERT INTO fj_actions (
             id, action_type, account_uid, job_id, session_id, target_page_kind, target_page_key,
             session_sequence, revision_no, supersedes_action_id, priority, priority_source,
             authorization_mode, text, encrypt_resume_id, resume_filename, payload_json,
             base_raw_message_id, base_message_mid, base_conversation_revision, planned_at,
             status, available_at, canonical_status, created_at, updated_at, source_table, source_id
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'scheduler_default', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?)""",
        (action_id, action_type, account_uid, job_id, session_id, page_kind, page_key,
         session_sequence, revision_no, supersedes_action_id, priority, authorization_mode, text,
         encrypt_resume_id, resume_filename, json.dumps(action_payload, ensure_ascii=False),
         base_raw_message_id, base_message_mid, base_conversation_revision, now, status, now,
         now, now, source_table, source_id or action_id),
    )
    action = connection.execute("SELECT * FROM fj_actions WHERE id=?", (action_id,)).fetchone()
    _log(connection, action, "action_created")
    if authorization_mode == "manual" and status == "queued":
        _log(connection, action, "action_confirmed")
    return action_id


def _session_ready(connection: sqlite3.Connection, action: sqlite3.Row) -> tuple[bool, str]:
    if not action["session_id"]:
        return True, ""
    if action["session_sequence"] is None:
        return False, "session_sequence_required"
    predecessor = connection.execute(
        """SELECT status FROM fj_actions WHERE session_id=? AND session_sequence < ?
           AND status NOT IN ({})
           ORDER BY session_sequence ASC, revision_no ASC LIMIT 1""".format(
            ", ".join("?" for _ in _TERMINAL_PREDECESSORS)
        ),
        (action["session_id"], action["session_sequence"], *_TERMINAL_PREDECESSORS),
    ).fetchone()
    if predecessor is None:
        return True, ""
    return False, "session_predecessor_unknown" if predecessor["status"] == "unknown" else "session_predecessor_pending"


def _context_ready(connection: sqlite3.Connection, action: sqlite3.Row) -> tuple[bool, str]:
    if action["action_type"] != "greeting" and (not action["account_uid"] or not action["target_page_key"]):
        return False, "identity_or_page_context_required"
    if action["action_type"] in {"chat_message", "resume_send"}:
        session = connection.execute("SELECT status, peer_uid, encrypt_peer_uid, security_id, encrypt_job_id FROM fj_chat_sessions WHERE id=?", (action["session_id"],)).fetchone()
        if session is None or session["status"] not in {"active", "human_takeover"} or not all(session[key] for key in ("peer_uid", "encrypt_peer_uid", "security_id", "encrypt_job_id")):
            return False, "chat_identity_required"
    if action["action_type"] == "greeting":
        job = connection.execute("SELECT encrypt_job_id FROM fj_boss_jobs WHERE id=?", (action["job_id"],)).fetchone()
        if job is None or not job["encrypt_job_id"]:
            return False, "job_context_required"
    return True, ""


def _set_waiting(connection: sqlite3.Connection, action: sqlite3.Row, code: str, detail: str = "") -> None:
    now = _now()
    connection.execute(
        """UPDATE fj_actions SET waiting_reason_code=?, waiting_reason_detail=?,
             waiting_since_at=COALESCE(waiting_since_at, ?), updated_at=? WHERE id=?""",
        (code, detail or code, now, now, action["id"]),
    )


def _candidates(connection: sqlite3.Connection, account_uid: str, now: str) -> list[sqlite3.Row]:
    return connection.execute(
        """SELECT * FROM fj_actions
           WHERE account_uid=? AND available_at <= ?
             AND (status='queued' OR (status IN ('claimed','preflighting') AND lease_expires_at IS NOT NULL AND lease_expires_at <= ?))
           ORDER BY priority DESC, created_at ASC, id ASC""",
        (account_uid, now, now),
    ).fetchall()


def list_eligible_actions(db: Database, *, account_uid: str) -> list[dict[str, Any]]:
    """返回插件可在当前真实页面上匹配的有序统一 Action。"""
    now = _now()
    with db.connect() as connection:
        if not bool(_runtime(connection)["send_enabled"]):
            return []
        actions: list[dict[str, Any]] = []
        for action in _candidates(connection, account_uid, now):
            session_ready, _ = _session_ready(connection, action)
            context_ready, _ = _context_ready(connection, action)
            if session_ready and context_ready:
                actions.append(_serialize(connection, str(action["id"])))
        return actions


def claim_unified_action(
    db: Database, executor_id: str, *, action_id: str, account_uid: str
) -> dict[str, Any] | None:
    """只领取插件已在真实页面匹配出的指定 Action。"""
    now = _now()
    with db.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        runtime = _runtime(connection)
        if not bool(runtime["send_enabled"]):
            for action in _candidates(connection, account_uid, now):
                _set_waiting(connection, action, "send_disabled", "发送开关关闭，动作保留在队列中")
            connection.commit()
            return None
        action = connection.execute(
            """SELECT * FROM fj_actions WHERE id=? AND account_uid=? AND available_at <= ?
               AND (status='queued' OR (status IN ('claimed','preflighting')
                    AND lease_expires_at IS NOT NULL AND lease_expires_at <= ?))""",
            (action_id, account_uid, now, now),
        ).fetchone()
        if action is None:
            connection.commit()
            return None
        session_ready, session_code = _session_ready(connection, action)
        context_ready, context_code = _context_ready(connection, action)
        if not session_ready or not context_ready:
            _set_waiting(connection, action, session_code or context_code)
            connection.commit()
            return None
        new_epoch = int(action["execution_epoch"] or 0) + 1
        cursor = connection.execute(
            """UPDATE fj_actions SET status='claimed', lease_owner=?, lease_expires_at=?,
                 execution_epoch=?, attempt_count=attempt_count+1, waiting_reason_code='',
                 waiting_reason_detail='', waiting_since_at=NULL, updated_at=?
               WHERE id=? AND (status='queued' OR (status IN ('claimed','preflighting') AND lease_expires_at IS NOT NULL AND lease_expires_at <= ?))""",
            (executor_id, _after(LEASE_SECONDS), new_epoch, now, action["id"], now),
        )
        if cursor.rowcount != 1:
            connection.commit()
            return None
        claimed = connection.execute("SELECT * FROM fj_actions WHERE id=?", (action["id"],)).fetchone()
        _log(connection, claimed, "action_claimed")
        connection.commit()
        return _serialize(connection, str(action["id"]))


def _message_decision(snapshot: dict[str, Any], message_id: str) -> str:
    decisions = snapshot.get("message_decisions")
    if isinstance(decisions, dict):
        return str(decisions.get(message_id) or "")
    return ""


def _create_replacement(connection: sqlite3.Connection, action: sqlite3.Row, snapshot: dict[str, Any], new_message_ids: list[str]) -> str:
    replacement_text = str(snapshot.get("replacement_text") or action["text"])
    status = "awaiting_confirmation" if replacement_text != str(action["text"]) and action["authorization_mode"] == "manual" else "queued"
    # 活跃 sequence 唯一索引要求先释放旧 revision，再创建继承同一 sequence 的 replacement。
    connection.execute("UPDATE fj_actions SET status='superseded', updated_at=? WHERE id=?", (_now(), action["id"]))
    replacement_id = create_action(
        connection, action_type="chat_message", account_uid=str(action["account_uid"]), job_id=action["job_id"],
        session_id=str(action["session_id"]), text=replacement_text,
        payload={**(_loads(str(action["payload_json"]), {})), "preflight_replacement": True},
        base_raw_message_id=new_message_ids[-1] if new_message_ids else action["base_raw_message_id"],
        base_message_mid="", base_conversation_revision=int(snapshot.get("conversation_revision") or action["base_conversation_revision"] or 0),
        authorization_mode=str(action["authorization_mode"]), status=status,
        session_sequence=int(action["session_sequence"]), revision_no=int(action["revision_no"] or 1) + 1,
        supersedes_action_id=str(action["id"]), source_table="fj_actions_replacement",
    )
    transfer_action_coverage(connection, previous_action_id=str(action["id"]), replacement_action_id=replacement_id)
    link_action_messages(connection, action_id=replacement_id, message_ids=new_message_ids, role="new_context")
    replacement = connection.execute("SELECT * FROM fj_actions WHERE id=?", (replacement_id,)).fetchone()
    _log(connection, action, "action_superseded", replacement_action_id=replacement_id)
    _log(connection, replacement, "replacement_created", status=status)
    return replacement_id


def _chat_preflight(connection: sqlite3.Connection, action: sqlite3.Row, snapshot: dict[str, Any]) -> tuple[str, str]:
    version = int(snapshot.get("conversation_revision") or 0)
    session = connection.execute("SELECT session_version FROM fj_chat_sessions WHERE id=?", (action["session_id"],)).fetchone()
    current_version = int(session["session_version"] or 0) if session else 0
    if version and version != current_version:
        return "blocked", "snapshot_revision_mismatch"
    baseline = int(action["base_conversation_revision"] or 0)
    if current_version <= baseline:
        return "passed", "no_new_message"
    rows = connection.execute(
        """SELECT m.id, sem.semantic_type, sem.reply_required FROM fj_chat_messages m
             LEFT JOIN fj_chat_message_semantics sem ON sem.raw_message_id=m.id
           WHERE m.session_id=? AND m.direction='inbound' AND m.id NOT IN
             (SELECT raw_message_id FROM fj_action_message_links WHERE action_id=? AND role IN ('trigger','covered'))
           ORDER BY m.observed_at, m.id""",
        (action["session_id"], action["id"]),
    ).fetchall()
    new_ids = [str(row["id"]) for row in rows]
    required = [row for row in rows if bool(row["reply_required"])]
    if not required:
        return "passed", "non_reply_platform_event"
    decisions = {_message_decision(snapshot, str(row["id"])) for row in required}
    if "relevant_to_existing_reply" in decisions:
        payload = _loads(str(action["payload_json"]), {})
        relation = snapshot.get("relation") if isinstance(snapshot.get("relation"), dict) else {}
        planning_task_id = str(relation.get("replacement_reply_task_id") or payload.get("replacement_reply_task_id") or "")
        if planning_task_id:
            connection.execute(
                "UPDATE fj_actions SET payload_json=?, updated_at=? WHERE id=?",
                (json.dumps({**payload, "replacement_reply_task_id": planning_task_id}, ensure_ascii=False), _now(), action["id"]),
            )
            # A' 由既有 reply task 生成并经过确认；生成前 A 保持等待。
            return "waiting", "replacement_reply_pending"
        _create_replacement(connection, action, snapshot, new_ids)
        return "replan", "relevant_to_existing_reply"
    if "independent_reply_required" in decisions:
        payload = _loads(str(action["payload_json"]), {})
        relation = snapshot.get("relation") if isinstance(snapshot.get("relation"), dict) else {}
        planning_task_id = str(relation.get("independent_reply_task_id") or payload.get("independent_reply_task_id") or "")
        if planning_task_id:
            connection.execute(
                "UPDATE fj_actions SET payload_json=?, updated_at=? WHERE id=?",
                (json.dumps({**payload, "independent_reply_task_id": planning_task_id}, ensure_ascii=False), _now(), action["id"]),
            )
            # reply task 的确认只是草稿状态；只有对应 B Action 已 queued 才能解除 A 的屏障。
            followups = connection.execute(
                "SELECT id, status, session_sequence, payload_json FROM fj_actions WHERE session_id=? AND action_type='chat_message' AND id<>?",
                (action["session_id"], action["id"]),
            ).fetchall()
            followup = next(
                (
                    row for row in followups
                    if str(_loads(str(row["payload_json"]), {}).get("reply_task_id") or "") == planning_task_id
                ),
                None,
            )
            if (
                followup is None
                or str(followup["status"]) != "queued"
                or int(followup["session_sequence"] or 0) <= int(action["session_sequence"] or 0)
            ):
                return "waiting", "independent_followup_pending"
            return "passed", "independent_followup_ready"
        next_id = str(payload.get("independent_followup_action_id") or "")
        followup = connection.execute("SELECT status FROM fj_actions WHERE id=?", (next_id,)).fetchone() if next_id else None
        if followup is None:
            # B 创建后，A 保持在队列中等待 B 被确认或自动准备完成。
            next_id = create_action(
                connection, action_type="chat_message", account_uid=str(action["account_uid"]), job_id=action["job_id"],
                session_id=str(action["session_id"]), text=str(snapshot.get("independent_text") or ""),
                payload={"preflight_independent": True}, base_raw_message_id=str(required[-1]["id"]),
                base_conversation_revision=current_version, authorization_mode="manual", status="awaiting_confirmation",
                source_table="fj_actions_preflight_independent",
            )
            connection.execute(
                "UPDATE fj_actions SET payload_json=?, updated_at=? WHERE id=?",
                (json.dumps({**payload, "independent_followup_action_id": next_id}, ensure_ascii=False), _now(), action["id"]),
            )
            link_action_messages(connection, action_id=next_id, message_ids=[str(row["id"]) for row in required], role="covered")
            _log(connection, connection.execute("SELECT * FROM fj_actions WHERE id=?", (next_id,)).fetchone(), "replacement_created", independent=True)
            return "waiting", "independent_followup_pending"
        if str(followup["status"]) == "awaiting_confirmation":
            return "waiting", "independent_followup_pending"
        return "passed", "independent_followup_ready"
    # 关系无法可靠判断时保留 A，等待人工或后续事实，不进入 dispatch。
    return "waiting", "classification_uncertain"


def _resume_preflight(connection: sqlite3.Connection, action: sqlite3.Row, snapshot: dict[str, Any]) -> tuple[str, str]:
    session = connection.execute("SELECT account_uid, latest_inbound_message_id FROM fj_chat_sessions WHERE id=?", (action["session_id"],)).fetchone()
    if session is None or str(session["account_uid"]) != str(action["account_uid"]):
        return "blocked", "identity_mismatch"
    recruiter_reply = connection.execute(
        """SELECT 1 FROM fj_chat_messages m JOIN fj_chat_message_semantics sem ON sem.raw_message_id=m.id
           WHERE m.session_id=? AND m.direction='inbound' AND sem.semantic_type='recruiter_text'
           ORDER BY m.observed_at DESC LIMIT 1""", (action["session_id"],)
    ).fetchone()
    if not session["latest_inbound_message_id"] or recruiter_reply is None:
        return "blocked", "hr_reply_required"
    duplicate = connection.execute(
        """SELECT 1 FROM fj_actions WHERE session_id=? AND action_type='resume_send' AND id<>?
           AND status IN ('succeeded','unknown') LIMIT 1""", (action["session_id"], action["id"])
    ).fetchone()
    if duplicate is not None:
        return "blocked", "duplicate_resume_send"
    resume_snapshot = snapshot.get("resume_list_snapshot")
    if not isinstance(resume_snapshot, dict) or resume_snapshot.get("error"):
        return "blocked", "resume_snapshot_unavailable"
    attachments = resume_snapshot.get("attachments")
    if not isinstance(attachments, list):
        return "blocked", "resume_snapshot_unavailable"
    valid = any(
        isinstance(item, dict)
        and str(item.get("encryptResumeId") or "") == str(action["encrypt_resume_id"])
        and str(item.get("filename") or "") == str(action["resume_filename"])
        for item in attachments
    )
    return ("passed", "resume_fixed_attachment_valid") if valid else ("stale", "resume_attachment_invalid")


def _greeting_preflight(connection: sqlite3.Connection, action: sqlite3.Row, snapshot: dict[str, Any]) -> tuple[str, str]:
    job = connection.execute("SELECT encrypt_job_id FROM fj_boss_jobs WHERE id=?", (action["job_id"],)).fetchone()
    if (
        job is None or not job["encrypt_job_id"]
        or str(snapshot.get("encrypt_job_id") or "") != str(job["encrypt_job_id"])
        or not str(snapshot.get("security_id") or "")
    ):
        return "blocked", "identity_mismatch"
    if snapshot.get("contacted") is True:
        return "superseded", "already_contacted"
    if snapshot.get("contacted") is not False:
        return "blocked", "contact_state_unknown"
    duplicate = connection.execute("SELECT 1 FROM fj_actions WHERE job_id=? AND action_type='greeting' AND id<>? AND status IN ('succeeded','unknown') LIMIT 1", (action["job_id"], action["id"])).fetchone()
    return ("blocked", "duplicate_greeting") if duplicate else ("passed", "greeting_context_valid")


def preflight_action(db: Database, executor_id: str, action_id: str, execution_epoch: int, snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
    """在 dispatch 前以最新页面/会话快照重新决定是否允许副作用。"""
    snapshot = snapshot or {}
    with db.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        action = connection.execute("SELECT * FROM fj_actions WHERE id=?", (action_id,)).fetchone()
        if action is None or action["lease_owner"] != executor_id or int(action["execution_epoch"] or 0) != execution_epoch or action["status"] != "claimed":
            raise AppError(409, "UNIFIED_ACTION_LEASE_LOST", "统一动作租约已失效。")
        if not bool(_runtime(connection)["send_enabled"]):
            _set_waiting(connection, action, "send_disabled", "运行时发送开关关闭，恢复后需要重新预检")
            connection.execute("UPDATE fj_actions SET status='queued', lease_owner=NULL, lease_expires_at=NULL WHERE id=?", (action_id,))
            connection.commit()
            return {"decision": "waiting", "reason_code": "send_disabled", "action": _serialize(connection, action_id)}
        now = _now()
        connection.execute("UPDATE fj_actions SET status='preflighting', preflight_state='running', updated_at=? WHERE id=?", (now, action_id))
        _log(connection, action, "preflight_started")
        action = connection.execute("SELECT * FROM fj_actions WHERE id=?", (action_id,)).fetchone()
        if action["action_type"] == "chat_message":
            decision, reason = _chat_preflight(connection, action, snapshot)
        elif action["action_type"] == "resume_send":
            decision, reason = _resume_preflight(connection, action, snapshot)
        else:
            decision, reason = _greeting_preflight(connection, action, snapshot)
        if decision == "passed":
            token = _token()
            connection.execute(
                """UPDATE fj_actions SET status='claimed', preflight_state='passed', preflight_observed_revision=?,
                     preflight_completed_at=?, preflight_reason_code=?, dispatch_token=?, dispatch_deadline_at=?, updated_at=? WHERE id=?""",
                (snapshot.get("conversation_revision"), now, reason, token, _after(DISPATCH_SECONDS), now, action_id),
            )
            updated = connection.execute("SELECT * FROM fj_actions WHERE id=?", (action_id,)).fetchone()
            _log(connection, updated, "preflight_passed", reason_code=reason)
            connection.commit()
            return {"decision": "dispatch", "dispatch_token": token, "dispatch_deadline_at": updated["dispatch_deadline_at"], "action": _serialize(connection, action_id)}
        if decision == "waiting":
            _set_waiting(connection, action, reason)
            connection.execute(
                """UPDATE fj_actions SET status='queued', lease_owner=NULL, lease_expires_at=NULL,
                   preflight_state='replan_required', preflight_reason_code=?, updated_at=? WHERE id=?""",
                (reason, now, action_id),
            )
            _log(connection, action, "preflight_waiting", reason_code=reason)
            connection.commit()
            return {"decision": "waiting", "reason_code": reason, "action": _serialize(connection, action_id)}
        if decision == "replan":
            connection.execute("UPDATE fj_actions SET preflight_state='replan_required', preflight_reason_code=?, updated_at=? WHERE id=?", (reason, now, action_id))
            _log(connection, action, "preflight_replan_required", reason_code=reason)
        else:
            connection.execute("UPDATE fj_actions SET status=?, preflight_state='failed', preflight_reason_code=?, completed_at=CASE WHEN ? IN ('stale','superseded') THEN ? ELSE completed_at END, updated_at=? WHERE id=?", (decision, reason, decision, now, now, action_id))
            _log(connection, action, "action_superseded" if decision == "superseded" else "preflight_replan_required", reason_code=reason)
        connection.commit()
        return {"decision": "replan" if decision == "replan" else "blocked", "reason_code": reason, "action": _serialize(connection, action_id)}


def mark_dispatch_started(db: Database, executor_id: str, action_id: str, execution_epoch: int, dispatch_token: str) -> dict[str, Any]:
    with db.connect() as connection:
        action = connection.execute("SELECT * FROM fj_actions WHERE id=?", (action_id,)).fetchone()
        deadline = _parse(str(action["dispatch_deadline_at"] or "")) if action else None
        if action is None or action["lease_owner"] != executor_id or int(action["execution_epoch"] or 0) != execution_epoch or action["status"] != "claimed" or not dispatch_token or dispatch_token != action["dispatch_token"] or deadline is None or deadline <= datetime.now(timezone.utc):
            raise AppError(409, "UNIFIED_DISPATCH_NOT_ALLOWED", "dispatch token 已失效，需要重新预检。")
        if not bool(_runtime(connection)["send_enabled"]):
            _set_waiting(connection, action, "send_disabled")
            connection.execute("UPDATE fj_actions SET status='queued', lease_owner=NULL, lease_expires_at=NULL, dispatch_token='' WHERE id=?", (action_id,))
            raise AppError(409, "CHAT_SEND_DISABLED", "自动代聊发送开关已关闭。")
        now = _now()
        connection.execute("UPDATE fj_actions SET status='dispatching', dispatched_at=?, dispatch_token='', updated_at=? WHERE id=?", (now, now, action_id))
        _log(connection, action, "dispatch_started")
        return _serialize(connection, action_id)


def complete_action(db: Database, executor_id: str, action_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    with db.connect() as connection:
        action = connection.execute("SELECT * FROM fj_actions WHERE id=?", (action_id,)).fetchone()
        if action is None:
            raise AppError(409, "UNIFIED_ACTION_LEASE_LOST", "统一动作租约已失效。")
        if int(action["execution_epoch"] or 0) != int(payload.get("execution_epoch") or 0):
            raise AppError(409, "STALE_EXECUTION_EPOCH", "统一动作执行轮次已经失效。")
        # 可信 outbound observation 已确认成功后，本地 sender completion 只能幂等返回。
        if str(action["status"]) == "succeeded" or str(action["canonical_status"] or "") == "succeeded":
            return _serialize(connection, action_id)
        if action["lease_owner"] != executor_id:
            raise AppError(409, "UNIFIED_ACTION_LEASE_LOST", "统一动作租约已失效。")
        outcome = str(payload.get("outcome") or "unknown")
        if outcome not in {"accepted", "failed", "unknown"}:
            outcome = "unknown"
        if outcome == "failed" and str(payload.get("status_code") or "") == "resume_attachment_invalid":
            outcome = "stale"
        now = _now()
        connection.execute(
            """UPDATE fj_actions SET status=?, outcome=?, status_code=?, completed_at=?, lease_expires_at=NULL,
                 dispatch_deadline_at=NULL, updated_at=? WHERE id=?""",
            (outcome, outcome, str(payload.get("status_code") or ""), now, now, action_id),
        )
        _log(connection, action, "transport_accepted" if outcome == "accepted" else outcome, status_code=str(payload.get("status_code") or ""))
        return _serialize(connection, action_id)


def observe_outbound_result(connection: sqlite3.Connection, *, session_id: str, client_mid: str, raw_message_id: str) -> None:
    """可信远端 outbound 回显才把统一文本或简历 Action 提升为 succeeded。"""
    if not client_mid:
        return
    action = connection.execute(
        """SELECT * FROM fj_actions WHERE session_id=? AND action_type IN ('chat_message','resume_send')
           AND json_extract(payload_json, '$.client_mid')=?
           ORDER BY updated_at DESC LIMIT 1""",
        (session_id, client_mid),
    ).fetchone()
    if action is None:
        return
    now = _now()
    link_action_messages(connection, action_id=str(action["id"]), message_ids=[raw_message_id], role="outbound_result")
    if str(action["status"]) == "succeeded" or str(action["canonical_status"] or "") == "succeeded":
        return
    connection.execute(
        """UPDATE fj_actions SET status='succeeded', canonical_status='succeeded', outcome='accepted',
             completed_at=COALESCE(completed_at, ?), updated_at=? WHERE id=?""",
        (now, now, action["id"]),
    )
    connection.execute(
        """UPDATE fj_chat_message_states SET reply_state='replied', replied_at=?, updated_at=?
           WHERE raw_message_id IN (SELECT raw_message_id FROM fj_action_message_links
                                    WHERE action_id=? AND role IN ('trigger','covered'))
             AND reply_state<>'not_required'""",
        (now, now, action["id"]),
    )
    _log(connection, action, "outbound_observed", raw_message_id=raw_message_id)
    _log(connection, action, "succeeded")
