from __future__ import annotations

import json
import sqlite3
from typing import Any

from backend.app.db import Database
from backend.app.utils import new_id, utc_now


ACTIVITY_EVENT_TYPES = {
    "job_discovered",
    "job_shortlisted",
    "candidate_initiated_contact",
    "recruiter_initiated_contact",
    "conversation_state_analyzed",
    "greeting_requested",
    "greeting_sent",
    "greeting_failed",
    "recruiter_replied",
    "candidate_replied",
    "resume_requested",
    "resume_submitted",
    "resume_accepted",
    "resume_viewed",
    "under_review",
    "interview_intent_detected",
    "interview_invited",
    "interview_scheduled",
    "rejected",
    "job_closed",
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
    "resume_viewed",
    "under_review",
    "interview_scheduling",
    "interviewing",
    "offer",
    "rejected",
    "closed",
}
EVIDENCE_LEVELS = {"direct", "strong_inferred", "weak_inferred"}
WAITING_ON_VALUES = {"candidate", "recruiter", "none", "unknown"}
CONTACT_ORIGINS = {
    "finejob_auto",
    "candidate_initiated",
    "recruiter_initiated",
    "external_candidate_initiated",
    "unknown",
}

_STAGE_RANK = {
    "discovered": 0,
    "shortlisted": 1,
    "greeted": 2,
    "communicating": 3,
    "resume_requested": 4,
    "resume_submitted": 5,
    "resume_viewed": 6,
    "under_review": 7,
    "interview_scheduling": 8,
    "interviewing": 9,
    "offer": 10,
}
_EVENT_STAGE = {
    "job_discovered": "discovered",
    "job_shortlisted": "shortlisted",
    "candidate_initiated_contact": "greeted",
    "recruiter_initiated_contact": "communicating",
    "greeting_sent": "greeted",
    "recruiter_replied": "communicating",
    "candidate_replied": "communicating",
    "resume_requested": "resume_requested",
    "resume_submitted": "resume_submitted",
    "resume_accepted": "resume_submitted",
    "resume_viewed": "resume_viewed",
    "under_review": "under_review",
    "interview_invited": "interview_scheduling",
    "interview_scheduled": "interviewing",
    "rejected": "rejected",
    "job_closed": "closed",
    "offer_received": "offer",
    "conversation_closed": "closed",
}

_EVENT_WAITING_ON = {
    "candidate_initiated_contact": "recruiter",
    "recruiter_initiated_contact": "candidate",
    "greeting_sent": "recruiter",
    "recruiter_replied": "candidate",
    "candidate_replied": "recruiter",
    "resume_requested": "candidate",
    "resume_submitted": "recruiter",
    "resume_accepted": "recruiter",
    "resume_viewed": "recruiter",
    "under_review": "recruiter",
    "interview_invited": "candidate",
    "interview_scheduled": "none",
    "rejected": "none",
    "job_closed": "none",
    "conversation_closed": "none",
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
        ORDER BY occurred_at ASC,
          CASE
            WHEN event_type IN ('candidate_initiated_contact', 'recruiter_initiated_contact') THEN 10
            WHEN event_type IN ('recruiter_replied', 'candidate_replied') THEN 20
            WHEN event_type = 'conversation_state_analyzed' THEN 40
            WHEN event_type IN ('rejected', 'job_closed', 'offer_received') THEN 50
            WHEN event_type = 'manual_stage_changed' THEN 60
            ELSE 30
          END ASC,
          created_at ASC, id ASC
        """,
        (job_id,),
    ).fetchall()
    stage: str | None = None
    stage_event: sqlite3.Row | None = None
    waiting_on = "unknown"
    waiting_since_at: str | None = None
    contact_origin = "unknown"
    rejection_reason_source = "unknown"
    rejection_reason_category = "unknown"
    rejection_reason_summary = ""
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

        # 正式终态只接受人工重新开启，后续自动事实不会覆盖结果。
        if stage in {"offer", "rejected", "closed"} and not allow_reopen:
            continue

        origin = str(payload.get("contact_origin") or "")
        if origin in CONTACT_ORIGINS and origin != "unknown":
            if contact_origin == "unknown" or origin == "finejob_auto":
                contact_origin = origin

        event_waiting = str(payload.get("waiting_on") or _EVENT_WAITING_ON.get(event_type) or "")
        if event_waiting in WAITING_ON_VALUES:
            waiting_on = event_waiting
            waiting_since_at = str(payload.get("waiting_since_at") or row["occurred_at"])

        if event_type in {"rejected", "job_closed"}:
            rejection_reason_source, rejection_reason_category, rejection_reason_summary = (
                _rejection_fields(payload)
            )

        if candidate is None:
            continue
        if candidate in {"rejected", "closed"}:
            stage = candidate
            stage_event = row
            continue
        if allow_reopen or stage is None or _advances(stage, candidate):
            stage = candidate
            stage_event = row

    if stage in {"offer", "rejected", "closed"}:
        waiting_on = "none"
        waiting_since_at = str(stage_event["occurred_at"]) if stage_event else waiting_since_at
    if stage not in {"rejected", "closed"}:
        rejection_reason_source = "unknown"
        rejection_reason_category = "unknown"
        rejection_reason_summary = ""

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
        and existing["waiting_on"] == waiting_on
        and existing["waiting_since_at"] == waiting_since_at
        and existing["contact_origin"] == contact_origin
        and existing["rejection_reason_source"] == rejection_reason_source
        and existing["rejection_reason_category"] == rejection_reason_category
        and existing["rejection_reason_summary"] == rejection_reason_summary
    ):
        now = str(existing["updated_at"])
    connection.execute(
        """
        INSERT INTO fj_job_pipeline_snapshots (
          job_id, company_id, stage, stage_source, stage_event_id,
          stage_updated_at, waiting_on, waiting_since_at, contact_origin,
          rejection_reason_source, rejection_reason_category,
          rejection_reason_summary, projection_version, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 2, ?, ?)
        ON CONFLICT(job_id) DO UPDATE SET
          company_id = excluded.company_id,
          stage = excluded.stage,
          stage_source = excluded.stage_source,
          stage_event_id = excluded.stage_event_id,
          stage_updated_at = excluded.stage_updated_at,
          waiting_on = excluded.waiting_on,
          waiting_since_at = excluded.waiting_since_at,
          contact_origin = excluded.contact_origin,
          rejection_reason_source = excluded.rejection_reason_source,
          rejection_reason_category = excluded.rejection_reason_category,
          rejection_reason_summary = excluded.rejection_reason_summary,
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
            waiting_on,
            waiting_since_at,
            contact_origin,
            rejection_reason_source,
            rejection_reason_category,
            rejection_reason_summary,
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
            "SELECT id, job_id, history_has_more FROM fj_chat_sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
        if session is None or not session["job_id"]:
            return 0
        job_id = str(session["job_id"])
        messages = connection.execute(
            """
            SELECT id, direction, message_type, sent_at, platform_message_id, client_mid, source
            FROM fj_chat_messages
            WHERE session_id = ?
              AND NOT (source = 'assistant' AND platform_message_id LIKE 'assistant:%')
            ORDER BY sent_at, rowid
            """,
            (session_id,),
        ).fetchall()
        created += append_contact_origin_for_session_with_connection(connection, session, messages)
        for message in messages:
            if message["message_type"] == "system":
                continue
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
        SELECT m.id, m.direction, m.message_type, m.sent_at, m.platform_message_id, m.source,
               s.id AS session_id, s.job_id
        FROM fj_chat_messages m
        JOIN fj_chat_sessions s ON s.id = m.session_id
        WHERE s.job_id IS NOT NULL
          AND NOT (m.source = 'assistant' AND m.platform_message_id LIKE 'assistant:%')
        """
    ).fetchall():
        if message["message_type"] == "system":
            continue
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

    for session in connection.execute(
        """
        SELECT id, job_id, history_has_more
        FROM fj_chat_sessions
        WHERE job_id IS NOT NULL
        """
    ).fetchall():
        messages = connection.execute(
            """
            SELECT id, direction, message_type, sent_at, platform_message_id, client_mid, source
            FROM fj_chat_messages
            WHERE session_id = ?
              AND NOT (source = 'assistant' AND platform_message_id LIKE 'assistant:%')
            ORDER BY sent_at ASC, rowid ASC
            """,
            (session["id"],),
        ).fetchall()
        append_contact_origin_for_session_with_connection(connection, session, messages)

    for application in connection.execute(
        """
        SELECT id, job_id, status, updated_at, source, evidence_level
        FROM fj_job_applications
        WHERE status IN ('rejected', 'offer', 'closed')
        """
    ).fetchall():
        status = str(application["status"])
        event_type = {
            "rejected": "rejected",
            "offer": "offer_received",
            "closed": "job_closed",
        }[status]
        append_job_activity_with_connection(
            connection,
            job_id=str(application["job_id"]),
            event_type=event_type,
            occurred_at=str(application["updated_at"]),
            source="migration",
            source_ref_type="job_application",
            source_ref_id=str(application["id"]),
            confidence=1.0 if application["evidence_level"] == "confirmed" else 0.9,
            evidence_level=(
                "direct" if application["evidence_level"] == "confirmed" else "strong_inferred"
            ),
            payload={
                "legacy_status": status,
                "waiting_on": "none",
                "derived_by": "migration",
            },
            dedupe_key=f"job_application:{application['id']}:{event_type}",
        )

    # 即使事件早已迁移，缺失或旧版本 snapshot 也可由完整事件流恢复。
    for job in connection.execute("SELECT id FROM fj_boss_jobs").fetchall():
        project_job_pipeline(connection, str(job["id"]))


def _advances(current: str, candidate: str) -> bool:
    if current in {"offer", "rejected", "closed"}:
        return False
    return _STAGE_RANK.get(candidate, -1) >= _STAGE_RANK.get(current, -1)


def append_contact_origin_for_session_with_connection(
    connection: sqlite3.Connection,
    session: sqlite3.Row,
    messages: list[sqlite3.Row],
) -> int:
    """只在完整历史和明确动作证据下建立沟通来源事实。"""
    if bool(session["history_has_more"]):
        return 0
    first = next((message for message in messages if message["message_type"] != "system"), None)
    if first is None:
        return 0

    job_id = str(session["job_id"] or "")
    if not job_id:
        return 0
    first_keys = first.keys()
    first_client_mid = (
        str(first["client_mid"] or "") if "client_mid" in first_keys else ""
    )
    auto_action = connection.execute(
        """
        SELECT id, COALESCE(completed_at, updated_at) AS occurred_at
        FROM fj_automation_actions
        WHERE job_id = ? AND action_type = 'BOSS_DEFAULT_GREETING'
          AND status = 'succeeded'
        ORDER BY occurred_at ASC LIMIT 1
        """,
        (job_id,),
    ).fetchone()
    if auto_action is not None:
        origin = "finejob_auto"
        event_type = "candidate_initiated_contact"
        source = "executor"
        source_ref_type = "automation_action"
        source_ref_id = str(auto_action["id"])
        occurred_at = str(auto_action["occurred_at"] or first["sent_at"])
        dedupe_key = f"job:{job_id}:contact_origin:finejob_auto"
    elif first["direction"] == "outbound" and first_client_mid:
        manual_action = connection.execute(
            """
            SELECT id, COALESCE(completed_at, updated_at) AS occurred_at
            FROM fj_chat_send_actions
            WHERE session_id = ? AND client_mid = ?
              AND status = 'accepted'
            ORDER BY occurred_at ASC LIMIT 1
            """,
            (session["id"], first_client_mid),
        ).fetchone()
        if manual_action is not None:
            origin = "candidate_initiated"
            event_type = "candidate_initiated_contact"
            source = "assistant"
            source_ref_type = "chat_send_action"
            source_ref_id = str(manual_action["id"])
            occurred_at = str(manual_action["occurred_at"] or first["sent_at"])
            dedupe_key = f"chat_session:{session['id']}:contact_origin:finejob_manual"
        else:
            origin = "external_candidate_initiated"
            event_type = "candidate_initiated_contact"
            source = "chat"
            source_ref_type = "chat_message"
            source_ref_id = str(first["id"])
            occurred_at = str(first["sent_at"])
            dedupe_key = f"chat_session:{session['id']}:contact_origin:external_candidate"
    elif first["direction"] == "inbound":
        origin = "recruiter_initiated"
        event_type = "recruiter_initiated_contact"
        source = "chat"
        source_ref_type = "chat_message"
        source_ref_id = str(first["id"])
        occurred_at = str(first["sent_at"])
        dedupe_key = f"chat_session:{session['id']}:contact_origin:recruiter"
    else:
        origin = "external_candidate_initiated"
        event_type = "candidate_initiated_contact"
        source = "chat"
        source_ref_type = "chat_message"
        source_ref_id = str(first["id"])
        occurred_at = str(first["sent_at"])
        dedupe_key = f"chat_session:{session['id']}:contact_origin:external_candidate"

    _activity, inserted = append_job_activity_with_connection(
        connection,
        job_id=job_id,
        chat_session_id=str(session["id"]),
        event_type=event_type,
        occurred_at=occurred_at,
        source=source,
        source_ref_type=source_ref_type,
        source_ref_id=source_ref_id,
        confidence=1.0,
        evidence_level="direct",
        payload={
            "contact_origin": origin,
            "derived_by": "rule",
            "evidence_message_id": str(first["id"]),
        },
        dedupe_key=dedupe_key,
    )
    return int(inserted)


def _rejection_fields(payload: dict[str, Any]) -> tuple[str, str, str]:
    analysis = payload.get("rejection_analysis")
    source = payload
    if isinstance(analysis, dict):
        source = {**payload, **analysis}
    reason_source = str(
        source.get("rejection_reason_source") or source.get("reason_source") or "unknown"
    )
    reason_source = {
        "explicit": "recruiter_explicit",
        "inferred": "ai_inferred",
    }.get(reason_source, reason_source)
    if reason_source not in {"recruiter_explicit", "ai_inferred", "unknown"}:
        reason_source = "unknown"

    category = str(
        source.get("rejection_reason_category") or source.get("reason_type") or "unknown"
    )
    category = {
        "experience_mismatch": "experience",
        "skill_mismatch": "skills",
        "education_mismatch": "education",
        "salary_mismatch": "salary",
        "location_mismatch": "location",
        "availability_mismatch": "availability",
        "background_mismatch": "industry_background",
    }.get(category, category)
    allowed_categories = {
        "experience", "education", "skills", "industry_background", "salary",
        "location", "availability", "position_filled", "headcount_closed", "fit",
        "other", "unknown",
    }
    if category not in allowed_categories:
        category = "unknown"
    summary = str(
        source.get("rejection_reason_summary") or source.get("reason_text") or ""
    ).strip()[:500]
    return reason_source, category, summary


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
