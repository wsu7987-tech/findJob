from __future__ import annotations

import json

from backend.app.db import Database
from backend.app.utils import utc_now


def query_action_logs(
    db: Database,
    *,
    query: str = "",
    level: str | None = None,
    action_type: str | None = None,
    category: str | None = None,
    outcome: str | None = None,
    created_from: str | None = None,
    created_to: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> dict[str, object]:
    category_sql = """
      CASE
        WHEN l.action_type LIKE 'review_%' THEN 'review'
        WHEN l.action_type = 'executor_control' OR l.action_type LIKE 'boss_%'
          OR l.action_type LIKE 'action_%' THEN 'execution'
        WHEN l.action_type LIKE '%chat%' THEN 'chat'
        ELSE 'system'
      END
    """
    outcome_sql = """
      CASE
        WHEN l.level = 'error' THEN 'failed'
        WHEN l.action_type LIKE '%succeeded%' OR l.action_type LIKE '%accepted%'
          OR l.action_type LIKE '%opened%' OR l.action_type LIKE '%approved%'
          OR l.action_type LIKE '%restored%' THEN 'succeeded'
        WHEN l.level = 'warning' THEN 'warning'
        ELSE 'info'
      END
    """
    conditions: list[str] = []
    values: list[object] = []
    search = query.strip()
    if search:
        wildcard = f"%{search}%"
        conditions.append(
            "(l.message LIKE ? OR l.action_type LIKE ? OR l.detail_json LIKE ? "
            "OR j.title LIKE ? OR j.company_name LIKE ?)"
        )
        values.extend([wildcard, wildcard, wildcard, wildcard, wildcard])
    if level == "issue":
        conditions.append("l.level IN ('warning', 'error')")
    elif level:
        conditions.append("l.level = ?")
        values.append(level)
    if action_type:
        conditions.append("l.action_type = ?")
        values.append(action_type)
    if category:
        conditions.append(f"({category_sql}) = ?")
        values.append(category)
    if outcome:
        conditions.append(f"({outcome_sql}) = ?")
        values.append(outcome)
    if created_from:
        conditions.append("l.created_at >= ?")
        values.append(created_from)
    if created_to:
        conditions.append("l.created_at <= ?")
        values.append(created_to)
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    joins = """
      LEFT JOIN fj_automation_actions a
        ON a.id = json_extract(l.detail_json, '$.action_id')
      LEFT JOIN fj_boss_jobs j
        ON j.id = COALESCE(json_extract(l.detail_json, '$.job_id'), a.job_id)
    """
    offset = (page - 1) * page_size
    with db.connect() as connection:
        total = int(connection.execute(
            f"SELECT COUNT(DISTINCT l.id) FROM fj_action_logs l {joins} {where}",
            values,
        ).fetchone()[0])
        rows = connection.execute(
            f"""
            SELECT l.id, l.level, l.action_type, l.message, l.detail_json, l.created_at,
                   {category_sql} AS category, {outcome_sql} AS outcome,
                   j.id AS job_id, j.title AS job_title, j.company_name
            FROM fj_action_logs l
            {joins}
            {where}
            ORDER BY l.created_at DESC, l.id DESC
            LIMIT ? OFFSET ?
            """,
            [*values, page_size, offset],
        ).fetchall()
        action_types = [
            str(row[0])
            for row in connection.execute(
                "SELECT DISTINCT action_type FROM fj_action_logs ORDER BY action_type"
            ).fetchall()
        ]
    return {
        "logs": [_serialize_log(row) for row in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
        "action_types": action_types,
    }


def cleanup_action_logs(db: Database, *, before: str) -> int:
    with db.connect() as connection:
        cursor = connection.execute(
            "DELETE FROM fj_action_logs WHERE created_at < ?",
            (before,),
        )
    return max(0, int(cursor.rowcount))


def get_operations_dashboard(db: Database) -> dict[str, object]:
    from backend.app.services.fine_job.boss_executor import executor_status

    with db.connect() as connection:
        review_counts = _group_counts(connection, "fj_review_items", "status")
        action_counts = _group_counts(connection, "fj_automation_actions", "status")
        execution_counts = _group_counts(connection, "fj_automation_actions", "execution_state")
        capture_counts = _group_counts(connection, "fj_boss_capture_batches", "status")
        chat_pending_reviews = int(connection.execute(
            "SELECT COUNT(*) FROM fj_chat_reply_tasks WHERE status = 'awaiting_review'"
        ).fetchone()[0]) + int(connection.execute(
            """
            SELECT COUNT(*) FROM fj_chat_send_actions
            WHERE operation_kind = 'resume' AND status = 'queued' AND confirmation_status = 'pending'
            """
        ).fetchone()[0])
        chat_queued_actions = int(connection.execute(
            """
            SELECT COUNT(*) FROM fj_chat_send_actions
            WHERE confirmation_status = 'confirmed' AND status = 'queued'
              AND operation_kind IN ('text', 'resume')
            """
        ).fetchone()[0])
        chat_active_actions = int(connection.execute(
            """
            SELECT COUNT(*) FROM fj_chat_send_actions
            WHERE confirmation_status = 'confirmed' AND status IN ('leased', 'dispatching')
              AND operation_kind IN ('text', 'resume')
            """
        ).fetchone()[0])
        metrics = {
            "jobs": int(connection.execute("SELECT COUNT(*) FROM fj_boss_jobs").fetchone()[0]),
            "detailed_jobs": int(connection.execute(
                "SELECT COUNT(*) FROM fj_boss_jobs WHERE detail_status = 'completed'"
            ).fetchone()[0]),
            "evaluated_jobs": int(connection.execute(
                "SELECT COUNT(DISTINCT job_id) FROM fj_job_evaluations"
            ).fetchone()[0]),
            "pending_reviews": review_counts.get("pending", 0) + chat_pending_reviews,
            "queued_actions": action_counts.get("queued", 0) + chat_queued_actions,
            "active_actions": sum(execution_counts.get(state, 0) for state in ("running",)) + chat_active_actions,
            "successful_actions": action_counts.get("succeeded", 0),
            "issue_actions": sum(action_counts.get(state, 0) for state in ("failed", "blocked", "unknown")),
        }
    runtime = executor_status(db)
    queue = runtime.get("queue") if isinstance(runtime.get("queue"), dict) else {"actions": [], "total": 0}
    warnings = query_action_logs(db, level="warning", page_size=8)["logs"]
    errors = query_action_logs(db, level="error", page_size=8)["logs"]
    recent_issues = sorted(
        [*warnings, *errors], key=lambda item: str(item["created_at"]), reverse=True
    )[:8]
    return {
        "generated_at": utc_now(),
        "metrics": metrics,
        "review_counts": review_counts,
        "action_counts": action_counts,
        "execution_counts": execution_counts,
        "capture_counts": capture_counts,
        "executor": runtime.get("executor"),
        "current_task": runtime.get("current_task"),
        "queue": queue,
        "recent_issues": recent_issues,
    }


def _group_counts(connection, table: str, column: str) -> dict[str, int]:
    rows = connection.execute(
        f"SELECT {column}, COUNT(*) AS total FROM {table} GROUP BY {column}"
    ).fetchall()
    return {str(row[0]): int(row[1]) for row in rows}


def _serialize_log(row) -> dict[str, object]:
    try:
        detail = json.loads(row["detail_json"] or "{}")
    except json.JSONDecodeError:
        detail = {}
    return {
        "id": row["id"],
        "level": row["level"],
        "action_type": row["action_type"],
        "message": row["message"],
        "detail": detail if isinstance(detail, dict) else {},
        "created_at": row["created_at"],
        "category": row["category"],
        "outcome": row["outcome"],
        "job_id": row["job_id"],
        "job_title": row["job_title"],
        "company_name": row["company_name"],
    }
