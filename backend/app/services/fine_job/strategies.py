from __future__ import annotations

import json

from backend.app.db import Database
from backend.app.errors import AppError
from backend.app.schemas.fine_job.strategies import (
    FineJobFilterStrategyPayload,
    FineJobRecommendationStrategyPayload,
)
from backend.app.utils import new_id, utc_now


FILTER_LIST_FIELDS = (
    "search_keywords",
    "cities",
    "title_include_any",
    "title_include_all",
    "title_exclude",
    "company_include",
    "company_exclude",
    "company_scales",
    "company_industries",
    "company_stages",
    "degrees",
    "experiences",
    "job_types",
    "skill_include_any",
    "skill_include_all",
    "skill_exclude",
    "boss_active_statuses",
)
RECOMMENDATION_LIST_FIELDS = (
    "desired_responsibilities",
    "required_skills",
    "preferred_skills",
    "excluded_terms",
    "preferred_industries",
)


def list_filter_strategies(db: Database) -> list[dict[str, object]]:
    _migrate_legacy_intent(db)
    with db.connect() as connection:
        rows = connection.execute(
            "SELECT * FROM fj_job_filter_strategies ORDER BY enabled DESC, updated_at DESC, id DESC"
        ).fetchall()
    return [_serialize_filter(row) for row in rows]


def get_filter_strategy(db: Database, strategy_id: str) -> dict[str, object]:
    with db.connect() as connection:
        row = connection.execute(
            "SELECT * FROM fj_job_filter_strategies WHERE id = ?", (strategy_id,)
        ).fetchone()
    if row is None:
        raise _not_found("岗位筛选策略")
    return _serialize_filter(row)


def save_filter_strategy(
    db: Database,
    payload: FineJobFilterStrategyPayload,
    *,
    strategy_id: str | None = None,
) -> dict[str, object]:
    now = utc_now()
    identifier = strategy_id or new_id()
    existing = _existing_created_at(db, "fj_job_filter_strategies", identifier)
    values = payload.model_dump()
    columns = [
        "id", "name", "enabled", *[f"{field}_json" for field in FILTER_LIST_FIELDS],
        "monthly_salary_min", "monthly_salary_max_at_least", "daily_salary_min",
        "unknown_value_policy", "notes", "created_at", "updated_at",
    ]
    row_values = [
        identifier,
        payload.name.strip(),
        int(payload.enabled),
        *[_dump_list(values[field]) for field in FILTER_LIST_FIELDS],
        payload.monthly_salary_min,
        payload.monthly_salary_max_at_least,
        payload.daily_salary_min,
        payload.unknown_value_policy,
        payload.notes.strip(),
        existing or now,
        now,
    ]
    placeholders = ", ".join("?" for _ in columns)
    updates = ", ".join(f"{column}=excluded.{column}" for column in columns[1:-2])
    with db.connect() as connection:
        connection.execute(
            f"INSERT INTO fj_job_filter_strategies ({', '.join(columns)}) VALUES ({placeholders}) "
            f"ON CONFLICT(id) DO UPDATE SET {updates}, updated_at=excluded.updated_at",
            row_values,
        )
    return get_filter_strategy(db, identifier)


def delete_filter_strategy(db: Database, strategy_id: str) -> None:
    _delete_strategy(db, "fj_job_filter_strategies", strategy_id, "岗位筛选策略")


def list_recommendation_strategies(db: Database) -> list[dict[str, object]]:
    with db.connect() as connection:
        rows = connection.execute(
            "SELECT * FROM fj_job_recommendation_strategies ORDER BY enabled DESC, updated_at DESC, id DESC"
        ).fetchall()
    return [_serialize_recommendation(row) for row in rows]


def get_recommendation_strategy(db: Database, strategy_id: str) -> dict[str, object]:
    with db.connect() as connection:
        row = connection.execute(
            "SELECT * FROM fj_job_recommendation_strategies WHERE id = ?", (strategy_id,)
        ).fetchone()
    if row is None:
        raise _not_found("岗位建议投递策略")
    return _serialize_recommendation(row)


def save_recommendation_strategy(
    db: Database,
    payload: FineJobRecommendationStrategyPayload,
    *,
    strategy_id: str | None = None,
) -> dict[str, object]:
    now = utc_now()
    identifier = strategy_id or new_id()
    existing = _existing_created_at(db, "fj_job_recommendation_strategies", identifier)
    values = payload.model_dump()
    columns = [
        "id", "name", "enabled", "filter_strategy_id", "resume_id", "evaluation_method",
        *[f"{field}_json" for field in RECOMMENDATION_LIST_FIELDS],
        "work_preferences", "risk_notes", "minimum_confidence",
        "insufficient_info_action", "notes", "created_at", "updated_at",
    ]
    row_values = [
        identifier,
        payload.name.strip(),
        int(payload.enabled),
        payload.filter_strategy_id or None,
        payload.resume_id or None,
        payload.evaluation_method,
        *[_dump_list(values[field]) for field in RECOMMENDATION_LIST_FIELDS],
        payload.work_preferences.strip(),
        payload.risk_notes.strip(),
        payload.minimum_confidence,
        payload.insufficient_info_action,
        payload.notes.strip(),
        existing or now,
        now,
    ]
    placeholders = ", ".join("?" for _ in columns)
    updates = ", ".join(f"{column}=excluded.{column}" for column in columns[1:-2])
    with db.connect() as connection:
        connection.execute(
            f"INSERT INTO fj_job_recommendation_strategies ({', '.join(columns)}) VALUES ({placeholders}) "
            f"ON CONFLICT(id) DO UPDATE SET {updates}, updated_at=excluded.updated_at",
            row_values,
        )
    return get_recommendation_strategy(db, identifier)


def delete_recommendation_strategy(db: Database, strategy_id: str) -> None:
    _delete_strategy(
        db, "fj_job_recommendation_strategies", strategy_id, "岗位建议投递策略"
    )


def _serialize_filter(row) -> dict[str, object]:
    return {
        "id": row["id"],
        "name": row["name"],
        "enabled": bool(row["enabled"]),
        **{field: _load_list(row[f"{field}_json"]) for field in FILTER_LIST_FIELDS},
        "monthly_salary_min": row["monthly_salary_min"],
        "monthly_salary_max_at_least": row["monthly_salary_max_at_least"],
        "daily_salary_min": row["daily_salary_min"],
        "unknown_value_policy": row["unknown_value_policy"],
        "notes": row["notes"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _serialize_recommendation(row) -> dict[str, object]:
    return {
        "id": row["id"],
        "name": row["name"],
        "enabled": bool(row["enabled"]),
        "filter_strategy_id": row["filter_strategy_id"],
        "resume_id": row["resume_id"],
        "evaluation_method": row["evaluation_method"],
        **{field: _load_list(row[f"{field}_json"]) for field in RECOMMENDATION_LIST_FIELDS},
        "work_preferences": row["work_preferences"],
        "risk_notes": row["risk_notes"],
        "minimum_confidence": row["minimum_confidence"],
        "insufficient_info_action": row["insufficient_info_action"],
        "notes": row["notes"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _migrate_legacy_intent(db: Database) -> None:
    with db.connect() as connection:
        if connection.execute("SELECT 1 FROM fj_job_filter_strategies LIMIT 1").fetchone():
            return
        row = connection.execute("SELECT * FROM fj_job_intents WHERE id = 'default'").fetchone()
        if row is None:
            return
        now = utc_now()
        connection.execute(
            """
            INSERT INTO fj_job_filter_strategies (
              id, name, enabled, search_keywords_json, cities_json,
              title_include_any_json, title_include_all_json, title_exclude_json,
              company_include_json, company_exclude_json, company_scales_json,
              company_industries_json, company_stages_json, degrees_json,
              experiences_json, job_types_json, monthly_salary_min,
              monthly_salary_max_at_least, daily_salary_min, skill_include_any_json,
              skill_include_all_json, skill_exclude_json, boss_active_statuses_json,
              unknown_value_policy, notes, created_at, updated_at
            ) VALUES (?, ?, 1, ?, ?, ?, '[]', ?, '[]', '[]', '[]', '[]', '[]',
                      '[]', '[]', '[]', ?, ?, NULL, ?, '[]', '[]', '[]', 'review', ?, ?, ?)
            """,
            (
                "legacy-intent-default",
                str(row["target_title"] or "默认岗位筛选").strip() or "默认岗位筛选",
                row["keywords_json"],
                row["cities_json"],
                json.dumps([row["target_title"]], ensure_ascii=False)
                if str(row["target_title"] or "").strip() else "[]",
                row["excluded_keywords_json"],
                row["salary_min"],
                row["salary_max"],
                row["expanded_keywords_json"],
                row["notes"],
                row["created_at"] or now,
                now,
            ),
        )


def _existing_created_at(db: Database, table: str, identifier: str) -> str | None:
    with db.connect() as connection:
        row = connection.execute(
            f"SELECT created_at FROM {table} WHERE id = ?", (identifier,)
        ).fetchone()
    return str(row["created_at"]) if row else None


def _delete_strategy(db: Database, table: str, identifier: str, label: str) -> None:
    with db.connect() as connection:
        cursor = connection.execute(f"DELETE FROM {table} WHERE id = ?", (identifier,))
    if cursor.rowcount == 0:
        raise _not_found(label)


def _dump_list(values: object) -> str:
    cleaned: list[str] = []
    seen: set[str] = set()
    for value in values if isinstance(values, list) else []:
        text = str(value).strip()
        if text and text not in seen:
            cleaned.append(text)
            seen.add(text)
    return json.dumps(cleaned, ensure_ascii=False)


def _load_list(value: str | None) -> list[str]:
    try:
        parsed = json.loads(value or "[]")
    except json.JSONDecodeError:
        return []
    return [str(item) for item in parsed] if isinstance(parsed, list) else []


def _not_found(label: str) -> AppError:
    return AppError(
        status_code=404,
        error_category="NOT_FOUND",
        error_message=f"{label}不存在。",
    )
