from __future__ import annotations

import json
import sqlite3
from typing import Any

from backend.app.db import Database


def get_job_progress(
    db: Database,
    job_id: str,
    *,
    session_id: str | None = None,
) -> dict[str, Any] | None:
    with db.connect() as connection:
        return build_job_progress_with_connection(
            connection, job_id, session_id=session_id
        )


def build_job_progress_with_connection(
    connection: sqlite3.Connection,
    job_id: str,
    *,
    session_id: str | None = None,
) -> dict[str, Any] | None:
    snapshot = connection.execute(
        "SELECT * FROM fj_job_pipeline_snapshots WHERE job_id = ?", (job_id,)
    ).fetchone()
    if snapshot is None:
        return None

    selected_session_id = session_id or _latest_session_id(connection, job_id)
    attention = None
    draft = None
    analysis_updated_at = None
    if selected_session_id:
        attention = connection.execute(
            "SELECT * FROM fj_chat_attention_states WHERE session_id = ?",
            (selected_session_id,),
        ).fetchone()
        draft = connection.execute(
            """
            SELECT id, action_kind, draft_text, final_text, status
            FROM fj_chat_reply_tasks
            WHERE session_id = ?
              AND status IN ('pending_generation', 'generating', 'awaiting_review')
            ORDER BY updated_at DESC LIMIT 1
            """,
            (selected_session_id,),
        ).fetchone()
        insight = connection.execute(
            """
            SELECT updated_at FROM fj_conversation_insights
            WHERE session_id = ? AND status = 'analyzed'
            ORDER BY updated_at DESC, created_at DESC LIMIT 1
            """,
            (selected_session_id,),
        ).fetchone()
        analysis_updated_at = str(insight["updated_at"]) if insight else None

    latest_activity = connection.execute(
        """
        SELECT * FROM fj_job_activity_events
        WHERE job_id = ?
        ORDER BY occurred_at DESC, created_at DESC, id DESC LIMIT 1
        """,
        (job_id,),
    ).fetchone()
    stage = str(snapshot["stage"])
    stage_updated_at = str(snapshot["stage_updated_at"])
    waiting_on = str(snapshot["waiting_on"] or "unknown")
    resume_delivery = _resume_delivery(connection, selected_session_id)
    recruiter_contact = _pending_resume_recruiter_contact(
        connection,
        job_id=job_id,
        session_id=selected_session_id,
        stage=stage,
        resume_delivery=resume_delivery,
    )
    # 招聘方主动触达后，未实际发送简历的会话统一引导到发简历。
    if recruiter_contact is not None:
        stage = "resume_requested"
        stage_updated_at = str(recruiter_contact["occurred_at"])
        waiting_on = "candidate"
    reason_source = str(snapshot["rejection_reason_source"] or "unknown")
    reason_category = str(snapshot["rejection_reason_category"] or "unknown")
    rejection_party = _rejection_party(connection, snapshot["stage_event_id"])
    decision = str(attention["decision"] or "wait") if attention else "wait"
    primary_action = _primary_action(
        stage, waiting_on, reason_source, reason_category, decision, rejection_party
    )
    draft_text = ""
    if draft:
        draft_text = str(draft["final_text"] or draft["draft_text"] or "")

    return {
        "job_id": job_id,
        "session_id": selected_session_id,
        "stage": stage,
        "stage_updated_at": stage_updated_at,
        "waiting_on": waiting_on,
        "waiting_since_at": snapshot["waiting_since_at"],
        "contact_origin": str(snapshot["contact_origin"] or "unknown"),
        "latest_activity": _activity_payload(latest_activity),
        "followup": {
            "decision": decision,
            "reason_code": str(attention["reason_code"] or "") if attention else "",
            "reason_summary": str(attention["reason"] or "") if attention else "",
            "recommended_at": attention["recommended_at"] if attention else None,
            "recommended_action": str(attention["recommended_action"] or "no_further_action") if attention else "no_further_action",
            "draft_message": draft_text,
            "draft_task_id": str(draft["id"]) if draft else None,
        },
        "outcome": {
            "status": stage if stage in {"offer", "rejected", "closed"} else "ongoing",
            "rejection_reason_source": reason_source,
            "rejection_reason_category": str(snapshot["rejection_reason_category"] or "unknown"),
            "rejection_reason_summary": str(snapshot["rejection_reason_summary"] or ""),
            "rejection_party": rejection_party,
        },
        "resume_delivery": resume_delivery,
        "primary_action": primary_action,
        "analysis_updated_at": analysis_updated_at,
    }


def _pending_resume_recruiter_contact(
    connection: sqlite3.Connection,
    *,
    job_id: str,
    session_id: str | None,
    stage: str,
    resume_delivery: dict[str, Any],
) -> sqlite3.Row | None:
    """找出需要候选人发送简历的招聘方主动触达。"""
    if not session_id or stage in {"offer", "rejected", "closed"}:
        return None
    if str(resume_delivery.get("status") or "") in {"sent", "received", "viewed", "withdrawn"}:
        return None
    return connection.execute(
        """
        SELECT occurred_at
        FROM fj_job_activity_events
        WHERE job_id = ? AND chat_session_id = ?
          AND event_type IN ('recruiter_initiated_contact', 'recruiter_replied')
        ORDER BY occurred_at DESC, created_at DESC, id DESC
        LIMIT 1
        """,
        (job_id, session_id),
    ).fetchone()


def _latest_session_id(connection: sqlite3.Connection, job_id: str) -> str | None:
    row = connection.execute(
        """
        SELECT id FROM fj_chat_sessions
        WHERE job_id = ?
        ORDER BY COALESCE(
          last_message_at, platform_latest_message_at, updated_at, created_at
        ) DESC, id DESC
        LIMIT 1
        """,
        (job_id,),
    ).fetchone()
    return str(row["id"]) if row else None


def _activity_payload(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    payload = dict(row)
    try:
        payload["payload"] = json.loads(str(payload.pop("payload_json") or "{}"))
    except json.JSONDecodeError:
        payload["payload"] = {}
    payload.pop("dedupe_key", None)
    return payload


def _rejection_party(connection: sqlite3.Connection, stage_event_id: str) -> str | None:
    row = connection.execute(
        "SELECT payload_json FROM fj_job_activity_events WHERE id = ?", (stage_event_id,)
    ).fetchone()
    if row is None:
        return None
    try:
        party = json.loads(str(row["payload_json"] or "{}")).get("rejection_party")
    except json.JSONDecodeError:
        return None
    return party if party in {"candidate", "recruiter"} else None


def _resume_delivery(
    connection: sqlite3.Connection, session_id: str | None
) -> dict[str, Any]:
    """会话简历状态以平台动作优先，发送队列只表达尚未被平台观察到的过程。"""
    if not session_id:
        return {"status": "not_started", "source": "none", "occurred_at": None}
    action_message = connection.execute(
        """
        SELECT action_type, sent_at FROM fj_chat_messages
        WHERE session_id = ? AND display_kind = 'action'
          AND action_type IN (
            'resume_sent', 'resume_sent_confirmed', 'resume_received',
            'resume_viewed', 'resume_withdrawn', 'resume_withdrawn_by_hr'
          )
        ORDER BY sent_at DESC, rowid DESC LIMIT 1
        """,
        (session_id,),
    ).fetchone()
    if action_message is not None:
        status = {
            "resume_sent": "sent",
            "resume_sent_confirmed": "sent",
            "resume_received": "received",
            "resume_viewed": "viewed",
            "resume_withdrawn": "withdrawn",
            "resume_withdrawn_by_hr": "withdrawn",
        }[str(action_message["action_type"])]
        return {"status": status, "source": "chat_action", "occurred_at": action_message["sent_at"]}

    action = connection.execute(
        """
        SELECT confirmation_status, status, outcome, created_at, updated_at
        FROM fj_chat_send_actions
        WHERE session_id = ? AND operation_kind = 'resume'
        ORDER BY created_at DESC LIMIT 1
        """,
        (session_id,),
    ).fetchone()
    if action is None:
        return {"status": "not_started", "source": "none", "occurred_at": None}
    if str(action["confirmation_status"] or "") == "pending":
        status = "pending_confirmation"
    elif str(action["status"] or "") in {"leased", "dispatching"}:
        status = "sending"
    elif str(action["outcome"] or "") == "failed":
        status = "failed"
    elif str(action["outcome"] or "") == "unknown":
        status = "unknown"
    elif str(action["outcome"] or "") == "accepted":
        status = "awaiting_observation"
    else:
        status = "queued"
    return {"status": status, "source": "send_action", "occurred_at": action["updated_at"] or action["created_at"]}


def _primary_action(
    stage: str,
    waiting_on: str,
    rejection_reason_source: str,
    rejection_reason_category: str,
    decision: str,
    rejection_party: str | None,
) -> dict[str, Any] | None:
    if stage == "rejected" and rejection_party == "candidate":
        return None
    if stage == "rejected" and (
        rejection_reason_source == "unknown"
        or rejection_reason_category in {"unknown", "fit"}
    ):
        return {"type": "ask_rejection_reason", "label": "询问拒绝原因"}
    if stage in {"offer", "rejected", "closed"}:
        return None
    if waiting_on == "candidate":
        return {"type": "reply", "label": "生成回复"}
    if waiting_on == "recruiter" and decision == "follow":
        return {"type": "followup", "label": "生成跟进"}
    return None
