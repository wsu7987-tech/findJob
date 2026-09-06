from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from backend.app.config import AppConfig
from backend.app.db import Database
from backend.app.errors import AppError
from backend.app.services.fine_job import boss_chat
from backend.app.utils import utc_now


ACTION_TYPES = {
    "respond_interview",
    "send_resume",
    "reply_recruiter",
    "review_draft",
    "followup_recruiter",
    "ask_rejection_reason",
}
PRIORITY_ORDER = {"urgent": 0, "high": 1, "normal": 2, "low": 3}
IN_FLIGHT_SEND_STATUSES = {"queued", "leased", "dispatching", "unknown", "accepted"}
CANDIDATE_RESPONSE_DELAY = timedelta(days=1)
SEVERE_FOLLOWUP_OVERDUE = timedelta(days=3)
BATCH_DRAFT_ACTION_TYPES = {
    "respond_interview",
    "reply_recruiter",
    "followup_recruiter",
    "ask_rejection_reason",
}


def list_job_actions(
    db: Database,
    *,
    status: str = "active",
    priority: str | None = None,
    action_type: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    generated_at = _as_utc(now or datetime.now(timezone.utc))
    with db.connect() as connection:
        items = _derive_current_actions(connection, generated_at)
        _apply_user_states(connection, items, generated_at)

    summary = _summary(items)
    filtered = [item for item in items if item["state"] == status]
    if priority:
        filtered = [item for item in filtered if item["priority_tier"] == priority]
    if action_type:
        filtered = [item for item in filtered if item["action_type"] == action_type]
    filtered.sort(key=_sort_key)
    return {
        "summary": summary,
        "items": [_public_item(item) for item in filtered],
        "generated_at": _format_time(generated_at),
    }


def generate_job_action_drafts(
    db: Database,
    config: AppConfig,
    action_keys: list[str],
) -> dict[str, Any]:
    results = [
        _generate_job_action_draft(db, config, action_key)
        for action_key in action_keys
    ]
    return {"results": results}


def _generate_job_action_draft(
    db: Database,
    config: AppConfig,
    action_key: str,
) -> dict[str, Any]:
    try:
        now = datetime.now(timezone.utc)
        with db.connect() as connection:
            items = _derive_current_actions(connection, now, include_generation_state=True)
            _apply_user_states(connection, items, now)
            item = next(
                (
                    candidate
                    for candidate in items
                    if action_key in {
                        candidate["action_key"],
                        candidate.get("_business_action_key"),
                    }
                ),
                None,
            )

        if item is None:
            return _draft_result(action_key, "skipped", error="该行动已失效，请刷新后重试。")
        if item["state"] != "active":
            return _draft_result(action_key, "skipped", error="该行动当前无需生成草稿。")
        if item["action_type"] == "review_draft" or item.get("_generation_status") in {
            "pending_generation",
            "generating",
            "awaiting_review",
        }:
            reply_task_id = item.get("_generation_task_id") or (
                item.get("reply_task") or {}
            ).get("id")
            return _draft_result(
                action_key,
                "already_exists",
                reply_task_id=reply_task_id,
            )
        if item["action_type"] not in BATCH_DRAFT_ACTION_TYPES:
            return _draft_result(action_key, "skipped", error="该行动不支持批量生成草稿。")

        action_kind = str(item["primary_action"].get("action_kind") or "")
        task, created = boss_chat.generate_reply_for_action(
            db,
            config,
            str(item["session_id"]),
            action_kind=action_kind,
            job_action_key=str(item["_business_action_key"]),
        )
        return _draft_result(
            action_key,
            "created" if created else "already_exists",
            reply_task_id=str(task.get("id") or "") or None,
        )
    except AppError as exc:
        return _draft_result(action_key, "failed", error=exc.error_message)
    except Exception as exc:
        return _draft_result(action_key, "failed", error=str(exc)[:500] or "草稿生成失败。")


def _draft_result(
    action_key: str,
    status: str,
    *,
    reply_task_id: str | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    return {
        "action_key": action_key,
        "status": status,
        "reply_task_id": reply_task_id,
        "error": error,
    }


def set_action_state(
    db: Database,
    action_key: str,
    status: str,
    *,
    snoozed_until: datetime | None = None,
) -> dict[str, Any]:
    if status not in {"snoozed", "dismissed", "completed"}:
        raise AppError(422, "JOB_ACTION_STATE_INVALID", "行动状态无效。")
    now = datetime.now(timezone.utc)
    normalized_snooze: datetime | None = None
    if status == "snoozed":
        if snoozed_until is None:
            raise AppError(422, "JOB_ACTION_SNOOZE_TIME_REQUIRED", "请提供稍后处理时间。")
        normalized_snooze = _as_utc(snoozed_until)
        if normalized_snooze <= now:
            raise AppError(422, "JOB_ACTION_SNOOZE_TIME_INVALID", "稍后处理时间必须晚于当前时间。")

    with db.connect() as connection:
        # 写入前根据正式状态重算，数据库字段全部使用服务端解析出的当前行动。
        item = _find_current_action(connection, action_key, now)
        if item is None:
            existing = connection.execute(
                "SELECT 1 FROM fj_job_action_item_states WHERE action_key = ?",
                (action_key,),
            ).fetchone()
            if existing:
                raise AppError(409, "JOB_ACTION_EXPIRED", "该行动已经失效，请刷新今日行动。")
            raise AppError(404, "JOB_ACTION_NOT_FOUND", "当前行动不存在。")

        timestamp = utc_now()
        snooze_value = _format_time(normalized_snooze) if normalized_snooze else None
        connection.execute(
            """
            INSERT INTO fj_job_action_item_states (
              action_key, job_id, session_id, action_type, status,
              snoozed_until, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(action_key) DO UPDATE SET
              job_id = excluded.job_id,
              session_id = excluded.session_id,
              action_type = excluded.action_type,
              status = excluded.status,
              snoozed_until = excluded.snoozed_until,
              updated_at = excluded.updated_at
            """,
            (
                action_key,
                item["job_id"],
                item["session_id"],
                item["action_type"],
                status,
                snooze_value,
                timestamp,
                timestamp,
            ),
        )
        item["state"] = status
        item["snoozed_until"] = snooze_value
        item["secondary_actions"] = ["restore"]
    return {
        "action_key": action_key,
        "state": status,
        "snoozed_until": snooze_value,
        "item": _public_item(item),
    }


def restore_action_state(db: Database, action_key: str) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    with db.connect() as connection:
        existing = connection.execute(
            "SELECT 1 FROM fj_job_action_item_states WHERE action_key = ?",
            (action_key,),
        ).fetchone()
        if existing is None:
            raise AppError(404, "JOB_ACTION_STATE_NOT_FOUND", "该行动没有可恢复的用户状态。")
        connection.execute(
            "DELETE FROM fj_job_action_item_states WHERE action_key = ?",
            (action_key,),
        )
        item = _find_current_action(connection, action_key, now)
    return {
        "action_key": action_key,
        "state": "active" if item else None,
        "snoozed_until": None,
        "item": _public_item(item) if item else None,
    }


def _find_current_action(
    connection: sqlite3.Connection,
    action_key: str,
    now: datetime,
) -> dict[str, Any] | None:
    return next(
        (item for item in _derive_current_actions(connection, now) if item["action_key"] == action_key),
        None,
    )


def _derive_current_actions(
    connection: sqlite3.Connection,
    now: datetime,
    *,
    include_generation_state: bool = False,
) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        WITH ranked_sessions AS (
          SELECT s.*,
                 ROW_NUMBER() OVER (
                   PARTITION BY s.job_id
                   ORDER BY COALESCE(
                     s.last_message_at, s.platform_latest_message_at,
                     s.updated_at, s.created_at
                   ) DESC, s.id DESC
                 ) AS row_number
          FROM fj_chat_sessions s
          WHERE s.job_id IS NOT NULL
        )
        SELECT p.job_id, p.stage, p.stage_event_id, p.stage_updated_at,
               p.waiting_on, p.waiting_since_at,
               p.rejection_reason_source, p.rejection_reason_category,
               j.title, j.company_name,
               s.id AS session_id, s.session_version,
               s.latest_message_id, s.latest_inbound_message_id,
               s.job_title AS session_job_title,
               s.company_name AS session_company_name,
               att.attention_status, att.recommended_action,
               att.decision AS attention_decision,
               att.reason AS attention_reason,
               att.reason_code AS attention_reason_code,
               att.recommended_at, att.updated_at AS attention_updated_at,
               att.insight_id AS attention_insight_id,
               att.evidence_message_ids_json
        FROM fj_job_pipeline_snapshots p
        JOIN fj_boss_jobs j ON j.id = p.job_id
        LEFT JOIN ranked_sessions s
          ON s.job_id = p.job_id AND s.row_number = 1
        LEFT JOIN fj_chat_attention_states att ON att.session_id = s.id
        """
    ).fetchall()
    session_ids = [str(row["session_id"]) for row in rows if row["session_id"]]
    tasks = _load_active_tasks(connection, session_ids)
    items: list[dict[str, Any]] = []
    for row in rows:
        if not row["session_id"]:
            continue
        base = _build_business_action(connection, row, now)
        if base is None:
            continue
        if _trigger_was_consumed(connection, base, row):
            continue
        task = _matching_task(tasks.get(str(row["session_id"]), []), base, row)
        if task is not None:
            task_status = str(task["status"])
            if task_status in {"pending_generation", "generating"}:
                if include_generation_state:
                    base["_generation_status"] = task_status
                    base["_generation_task_id"] = str(task["id"])
                else:
                    continue
            if task_status == "awaiting_review":
                base = _as_review_action(base, task)
            elif task_status == "confirmed" and _send_action_suppresses(task):
                continue
        items.append(base)
    return items


def _load_active_tasks(
    connection: sqlite3.Connection,
    session_ids: list[str],
) -> dict[str, list[sqlite3.Row]]:
    if not session_ids:
        return {}
    placeholders = ",".join("?" for _ in session_ids)
    rows = connection.execute(
        f"""
        SELECT t.*, send.status AS send_status,
               send.canonical_status AS send_canonical_status
        FROM fj_chat_reply_tasks t
        LEFT JOIN fj_chat_send_actions send ON send.reply_task_id = t.id
        WHERE t.session_id IN ({placeholders})
          AND t.status IN (
            'pending_generation', 'generating', 'awaiting_review', 'confirmed'
          )
        ORDER BY t.updated_at DESC, t.created_at DESC, t.id DESC
        """,
        tuple(session_ids),
    ).fetchall()
    result: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        result[str(row["session_id"])].append(row)
    return result


def _build_business_action(
    connection: sqlite3.Connection,
    row: sqlite3.Row,
    now: datetime,
) -> dict[str, Any] | None:
    stage = str(row["stage"])
    waiting_on = str(row["waiting_on"] or "unknown")
    session_id = str(row["session_id"])
    latest_message_id = str(row["latest_message_id"] or "")
    latest_inbound_message_id = str(row["latest_inbound_message_id"] or "")
    stage_event_id = str(row["stage_event_id"])

    # 终态先于草稿判断，避免历史草稿继续成为当前行动。
    if stage in {"offer", "closed"}:
        return None

    action_type = ""
    action_key = ""
    expected_action_kind = ""
    trigger_type = "activity_event"
    trigger_id = stage_event_id
    trigger_message_id = ""

    if stage == "rejected":
        if str(row["recommended_action"] or "") != "ask_rejection_reason":
            return None
        action_type = "ask_rejection_reason"
        action_key = f"rejection_reason:{row['job_id']}:{stage_event_id}"
        expected_action_kind = "ask_rejection_reason"
        trigger_message_id = latest_message_id
    elif waiting_on == "candidate":
        trigger_message_id = latest_inbound_message_id
        if stage == "interview_scheduling":
            action_type = "respond_interview"
            action_key = f"interview:{row['job_id']}:{stage_event_id}"
            expected_action_kind = "reply"
        elif stage == "resume_requested":
            if _has_later_resume_submission(connection, str(row["job_id"]), stage_event_id):
                return None
            action_type = "send_resume"
            action_key = f"resume:{row['job_id']}:{stage_event_id}"
            expected_action_kind = "reply"
        else:
            if not latest_inbound_message_id:
                return None
            action_type = "reply_recruiter"
            action_key = f"reply:{session_id}:{latest_inbound_message_id}"
            expected_action_kind = "reply"
            trigger_type = "message"
            trigger_id = latest_inbound_message_id
    elif waiting_on == "recruiter" and (
        str(row["attention_decision"] or "") == "follow"
        or str(row["attention_status"] or "") == "needs_followup"
    ):
        action_type = "followup_recruiter"
        trigger_message_id = latest_message_id
        followup_revision = str(
            row["attention_insight_id"]
            or row["recommended_at"]
            or row["attention_updated_at"]
            or latest_message_id
            or stage_event_id
        )
        trigger_id = latest_message_id or stage_event_id
        trigger_type = "message" if latest_message_id else "activity_event"
        action_key = f"followup:{session_id}:{followup_revision}"
        expected_action_kind = "followup"
    else:
        return None

    timing = _priority_and_due(action_type, row, now)
    reason_code, reason_summary = _reason(action_type, row)
    evidence_messages = _load_json_list(row["evidence_message_ids_json"])
    if trigger_message_id and trigger_message_id not in evidence_messages:
        evidence_messages.insert(0, trigger_message_id)
    activity_ids = [stage_event_id] if trigger_type == "activity_event" else []
    label = {
        "respond_interview": "回复面试",
        "send_resume": "查看并处理",
        "reply_recruiter": "生成回复",
        "followup_recruiter": "生成跟进",
        "ask_rejection_reason": "询问原因",
    }[action_type]
    return {
        "action_key": action_key,
        "job_id": str(row["job_id"]),
        "session_id": session_id,
        "action_type": action_type,
        "priority_tier": timing["priority_tier"],
        "title": str(row["title"] or row["session_job_title"] or ""),
        "company_name": str(row["company_name"] or row["session_company_name"] or ""),
        "stage": stage,
        "waiting_on": waiting_on,
        "waiting_since_at": row["waiting_since_at"],
        "due_at": timing["due_at"],
        "overdue_seconds": timing["overdue_seconds"],
        "reason_code": reason_code,
        "reason_summary": reason_summary,
        "evidence": {
            "trigger_type": trigger_type,
            "trigger_id": trigger_id,
            "message_ids": evidence_messages,
            "activity_event_ids": activity_ids,
            "attention_insight_id": row["attention_insight_id"],
        },
        "reply_task": None,
        "primary_action": {
            "type": "open_chat",
            "label": label,
            "route_name": "fine-job-chat",
            "query": {"session_id": session_id},
            "action_kind": expected_action_kind if action_type != "send_resume" else None,
            "reply_task_id": None,
        },
        "secondary_actions": ["snooze", "dismiss", "complete"],
        "state": "active",
        "snoozed_until": None,
        "_business_action_key": action_key,
        "_expected_action_kind": expected_action_kind,
        "_trigger_message_id": trigger_message_id,
        "_session_version": int(row["session_version"] or 0),
    }


def _trigger_was_consumed(
    connection: sqlite3.Connection,
    action: dict[str, Any],
    row: sqlite3.Row,
) -> bool:
    if action["action_type"] not in {"followup_recruiter", "ask_rejection_reason"}:
        return False
    trigger_started_at = (
        row["attention_updated_at"]
        if action["action_type"] == "followup_recruiter"
        else row["stage_updated_at"]
    )
    legacy_clause = ""
    params: list[Any] = [
        action["session_id"],
        action["_expected_action_kind"],
        action["_business_action_key"],
    ]
    if trigger_started_at:
        legacy_clause = "OR (t.job_action_key IS NULL AND t.confirmed_at >= ?)"
        params.append(trigger_started_at)
    row_result = connection.execute(
        f"""
        SELECT 1
        FROM fj_chat_reply_tasks t
        JOIN fj_chat_send_actions send ON send.reply_task_id = t.id
        WHERE t.session_id = ?
          AND t.action_kind = ?
          AND (t.job_action_key = ? {legacy_clause})
          AND (
            send.status IN ('queued', 'leased', 'dispatching', 'unknown', 'accepted')
            OR send.canonical_status IN ('pending', 'running', 'unknown', 'succeeded')
          )
        LIMIT 1
        """,
        tuple(params),
    ).fetchone()
    return row_result is not None


def _matching_task(
    tasks: list[sqlite3.Row],
    action: dict[str, Any],
    row: sqlite3.Row,
) -> sqlite3.Row | None:
    expected_kind = action["_expected_action_kind"]
    trigger_message_id = action["_trigger_message_id"]
    session_version = action["_session_version"]
    for task in tasks:
        if str(task["action_kind"] or "reply") != expected_kind:
            continue
        if task["job_action_key"] and str(task["job_action_key"]) != action["_business_action_key"]:
            continue
        if trigger_message_id and str(task["based_on_message_id"] or "") != trigger_message_id:
            continue
        if int(task["based_on_session_version"] or 0) != session_version:
            continue
        # rejected 只接受询问原因草稿，普通回复和跟进草稿不会进入审核行动。
        if str(row["stage"]) == "rejected" and expected_kind != "ask_rejection_reason":
            continue
        return task
    return None


def _send_action_suppresses(task: sqlite3.Row) -> bool:
    send_status = str(task["send_status"] or "")
    canonical_status = str(task["send_canonical_status"] or "")
    return send_status in IN_FLIGHT_SEND_STATUSES or canonical_status in {
        "pending",
        "running",
        "unknown",
        "succeeded",
    }


def _as_review_action(action: dict[str, Any], task: sqlite3.Row) -> dict[str, Any]:
    reviewed = dict(action)
    reviewed["action_key"] = f"review_draft:{task['id']}"
    reviewed["action_type"] = "review_draft"
    reviewed["reply_task"] = {
        "id": str(task["id"]),
        "action_kind": str(task["action_kind"] or "reply"),
        "status": "awaiting_review",
        "based_on_message_id": str(task["based_on_message_id"]),
        "based_on_session_version": int(task["based_on_session_version"]),
        "draft_text": str(task["draft_text"] or ""),
        "final_text": str(task["final_text"] or ""),
        "generated_at": task["generated_at"],
        "updated_at": str(task["updated_at"]),
    }
    reviewed["evidence"] = {
        **action["evidence"],
        "trigger_type": "reply_task",
        "trigger_id": str(task["id"]),
    }
    reviewed["primary_action"] = {
        **action["primary_action"],
        "label": "审核草稿",
        "reply_task_id": str(task["id"]),
    }
    return reviewed


def _has_later_resume_submission(
    connection: sqlite3.Connection,
    job_id: str,
    request_event_id: str,
) -> bool:
    anchor = connection.execute(
        "SELECT occurred_at, created_at, id FROM fj_job_activity_events WHERE id = ?",
        (request_event_id,),
    ).fetchone()
    if anchor is None:
        return False
    row = connection.execute(
        """
        SELECT 1 FROM fj_job_activity_events
        WHERE job_id = ?
          AND event_type IN ('resume_submitted', 'resume_accepted', 'resume_viewed')
          AND (
            occurred_at > ?
            OR (occurred_at = ? AND created_at > ?)
            OR (occurred_at = ? AND created_at = ? AND id > ?)
          )
        LIMIT 1
        """,
        (
            job_id,
            anchor["occurred_at"],
            anchor["occurred_at"],
            anchor["created_at"],
            anchor["occurred_at"],
            anchor["created_at"],
            anchor["id"],
        ),
    ).fetchone()
    return row is not None


def _priority_and_due(
    action_type: str,
    row: sqlite3.Row,
    now: datetime,
) -> dict[str, Any]:
    waiting_since = _parse_time(row["waiting_since_at"] or row["stage_updated_at"])
    due: datetime | None = None
    priority = "low"
    if action_type == "respond_interview":
        due = waiting_since
        priority = "urgent"
    elif action_type in {"send_resume", "reply_recruiter"}:
        due = waiting_since + CANDIDATE_RESPONSE_DELAY if waiting_since else None
        priority = "urgent" if due and now > due else "high"
    elif action_type == "followup_recruiter":
        due = _parse_time(row["recommended_at"])
        priority = (
            "high"
            if due and now - due >= SEVERE_FOLLOWUP_OVERDUE
            else "normal"
        )
    overdue = max(0, int((now - due).total_seconds())) if due else 0
    return {
        "priority_tier": priority,
        "due_at": _format_time(due) if due else None,
        "overdue_seconds": overdue,
    }


def _reason(action_type: str, row: sqlite3.Row) -> tuple[str, str]:
    defaults = {
        "respond_interview": ("candidate_interview_response_due", "招聘方正在等待确认面试安排。"),
        "send_resume": ("candidate_resume_due", "招聘方已请求发送或补充简历。"),
        "reply_recruiter": ("user_owes_reply", "招聘方发来了需要处理的新消息。"),
        "followup_recruiter": ("recruiter_owes_reply", "等待招聘方反馈已达到建议跟进时间。"),
        "ask_rejection_reason": ("rejected_no_reason", "已确认拒绝，建议询问具体原因。"),
    }
    code, summary = defaults[action_type]
    attention_code = str(row["attention_reason_code"] or "")
    attention_reason = str(row["attention_reason"] or "")
    if action_type in {"reply_recruiter", "followup_recruiter", "ask_rejection_reason"}:
        return attention_code or code, attention_reason or summary
    return code, summary


def _apply_user_states(
    connection: sqlite3.Connection,
    items: list[dict[str, Any]],
    now: datetime,
) -> None:
    if not items:
        return
    placeholders = ",".join("?" for _ in items)
    rows = connection.execute(
        f"SELECT * FROM fj_job_action_item_states WHERE action_key IN ({placeholders})",
        tuple(item["action_key"] for item in items),
    ).fetchall()
    states = {str(row["action_key"]): row for row in rows}
    for item in items:
        row = states.get(item["action_key"])
        if row is None:
            continue
        state = str(row["status"])
        snoozed_until = row["snoozed_until"]
        if state == "snoozed":
            expiry = _parse_time(snoozed_until)
            if expiry is None or expiry <= now:
                continue
        item["state"] = state
        item["snoozed_until"] = snoozed_until
        item["secondary_actions"] = ["restore"]


def _summary(items: list[dict[str, Any]]) -> dict[str, int]:
    result = {"urgent": 0, "high": 0, "normal": 0, "low": 0, "snoozed": 0}
    for item in items:
        if item["state"] == "snoozed":
            result["snoozed"] += 1
        elif item["state"] == "active":
            result[item["priority_tier"]] += 1
    return result


def _sort_key(item: dict[str, Any]) -> tuple[Any, ...]:
    due = _parse_time(item["due_at"])
    waiting = _parse_time(item["waiting_since_at"])
    maximum = datetime.max.replace(tzinfo=timezone.utc)
    return (
        PRIORITY_ORDER[item["priority_tier"]],
        due or maximum,
        waiting or maximum,
        item["action_key"],
    )


def _public_item(item: dict[str, Any] | None) -> dict[str, Any] | None:
    if item is None:
        return None
    return {key: value for key, value in item.items() if not key.startswith("_")}


def _load_json_list(value: Any) -> list[str]:
    try:
        loaded = json.loads(str(value or "[]"))
    except json.JSONDecodeError:
        return []
    if not isinstance(loaded, list):
        return []
    return [str(item) for item in loaded if str(item)]


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return _as_utc(parsed)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _format_time(value: datetime) -> str:
    return _as_utc(value).replace(microsecond=0).isoformat().replace("+00:00", "Z")
