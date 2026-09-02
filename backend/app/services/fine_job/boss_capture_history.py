from __future__ import annotations

import hashlib
import json
from typing import Literal
from urllib.parse import urlsplit, urlunsplit

from backend.app.db import Database
from backend.app.errors import AppError
from backend.app.utils import new_id, utc_now
from backend.app.services.fine_job.companies import resolve_company


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
    search_keyword: str = "",
    jobs: list[dict[str, object]],
    collected_at: str | None = None,
) -> list[dict[str, object]]:
    now = collected_at or utc_now()
    enriched: list[dict[str, object]] = []
    with db.connect() as connection:
        for raw_job in jobs:
            job = dict(raw_job)
            company = resolve_company(connection, _company(job), source="capture")
            company_id = str(company["id"]) if company is not None else None
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
                      company_name, company_id, company_scale, company_stage, company_industry,
                      welfare, salary, location, experience, degree,
                      boss_active_status, job_link, tags, skills,
                      job_labels, search_keyword, payload_json, detail_json, detail_status,
                      detail_error, delivery_evaluation_json, detail_collected_at, first_collected_at,
                      last_collected_at, collect_count, latest_batch_id
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        history_job_id,
                        dedupe_key,
                        _text(job.get("job_id")),
                        _text(job.get("encrypt_job_id")),
                        _text(job.get("title")),
                        _company(job),
                        company_id,
                        _text(job.get("company_scale")),
                        _text(job.get("company_stage")),
                        _text(job.get("company_industry")),
                        _text(job.get("welfare")),
                        _text(job.get("salary")),
                        _text(job.get("location")),
                        _text(job.get("experience")),
                        _text(job.get("degree")),
                        _text(job.get("boss_active_status")),
                        _text(job.get("job_link")),
                        _text(job.get("tags")),
                        _text(job.get("skills")),
                        _text(job.get("job_labels")),
                        _text(search_keyword),
                        _json(job),
                        detail_json,
                        detail_status,
                        _optional_text(job.get("detail_error")),
                        _json_or_none(job.get("delivery_evaluation")),
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
                delivery_evaluation_json = (
                    _json_or_none(job.get("delivery_evaluation"))
                    or existing["delivery_evaluation_json"]
                )
                incoming_status = str(job.get("detail_status") or "not_collected")
                detail_status = (
                    incoming_status
                    if incoming_status != "not_collected" or not existing["detail_json"]
                    else str(existing["detail_status"])
                )
                # 重复采集时把历史详情状态带回当前任务，避免已完成详情被误判为未采集。
                if incoming_status == "not_collected" and existing["detail_json"]:
                    job["detail"] = _load_json(detail_json)
                    job["detail_status"] = detail_status
                    job["detail_error"] = existing["detail_error"]
                    job["detail_version"] = existing["detail_version"]
                    job["detail_collected_at"] = existing["detail_collected_at"]
                if delivery_evaluation_json:
                    job["delivery_evaluation"] = _load_json(delivery_evaluation_json)
                existing_payload = _load_json(existing["payload_json"])
                if isinstance(existing_payload, dict):
                    # 重复采集后继续保留上一次正式筛选结果，新的筛选完成时再覆盖。
                    for key in (
                        "filter_status",
                        "strategy_filter_status",
                        "final_filter_status",
                        "filter_reasons",
                        "filter_missing_fields",
                        "filter_strategy_id",
                    ):
                        if key not in job and key in existing_payload:
                            job[key] = existing_payload[key]
                connection.execute(
                    """
                    UPDATE fj_boss_jobs
                    SET source_job_id = ?, encrypt_job_id = ?, title = ?,
                        company_name = ?, company_id = COALESCE(?, company_id),
                        company_scale = ?, company_stage = ?,
                        company_industry = ?, welfare = ?, salary = ?, location = ?,
                        experience = ?, degree = ?, boss_active_status = ?, job_link = ?,
                        tags = ?, skills = ?, job_labels = ?, search_keyword = ?, payload_json = ?,
                        detail_json = ?, detail_status = ?, detail_error = ?,
                        delivery_evaluation_json = ?,
                        detail_collected_at = COALESCE(?, detail_collected_at),
                        last_collected_at = ?, collect_count = ?, latest_batch_id = ?
                    WHERE id = ?
                    """,
                    (
                        _prefer(job.get("job_id"), existing["source_job_id"]),
                        _prefer(job.get("encrypt_job_id"), existing["encrypt_job_id"]),
                        _prefer(job.get("title"), existing["title"]),
                        _prefer(_company(job), existing["company_name"]),
                        company_id,
                        _prefer(job.get("company_scale"), existing["company_scale"]),
                        _prefer(job.get("company_stage"), existing["company_stage"]),
                        _prefer(job.get("company_industry"), existing["company_industry"]),
                        _prefer(job.get("welfare"), existing["welfare"]),
                        _prefer(job.get("salary"), existing["salary"]),
                        _prefer(job.get("location"), existing["location"]),
                        _prefer(job.get("experience"), existing["experience"]),
                        _prefer(job.get("degree"), existing["degree"]),
                        _prefer(job.get("boss_active_status"), existing["boss_active_status"]),
                        _prefer(job.get("job_link"), existing["job_link"]),
                        _prefer(job.get("tags"), existing["tags"]),
                        _prefer(job.get("skills"), existing["skills"]),
                        _prefer(job.get("job_labels"), existing["job_labels"]),
                        _text(search_keyword),
                        _json(job),
                        detail_json,
                        detail_status,
                        _optional_text(job.get("detail_error")),
                        delivery_evaluation_json,
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
                    "delivery_evaluation": _load_json(
                        delivery_evaluation_json
                        if existing is not None
                        else _json_or_none(job.get("delivery_evaluation"))
                    ),
                    "history_record_id": history_job_id,
                    "company_id": company_id or (existing["company_id"] if existing is not None else None),
                    "company_type": company["company_type"] if company is not None else "unknown",
                    "is_outsourcing_company": bool(company is not None and company["company_type"] == "outsourcing"),
                    "is_blacklisted": bool(company is not None and company["is_blacklisted"]),
                    "search_keyword": _text(search_keyword),
                    "is_previously_collected": was_previously_collected,
                    "first_collected_at": first_collected_at,
                    "last_collected_at": now,
                    "collect_count": collect_count,
                }
            )
    return enriched


def record_chat_job(
    db: Database,
    *,
    session: dict[str, object],
    application_status: str | None,
) -> dict[str, object]:
    """把聊天会话中的 BOSS 岗位补录为历史岗位，并保留详情采集入口。"""
    encrypt_job_id = _text(session.get("encrypt_job_id"))
    if not encrypt_job_id:
        raise AppError(409, "JOB_ID_MISSING", "当前聊天缺少 BOSS 岗位加密标识，无法获取岗位详情。")

    with db.connect() as connection:
        existing = connection.execute(
            "SELECT id FROM fj_boss_jobs WHERE encrypt_job_id = ? LIMIT 1",
            (encrypt_job_id,),
        ).fetchone()
    if existing is not None:
        return {
            "history_record_id": str(existing["id"]),
            "created": False,
        }

    capture_id = new_id()
    now = utc_now()
    create_capture_batch(
        db,
        capture_id=capture_id,
        keyword="聊天岗位补录",
        city="",
        pages=1,
        auto_details=False,
        created_at=now,
    )
    job_link = f"https://www.zhipin.com/job_detail/{encrypt_job_id}.html"
    persisted = record_capture_jobs(
        db,
        capture_id=capture_id,
        search_keyword="聊天岗位补录",
        jobs=[
            {
                # 聊天会话里的 job_id 是本地关联值，新补录记录的来源岗位编号暂时留空。
                "job_id": "",
                "encrypt_job_id": encrypt_job_id,
                # 保留聊天列表已经返回的岗位名称，详情采集后再补充完整岗位资料。
                "title": _text(session.get("job_title")),
                "boss_name": _text(session.get("company_name")),
                "job_link": job_link,
                "filter_status": "pass_for_human",
                "strategy_filter_status": "pass_for_human",
                "final_filter_status": "pass_for_human",
                "application_status": application_status,
                "detail_status": "queued",
            }
        ],
        collected_at=now,
    )
    history_record_id = str(persisted[0]["history_record_id"])
    with db.connect() as connection:
        connection.execute(
            "UPDATE fj_chat_sessions SET job_id = ?, updated_at = ? WHERE id = ?",
            (history_record_id, now, str(session["id"])),
        )
        connection.execute(
            "UPDATE fj_boss_capture_batches SET status = 'running', jobs_collected = 1, updated_at = ? WHERE id = ?",
            (now, capture_id),
        )
    return {
        "history_record_id": history_record_id,
        "created": True,
    }


def update_capture_job_filter_result(
    db: Database,
    *,
    job: dict[str, object],
    result: dict[str, object],
) -> None:
    """把当前筛选结论同步到历史岗位的最新数据。"""
    history_record_id = _text(job.get("history_record_id") or job.get("id"))
    identity_column = "id" if history_record_id else "dedupe_key"
    identity_value = history_record_id or build_job_dedupe_key(job)
    with db.connect() as connection:
        row = connection.execute(
            f"SELECT payload_json FROM fj_boss_jobs WHERE {identity_column} = ?",
            (identity_value,),
        ).fetchone()
        if row is None:
            return
        payload = _load_json(row["payload_json"])
        if not isinstance(payload, dict):
            payload = {}
        payload.update(
            {
                "filter_status": result.get("status"),
                "strategy_filter_status": result.get(
                    "strategy_filter_status", result.get("status")
                ),
                "final_filter_status": result.get(
                    "final_filter_status", result.get("status")
                ),
                "filter_reasons": list(result.get("reasons") or []),
                "filter_missing_fields": list(result.get("missing_fields") or []),
                "filter_strategy_id": result.get("strategy_id"),
                "cooldown_excluded": bool(result.get("cooldown_excluded")),
                "cooldown_reasons": list(result.get("cooldown_reasons") or []),
            }
        )
        connection.execute(
            f"UPDATE fj_boss_jobs SET payload_json = ? WHERE {identity_column} = ?",
            (_json(payload), identity_value),
        )


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
            f"""
            SELECT id, encrypt_job_id, detail_json, payload_json
            FROM fj_boss_jobs
            WHERE {identity_column} = ?
            """,
            (identity_value,),
        ).fetchone()
        if existing is None:
            return
        if status == "completed":
            detail_payload = detail if isinstance(detail, dict) else {}
            payload = _load_json(existing["payload_json"])
            if not isinstance(payload, dict):
                payload = {}
            field_values = {
                "title": _text(detail_payload.get("title")),
                "salary": _text(detail_payload.get("salary")),
                "company_name": _text(
                    detail_payload.get("company_name") or detail_payload.get("company")
                ),
                "company_scale": _text(detail_payload.get("company_scale")),
                "company_industry": _text(detail_payload.get("company_industry")),
                "company_stage": _text(detail_payload.get("company_stage")),
                "welfare": _text(detail_payload.get("welfare")),
                "location": _text(detail_payload.get("location")),
                "experience": _text(detail_payload.get("experience")),
                "degree": _text(detail_payload.get("degree")),
            }
            for field_name, value in field_values.items():
                if value:
                    payload[field_name] = value
            assignments = [
                "detail_json = ?",
                "payload_json = ?",
                "detail_status = 'completed'",
                "detail_error = NULL",
                "detail_collected_at = ?",
                "detail_version = detail_version + 1",
                "boss_active_status = CASE WHEN ? <> '' THEN ? ELSE boss_active_status END",
            ]
            values: list[object] = [
                _json_or_none(detail),
                _json(payload),
                now,
                _text(detail_payload.get("boss_active_status")),
                _text(detail_payload.get("boss_active_status")),
            ]
            for field_name, value in field_values.items():
                if value:
                    assignments.append(f"{field_name} = ?")
                    values.append(value)
            values.append(identity_value)
            connection.execute(
                f"UPDATE fj_boss_jobs SET {', '.join(assignments)} WHERE {identity_column} = ?",
                values,
            )
            if field_values["title"]:
                # 详情采集得到标题后回填关联会话，列表立即展示真实岗位名称。
                connection.execute(
                    """
                    UPDATE fj_chat_sessions
                    SET job_title = ?, updated_at = ?
                    WHERE job_title = ''
                      AND (job_id = ? OR encrypt_job_id = ?)
                    """,
                    (field_values["title"], now, existing["id"], existing["encrypt_job_id"]),
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
    if status == "completed":
        from backend.app.services.fine_job.filter_exclusions import record_job_event

        record_job_event(db, "detail", str(existing["id"]), now)


def update_capture_job_delivery_evaluation(
    db: Database,
    *,
    job: dict[str, object],
    evaluation: dict[str, object],
) -> None:
    """持久化投递评估，供历史采集页面继续查看。"""
    # 历史详情接口返回的是历史行 id；详情采集任务则携带 history_record_id。
    # 两种调用都要优先按历史主键回写，避免无来源岗位编号时无法定位记录。
    history_record_id = _text(job.get("history_record_id") or job.get("id"))
    identity_column = "id" if history_record_id else "dedupe_key"
    identity_value = history_record_id or build_job_dedupe_key(job)
    with db.connect() as connection:
        row = connection.execute(
            f"SELECT id, payload_json FROM fj_boss_jobs WHERE {identity_column} = ?",
            (identity_value,),
        ).fetchone()
        if row is None:
            return
        try:
            payload = json.loads(row["payload_json"] or "{}")
        except json.JSONDecodeError:
            payload = {}
        payload.update(
            {
                "delivery_evaluation": evaluation,
                "recommended": evaluation.get("decision") == "recommend",
                "recommendation_source": evaluation.get("source"),
                "recommendation_reason": "；".join(
                    str(value) for value in evaluation.get("reasons") or []
                ),
            }
        )
        connection.execute(
            f"""
            UPDATE fj_boss_jobs
            SET delivery_evaluation_json = ?, payload_json = ?
            WHERE {identity_column} = ?
            """,
            (_json(evaluation), _json(payload), identity_value),
        )
def get_capture_history_job(db: Database, history_job_id: str) -> dict[str, object]:
    with db.connect() as connection:
        row = connection.execute(
            """
            SELECT id, source_job_id, encrypt_job_id, title, company_name, company_id,
                   (SELECT company_type FROM fj_companies c WHERE c.id = fj_boss_jobs.company_id) AS company_type,
                   (SELECT is_blacklisted FROM fj_companies c WHERE c.id = fj_boss_jobs.company_id) AS is_blacklisted,
                   (SELECT status FROM fj_job_applications a WHERE a.job_id = fj_boss_jobs.id) AS application_status,
                   (SELECT applied_at FROM fj_job_applications a WHERE a.job_id = fj_boss_jobs.id) AS applied_at,
                   company_scale, company_stage, company_industry, welfare,
                   salary, location, experience, degree,
                   boss_active_status, job_link, tags, skills, job_labels, search_keyword, payload_json,
                   detail_json, detail_status, detail_error, detail_version, delivery_evaluation_json, detail_collected_at,
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
    search_keyword: str = "",
    city: str = "",
    company_scale: str = "",
    company_industry: str = "",
    company_stage: str = "",
    detail_status: str = "",
    repeat_status: str = "all",
    collected_from: str = "",
    collected_to: str = "",
    sort_by: HistorySortField = "last_collected_at",
    sort_order: HistorySortOrder = "desc",
    page: int = 1,
    page_size: int = 20,
) -> dict[str, object]:
    from backend.app.services.fine_job.job_applications import sync_succeeded_applications

    sync_succeeded_applications(db)
    conditions: list[str] = []
    values: list[object] = []
    if query.strip():
        like = f"%{query.strip()}%"
        conditions.append("(title LIKE ? OR company_name LIKE ? OR skills LIKE ?)")
        values.extend([like, like, like])
    if search_keyword.strip():
        conditions.append("search_keyword = ?")
        values.append(search_keyword.strip())
    if city.strip():
        conditions.append("location LIKE ?")
        values.append(f"%{city.strip()}%")
    if company_scale.strip():
        conditions.append("company_scale = ?")
        values.append(company_scale.strip())
    if company_industry.strip():
        conditions.append("company_industry = ?")
        values.append(company_industry.strip())
    if company_stage.strip():
        conditions.append("company_stage = ?")
        values.append(company_stage.strip())
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
            SELECT id, source_job_id, encrypt_job_id, title, company_name, company_id,
                   (SELECT company_type FROM fj_companies c WHERE c.id = fj_boss_jobs.company_id) AS company_type,
                   (SELECT is_blacklisted FROM fj_companies c WHERE c.id = fj_boss_jobs.company_id) AS is_blacklisted,
                   (SELECT status FROM fj_job_applications a WHERE a.job_id = fj_boss_jobs.id) AS application_status,
                   (SELECT applied_at FROM fj_job_applications a WHERE a.job_id = fj_boss_jobs.id) AS applied_at,
                   company_scale, salary,
                   company_stage, company_industry, welfare, location, experience,
                   degree, boss_active_status, job_link,
                   tags, skills, job_labels, search_keyword, payload_json, detail_json, detail_status,
                   detail_error, detail_version, delivery_evaluation_json, detail_collected_at, first_collected_at,
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
    payload = _load_json(row["payload_json"])
    if not isinstance(payload, dict):
        payload = {}
    return {
        "id": row["id"],
        "job_id": row["source_job_id"],
        "encrypt_job_id": row["encrypt_job_id"],
        "title": row["title"],
        "boss_name": row["company_name"],
        "company_id": row["company_id"],
        "company_type": row["company_type"] or "unknown",
        "is_outsourcing_company": row["company_type"] == "outsourcing",
        "is_blacklisted": bool(row["is_blacklisted"]),
        "application_status": row["application_status"],
        "applied_at": row["applied_at"],
        "company_scale": row["company_scale"],
        "company_stage": row["company_stage"],
        "company_industry": row["company_industry"],
        "welfare": row["welfare"],
        "salary": row["salary"],
        "location": row["location"],
        "experience": row["experience"],
        "degree": row["degree"],
        "boss_active_status": row["boss_active_status"],
        "job_link": row["job_link"],
        "tags": row["tags"],
        "skills": row["skills"],
        "job_labels": row["job_labels"],
        "search_keyword": row["search_keyword"],
        "filter_status": payload.get("filter_status"),
        "strategy_filter_status": payload.get(
            "strategy_filter_status", payload.get("filter_status")
        ),
        "final_filter_status": payload.get(
            "final_filter_status", payload.get("filter_status")
        ),
        "filter_reasons": list(payload.get("filter_reasons") or []),
        "filter_missing_fields": list(payload.get("filter_missing_fields") or []),
        "filter_strategy_id": payload.get("filter_strategy_id"),
        "cooldown_excluded": bool(payload.get("cooldown_excluded")),
        "cooldown_reasons": list(payload.get("cooldown_reasons") or []),
        "detail": detail,
        "delivery_evaluation": _load_json(row["delivery_evaluation_json"]),
        "recommended": (
            (_load_json(row["delivery_evaluation_json"]) or {}).get("decision") == "recommend"
        ),
        "recommendation_source": (
            (_load_json(row["delivery_evaluation_json"]) or {}).get("source")
        ),
        "recommendation_reason": "；".join(
            str(value)
            for value in ((_load_json(row["delivery_evaluation_json"]) or {}).get("reasons") or [])
        ) or None,
        "detail_status": row["detail_status"],
        "detail_error": row["detail_error"],
        "detail_version": row["detail_version"],
        "detail_collected_at": row["detail_collected_at"],
        "first_collected_at": row["first_collected_at"],
        "last_collected_at": row["last_collected_at"],
        "collect_count": row["collect_count"],
        "latest_capture_id": row["latest_batch_id"],
        "is_previously_collected": int(row["collect_count"]) > 1,
    }


def _company(job: dict[str, object]) -> str:
    return _text(job.get("boss_name") or job.get("company"))


def _load_json(value: object) -> object:
    if not value:
        return None
    try:
        return json.loads(str(value))
    except (TypeError, json.JSONDecodeError):
        return None


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
