from __future__ import annotations

import json
from typing import Any

from backend.app.db import Database
from backend.app.errors import AppError


def get_job_journey(db: Database, job_id: str) -> dict[str, Any]:
    with db.connect() as connection:
        job = connection.execute(
            "SELECT id FROM fj_boss_jobs WHERE id = ?",
            (job_id,),
        ).fetchone()
        if job is None:
            raise AppError(404, "NOT_FOUND", "岗位不存在。")

        pipeline_row = connection.execute(
            "SELECT * FROM fj_job_pipeline_snapshots WHERE job_id = ?",
            (job_id,),
        ).fetchone()
        application_row = connection.execute(
            "SELECT * FROM fj_job_applications WHERE job_id = ?",
            (job_id,),
        ).fetchone()
        activity_rows = connection.execute(
            """
            SELECT * FROM fj_job_activity_events
            WHERE job_id = ?
            ORDER BY occurred_at DESC, created_at DESC, id DESC
            """,
            (job_id,),
        ).fetchall()
        automation_rows = connection.execute(
            """
            SELECT a.*,
              (SELECT browser_target_id FROM fj_boss_navigation_tasks n
               WHERE n.action_id = a.id ORDER BY n.created_at DESC LIMIT 1) AS leader_tab_id
            FROM fj_automation_actions a
            WHERE a.job_id = ?
            ORDER BY a.created_at DESC, a.id DESC
            """,
            (job_id,),
        ).fetchall()
        chat_rows = connection.execute(
            """
            SELECT a.*, s.job_id
            FROM fj_chat_send_actions a
            JOIN fj_chat_sessions s ON s.id = a.session_id
            WHERE s.job_id = ?
            ORDER BY a.created_at DESC, a.id DESC
            """,
            (job_id,),
        ).fetchall()

        executions = [
            _execution_summary(connection, "automation_action", row)
            for row in automation_rows
        ]
        executions.extend(
            _execution_summary(connection, "chat_send_action", row)
            for row in chat_rows
        )
        executions.sort(key=lambda item: (str(item["created_at"]), str(item["action_ref_id"])), reverse=True)

    pipeline = dict(pipeline_row) if pipeline_row else None
    legacy_application = dict(application_row) if application_row else None
    return {
        "job_id": job_id,
        "pipeline": pipeline,
        "legacy_application": legacy_application,
        "activities": [_activity_summary(row) for row in activity_rows],
        "executions": executions,
    }


def _execution_summary(connection, action_ref_type: str, row) -> dict[str, Any]:
    evidence_rows = connection.execute(
        """
        SELECT * FROM fj_execution_evidence
        WHERE action_ref_type = ? AND action_ref_id = ?
        ORDER BY observed_at DESC, created_at DESC, id DESC
        """,
        (action_ref_type, row["id"]),
    ).fetchall()
    reconciliation_rows = connection.execute(
        """
        SELECT * FROM fj_execution_reconciliations
        WHERE action_ref_type = ? AND action_ref_id = ?
        ORDER BY reconciled_at DESC, created_at DESC, id DESC
        """,
        (action_ref_type, row["id"]),
    ).fetchall()
    if action_ref_type == "automation_action":
        raw_status_code = row["last_status_code"] or ""
        executor_id = row["executor_id"] or ""
        dispatch_started_at = row["dispatch_started_at"]
        error_message = row["last_error"] or ""
        action_type = row["action_type"]
        session_id = None
        dedupe_identity = row["idempotency_key"]
    else:
        raw_status_code = row["status_code"] or ""
        executor_id = row["lease_owner"] or ""
        dispatch_started_at = row["dispatched_at"]
        error_message = row["error_message"] or ""
        action_type = "BOSS_CHAT_SEND"
        session_id = row["session_id"]
        dedupe_identity = row["reply_task_id"]
    canonical_status = row["canonical_status"] or _canonical_from_raw(str(row["status"]))
    return {
        "action_ref_type": action_ref_type,
        "action_ref_id": str(row["id"]),
        "action_type": str(action_type),
        "dedupe_identity": str(dedupe_identity),
        "session_id": str(session_id) if session_id else None,
        "raw_status": str(row["status"]),
        "canonical_status": str(canonical_status),
        "canonical_reason": str(row["canonical_reason"] or ""),
        "canonical_updated_at": row["canonical_updated_at"],
        "status_code": str(raw_status_code),
        "error_message": str(error_message),
        "executor_id": str(executor_id),
        "leader_tab_id": str(row["leader_tab_id"] or ""),
        "execution_epoch": int(row["execution_epoch"] or 0),
        "attempt_count": int(row["attempt_count"] or 0),
        "created_at": str(row["created_at"]),
        "started_at": row["started_at"] if action_ref_type == "automation_action" else None,
        "dispatch_started_at": dispatch_started_at,
        "completed_at": row["completed_at"],
        "evidence": [_evidence_summary(item) for item in evidence_rows],
        "reconciliations": [dict(item) for item in reconciliation_rows],
    }


def _activity_summary(row) -> dict[str, Any]:
    item = dict(row)
    item["payload"] = _public_payload(item.pop("payload_json"))
    item.pop("dedupe_key", None)
    return item


def _evidence_summary(row) -> dict[str, Any]:
    item = dict(row)
    item["payload"] = _public_payload(item.pop("payload_json"))
    item.pop("dedupe_key", None)
    return item


def _public_payload(value: object) -> dict[str, Any]:
    try:
        payload = json.loads(str(value or "{}"))
    except json.JSONDecodeError:
        return {}
    if not isinstance(payload, dict):
        return {}
    allowed = {
        "direction",
        "platform_message_id",
        "session_id",
        "match_method",
        "status_code",
        "evidence_id",
        "legacy_status",
        "legacy_filter_status",
        "filter_status",
        "stage",
        "allow_reopen",
        "confirmed",
    }
    return {key: payload[key] for key in allowed if key in payload}


def _canonical_from_raw(raw_status: str) -> str:
    if raw_status in {"queued", "leased", "running"}:
        return "pending"
    if raw_status == "accepted":
        return "unknown"
    return raw_status
