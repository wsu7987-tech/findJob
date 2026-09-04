from __future__ import annotations

from typing import Literal

from backend.app.db import Database
from backend.app.errors import AppError
from backend.app.utils import new_id, utc_now
from backend.app.services.fine_job.job_activity import append_job_activity_with_connection


JobApplicationStatus = Literal[
    "pending_greeting",
    "pending_application",
    "communicating",
    "rejected",
]


def set_job_application_status(
    db: Database,
    job_id: str,
    *,
    status: JobApplicationStatus | None,
    source: str,
    applied_at: str | None = None,
    note: str = "",
    source_action_id: str | None = None,
    evidence_level: str = "confirmed",
) -> dict[str, object]:
    """保存岗位投递阶段，保留岗位关联和人工状态来源。"""
    now = utc_now()
    event_at = applied_at or now
    with db.connect() as connection:
        job = connection.execute(
            "SELECT id, company_id FROM fj_boss_jobs WHERE id = ? OR source_job_id = ? LIMIT 1",
            (job_id, job_id),
        ).fetchone()
        if job is None:
            raise AppError(404, "NOT_FOUND", "岗位不存在。")
        resolved_job_id = str(job["id"])
        connection.execute(
            """
            INSERT INTO fj_job_applications (
              id, job_id, company_id, status, source, source_action_id,
              evidence_level, applied_at, note, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(job_id) DO UPDATE SET
              company_id = excluded.company_id, status = excluded.status,
              source = excluded.source, source_action_id = excluded.source_action_id,
              evidence_level = excluded.evidence_level, applied_at = excluded.applied_at,
              note = excluded.note, updated_at = excluded.updated_at
            """,
            (
                new_id(), resolved_job_id, job["company_id"], status, source,
                source_action_id, evidence_level, event_at, note.strip(), now, now,
            ),
        )
        if status == "rejected":
            application = connection.execute(
                "SELECT id FROM fj_job_applications WHERE job_id = ?",
                (resolved_job_id,),
            ).fetchone()
            append_job_activity_with_connection(
                connection,
                job_id=resolved_job_id,
                company_id=str(job["company_id"]) if job["company_id"] else None,
                event_type="rejected",
                occurred_at=event_at,
                source="manual" if source == "manual" else source,
                source_ref_type="job_application",
                source_ref_id=str(application["id"]),
                confidence=1.0,
                evidence_level="direct" if evidence_level == "confirmed" else "strong_inferred",
                payload={"legacy_status": "rejected"},
                dedupe_key=f"job_application:{application['id']}:rejected",
            )
    if status in {"pending_application", "communicating"}:
        from backend.app.services.fine_job.filter_exclusions import record_job_event

        record_job_event(db, "application", resolved_job_id, event_at)
    else:
        from backend.app.services.fine_job.filter_exclusions import mark_all_states_stale

        mark_all_states_stale(db)
    return get_job_application(db, resolved_job_id)


def set_job_application(
    db: Database,
    job_id: str,
    *,
    applied: bool,
    source: str,
    applied_at: str | None = None,
    note: str = "",
    source_action_id: str | None = None,
    evidence_level: str = "confirmed",
) -> dict[str, object]:
    # 兼容已有调用入口：成功打招呼进入待投递，撤销标记回到待打招呼。
    return set_job_application_status(
        db,
        job_id,
        status="pending_application" if applied else "pending_greeting",
        source=source,
        applied_at=applied_at,
        note=note,
        source_action_id=source_action_id,
        evidence_level=evidence_level,
    )


def get_job_application(db: Database, job_id: str) -> dict[str, object]:
    with db.connect() as connection:
        row = connection.execute(
            "SELECT * FROM fj_job_applications WHERE job_id = ?", (job_id,)
        ).fetchone()
    if row is None:
        raise AppError(404, "NOT_FOUND", "岗位投递记录不存在。")
    return {
        "job_id": row["job_id"],
        "company_id": row["company_id"],
        "status": row["status"],
        "source": row["source"],
        "applied_at": row["applied_at"],
        "note": row["note"],
    }


def sync_succeeded_applications(db: Database) -> int:
    """把成功打招呼动作同步为待投递状态，供下一次筛选立即使用。"""
    with db.connect() as connection:
        rows = connection.execute(
            """
            SELECT a.id, a.job_id, COALESCE(a.completed_at, a.updated_at) AS applied_at
            FROM fj_automation_actions a
            LEFT JOIN fj_job_applications p ON p.job_id = a.job_id
            WHERE a.status = 'succeeded'
              AND a.action_type = 'BOSS_DEFAULT_GREETING'
              AND (p.id IS NULL OR p.status IN ('pending_greeting', 'pending_application'))
            """
        ).fetchall()
    for row in rows:
        set_job_application_status(
            db,
            str(row["job_id"]),
            status="pending_application",
            source="boss_action",
            source_action_id=str(row["id"]),
            applied_at=str(row["applied_at"]),
        )
    return len(rows)
