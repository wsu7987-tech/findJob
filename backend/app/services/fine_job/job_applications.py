from __future__ import annotations

from backend.app.db import Database
from backend.app.errors import AppError
from backend.app.utils import new_id, utc_now


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
                new_id(), resolved_job_id, job["company_id"],
                "applied" if applied else "cleared", source, source_action_id,
                evidence_level, event_at, note.strip(), now, now,
            ),
        )
    if applied:
        from backend.app.services.fine_job.filter_exclusions import record_job_event

        record_job_event(db, "application", resolved_job_id, event_at)
    else:
        from backend.app.services.fine_job.filter_exclusions import mark_all_states_stale

        mark_all_states_stale(db)
    return get_job_application(db, resolved_job_id)


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
    """把新完成的打招呼动作同步为投递事实，供下一次筛选立即使用。"""
    with db.connect() as connection:
        rows = connection.execute(
            """
            SELECT a.id, a.job_id, COALESCE(a.completed_at, a.updated_at) AS applied_at
            FROM fj_automation_actions a
            LEFT JOIN fj_job_applications p ON p.job_id = a.job_id AND p.status = 'applied'
            WHERE a.status = 'succeeded' AND p.id IS NULL
            """
        ).fetchall()
    for row in rows:
        set_job_application(
            db,
            str(row["job_id"]),
            applied=True,
            source="boss_action",
            source_action_id=str(row["id"]),
            applied_at=str(row["applied_at"]),
        )
    return len(rows)
