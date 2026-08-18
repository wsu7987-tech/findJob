from __future__ import annotations

import hashlib
import json
from typing import Literal
from urllib.parse import urlsplit, urlunsplit

from backend.app.db import Database
from backend.app.errors import AppError
from backend.app.utils import new_id, utc_now


HistorySortField = Literal[
    "last_collected_at",
    "first_collected_at",
    "collect_count",
    "title",
    "company_name",
]
HistorySortOrder = Literal["asc", "desc"]

SORT_COLUMNS: dict[HistorySortField, str] = {
    "last_collected_at": "last_collected_at",
    "first_collected_at": "first_collected_at",
    "collect_count": "collect_count",
    "title": "title",
    "company_name": "company_name",
}


def create_capture_batch(
    db: Database,
    *,
    capture_id: str,
    keyword: str,
    city: str,
    pages: int,
    auto_details: bool,
    created_at: str,
) -> None:
    with db.connect() as connection:
        connection.execute(
            """
            INSERT INTO fj_boss_capture_batches (
              id, keyword, city, pages, auto_details, status,
              created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, 'queued', ?, ?)
            """,
            (capture_id, keyword, city, pages, int(auto_details), created_at, created_at),
        )


def update_capture_batch(
    db: Database,
    *,
    capture_id: str,
    status: str,
    source_url: str | None = None,
    jobs_collected: int | None = None,
    details_completed: int | None = None,
    details_failed: int | None = None,
    finished_at: str | None = None,
) -> None:
    assignments = ["status = ?", "updated_at = ?"]
    values: list[object] = [status, utc_now()]
    for column, value in (
        ("source_url", source_url),
        ("jobs_collected", jobs_collected),
        ("details_completed", details_completed),
        ("details_failed", details_failed),
        ("finished_at", finished_at),
    ):
        if value is not None:
            assignments.append(f"{column} = ?")
            values.append(value)
    values.append(capture_id)
    with db.connect() as connection:
        connection.execute(
            f"UPDATE fj_boss_capture_batches SET {', '.join(assignments)} WHERE id = ?",
            values,
        )


def record_capture_jobs(
    db: Database,
    *,
    capture_id: str,
    jobs: list[dict[str, object]],
    collected_at: str | None = None,
) -> list[dict[str, object]]:
    now = collected_at or utc_now()
    enriched: list[dict[str, object]] = []
    with db.connect() as connection:
        for raw_job in jobs:
            job = dict(raw_job)
            dedupe_key = build_job_dedupe_key(job)
            existing = connection.execute(
                "SELECT * FROM fj_boss_jobs WHERE dedupe_key = ?",
                (dedupe_key,),
            ).fetchone()
            association = None
            if existing is not None:
                association = connection.execute(
                    """
                    SELECT was_previously_collected
                    FROM fj_boss_capture_batch_jobs
                    WHERE capture_id = ? AND job_id = ?
                    """,
                    (capture_id, existing["id"]),
                ).fetchone()

            if existing is None:
                history_job_id = new_id()
                was_previously_collected = False
                first_collected_at = now
                collect_count = 1
                detail_json = _json_or_none(job.get("detail"))
                detail_status = str(job.get("detail_status") or "not_collected")
                connection.execute(
                    """
                    INSERT INTO fj_boss_jobs (
                      id, dedupe_key, source_job_id, encrypt_job_id, title,
                      company_name, company_scale, salary, location, experience,
                      degree, boss_active_status, job_link, tags, skills,
                      job_labels, payload_json, detail_json, detail_status,
                      detail_error, detail_collected_at, first_collected_at,
                      last_collected_at, collect_count, latest_batch_id
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        history_job_id,
                        dedupe_key,
                        _text(job.get("job_id")),
                        _text(job.get("encrypt_job_id")),
                        _text(job.get("title")),
                        _company(job),
                        _text(job.get("company_scale")),
                        _text(job.get("salary")),
                        _text(job.get("location")),
                        _text(job.get("experience")),
                        _text(job.get("degree")),
                        _text(job.get("boss_active_status")),
                        _text(job.get("job_link")),
                        _text(job.get("tags")),
                        _text(job.get("skills")),
                        _text(job.get("job_labels")),
                        _json(job),
                        detail_json,
                        detail_status,
                        _optional_text(job.get("detail_error")),
                        _optional_text(job.get("detail_collected_at")),
                        first_collected_at,
                        now,
                        collect_count,
                        capture_id,
                    ),
                )
            else:
                history_job_id = str(existing["id"])
                if association is None:
                    was_previously_collected = True
                    collect_count = int(existing["collect_count"]) + 1
                else:
                    was_previously_collected = bool(association["was_previously_collected"])
                    collect_count = int(existing["collect_count"])
                first_collected_at = str(existing["first_collected_at"])
                detail_json = _json_or_none(job.get("detail")) or existing["detail_json"]
                incoming_status = str(job.get("detail_status") or "not_collected")
                detail_status = (
                    incoming_status
                    if incoming_status != "not_collected" or not existing["detail_json"]
                    else str(existing["detail_status"])
                )
                connection.execute(
                    """
                    UPDATE fj_boss_jobs
                    SET source_job_id = ?, encrypt_job_id = ?, title = ?,
                        company_name = ?, company_scale = ?, salary = ?, location = ?,
                        experience = ?, degree = ?, boss_active_status = ?, job_link = ?,
                        tags = ?, skills = ?, job_labels = ?, payload_json = ?,
                        detail_json = ?, detail_status = ?, detail_error = ?,
                        detail_collected_at = COALESCE(?, detail_collected_at),
                        last_collected_at = ?, collect_count = ?, latest_batch_id = ?
                    WHERE id = ?
                    """,
                    (
                        _prefer(job.get("job_id"), existing["source_job_id"]),
                        _prefer(job.get("encrypt_job_id"), existing["encrypt_job_id"]),
                        _prefer(job.get("title"), existing["title"]),
                        _prefer(_company(job), existing["company_name"]),
                        _prefer(job.get("company_scale"), existing["company_scale"]),
                        _prefer(job.get("salary"), existing["salary"]),
                        _prefer(job.get("location"), existing["location"]),
                        _prefer(job.get("experience"), existing["experience"]),
                        _prefer(job.get("degree"), existing["degree"]),
                        _prefer(job.get("boss_active_status"), existing["boss_active_status"]),
                        _prefer(job.get("job_link"), existing["job_link"]),
                        _prefer(job.get("tags"), existing["tags"]),
                        _prefer(job.get("skills"), existing["skills"]),
                        _prefer(job.get("job_labels"), existing["job_labels"]),
                        _json(job),
                        detail_json,
                        detail_status,
                        _optional_text(job.get("detail_error")),
                        _optional_text(job.get("detail_collected_at")),
                        now,
                        collect_count,
                        capture_id,
                        history_job_id,
                    ),
                )

            if association is None:
                connection.execute(
                    """
                    INSERT INTO fj_boss_capture_batch_jobs (
                      capture_id, job_id, collected_at,
                      was_previously_collected, snapshot_json
                    )
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        capture_id,
                        history_job_id,
                        now,
                        int(was_previously_collected),
                        _json(job),
                    ),
                )
            enriched.append(
                {
                    **job,
                    "is_previously_collected": was_previously_collected,
                    "first_collected_at": first_collected_at,
                    "last_collected_at": now,
                    "collect_count": collect_count,
                }
            )
    return enriched


def update_capture_job_detail(
    db: Database,
    *,
    job: dict[str, object],
    detail: object,
    status: str,
    error: str | None = None,
    collected_at: str | None = None,
) -> None:
    history_record_id = _text(job.get("history_record_id"))
    identity_column = "id" if history_record_id else "dedupe_key"
    identity_value = history_record_id or build_job_dedupe_key(job)
    now = collected_at or utc_now()
    with db.connect() as connection:
        existing = connection.execute(
            f"SELECT detail_json FROM fj_boss_jobs WHERE {identity_column} = ?",
            (identity_value,),
        ).fetchone()
        if existing is None:
            return
        if status == "completed":
            connection.execute(
                f"""
                UPDATE fj_boss_jobs
                SET detail_json = ?, detail_status = 'completed', detail_error = NULL,
                    detail_collected_at = ?
                WHERE {identity_column} = ?
                """,
                (_json_or_none(detail), now, identity_value),
            )
        elif existing["detail_json"] is None:
            connection.execute(
                f"""
                UPDATE fj_boss_jobs
                SET detail_status = ?, detail_error = ?
                WHERE {identity_column} = ?
                """,
                (status, error, identity_value),
            )


def get_capture_history_job(db: Database, history_job_id: str) -> dict[str, object]:
    with db.connect() as connection:
        row = connection.execute(
            """
            SELECT id, source_job_id, encrypt_job_id, title, company_name,
                   company_scale, salary, location, experience, degree,
                   boss_active_status, job_link, tags, skills, job_labels,
                   detail_json, detail_status, detail_error, detail_collected_at,
                   first_collected_at, last_collected_at, collect_count,
                   latest_batch_id
            FROM fj_boss_jobs
            WHERE id = ?
            """,
            (history_job_id,),
        ).fetchone()
    if row is None:
        raise AppError(
            status_code=404,
            error_category="NOT_FOUND",
            error_message="历史岗位不存在。",
        )
    return _serialize_history_row(row)


def list_capture_history(
    db: Database,
    *,
    query: str = "",
    city: str = "",
    company_scale: str = "",
    detail_status: str = "",
    repeat_status: str = "all",
    collected_from: str = "",
    collected_to: str = "",
    sort_by: HistorySortField = "last_collected_at",
    sort_order: HistorySortOrder = "desc",
    page: int = 1,
    page_size: int = 20,
) -> dict[str, object]:
    conditions: list[str] = []
    values: list[object] = []
    if query.strip():
        like = f"%{query.strip()}%"
        conditions.append("(title LIKE ? OR company_name LIKE ? OR skills LIKE ?)")
        values.extend([like, like, like])
    if city.strip():
        conditions.append("location LIKE ?")
        values.append(f"%{city.strip()}%")
    if company_scale.strip():
        conditions.append("company_scale = ?")
        values.append(company_scale.strip())
    if detail_status.strip():
        conditions.append("detail_status = ?")
        values.append(detail_status.strip())
    if repeat_status == "repeated":
        conditions.append("collect_count > 1")
    elif repeat_status == "first_seen":
        conditions.append("collect_count = 1")
    if collected_from.strip():
        conditions.append("last_collected_at >= ?")
        values.append(collected_from.strip())
    if collected_to.strip():
        conditions.append("last_collected_at <= ?")
        values.append(collected_to.strip())

    where_sql = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    order_column = SORT_COLUMNS[sort_by]
    order_direction = "ASC" if sort_order == "asc" else "DESC"
    offset = (page - 1) * page_size
    with db.connect() as connection:
        total = int(
            connection.execute(
                f"SELECT COUNT(*) FROM fj_boss_jobs {where_sql}", values
            ).fetchone()[0]
        )
        rows = connection.execute(
            f"""
            SELECT id, source_job_id, encrypt_job_id, title, company_name, company_scale, salary,
                   location, experience, degree, boss_active_status, job_link,
                   tags, skills, job_labels, detail_json, detail_status,
                   detail_error, detail_collected_at, first_collected_at,
                   last_collected_at, collect_count, latest_batch_id
            FROM fj_boss_jobs
            {where_sql}
            ORDER BY {order_column} {order_direction}, id {order_direction}
            LIMIT ? OFFSET ?
            """,
            [*values, page_size, offset],
        ).fetchall()
    return {
        "items": [_serialize_history_row(row) for row in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


def build_job_dedupe_key(job: dict[str, object]) -> str:
    encrypt_job_id = _text(job.get("encrypt_job_id"))
    if encrypt_job_id:
        return f"boss:encrypt:{encrypt_job_id}"
    source_job_id = _text(job.get("job_id"))
    if source_job_id:
        return f"boss:job:{source_job_id}"
    job_link = _normalized_url(_text(job.get("job_link")))
    if job_link:
        return f"boss:url:{hashlib.sha256(job_link.encode('utf-8')).hexdigest()}"
    identity = "|".join(
        _text(value).lower()
        for value in (
            job.get("title"),
            _company(job),
            job.get("location"),
            job.get("salary"),
        )
    )
    return f"boss:fallback:{hashlib.sha256(identity.encode('utf-8')).hexdigest()}"


def _serialize_history_row(row) -> dict[str, object]:
    detail = None
    if row["detail_json"]:
        try:
            detail = json.loads(row["detail_json"])
        except json.JSONDecodeError:
            detail = None
    return {
        "id": row["id"],
        "job_id": row["source_job_id"],
        "encrypt_job_id": row["encrypt_job_id"],
        "title": row["title"],
        "boss_name": row["company_name"],
        "company_scale": row["company_scale"],
        "salary": row["salary"],
        "location": row["location"],
        "experience": row["experience"],
        "degree": row["degree"],
        "boss_active_status": row["boss_active_status"],
        "job_link": row["job_link"],
        "tags": row["tags"],
        "skills": row["skills"],
        "job_labels": row["job_labels"],
        "detail": detail,
        "detail_status": row["detail_status"],
        "detail_error": row["detail_error"],
        "detail_collected_at": row["detail_collected_at"],
        "first_collected_at": row["first_collected_at"],
        "last_collected_at": row["last_collected_at"],
        "collect_count": row["collect_count"],
        "latest_capture_id": row["latest_batch_id"],
        "is_previously_collected": int(row["collect_count"]) > 1,
    }


def _company(job: dict[str, object]) -> str:
    return _text(job.get("boss_name") or job.get("company"))


def _text(value: object) -> str:
    return str(value or "").strip()


def _optional_text(value: object) -> str | None:
    text = _text(value)
    return text or None


def _prefer(value: object, fallback: object) -> str:
    return _text(value) or _text(fallback)


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _json_or_none(value: object) -> str | None:
    return _json(value) if value is not None else None


def _normalized_url(value: str) -> str:
    if not value:
        return ""
    parsed = urlsplit(value)
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path.rstrip("/"), "", ""))
