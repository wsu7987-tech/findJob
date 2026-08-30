from __future__ import annotations

import json

from backend.app.db import Database
from backend.app.errors import AppError
from backend.app.schemas.fine_job.strategies import (
    FineJobFilterStrategyPayload,
    FineJobRecommendationStrategyPayload,
)
from backend.app.schemas.fine_job.profile_v3 import SearchKeywordPayload, StrategyChangeSetApply
from backend.app.services.fine_job import profile_store
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
    existing = _existing_row(db, "fj_job_filter_strategies", identifier)
    values = payload.model_dump()
    candidate_profile_id = _preserved_value(
        payload, "candidate_profile_id", existing, "candidate_profile_id"
    )
    resume_version_id = _preserved_value(
        payload, "resume_version_id", existing, "resume_version_id"
    )
    candidate_profile_id = _resolve_strategy_profile_id(
        db, candidate_profile_id, resume_version_id
    )
    source_type = _preserved_value(payload, "source_type", existing, "source_type") or "user"
    strategy_version = int(existing["strategy_version"]) + 1 if existing else 1
    columns = [
        "id", "name", "enabled", "candidate_profile_id", "resume_version_id",
        "source_type", "strategy_version", "based_on_analysis_run_id",
        "based_on_resume_content_version", "based_on_facts_version", "based_on_qa_version",
        *[f"{field}_json" for field in FILTER_LIST_FIELDS],
        "cooldown_rules_json",
        "monthly_salary_min", "monthly_salary_max_at_least", "daily_salary_min",
        "unknown_value_policy", "notes", "created_at", "updated_at",
    ]
    row_values = [
        identifier,
        payload.name.strip(),
        int(payload.enabled),
        candidate_profile_id,
        resume_version_id,
        source_type,
        strategy_version,
        _preserved_value(payload, "based_on_analysis_run_id", existing, "based_on_analysis_run_id"),
        _preserved_value(payload, "based_on_resume_content_version", existing, "based_on_resume_content_version"),
        _preserved_value(payload, "based_on_facts_version", existing, "based_on_facts_version"),
        _preserved_value(payload, "based_on_qa_version", existing, "based_on_qa_version"),
        *[_dump_list(values[field]) for field in FILTER_LIST_FIELDS],
        json.dumps(values["cooldown_rules"], ensure_ascii=False),
        payload.monthly_salary_min,
        payload.monthly_salary_max_at_least,
        payload.daily_salary_min,
        payload.unknown_value_policy,
        payload.notes.strip(),
        str(existing["created_at"]) if existing else now,
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
        connection.execute(
            "UPDATE fj_filter_exclusion_states SET status = 'stale', updated_at = ? WHERE strategy_id = ?",
            (now, identifier),
        )
    if candidate_profile_id:
        profile_store.bump_versions(
            db, str(candidate_profile_id), "strategy_version", "context_version"
        )
    if existing is None and payload.search_keywords:
        replace_search_keywords(
            db,
            identifier,
            [(keyword, "", True) for keyword in payload.search_keywords],
            source_type=source_type,
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
    existing = _existing_row(db, "fj_job_recommendation_strategies", identifier)
    values = payload.model_dump()
    candidate_profile_id = _preserved_value(
        payload, "candidate_profile_id", existing, "candidate_profile_id"
    )
    resume_version_id = _preserved_value(
        payload, "resume_version_id", existing, "resume_version_id"
    )
    candidate_profile_id = _resolve_strategy_profile_id(
        db, candidate_profile_id, resume_version_id
    )
    source_type = _preserved_value(payload, "source_type", existing, "source_type") or "user"
    strategy_version = int(existing["strategy_version"]) + 1 if existing else 1
    columns = [
        "id", "name", "enabled", "filter_strategy_id", "resume_id", "evaluation_method",
        "candidate_profile_id", "resume_version_id", "source_type", "strategy_version",
        "based_on_analysis_run_id", "based_on_resume_content_version",
        "based_on_facts_version", "based_on_qa_version",
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
        candidate_profile_id,
        resume_version_id,
        source_type,
        strategy_version,
        _preserved_value(payload, "based_on_analysis_run_id", existing, "based_on_analysis_run_id"),
        _preserved_value(payload, "based_on_resume_content_version", existing, "based_on_resume_content_version"),
        _preserved_value(payload, "based_on_facts_version", existing, "based_on_facts_version"),
        _preserved_value(payload, "based_on_qa_version", existing, "based_on_qa_version"),
        *[_dump_list(values[field]) for field in RECOMMENDATION_LIST_FIELDS],
        payload.work_preferences.strip(),
        payload.risk_notes.strip(),
        payload.minimum_confidence,
        payload.insufficient_info_action,
        payload.notes.strip(),
        str(existing["created_at"]) if existing else now,
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
    if candidate_profile_id:
        profile_store.bump_versions(
            db, str(candidate_profile_id), "strategy_version", "context_version"
        )
    return get_recommendation_strategy(db, identifier)


def list_search_keywords(db: Database, strategy_id: str) -> list[dict[str, object]]:
    get_filter_strategy(db, strategy_id)
    with db.connect() as connection:
        rows = connection.execute(
            """
            SELECT * FROM fj_filter_strategy_search_keywords
            WHERE filter_strategy_id = ?
            ORDER BY sort_order, created_at, id
            """,
            (strategy_id,),
        ).fetchall()
    return [_serialize_search_keyword(row) for row in rows]


def create_search_keyword(
    db: Database,
    strategy_id: str,
    payload: SearchKeywordPayload,
    *,
    source_type: str = "user",
) -> dict[str, object]:
    strategy = get_filter_strategy(db, strategy_id)
    keyword_id = new_id()
    now = utc_now()
    with db.connect() as connection:
        connection.execute(
            """
            INSERT INTO fj_filter_strategy_search_keywords (
              id, filter_strategy_id, keyword, reason, enabled, sort_order,
              source_type, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                keyword_id,
                strategy_id,
                payload.keyword.strip(),
                payload.reason.strip(),
                1 if payload.enabled else 0,
                payload.sort_order,
                source_type,
                now,
                now,
            ),
        )
    _sync_search_keywords_projection(db, strategy_id)
    _bump_strategy_context(db, strategy)
    return next(item for item in list_search_keywords(db, strategy_id) if item["id"] == keyword_id)


def update_search_keyword(
    db: Database,
    strategy_id: str,
    keyword_id: str,
    payload: SearchKeywordPayload,
) -> dict[str, object]:
    strategy = get_filter_strategy(db, strategy_id)
    with db.connect() as connection:
        cursor = connection.execute(
            """
            UPDATE fj_filter_strategy_search_keywords
            SET keyword = ?, reason = ?, enabled = ?, sort_order = ?, source_type = 'user', updated_at = ?
            WHERE id = ? AND filter_strategy_id = ?
            """,
            (
                payload.keyword.strip(),
                payload.reason.strip(),
                1 if payload.enabled else 0,
                payload.sort_order,
                utc_now(),
                keyword_id,
                strategy_id,
            ),
        )
    if cursor.rowcount == 0:
        raise _not_found("搜索词")
    _sync_search_keywords_projection(db, strategy_id)
    _bump_strategy_context(db, strategy)
    return next(item for item in list_search_keywords(db, strategy_id) if item["id"] == keyword_id)


def delete_search_keyword(db: Database, strategy_id: str, keyword_id: str) -> None:
    strategy = get_filter_strategy(db, strategy_id)
    with db.connect() as connection:
        cursor = connection.execute(
            "DELETE FROM fj_filter_strategy_search_keywords WHERE id = ? AND filter_strategy_id = ?",
            (keyword_id, strategy_id),
        )
    if cursor.rowcount == 0:
        raise _not_found("搜索词")
    _sync_search_keywords_projection(db, strategy_id)
    _bump_strategy_context(db, strategy)


def reorder_search_keywords(
    db: Database,
    strategy_id: str,
    keyword_ids: list[str],
) -> list[dict[str, object]]:
    strategy = get_filter_strategy(db, strategy_id)
    current = list_search_keywords(db, strategy_id)
    current_ids = [str(item["id"]) for item in current]
    if len(keyword_ids) != len(set(keyword_ids)) or set(keyword_ids) != set(current_ids):
        raise AppError(422, "VALIDATION_FAILED", "排序列表必须完整包含当前策略的全部搜索词。")
    now = utc_now()
    with db.connect() as connection:
        connection.executemany(
            "UPDATE fj_filter_strategy_search_keywords SET sort_order = ?, updated_at = ? WHERE id = ? AND filter_strategy_id = ?",
            [(index, now, keyword_id, strategy_id) for index, keyword_id in enumerate(keyword_ids)],
        )
    _sync_search_keywords_projection(db, strategy_id)
    _bump_strategy_context(db, strategy)
    return list_search_keywords(db, strategy_id)


def replace_search_keywords(
    db: Database,
    strategy_id: str,
    keywords: list[tuple[str, str, bool]],
    *,
    source_type: str,
) -> list[dict[str, object]]:
    strategy = get_filter_strategy(db, strategy_id)
    now = utc_now()
    with db.connect() as connection:
        connection.execute(
            "DELETE FROM fj_filter_strategy_search_keywords WHERE filter_strategy_id = ?",
            (strategy_id,),
        )
        connection.executemany(
            """
            INSERT INTO fj_filter_strategy_search_keywords (
              id, filter_strategy_id, keyword, reason, enabled, sort_order,
              source_type, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    new_id(), strategy_id, keyword.strip(), reason.strip(),
                    1 if enabled else 0, index, source_type, now, now,
                )
                for index, (keyword, reason, enabled) in enumerate(keywords)
                if keyword.strip()
            ],
        )
    _sync_search_keywords_projection(db, strategy_id)
    _bump_strategy_context(db, strategy)
    return list_search_keywords(db, strategy_id)


def list_strategy_change_sets(
    db: Database,
    strategy_id: str,
) -> list[dict[str, object]]:
    with db.connect() as connection:
        rows = connection.execute(
            """
            SELECT * FROM fj_strategy_change_sets
            WHERE target_strategy_id = ?
            ORDER BY CASE status WHEN 'draft' THEN 0 ELSE 1 END, created_at DESC, id
            """,
            (strategy_id,),
        ).fetchall()
    return [_serialize_strategy_change_set(row) for row in rows]


def get_strategy_change_set(db: Database, change_set_id: str) -> dict[str, object]:
    with db.connect() as connection:
        row = connection.execute(
            "SELECT * FROM fj_strategy_change_sets WHERE id = ?", (change_set_id,)
        ).fetchone()
    if row is None:
        raise _not_found("策略 AI 变更")
    return _serialize_strategy_change_set(row)


def apply_strategy_change_set(
    db: Database,
    change_set_id: str,
    request: StrategyChangeSetApply,
) -> dict[str, object]:
    change_set = get_strategy_change_set(db, change_set_id)
    if change_set["status"] != "draft":
        raise AppError(409, "STRATEGY_CHANGE_SET_FINISHED", "策略 AI 变更已经结束。")
    strategy_type = str(change_set["strategy_type"])
    target_id = str(change_set.get("target_strategy_id") or "")
    payload = dict(change_set["payload"])
    if strategy_type == "filter":
        strategy_payload = FineJobFilterStrategyPayload(**payload)
        if request.name:
            strategy_payload.name = request.name.strip()
        elif request.mode == "save_as_new":
            strategy_payload.name = f"{strategy_payload.name}-新版本"
        save_filter_strategy(
            db,
            strategy_payload,
            strategy_id=target_id if request.mode == "update_current" else None,
        )
    elif strategy_type == "recommendation":
        recommendation_payload = FineJobRecommendationStrategyPayload(**payload)
        if request.name:
            recommendation_payload.name = request.name.strip()
        elif request.mode == "save_as_new":
            recommendation_payload.name = f"{recommendation_payload.name}-新版本"
        save_recommendation_strategy(
            db,
            recommendation_payload,
            strategy_id=target_id if request.mode == "update_current" else None,
        )
    else:
        keyword_items = [
            (
                str(item.get("keyword") or ""),
                str(item.get("reason") or ""),
                bool(item.get("enabled", True)),
            )
            for item in list(payload.get("keywords") or [])
            if isinstance(item, dict)
        ]
        keyword_target_id = target_id
        if request.mode == "save_as_new":
            current = get_filter_strategy(db, target_id)
            copied = FineJobFilterStrategyPayload(**current)
            copied.name = request.name.strip() if request.name else f"{copied.name}-新版本"
            copied.search_keywords = []
            keyword_target_id = str(save_filter_strategy(db, copied)["id"])
        replace_search_keywords(
            db, keyword_target_id, keyword_items, source_type="ai"
        )
    now = utc_now()
    with db.connect() as connection:
        connection.execute(
            """
            UPDATE fj_strategy_change_sets
            SET status = 'applied', applied_at = ?, updated_at = ? WHERE id = ?
            """,
            (now, now, change_set_id),
        )
    return get_strategy_change_set(db, change_set_id)


def delete_recommendation_strategy(db: Database, strategy_id: str) -> None:
    _delete_strategy(
        db, "fj_job_recommendation_strategies", strategy_id, "岗位建议投递策略"
    )


def _serialize_filter(row) -> dict[str, object]:
    return {
        "id": row["id"],
        "name": row["name"],
        "enabled": bool(row["enabled"]),
        "candidate_profile_id": row["candidate_profile_id"],
        "resume_version_id": row["resume_version_id"],
        "source_type": row["source_type"],
        "strategy_version": int(row["strategy_version"]),
        "based_on_analysis_run_id": row["based_on_analysis_run_id"],
        "based_on_resume_content_version": row["based_on_resume_content_version"],
        "based_on_facts_version": row["based_on_facts_version"],
        "based_on_qa_version": row["based_on_qa_version"],
        **{field: _load_list(row[f"{field}_json"]) for field in FILTER_LIST_FIELDS},
        "cooldown_rules": _load_object(row["cooldown_rules_json"]),
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
        "candidate_profile_id": row["candidate_profile_id"],
        "resume_version_id": row["resume_version_id"],
        "source_type": row["source_type"],
        "strategy_version": int(row["strategy_version"]),
        "based_on_analysis_run_id": row["based_on_analysis_run_id"],
        "based_on_resume_content_version": row["based_on_resume_content_version"],
        "based_on_facts_version": row["based_on_facts_version"],
        "based_on_qa_version": row["based_on_qa_version"],
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


def _existing_row(db: Database, table: str, identifier: str):
    with db.connect() as connection:
        row = connection.execute(
            f"SELECT * FROM {table} WHERE id = ?", (identifier,)
        ).fetchone()
    return row


def _preserved_value(payload, field: str, existing, column: str):
    if field in payload.model_fields_set or existing is None:
        return getattr(payload, field)
    return existing[column]


def _resolve_strategy_profile_id(
    db: Database,
    candidate_profile_id: object,
    resume_version_id: object,
) -> str | None:
    """具体简历唯一确定所属档案，策略保存时同步这项关联。"""
    profile_id = str(candidate_profile_id or "").strip()
    version_id = str(resume_version_id or "").strip()
    if not version_id:
        return profile_id or None
    resume_version = profile_store.get_resume_version(db, version_id)
    resume_profile_id = str(resume_version.get("profile_id") or "").strip()
    if profile_id and profile_id != resume_profile_id:
        raise AppError(422, "STRATEGY_PROFILE_MISMATCH", "策略关联档案与具体简历所属档案不一致。")
    return resume_profile_id


def _serialize_search_keyword(row) -> dict[str, object]:
    return {
        "id": str(row["id"]),
        "filter_strategy_id": str(row["filter_strategy_id"]),
        "keyword": str(row["keyword"]),
        "reason": str(row["reason"]),
        "enabled": bool(row["enabled"]),
        "sort_order": int(row["sort_order"]),
        "source_type": str(row["source_type"]),
        "created_at": str(row["created_at"]),
        "updated_at": str(row["updated_at"]),
    }


def _serialize_strategy_change_set(row) -> dict[str, object]:
    try:
        payload = json.loads(str(row["payload_json"]))
    except json.JSONDecodeError:
        payload = {}
    return {
        "id": str(row["id"]),
        "profile_id": str(row["profile_id"]),
        "resume_version_id": str(row["resume_version_id"]),
        "strategy_type": str(row["strategy_type"]),
        "target_strategy_id": row["target_strategy_id"],
        "payload": payload if isinstance(payload, dict) else {},
        "status": str(row["status"]),
        "operation_run_id": row["operation_run_id"],
        "created_at": str(row["created_at"]),
        "updated_at": str(row["updated_at"]),
        "applied_at": row["applied_at"],
    }


def _sync_search_keywords_projection(db: Database, strategy_id: str) -> None:
    with db.connect() as connection:
        rows = connection.execute(
            """
            SELECT keyword FROM fj_filter_strategy_search_keywords
            WHERE filter_strategy_id = ? AND enabled = 1
            ORDER BY sort_order, created_at, id
            """,
            (strategy_id,),
        ).fetchall()
        connection.execute(
            "UPDATE fj_job_filter_strategies SET search_keywords_json = ?, updated_at = ? WHERE id = ?",
            (_dump_list([row["keyword"] for row in rows]), utc_now(), strategy_id),
        )


def _bump_strategy_context(db: Database, strategy: dict[str, object]) -> None:
    profile_id = strategy.get("candidate_profile_id")
    if profile_id:
        profile_store.bump_versions(
            db, str(profile_id), "strategy_version", "context_version"
        )


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


def _load_object(value: str | None) -> dict[str, object]:
    try:
        parsed = json.loads(value or "{}")
    except json.JSONDecodeError:
        return {}
    return dict(parsed) if isinstance(parsed, dict) else {}


def _not_found(label: str) -> AppError:
    return AppError(
        status_code=404,
        error_category="NOT_FOUND",
        error_message=f"{label}不存在。",
    )
