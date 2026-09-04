from __future__ import annotations

import json
import random
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
from backend.app.services.fine_job.execution_reconciliation import (
    observe_outbound_chat_message,
    record_execution_evidence_with_connection,
    set_canonical_from_raw,
)
from backend.app.services.fine_job.job_activity import append_job_activity_with_connection


RUNTIME_ID = "boss"
SEND_LEASE_SECONDS = 30
SEND_DISPATCH_TIMEOUT_SECONDS = 45
REPLY_DEBOUNCE_SECONDS = 3

_generation_timers: dict[str, threading.Timer] = {}
_generation_timers_lock = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _after(seconds: int) -> str:
    return (
        datetime.now(timezone.utc) + timedelta(seconds=seconds)
    ).replace(microsecond=0).isoformat().replace("+00:00", "Z")


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


def _loads(value: str | None, fallback: Any) -> Any:
    try:
        return json.loads(value or "")
    except (TypeError, ValueError):
        return fallback


def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    result = dict(row)
    for key in ("listen_enabled", "generation_enabled", "send_enabled"):
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
          id, listen_enabled, generation_enabled, send_enabled,
          trigger_mode, interval_minutes, leader_epoch, created_at, updated_at
        ) VALUES (?, 0, 0, 0, 'interval', 30, 0, ?, ?)
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
        "trigger_mode",
        "interval_minutes",
    }
    updates = {key: value for key, value in changes.items() if key in allowed and value is not None}
    if not updates:
        return get_runtime(db)
    for key in ("listen_enabled", "generation_enabled", "send_enabled"):
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
    synced_at = _now()
    with db.connect() as connection:
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
            job_title = str(raw.get("jobName") or raw.get("jobTitle") or "")
            if not job_title and job_id:
                job_row = connection.execute(
                    "SELECT title FROM fj_boss_jobs WHERE id = ?", (job_id,)
                ).fetchone()
                job_title = str(job_row["title"] or "") if job_row else ""
            existing = _friend_session_row(
                connection,
                account_uid=account_uid,
                peer_uid=peer_uid,
                encrypt_job_id=encrypt_job_id,
            )
            latest_msg_id = str(info.get("msgId") or "")
            previous_msg_id = str(existing["platform_latest_msg_id"] or "") if existing else ""
            message_changed = bool(previous_msg_id and latest_msg_id and previous_msg_id != latest_msg_id)
            if message_changed:
                changed_count += 1
            latest_message_at = _epoch_ms_to_iso(info.get("msgTime") or raw.get("lastTS"))
            latest_message_text = str(
                raw.get("lastMsg") or info.get("showText") or ""
            )
            identity_complete = bool(encrypt_peer_uid and security_id and encrypt_job_id)
            status = (
                "human_takeover"
                if identity_complete
                else "unsupported"
            )
            if existing is None:
                session_id = _id("chat_session")
                connection.execute(
                    """
                    INSERT INTO fj_chat_sessions (
                      id, platform, account_uid, peer_uid, encrypt_peer_uid, security_id,
                      job_id, encrypt_job_id, job_title, peer_name, company_name,
                      peer_title,
                      platform_latest_msg_id, platform_latest_message_status,
                      platform_relation_type, platform_chat_status, platform_latest_message_text,
                      platform_latest_message_at, platform_latest_from_id, platform_latest_to_id,
                      platform_synced_at, platform_list_index, message_update_required, status, session_version,
                      created_at, updated_at
                    ) VALUES (?, 'boss', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
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
                  job_title = CASE WHEN ? <> '' THEN ? ELSE job_title END,
                  peer_name = CASE WHEN ? <> '' THEN ? ELSE peer_name END,
                  peer_title = CASE WHEN ? <> '' THEN ? ELSE peer_title END,
                  company_name = CASE WHEN ? <> '' THEN ? ELSE company_name END,
                  platform_latest_msg_id = ?,
                  platform_latest_message_status = ?,
                  platform_relation_type = ?,
                  platform_chat_status = ?,
                  platform_latest_message_text = ?,
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
        return {
            "account_uid": account_uid,
            "count": synced_count,
            "created_count": created_count,
            "changed_count": changed_count,
            "source_url": source_url,
            "synced_at": synced_at,
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
) -> None:
    session = connection.execute(
        "SELECT job_id, company_name FROM fj_chat_sessions WHERE id = ?",
        (session_id,),
    ).fetchone()
    if session is None or not session["job_id"]:
        return
    event_type = "recruiter_replied" if direction == "inbound" else "candidate_replied"
    append_job_activity_with_connection(
        connection,
        job_id=str(session["job_id"]),
        chat_session_id=session_id,
        event_type=event_type,
        occurred_at=occurred_at,
        source="chat",
        source_ref_type="chat_message",
        source_ref_id=message_id,
        evidence_level="direct",
        payload={"direction": direction, "platform_message_id": platform_message_id},
        dedupe_key=f"chat_message:{message_id}:{event_type}",
    )


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
    now = _now()
    with db.connect() as connection:
        session = _session_or_404(connection, session_id)
        account_uid = str(session["account_uid"] or "")
        for raw in messages:
            if not isinstance(raw, dict) or not raw.get("mid"):
                continue
            message_id = str(raw["mid"])
            sender = raw.get("from") if isinstance(raw.get("from"), dict) else {}
            receiver = raw.get("to") if isinstance(raw.get("to"), dict) else {}
            sender_uid = str(sender.get("uid") or "")
            receiver_uid = str(receiver.get("uid") or "")
            direction = "outbound" if sender_uid == account_uid else "inbound"
            body = raw.get("body") if isinstance(raw.get("body"), dict) else {}
            body_type = _optional_int(body.get("type"))
            message_type = "text" if body_type == 1 else "system"
            sent_at = _epoch_ms_to_iso(raw.get("time")) or now
            local_message_id = _id("chat_message")
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO fj_chat_messages (
                  id, session_id, platform_message_id, direction, message_type,
                  content, sender_uid, receiver_uid, client_mid, source,
                  sent_at, observed_at, raw_meta_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, '', 'websocket', ?, ?, ?, ?)
                """,
                (
                    local_message_id,
                    session_id,
                    message_id,
                    direction,
                    message_type,
                    _history_message_content(raw),
                    sender_uid,
                    receiver_uid,
                    sent_at,
                    now,
                    json.dumps({"history": True, "platform_type": raw.get("type"), "raw": raw}, ensure_ascii=False),
                    now,
                ),
            )
            inserted_count += int(cursor.rowcount > 0)
            if cursor.rowcount > 0:
                _record_message_activity(
                    connection,
                    session_id=session_id,
                    message_id=local_message_id,
                    direction=direction,
                    occurred_at=sent_at,
                    platform_message_id=message_id,
                )
                if direction == "outbound":
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
            WHERE session_id = ? AND direction = 'inbound'
            ORDER BY sent_at DESC, rowid DESC LIMIT 1
            """,
            (session_id,),
        ).fetchone()
        current_platform_msg_id = str(session["platform_latest_msg_id"] or "")
        current_message_loaded = bool(current_platform_msg_id and connection.execute(
            "SELECT 1 FROM fj_chat_messages WHERE session_id = ? AND platform_message_id = ? LIMIT 1",
            (session_id, current_platform_msg_id),
        ).fetchone())
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
                message.get("job_title") or "",
                message.get("peer_name") or "",
                message.get("company_name") or "",
                "human_takeover" if identity_complete else "unsupported",
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
          job_title = CASE WHEN ? <> '' THEN ? ELSE job_title END,
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
            message.get("job_title") or "",
            message.get("job_title") or "",
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
            "UPDATE fj_chat_sessions SET status = 'human_takeover', updated_at = ? WHERE id = ?",
            (now, session["id"]),
        )
        return None
    # 还没有开始真实发送的旧动作可以安全取消，禁止新消息到达后发送旧草稿。
    connection.execute(
        """
        UPDATE fj_chat_send_actions SET status = 'cancelled', updated_at = ?, completed_at = ?,
          canonical_status = 'cancelled', canonical_updated_at = ?,
          canonical_reason = '新 inbound 消息使未发送草稿失效'
        WHERE reply_task_id IN (
          SELECT id FROM fj_chat_reply_tasks WHERE session_id = ?
        ) AND status IN ('queued', 'leased')
        """,
        (now, now, now, session["id"]),
    )
    pending = connection.execute(
        """
        SELECT * FROM fj_chat_reply_tasks
        WHERE session_id = ? AND status = 'pending_generation'
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
          id, session_id, trigger_source, status, based_on_message_id,
          based_on_session_version, generation_due_at, input_message_ids_json,
          created_at, updated_at
        ) VALUES (?, ?, ?, 'pending_generation', ?, ?, ?, ?, ?, ?)
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
            session = _find_or_create_session(
                connection,
                account_uid=event["account_uid"],
                message=message,
            )
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
                            sender_uid = ?, receiver_uid = ?, source = 'assistant', sent_at = ?,
                            observed_at = ?, raw_meta_json = ?
                        WHERE id = ?
                        """,
                        (
                            message["platform_message_id"],
                            message["direction"],
                            message.get("message_type") or "text",
                            message.get("content") or "",
                            message.get("sender_uid") or "",
                            message.get("receiver_uid") or "",
                            message["sent_at"],
                            message["observed_at"],
                            json.dumps(message.get("raw_meta") or {}, ensure_ascii=False),
                            message_id,
                        ),
                    )
                else:
                    connection.execute(
                        """
                        INSERT INTO fj_chat_messages (
                          id, session_id, platform_message_id, direction, message_type,
                          content, sender_uid, receiver_uid, client_mid, source,
                          sent_at, observed_at, raw_meta_json, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            message_id,
                            session["id"],
                            message["platform_message_id"],
                            message["direction"],
                            message.get("message_type") or "text",
                            message.get("content") or "",
                            message.get("sender_uid") or "",
                            message.get("receiver_uid") or "",
                            message.get("client_mid") or "",
                            message.get("source") or "websocket",
                            message["sent_at"],
                            message["observed_at"],
                            json.dumps(message.get("raw_meta") or {}, ensure_ascii=False),
                            _now(),
                        ),
                    )
            except sqlite3.IntegrityError:
                duplicates += 1
                continue
            _record_message_activity(
                connection,
                session_id=str(session["id"]),
                message_id=message_id,
                direction=str(message["direction"]),
                occurred_at=str(message["sent_at"]),
                platform_message_id=str(message["platform_message_id"]),
            )
            if message["direction"] == "outbound":
                observe_outbound_chat_message(
                    connection,
                    message_id=message_id,
                    observed_account_uid=str(event["account_uid"]),
                )
            next_version = int(session["session_version"]) + 1
            inbound_id = message_id if message["direction"] == "inbound" else session["latest_inbound_message_id"]
            next_status = "human_takeover" if (
                message["direction"] == "outbound" and message.get("source") == "manual"
            ) else session["status"]
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
            if next_status == "human_takeover":
                connection.execute(
                    """
                    UPDATE fj_chat_reply_tasks
                    SET status = 'cancelled', cancelled_at = ?, updated_at = ?
                    WHERE session_id = ? AND status IN (
                      'pending_generation', 'generating', 'awaiting_review', 'confirmed'
                    )
                    """,
                    (_now(), _now(), session["id"]),
                )
                _cancel_session_send_actions(
                    connection,
                    str(session["id"]),
                    "manual_message_takeover",
                )
            elif message["direction"] == "inbound" and message.get("message_type", "text") == "text":
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
            # 历史岗位已拿到标题时，优先补齐旧会话留下的空标题。
            if not payload.get("job_title"):
                payload["job_title"] = str(history["title"] or "")
    payload["identity_state"] = (
        "ready"
        if payload.get("encrypt_peer_uid") and payload.get("security_id") and payload.get("encrypt_job_id")
        else "incomplete"
    )
    payload["job_context_state"] = "linked" if payload.get("job_id") else "unlinked"
    return payload


def list_sessions(
    db: Database,
    *,
    status: str | None = None,
    account_uid: str | None = None,
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
              COALESCE(NULLIF(s.platform_latest_message_text, ''), m.content) AS latest_message_content,
              CASE
                WHEN s.platform_latest_msg_id <> '' AND s.platform_latest_from_id = s.account_uid THEN 'outbound'
                WHEN s.platform_latest_msg_id <> '' AND s.platform_latest_to_id = s.account_uid THEN 'inbound'
                WHEN m.id IS NOT NULL THEN m.direction
                ELSE NULL
              END AS latest_message_direction,
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
              (
                SELECT COUNT(*) FROM fj_chat_reply_tasks pending
                WHERE pending.session_id = s.id
                  AND pending.status IN ('pending_generation', 'generating', 'awaiting_review')
              ) AS unhandled_count
            FROM fj_chat_sessions s
            LEFT JOIN fj_chat_messages m ON m.id = s.latest_message_id
            LEFT JOIN fj_chat_reply_tasks t ON t.id = (
              SELECT id FROM fj_chat_reply_tasks
              WHERE session_id = s.id
              ORDER BY created_at DESC LIMIT 1
            )
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
            WHERE session_id = ?
            ORDER BY sent_at ASC, rowid ASC
            """,
            (session_id,),
        ).fetchall()
        tasks = connection.execute(
            "SELECT * FROM fj_chat_reply_tasks WHERE session_id = ? ORDER BY created_at DESC",
            (session_id,),
        ).fetchall()
        actions = connection.execute(
            "SELECT * FROM fj_chat_send_actions WHERE session_id = ? ORDER BY created_at DESC LIMIT 20",
            (session_id,),
        ).fetchall()
        message_count = int(connection.execute(
            "SELECT COUNT(*) FROM fj_chat_messages WHERE session_id = ?",
            (session_id,),
        ).fetchone()[0])
        return {
            "session": _session_payload(session, connection),
            "messages": [_row(item) for item in messages],
            "reply_tasks": [_row(item) for item in tasks],
            "send_actions": [_row(item) for item in actions],
            "messages_truncated": False,
            "message_count": message_count,
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
              AND content = '附件状态更新'
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
        SELECT direction, message_type, content, sent_at
        FROM fj_chat_messages WHERE session_id = ?
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
        "candidate_profile_context": candidate_context,
        "resume_facts": resume_facts if confirmed_profile_count == 0 else [],
        "job_intent": _row(intent_row) if intent_row else None,
    }


def _chat_completion(config: AppConfig, context: dict[str, Any], instruction: str) -> tuple[dict[str, Any], str]:
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
        return {
            "decision": "reply",
            "reply_text": f"您好，感谢您的消息。关于“{subject}”，我确认一下相关情况后回复您。",
            "facts_used": [],
            "warnings": [],
            "requires_user_input": False,
            "reason": "根据最新招聘方消息生成保守回复",
        }, model
    if provider not in {"openai", "openai-compatible"}:
        raise AppError(status_code=422, error_category="UNSUPPORTED_LLM_PROVIDER", error_message=f"不支持的 LLM 提供方：{config.llm_provider}")
    if not config.llm_api_key:
        raise AppError(status_code=422, error_category="LLM_NOT_CONFIGURED", error_message="请先配置 LLM API Key。")
    system_prompt = (
        "你是求职者的沟通助手。只根据给定的本地对话、岗位信息、已确认候选人上下文和求职意向，"
        "生成一条简洁、自然、诚实的中文回复。不得虚构经历、薪资、到岗时间或联系方式；"
        "资料不足时应明确表示需要确认。只输出 JSON 对象，字段为 decision、reply_text、facts_used、"
        "warnings、requires_user_input、reason；decision 只能是 reply、manual、ignore。"
    )
    user_prompt = json.dumps(
        {"context": context, "temporary_instruction": instruction},
        ensure_ascii=False,
    )
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
    if not isinstance(result, dict):
        raise AppError(status_code=502, error_category="INVALID_LLM_RESPONSE", error_message="LLM 回复结构无效。")
    decision = str(result.get("decision") or "")
    if decision not in {"reply", "manual", "ignore"}:
        raise AppError(status_code=502, error_category="INVALID_LLM_RESPONSE", error_message="LLM 回复决定无效。")
    text = str(result.get("reply_text") or "").strip()
    if decision == "reply" and not text:
        raise AppError(status_code=502, error_category="INVALID_LLM_RESPONSE", error_message="LLM 返回了空回复。")
    facts_used = result.get("facts_used") if isinstance(result.get("facts_used"), list) else []
    warnings = result.get("warnings") if isinstance(result.get("warnings"), list) else []
    return {
        "decision": decision,
        "reply_text": text[:5000],
        "facts_used": [str(item)[:300] for item in facts_used if str(item).strip()][:30],
        "warnings": [str(item)[:80] for item in warnings if str(item).strip()][:20],
        "requires_user_input": bool(result.get("requires_user_input")),
        "reason": str(result.get("reason") or "")[:500],
    }, model


def generate_reply(
    db: Database,
    config: AppConfig,
    session_id: str,
    *,
    instruction: str = "",
    regenerate: bool = False,
) -> dict[str, Any]:
    with db.connect() as connection:
        session = _session_or_404(connection, session_id)
        if session["status"] not in {"active", "unsupported"}:
            raise AppError(status_code=409, error_category="CHAT_SESSION_TAKEN_OVER", error_message="该会话已由人工接管，不能生成自动回复。")
        if not session["latest_inbound_message_id"]:
            raise AppError(status_code=409, error_category="NO_INBOUND_MESSAGE", error_message="会话没有可回复的新消息。")
        current = connection.execute(
            """
            SELECT * FROM fj_chat_reply_tasks WHERE session_id = ?
              AND status IN ('pending_generation', 'generating', 'awaiting_review')
            ORDER BY created_at DESC LIMIT 1
            """,
            (session_id,),
        ).fetchone()
        now = _now()
        if current is not None and current["status"] == "generating":
            raise AppError(
                status_code=409,
                error_category="CHAT_REPLY_GENERATING",
                error_message="该会话正在生成回复，请稍后查看结果。",
            )
        if current is None or regenerate:
            if current is not None:
                connection.execute(
                    "UPDATE fj_chat_reply_tasks SET status = 'stale', cancelled_at = ?, updated_at = ? WHERE id = ?",
                    (now, now, current["id"]),
                )
            task_id = _id("chat_reply")
            connection.execute(
                """
                INSERT INTO fj_chat_reply_tasks (
                  id, session_id, trigger_source, status, based_on_message_id,
                  based_on_session_version, generation_due_at, input_message_ids_json,
                  created_at, updated_at
                ) VALUES (?, ?, 'manual', 'generating', ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    session_id,
                    session["latest_inbound_message_id"],
                    session["session_version"],
                    now,
                    json.dumps([session["latest_inbound_message_id"]], ensure_ascii=False),
                    now,
                    now,
                ),
            )
        else:
            task_id = str(current["id"])
            connection.execute(
                "UPDATE fj_chat_reply_tasks SET status = 'generating', generation_error = NULL, updated_at = ? WHERE id = ?",
                (now, task_id),
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
        completion, model = _chat_completion(config, context, instruction)
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
            task["based_on_message_id"] != session["latest_inbound_message_id"]
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
        return _row(connection.execute(
            "SELECT * FROM fj_chat_reply_tasks WHERE id = ?", (task_id,)
        ).fetchone()) or {}


def edit_reply(db: Database, task_id: str, final_text: str) -> dict[str, Any]:
    with db.connect() as connection:
        task = _task_or_404(connection, task_id)
        if task["status"] != "awaiting_review":
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
                "session_paused" if status == "paused" else "human_takeover",
            )
        return _row(connection.execute("SELECT * FROM fj_chat_sessions WHERE id = ?", (session_id,)).fetchone()) or {}


def cancel_reply(db: Database, task_id: str) -> dict[str, Any]:
    with db.connect() as connection:
        task = _task_or_404(connection, task_id)
        if task["status"] in {"confirmed", "cancelled", "stale"}:
            return _row(task) or {}
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
        if task["status"] != "awaiting_review":
            raise AppError(status_code=409, error_category="CHAT_REPLY_NOT_CONFIRMABLE", error_message="当前回复任务不可确认发送。")
        if session["status"] == "unsupported":
            raise AppError(
                status_code=409,
                error_category="CHAT_IDENTITY_INCOMPLETE",
                error_message="聊天对象身份不完整，请先在 BOSS 打开对应会话后重试。",
            )
        if session["status"] != "active":
            raise AppError(status_code=409, error_category="CHAT_SESSION_TAKEN_OVER", error_message="该会话已由人工接管。")
        if not session["encrypt_peer_uid"] or not session["security_id"] or not session["encrypt_job_id"]:
            raise AppError(
                status_code=409,
                error_category="CHAT_IDENTITY_INCOMPLETE",
                error_message="聊天对象身份不完整，请先在 BOSS 打开对应会话后重试。",
            )
        expected_message = payload["based_on_message_id"]
        expected_version = int(payload["based_on_session_version"])
        if (
            expected_message != task["based_on_message_id"]
            or expected_version != int(task["based_on_session_version"])
            or expected_message != session["latest_inbound_message_id"]
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


def _action_payload(connection: sqlite3.Connection, action_id: str) -> dict[str, Any]:
    row = connection.execute(
        """
        SELECT a.*, s.account_uid, s.peer_uid, s.encrypt_peer_uid,
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
        _ensure_runtime(connection)
        _sweep_stale_send_actions(connection)
        leader = connection.execute(
            "SELECT * FROM fj_chat_leaders WHERE account_uid = ?",
            (account_uid,),
        ).fetchone()
        leader_expiry = _parse_time(leader["lease_expires_at"]) if leader else None
        if (
            leader is None
            or leader["executor_id"] != executor_id
            or leader["tab_id"] != tab_id
            or int(leader["leader_epoch"]) != leader_epoch
            or not leader_expiry
            or leader_expiry <= now_dt
        ):
            raise AppError(status_code=409, error_category="CHAT_NOT_LEADER", error_message="当前标签页不是有效的聊天领导者。")
        action = connection.execute(
            """
            SELECT a.id FROM fj_chat_send_actions a
            JOIN fj_chat_sessions s ON s.id = a.session_id
            WHERE s.account_uid = ? AND (
              a.status = 'queued' OR (a.status = 'leased' AND a.lease_expires_at <= ?)
            )
            ORDER BY a.created_at ASC LIMIT 1
            """,
            (account_uid, now),
        ).fetchone()
        if action is None:
            return None
        next_epoch = int(connection.execute(
            "SELECT execution_epoch FROM fj_chat_send_actions WHERE id = ?", (action["id"],)
        ).fetchone()["execution_epoch"]) + 1
        connection.execute(
            """
            UPDATE fj_chat_send_actions SET status = 'leased', lease_owner = ?,
              lease_expires_at = ?, execution_epoch = ?, attempt_count = attempt_count + 1,
              leader_tab_id = ?, leader_epoch = ?, dispatch_deadline_at = NULL,
              updated_at = ? WHERE id = ?
            """,
            (
                executor_id,
                _after(SEND_LEASE_SECONDS),
                next_epoch,
                tab_id,
                leader_epoch,
                now,
                action["id"],
            ),
        )
        return _action_payload(connection, str(action["id"]))


def mark_dispatch_started(db: Database, executor_id: str, action_id: str, execution_epoch: int) -> dict[str, Any]:
    with db.connect() as connection:
        action = _action_payload(connection, action_id)
        if action["lease_owner"] != executor_id or int(action["execution_epoch"]) != execution_epoch or action["status"] != "leased":
            raise AppError(status_code=409, error_category="CHAT_ACTION_LEASE_LOST", error_message="发送动作租约已失效。")
        leader = connection.execute(
            "SELECT * FROM fj_chat_leaders WHERE account_uid = ?",
            (action["account_uid"],),
        ).fetchone()
        leader_expiry = _parse_time(leader["lease_expires_at"]) if leader else None
        if (
            leader is None
            or leader["executor_id"] != executor_id
            or leader["tab_id"] != action["leader_tab_id"]
            or int(leader["leader_epoch"]) != int(action["leader_epoch"])
            or not leader_expiry
            or leader_expiry <= datetime.now(timezone.utc)
        ):
            raise AppError(status_code=409, error_category="CHAT_NOT_LEADER", error_message="发送前领导页任期已失效。")
        now = _now()
        connection.execute(
            """
            UPDATE fj_chat_send_actions
            SET status = 'dispatching', dispatched_at = ?, dispatch_deadline_at = ?,
                canonical_status = 'dispatching', canonical_updated_at = ?,
                canonical_reason = '页面发送已开始', updated_at = ?
            WHERE id = ?
            """,
            (now, _after(SEND_DISPATCH_TIMEOUT_SECONDS), now, now, action_id),
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
        late_unknown_result = action["status"] == "unknown"
        if action["status"] not in {"leased", "dispatching"} and not late_unknown_result:
            return action
        outcome = payload["outcome"]
        now = _now()
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
        if outcome == "accepted":
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
            WHERE t.status = 'pending_generation' AND s.status = 'active'
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
            WHERE t.status = 'pending_generation' AND s.status = 'active'
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


def _batch_candidates(db: Database) -> list[dict[str, Any]]:
    """返回需要同步最新消息的会话，并计算岗位详情状态。"""
    with db.connect() as connection:
        rows = connection.execute(
            """
            SELECT s.id, s.peer_name, s.company_name, s.job_title,
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
            WHERE NOT EXISTS(
              SELECT 1 FROM fj_chat_messages m WHERE m.session_id = s.id
            ) OR s.message_update_required = 1
            -- 批量队列与左侧会话列表保持同一套 BOSS 顺序。
            ORDER BY s.platform_synced_at DESC, s.platform_list_index ASC, s.id ASC
            """
        ).fetchall()
    return [_row(row) or {} for row in rows]


def get_batch_summary(db: Database) -> dict[str, int]:
    candidates = _batch_candidates(db)
    queued = candidates[:CHAT_BATCH_LIMIT]
    return {
        "pending_chat_count": len(candidates),
        "pending_job_count": sum(
            1 for item in candidates if item.get("job_detail_status") != "completed"
        ),
        "queued_chat_count": len(queued),
        "batch_limit": CHAT_BATCH_LIMIT,
    }


class BossChatBatchManager:
    """按会话顺序同步最新聊天，并按需补齐岗位详情。"""

    def __init__(self) -> None:
        self._tasks: dict[str, dict[str, Any]] = {}
        self._active_task_id = ""

    def start(self, db: Database, config: AppConfig, *, batch_size: int = CHAT_BATCH_LIMIT) -> dict[str, Any]:
        if self._active_task_id:
            active = self._tasks.get(self._active_task_id)
            if active and active["status"] in {"queued", "running"}:
                return self.get(self._active_task_id)
        candidates = _batch_candidates(db)[:max(1, min(batch_size, CHAT_BATCH_LIMIT))]
        if not candidates:
            raise AppError(409, "CHAT_BATCH_EMPTY", "当前没有需要更新的聊天记录。")
        now = _now()
        task_id = _id("chat_batch")
        self._tasks[task_id] = {
            "id": task_id,
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
            "message": "批量更新任务已创建。",
            "created_at": now,
            "finished_at": None,
            "candidates": candidates,
        }
        self._active_task_id = task_id
        threading.Thread(target=self._run, args=(task_id, db, config), daemon=True).start()
        return self.get(task_id)

    def get(self, task_id: str) -> dict[str, Any]:
        task = self._tasks.get(task_id)
        if task is None:
            raise AppError(404, "CHAT_BATCH_NOT_FOUND", "批量更新任务不存在。")
        return {key: value for key, value in task.items() if key != "candidates"}

    def _run(self, task_id: str, db: Database, config: AppConfig) -> None:
        task = self._tasks[task_id]
        task.update(status="running", stage="syncing_chat", message="开始同步聊天记录。")
        try:
            for index, candidate in enumerate(task["candidates"], start=1):
                task.update(
                    current=index - 1,
                    current_session_name=str(candidate.get("peer_name") or candidate.get("company_name") or "当前会话"),
                    current_job_title=str(candidate.get("job_title") or ""),
                    stage="syncing_chat",
                    message="正在获取最新 20 条聊天消息。",
                )
                try:
                    with db.connect() as connection:
                        session = _session_or_404(connection, str(candidate["id"]))
                    captured = boss_scraper_service.capture_chat_history(
                        boss_id=str(session["encrypt_peer_uid"] or ""),
                        security_id=str(session["security_id"] or ""),
                    )
                    sync_history_messages(
                        db,
                        session_id=str(candidate["id"]),
                        messages=list(captured.get("messages") or []),
                        history_has_more=bool(captured.get("has_more")),
                        history_next_cursor=str(captured.get("next_cursor") or ""),
                    )
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
                    task.update(stage="waiting_next", message="等待后处理下一条聊天记录。")
                    time.sleep(random.randint(2, 5))
            task.update(status="completed", stage="completed", message="批量更新已完成。", finished_at=_now())
        except Exception as exc:
            task.update(status="failed", stage="failed", message=str(exc)[:300], finished_at=_now())
        finally:
            if self._active_task_id == task_id:
                self._active_task_id = ""


boss_chat_batch_manager = BossChatBatchManager()
