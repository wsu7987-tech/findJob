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
    waiting_on = str(snapshot["waiting_on"] or "unknown")
    reason_source = str(snapshot["rejection_reason_source"] or "unknown")
    reason_category = str(snapshot["rejection_reason_category"] or "unknown")
    decision = str(attention["decision"] or "wait") if attention else "wait"
    primary_action = _primary_action(
        stage, waiting_on, reason_source, reason_category, decision
    )
    draft_text = ""
    if draft:
        draft_text = str(draft["final_text"] or draft["draft_text"] or "")

    return {
        "job_id": job_id,
        "session_id": selected_session_id,
        "stage": stage,
        "stage_updated_at": str(snapshot["stage_updated_at"]),
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
        },
        "primary_action": primary_action,
        "analysis_updated_at": analysis_updated_at,
    }


def _latest_session_id(connection: sqlite3.Connection, job_id: str) -> str | None:
    row = connection.execute(
        """
        SELECT id FROM fj_chat_sessions
        WHERE job_id = ?
        ORDER BY COALESCE(last_message_at, platform_latest_message_at, updated_at) DESC
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


def _primary_action(
    stage: str,
    waiting_on: str,
    rejection_reason_source: str,
    rejection_reason_category: str,
    decision: str,
) -> dict[str, Any] | None:
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
