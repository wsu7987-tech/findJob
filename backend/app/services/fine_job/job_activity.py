from __future__ import annotations

import json
import sqlite3
from typing import Any

from backend.app.db import Database
from backend.app.utils import new_id, utc_now


ACTIVITY_EVENT_TYPES = {
    "job_discovered",
    "job_shortlisted",
    "greeting_requested",
    "greeting_sent",
    "greeting_failed",
    "recruiter_replied",
    "candidate_replied",
    "resume_requested",
    "resume_submitted",
    "interview_intent_detected",
    "interview_invited",
    "interview_scheduled",
    "rejected",
    "followup_recommended",
    "no_response_detected",
    "offer_received",
    "conversation_closed",
    "manual_stage_changed",
}
PIPELINE_STAGES = {
    "discovered",
    "shortlisted",
    "greeted",
    "communicating",
    "resume_requested",
    "resume_submitted",
    "interviewing",
    "offer",
    "rejected",
    "closed",
}
EVIDENCE_LEVELS = {"direct", "strong_inferred", "weak_inferred"}

_STAGE_RANK = {
    "discovered": 0,
    "shortlisted": 1,
    "greeted": 2,
    "communicating": 3,
    "resume_requested": 4,
    "resume_submitted": 5,
    "interviewing": 6,
    "offer": 7,
}
_EVENT_STAGE = {
    "job_discovered": "discovered",
    "job_shortlisted": "shortlisted",
    "greeting_sent": "greeted",
    "recruiter_replied": "communicating",
    "candidate_replied": "communicating",
    "resume_requested": "resume_requested",
    "resume_submitted": "resume_submitted",
    "interview_invited": "interviewing",
    "interview_scheduled": "interviewing",
    "rejected": "rejected",
    "offer_received": "offer",
    "conversation_closed": "closed",
}


def append_job_activity(
    db: Database,
    **kwargs: Any,
) -> tuple[dict[str, Any], bool]:
    with db.connect() as connection:
        return append_job_activity_with_connection(connection, **kwargs)


def append_job_activity_with_connection(
    connection: sqlite3.Connection,
    *,
    job_id: str,
    event_type: str,
    occurred_at: str,
    source: str,
    source_ref_type: str,
    source_ref_id: str,
    dedupe_key: str,
    company_id: str | None = None,
    chat_session_id: str | None = None,
    confidence: float = 1.0,
    evidence_level: str = "direct",
    payload: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], bool]:
    if event_type not in ACTIVITY_EVENT_TYPES:
        raise ValueError(f"Unsupported activity event type: {event_type}")
    if evidence_level not in EVIDENCE_LEVELS:
        raise ValueError(f"Unsupported activity evidence level: {evidence_level}")
    if not 0 <= confidence <= 1:
        raise ValueError("Activity confidence must be between 0 and 1")

    existing = connection.execute(
        "SELECT * FROM fj_job_activity_events WHERE dedupe_key = ?",
        (dedupe_key,),
    ).fetchone()
    if existing is not None:
        project_job_pipeline(connection, job_id)
        return _activity_row(existing), False

    if company_id is None:
        job = connection.execute(
            "SELECT company_id FROM fj_boss_jobs WHERE id = ?",
            (job_id,),
        ).fetchone()
        company_id = str(job["company_id"]) if job and job["company_id"] else None

    event_id = new_id()
    created_at = utc_now()
    connection.execute(
        """
        INSERT INTO fj_job_activity_events (
          id, job_id, company_id, chat_session_id, event_type, occurred_at,
          source, source_ref_type, source_ref_id, confidence, evidence_level,
          payload_json, dedupe_key, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_id,
            job_id,
            company_id,
            chat_session_id,
            event_type,
            occurred_at,
            source,
            source_ref_type,
            source_ref_id,
            confidence,
            evidence_level,
            json.dumps(payload or {}, ensure_ascii=False),
            dedupe_key,
            created_at,
        ),
    )
    project_job_pipeline(connection, job_id)
    row = connection.execute(
        "SELECT * FROM fj_job_activity_events WHERE id = ?",
        (event_id,),
    ).fetchone()
    return _activity_row(row), True


def project_job_pipeline(
    connection: sqlite3.Connection,
    job_id: str,
) -> dict[str, Any] | None:
    rows = connection.execute(
        """
        SELECT *
        FROM fj_job_activity_events
        WHERE job_id = ?
        ORDER BY occurred_at ASC, created_at ASC, id ASC
        """,
        (job_id,),
    ).fetchall()
    stage: str | None = None
    stage_event: sqlite3.Row | None = None
    for row in rows:
        payload = _load_json(row["payload_json"])
        event_type = str(row["event_type"])
        candidate = _EVENT_STAGE.get(event_type)
        allow_reopen = False
        if event_type == "manual_stage_changed":
            candidate = str(payload.get("stage") or "")
            allow_reopen = bool(payload.get("allow_reopen"))
            if candidate not in PIPELINE_STAGES:
                continue
        if candidate is None:
            continue

        # rejected/closed 只接受显式 reopen，普通自动事实不会越过终止阶段。
        if stage in {"rejected", "closed"} and not allow_reopen:
            continue
        if candidate in {"rejected", "closed"}:
            stage = candidate
            stage_event = row
            continue
        if allow_reopen or stage is None or _advances(stage, candidate):
            stage = candidate
            stage_event = row

    if stage is None or stage_event is None:
        connection.execute("DELETE FROM fj_job_pipeline_snapshots WHERE job_id = ?", (job_id,))
        return None

    now = utc_now()
    existing = connection.execute(
        "SELECT * FROM fj_job_pipeline_snapshots WHERE job_id = ?",
        (job_id,),
    ).fetchone()
    created_at = str(existing["created_at"]) if existing else now
    if (
        existing
        and existing["stage"] == stage
        and existing["stage_source"] == stage_event["source"]
        and existing["stage_event_id"] == stage_event["id"]
        and existing["stage_updated_at"] == stage_event["occurred_at"]
    ):
        now = str(existing["updated_at"])
    connection.execute(
        """
        INSERT INTO fj_job_pipeline_snapshots (
          job_id, company_id, stage, stage_source, stage_event_id,
          stage_updated_at, projection_version, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
        ON CONFLICT(job_id) DO UPDATE SET
          company_id = excluded.company_id,
          stage = excluded.stage,
          stage_source = excluded.stage_source,
          stage_event_id = excluded.stage_event_id,
          stage_updated_at = excluded.stage_updated_at,
          projection_version = excluded.projection_version,
          updated_at = excluded.updated_at
        """,
        (
            job_id,
            stage_event["company_id"],
            stage,
            stage_event["source"],
            stage_event["id"],
            stage_event["occurred_at"],
            created_at,
            now,
        ),
    )
    snapshot = connection.execute(
        "SELECT * FROM fj_job_pipeline_snapshots WHERE job_id = ?",
        (job_id,),
    ).fetchone()
    return dict(snapshot) if snapshot else None


def replay_job_pipeline(db: Database, job_id: str) -> dict[str, Any] | None:
    with db.connect() as connection:
        return project_job_pipeline(connection, job_id)


def reconcile_chat_session_activity(db: Database, session_id: str) -> int:
    """在会话完成岗位关联后补齐消息事实，并重放该岗位的 Pipeline。"""
    created = 0
    with db.connect() as connection:
        session = connection.execute(
            "SELECT job_id FROM fj_chat_sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
        if session is None or not session["job_id"]:
            return 0
        job_id = str(session["job_id"])
        messages = connection.execute(
            """
            SELECT id, direction, sent_at, platform_message_id, source
            FROM fj_chat_messages
            WHERE session_id = ?
              AND NOT (source = 'assistant' AND platform_message_id LIKE 'assistant:%')
            ORDER BY sent_at, rowid
            """,
            (session_id,),
        ).fetchall()
        for message in messages:
            event_type = (
                "recruiter_replied"
                if message["direction"] == "inbound"
                else "candidate_replied"
            )
            _activity, inserted = append_job_activity_with_connection(
                connection,
                job_id=job_id,
                chat_session_id=session_id,
                event_type=event_type,
                occurred_at=str(message["sent_at"]),
                source="chat",
                source_ref_type="chat_message",
                source_ref_id=str(message["id"]),
                evidence_level="direct",
                payload={
                    "direction": str(message["direction"]),
                    "platform_message_id": str(message["platform_message_id"]),
                },
                dedupe_key=f"chat_message:{message['id']}:{event_type}",
            )
            created += int(inserted)
        project_job_pipeline(connection, job_id)
    return created


def migrate_legacy_job_activity(connection: sqlite3.Connection) -> None:
    # 所有已保存岗位都具有直接的“发现岗位”原始事实。
    for job in connection.execute(
        "SELECT id, company_id, first_collected_at, payload_json FROM fj_boss_jobs"
    ).fetchall():
        job_id = str(job["id"])
        occurred_at = str(job["first_collected_at"] or utc_now())
        append_job_activity_with_connection(
            connection,
            job_id=job_id,
            company_id=str(job["company_id"]) if job["company_id"] else None,
            event_type="job_discovered",
            occurred_at=occurred_at,
            source="capture",
            source_ref_type="boss_job",
            source_ref_id=job_id,
            evidence_level="direct",
            dedupe_key=f"job:{job_id}:discovered",
        )
        payload = _load_json(job["payload_json"])
        final_filter_status = str(
            payload.get("final_filter_status") or payload.get("filter_status") or ""
        )
        if final_filter_status in {"pass", "pass_for_human"}:
            append_job_activity_with_connection(
                connection,
                job_id=job_id,
                company_id=str(job["company_id"]) if job["company_id"] else None,
                event_type="job_shortlisted",
                occurred_at=occurred_at,
                source="workflow",
                source_ref_type="boss_job_filter",
                source_ref_id=job_id,
                confidence=1.0,
                evidence_level="strong_inferred",
                payload={"legacy_filter_status": final_filter_status},
                dedupe_key=f"job:{job_id}:shortlisted",
            )

    for action in connection.execute(
        """
        SELECT id, job_id, execution_epoch, completed_at, updated_at
        FROM fj_automation_actions
        WHERE action_type = 'BOSS_DEFAULT_GREETING' AND status = 'succeeded'
        """
    ).fetchall():
        append_job_activity_with_connection(
            connection,
            job_id=str(action["job_id"]),
            event_type="greeting_sent",
            occurred_at=str(action["completed_at"] or action["updated_at"]),
            source="executor",
            source_ref_type="automation_action",
            source_ref_id=str(action["id"]),
            confidence=0.9,
            evidence_level="strong_inferred",
            dedupe_key=(
                f"automation_action:{action['id']}:epoch:{action['execution_epoch']}:greeting_sent"
            ),
        )

    for message in connection.execute(
        """
        SELECT m.id, m.direction, m.sent_at, m.platform_message_id, m.source,
               s.id AS session_id, s.job_id
        FROM fj_chat_messages m
        JOIN fj_chat_sessions s ON s.id = m.session_id
        WHERE s.job_id IS NOT NULL
          AND NOT (m.source = 'assistant' AND m.platform_message_id LIKE 'assistant:%')
        """
    ).fetchall():
        event_type = "recruiter_replied" if message["direction"] == "inbound" else "candidate_replied"
        append_job_activity_with_connection(
            connection,
            job_id=str(message["job_id"]),
            chat_session_id=str(message["session_id"]),
            event_type=event_type,
            occurred_at=str(message["sent_at"]),
            source="chat",
            source_ref_type="chat_message",
            source_ref_id=str(message["id"]),
            evidence_level="direct",
            payload={
                "direction": str(message["direction"]),
                "platform_message_id": str(message["platform_message_id"]),
            },
            dedupe_key=f"chat_message:{message['id']}:{event_type}",
        )

    for application in connection.execute(
        """
        SELECT id, job_id, updated_at, source, evidence_level
        FROM fj_job_applications
        WHERE status = 'rejected'
        """
    ).fetchall():
        append_job_activity_with_connection(
            connection,
            job_id=str(application["job_id"]),
            event_type="rejected",
            occurred_at=str(application["updated_at"]),
            source="migration",
            source_ref_type="job_application",
            source_ref_id=str(application["id"]),
            confidence=1.0 if application["evidence_level"] == "confirmed" else 0.9,
            evidence_level=(
                "direct" if application["evidence_level"] == "confirmed" else "strong_inferred"
            ),
            payload={"legacy_status": "rejected"},
            dedupe_key=f"job_application:{application['id']}:rejected",
        )

    # 即使事件早已迁移，缺失或旧版本 snapshot 也可由完整事件流恢复。
    for job in connection.execute("SELECT id FROM fj_boss_jobs").fetchall():
        project_job_pipeline(connection, str(job["id"]))


def _advances(current: str, candidate: str) -> bool:
    if current in {"rejected", "closed"}:
        return False
    return _STAGE_RANK.get(candidate, -1) >= _STAGE_RANK.get(current, -1)


def _activity_row(row: sqlite3.Row) -> dict[str, Any]:
    result = dict(row)
    result["payload"] = _load_json(result.pop("payload_json"))
    return result


def _load_json(value: object) -> dict[str, Any]:
    try:
        loaded = json.loads(str(value or "{}"))
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}
