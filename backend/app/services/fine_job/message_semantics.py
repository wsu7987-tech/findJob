from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from typing import Any


CLASSIFIER_VERSION = "boss-message-semantic-v1"


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _loads(value: str | None, fallback: Any) -> Any:
    try:
        return json.loads(value or "")
    except (TypeError, ValueError):
        return fallback


def _filename_key(value: str) -> str:
    return re.sub(r"\s+", "", value).casefold()


def _body_type(raw_body: dict[str, Any], raw_meta: dict[str, Any]) -> int | None:
    value = raw_body.get("type", raw_meta.get("platform_type"))
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _resume_context(
    connection: sqlite3.Connection,
    session_id: str,
) -> list[dict[str, str]]:
    rows = connection.execute(
        """
        SELECT a.id, a.resume_filename, a.status,
               EXISTS(
                 SELECT 1 FROM fj_action_message_links link
                 WHERE link.action_id = a.id AND link.role = 'outbound_result'
               ) AS outbound_observed
        FROM fj_actions a
        WHERE a.session_id = ? AND a.action_type = 'resume_send'
          AND a.status = 'succeeded'
          AND EXISTS (
            SELECT 1 FROM fj_action_message_links link
            WHERE link.action_id = a.id AND link.role = 'outbound_result'
          )
        ORDER BY created_at DESC, id DESC
        """,
        (session_id,),
    ).fetchall()
    return [
        {
            "action_id": str(row["id"]),
            "resume_filename": str(row["resume_filename"] or ""),
            "status": str(row["status"]),
            "outbound_observed": "true" if row["outbound_observed"] else "false",
        }
        for row in rows
    ]


def derive_message_semantics(connection: sqlite3.Connection, raw_message_id: str) -> None:
    """依据原始结构、方向和会话 Action 上下文生成可展示的消息语义。"""
    row = connection.execute(
        "SELECT * FROM fj_chat_messages WHERE id = ?", (raw_message_id,)
    ).fetchone()
    if row is None:
        return
    raw_body = _loads(str(row["raw_body_json"] or "{}"), {})
    raw_body = raw_body if isinstance(raw_body, dict) else {}
    raw_meta = _loads(str(row["raw_meta_json"] or "{}"), {})
    raw_meta = raw_meta if isinstance(raw_meta, dict) else {}
    raw_text = str(
        row["raw_content"]
        or row["content"]
        or raw_body.get("text")
        or raw_body.get("headTitle")
        or ""
    ).strip()
    direction = str(row["direction"])
    body_type = _body_type(raw_body, raw_meta)
    resumes = _resume_context(connection, str(row["session_id"]))
    context: dict[str, Any] = {
        "direction": direction,
        "body_type": body_type,
        "resume_actions": resumes,
    }
    semantic_type = "platform_event_unknown"
    display_text = raw_text or str(row["content"] or "")
    reply_required = False
    filename_match = next(
        (
            item for item in resumes
            if item["resume_filename"]
            and _filename_key(item["resume_filename"]) == _filename_key(raw_text)
        ),
        None,
    )
    has_resume_context = bool(resumes)
    # 附件确认文案或文件名形态缺少已观察成功的简历动作时，保留未知平台事件。
    weak_resume_event = bool(
        body_type in {4, 12}
        or raw_text == "对方已同意，您的附件简历已发送给对方"
        or re.search(r"\.(pdf|doc|docx|wps|rtf)$", raw_text, re.IGNORECASE)
    )

    if direction == "outbound" and body_type in {4, 12} and has_resume_context:
        semantic_type = "resume_sent"
        display_text = "已发送附件简历"
    elif direction == "inbound" and filename_match is not None:
        semantic_type = "resume_read_receipt"
        display_text = f"HR 已查看简历：{filename_match['resume_filename']}"
        context["matched_resume_action_id"] = filename_match["action_id"]
    elif direction == "inbound" and body_type in {4, 12} and has_resume_context:
        semantic_type = "resume_viewed"
        display_text = "HR 已查看附件简历"
    elif (
        raw_text == "对方已同意，您的附件简历已发送给对方"
        and has_resume_context
    ):
        semantic_type = "resume_received_confirmation"
        display_text = "对方已接收附件简历"
    elif "你与该职位竞争者PK情况" in raw_text:
        semantic_type = "platform_ad"
        display_text = "职位竞争情况推广"
    elif direction == "inbound" and weak_resume_event and not has_resume_context:
        semantic_type = "platform_event_unknown"
        display_text = raw_text or "未关联的附件平台事件"
    elif direction == "inbound" and str(row["message_type"] or "") == "text":
        semantic_type = "recruiter_text"
        display_text = raw_text
        reply_required = True

    now = _now()
    connection.execute(
        """
        INSERT INTO fj_chat_message_semantics (
          raw_message_id, semantic_type, display_text, reply_required,
          classifier_version, semantic_context_json, derived_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(raw_message_id) DO UPDATE SET
          semantic_type = excluded.semantic_type,
          display_text = excluded.display_text,
          reply_required = excluded.reply_required,
          classifier_version = excluded.classifier_version,
          semantic_context_json = excluded.semantic_context_json,
          derived_at = excluded.derived_at
        """,
        (
            raw_message_id,
            semantic_type,
            display_text,
            int(reply_required),
            CLASSIFIER_VERSION,
            json.dumps(context, ensure_ascii=False),
            now,
        ),
    )
    if reply_required:
        read_state, read_subject, reply_state = "unread", "candidate", "unanswered"
    elif semantic_type in {"resume_read_receipt", "resume_viewed"}:
        read_state, read_subject, reply_state = "read", "recruiter", "not_required"
    elif direction == "outbound":
        read_state, read_subject, reply_state = "unknown", "recruiter", "not_required"
    else:
        read_state, read_subject, reply_state = "not_applicable", "not_applicable", "not_required"
    connection.execute(
        """
        INSERT INTO fj_chat_message_states (
          raw_message_id, read_state, read_subject, reply_state, read_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(raw_message_id) DO UPDATE SET
          read_state = excluded.read_state,
          read_subject = excluded.read_subject,
          reply_state = CASE
            WHEN excluded.reply_state = 'not_required' THEN 'not_required'
            WHEN fj_chat_message_states.reply_state IN ('reply_planned', 'queued', 'replied')
              THEN fj_chat_message_states.reply_state
            ELSE excluded.reply_state
          END,
          read_at = COALESCE(fj_chat_message_states.read_at, excluded.read_at),
          updated_at = excluded.updated_at
        """,
        (
            raw_message_id,
            read_state,
            read_subject,
            reply_state,
            now if read_state == "read" else None,
            now,
        ),
    )


def backfill_message_derivations(connection: sqlite3.Connection) -> None:
    """仅为缺少派生数据的既有消息补齐第一版语义和状态。"""
    rows = connection.execute(
        """
        SELECT m.id
        FROM fj_chat_messages m
        LEFT JOIN fj_chat_message_semantics semantic ON semantic.raw_message_id = m.id
        WHERE semantic.raw_message_id IS NULL
        ORDER BY m.created_at ASC, m.id ASC
        """
    ).fetchall()
    for row in rows:
        derive_message_semantics(connection, str(row["id"]))
