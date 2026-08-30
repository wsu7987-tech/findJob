from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from backend.app.db import Database
from backend.app.errors import AppError
from backend.app.schemas.fine_job.profiles import (
    AnswerVariantPayload,
    AnswerVariantUpdate,
    CandidateProfileCreate,
    CandidateProfileUpdate,
    FactEvidencePayload,
    ProfileFactPayload,
    ProfileFactUpdate,
    ProfileQuestionPayload,
    ProfileQuestionUpdate,
    ProfileSourceCreateFile,
    ProfileSourceCreateText,
    ProfileSourceUpdate,
    ProfileVersionVector,
    ResumeVersionPayload,
    ResumeVersionUpdate,
    SearchCampaignPayload,
    SearchCampaignUpdate,
    SearchQueriesReplaceRequest,
)
from backend.app.utils import new_id, utc_now


DEFAULT_PROFILE_ID = "default"

DEFAULT_QUESTIONS: tuple[dict[str, object], ...] = (
    {
        "question_key": "current_city",
        "question_text": "目前所在城市是什么？",
        "reason": "用于岗位城市和通勤判断。",
        "required_stage": "search",
        "priority": "high",
        "writes_to_field": "current_city",
    },
    {
        "question_key": "acceptable_cities",
        "question_text": "可以接受哪些工作城市？",
        "reason": "用于生成岗位搜索范围。",
        "answer_type": "multi_select",
        "required_stage": "search",
        "priority": "high",
        "writes_to_field": "acceptable_cities",
    },
    {
        "question_key": "target_roles",
        "question_text": "目标岗位有哪些？可以填写多个岗位族。",
        "reason": "用于生成求职活动和搜索词。",
        "answer_type": "multi_select",
        "required_stage": "search",
        "priority": "high",
        "writes_to_field": "target_roles",
    },
    {
        "question_key": "expected_salary",
        "question_text": "期望薪资范围是多少，采用什么薪资口径？",
        "reason": "用于岗位筛选和薪资沟通。",
        "required_stage": "search",
        "priority": "high",
        "writes_to_field": "expected_salary",
    },
    {
        "question_key": "availability",
        "question_text": "最早到岗时间和通知期是多少？",
        "reason": "用于回复 HR 的到岗时间问题。",
        "required_stage": "chat",
        "priority": "high",
        "writes_to_field": "availability",
    },
    {
        "question_key": "leaving_reason",
        "question_text": "当前求职或离职原因是什么？",
        "reason": "用于生成真实、适合岗位场景的回答版本。",
        "required_stage": "chat",
        "priority": "high",
        "writes_to_field": None,
    },
    {
        "question_key": "current_salary",
        "question_text": "当前薪资是多少，是否愿意向 HR 披露？",
        "reason": "用于保存真实值和独立披露策略。",
        "required_stage": "chat",
        "priority": "high",
        "writes_to_field": "current_salary",
    },
    {
        "question_key": "education_confirmation",
        "question_text": "最高学历、专业、毕业时间和学习形式是什么？",
        "reason": "用于确认简历中的学历信息。",
        "required_stage": "application",
        "priority": "high",
        "writes_to_field": "education_summary",
    },
)


def ensure_default_profile(db: Database) -> dict[str, object]:
    now = utc_now()
    with db.connect() as connection:
        row = connection.execute(
            "SELECT * FROM fj_candidate_profiles WHERE id = ?", (DEFAULT_PROFILE_ID,)
        ).fetchone()
        if row is None:
            connection.execute(
                """
                INSERT INTO fj_candidate_profiles (
                  id, display_name, status, created_at, updated_at
                ) VALUES (?, '默认候选人', 'draft', ?, ?)
                """,
                (DEFAULT_PROFILE_ID, now, now),
            )
            _insert_default_questions(connection, DEFAULT_PROFILE_ID, now)
            row = connection.execute(
                "SELECT * FROM fj_candidate_profiles WHERE id = ?", (DEFAULT_PROFILE_ID,)
            ).fetchone()
    assert row is not None
    return _serialize_profile(row)


def list_profiles(db: Database) -> list[dict[str, object]]:
    ensure_default_profile(db)
    with db.connect() as connection:
        rows = connection.execute(
            "SELECT * FROM fj_candidate_profiles ORDER BY created_at, id"
        ).fetchall()
    return [_serialize_profile(row) for row in rows]


def get_profile(db: Database, profile_id: str) -> dict[str, object]:
    ensure_default_profile(db)
    with db.connect() as connection:
        row = connection.execute(
            "SELECT * FROM fj_candidate_profiles WHERE id = ?", (profile_id,)
        ).fetchone()
    if row is None:
        raise _not_found("候选人档案不存在。")
    return _serialize_profile(row)


def create_profile(db: Database, payload: CandidateProfileCreate) -> dict[str, object]:
    profile_id = new_id()
    now = utc_now()
    with db.connect() as connection:
        connection.execute(
            """
            INSERT INTO fj_candidate_profiles (
              id, display_name, status, created_at, updated_at
            ) VALUES (?, ?, 'draft', ?, ?)
            """,
            (profile_id, payload.display_name.strip(), now, now),
        )
        _insert_default_questions(connection, profile_id, now)
    return get_profile(db, profile_id)


def update_profile(
    db: Database,
    profile_id: str,
    payload: CandidateProfileUpdate,
) -> dict[str, object]:
    if payload.expected_versions is not None:
        require_versions(db, profile_id, payload.expected_versions)
    now = utc_now()
    with db.connect() as connection:
        cursor = connection.execute(
            """
            UPDATE fj_candidate_profiles
            SET display_name = ?, status = ?, updated_at = ?
            WHERE id = ?
            """,
            (payload.display_name.strip(), payload.status, now, profile_id),
        )
    if cursor.rowcount == 0:
        raise _not_found("候选人档案不存在。")
    return get_profile(db, profile_id)


def version_vector(db: Database, profile_id: str) -> dict[str, int]:
    profile = get_profile(db, profile_id)
    return dict(profile["versions"])  # type: ignore[arg-type]


def require_versions(
    db: Database,
    profile_id: str,
    expected: ProfileVersionVector | dict[str, int],
) -> None:
    expected_values = expected.model_dump() if isinstance(expected, ProfileVersionVector) else expected
    current = version_vector(db, profile_id)
    if any(int(current[key]) != int(expected_values[key]) for key in current):
        raise AppError(
            status_code=409,
            error_category="PROFILE_VERSION_CHANGED",
            error_message="候选人资料已经变化，请重新读取后再确认。",
        )


def bump_versions(db: Database, profile_id: str, *columns: str) -> None:
    allowed = {
        "sources_version",
        "facts_version",
        "questions_version",
        "answers_version",
        "strategy_version",
        "context_version",
    }
    selected = [column for column in columns if column in allowed]
    if not selected:
        return
    now = utc_now()
    assignments = ", ".join(f"{column} = {column} + 1" for column in selected)
    with db.connect() as connection:
        cursor = connection.execute(
            f"UPDATE fj_candidate_profiles SET {assignments}, updated_at = ? WHERE id = ?",
            (now, profile_id),
        )
    if cursor.rowcount == 0:
        raise _not_found("候选人档案不存在。")


def list_sources(db: Database, profile_id: str) -> list[dict[str, object]]:
    get_profile(db, profile_id)
    with db.connect() as connection:
        rows = connection.execute(
            """
            SELECT sources.*,
                   (
                     SELECT artifact.content
                     FROM fj_profile_artifacts AS artifact
                     WHERE artifact.source_id = sources.id
                       AND artifact.artifact_type = 'normalized_resume_markdown'
                       AND artifact.status IN ('draft', 'official')
                     ORDER BY artifact.version DESC, artifact.created_at DESC
                     LIMIT 1
                   ) AS normalized_markdown
            FROM fj_profile_sources AS sources
            WHERE sources.profile_id = ? AND sources.enabled = 1
            ORDER BY sources.updated_at DESC, sources.id DESC
            """,
            (profile_id,),
        ).fetchall()
    return [_serialize_source(row) for row in rows]


def get_source(db: Database, source_id: str) -> dict[str, object]:
    with db.connect() as connection:
        row = connection.execute(
            """
            SELECT sources.*,
                   (
                     SELECT artifact.content
                     FROM fj_profile_artifacts AS artifact
                     WHERE artifact.source_id = sources.id
                       AND artifact.artifact_type = 'normalized_resume_markdown'
                       AND artifact.status IN ('draft', 'official')
                     ORDER BY artifact.version DESC, artifact.created_at DESC
                     LIMIT 1
                   ) AS normalized_markdown
            FROM fj_profile_sources AS sources
            WHERE sources.id = ?
            """,
            (source_id,),
        ).fetchone()
    if row is None:
        raise _not_found("资料不存在。")
    return _serialize_source(row)


def create_text_source(
    db: Database,
    profile_id: str,
    payload: ProfileSourceCreateText,
) -> dict[str, object]:
    get_profile(db, profile_id)
    source_id = new_id()
    now = utc_now()
    with db.connect() as connection:
        connection.execute(
            """
            INSERT INTO fj_profile_sources (
              id, profile_id, source_type, title, raw_text, recognized_text, editable_text,
              recognizer_name, status, enabled, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'direct_text', 'uploaded', ?, ?, ?)
            """,
            (
                source_id,
                profile_id,
                payload.source_type,
                payload.title.strip(),
                payload.content,
                payload.content,
                payload.content,
                1 if payload.enabled else 0,
                now,
                now,
            ),
        )
    bump_versions(db, profile_id, "sources_version", "context_version")
    return get_source(db, source_id)


def create_file_source(
    db: Database,
    profile_id: str,
    payload: ProfileSourceCreateFile,
) -> dict[str, object]:
    get_profile(db, profile_id)
    path = Path(payload.file_path).expanduser().resolve()
    if not path.is_file():
        raise AppError(400, "VALIDATION_FAILED", "资料文件不存在。")
    if path.suffix.lower() != ".pdf":
        raise AppError(400, "VALIDATION_FAILED", "本版文件资料仅支持 PDF。")
    source_id = new_id()
    now = utc_now()
    title = payload.title.strip() if payload.title and payload.title.strip() else path.stem
    with db.connect() as connection:
        connection.execute(
            """
            INSERT INTO fj_profile_sources (
              id, profile_id, source_type, title, file_path, status, enabled,
              created_at, updated_at
            ) VALUES (?, ?, 'pdf', ?, ?, 'uploaded', ?, ?, ?)
            """,
            (source_id, profile_id, title, str(path), 1 if payload.enabled else 0, now, now),
        )
    bump_versions(db, profile_id, "sources_version", "context_version")
    return get_source(db, source_id)


def update_source(
    db: Database,
    source_id: str,
    payload: ProfileSourceUpdate,
) -> dict[str, object]:
    current = get_source(db, source_id)
    if int(current["source_version"]) != payload.expected_source_version:
        raise _version_error("资料已经变化，请重新读取。")
    next_status = "uploaded" if str(current["source_type"]) != "pdf" else str(current["status"])
    now = utc_now()
    with db.connect() as connection:
        connection.execute(
            """
            UPDATE fj_profile_sources
            SET title = ?, raw_text = ?, recognized_text = ?, enabled = ?,
                status = ?, source_version = source_version + 1, updated_at = ?
            WHERE id = ?
            """,
            (
                payload.title.strip(),
                payload.raw_text,
                payload.raw_text if str(current["source_type"]) != "pdf" else current["recognized_text"],
                1 if payload.enabled else 0,
                next_status,
                now,
                source_id,
            ),
        )
        # 上游资料发生变化后，未处理分析草稿全部失效。
        connection.execute(
            """
            UPDATE fj_profile_analysis_runs SET status = 'stale', updated_at = ?
            WHERE profile_id = ? AND status IN ('pending', 'needs_confirmation')
            """,
            (now, current["profile_id"]),
        )
    bump_versions(db, str(current["profile_id"]), "sources_version", "context_version")
    return get_source(db, source_id)


def delete_source(db: Database, source_id: str) -> None:
    current = get_source(db, source_id)
    with db.connect() as connection:
        connection.execute("DELETE FROM fj_profile_sources WHERE id = ?", (source_id,))
    bump_versions(db, str(current["profile_id"]), "sources_version", "context_version")


def list_resume_versions(db: Database, profile_id: str) -> list[dict[str, object]]:
    get_profile(db, profile_id)
    with db.connect() as connection:
        rows = connection.execute(
            "SELECT * FROM fj_resume_versions WHERE profile_id = ? AND deleted_at IS NULL ORDER BY CASE current_role WHEN 'base' THEN 0 ELSE 1 END, updated_at DESC",
            (profile_id,),
        ).fetchall()
    return [_serialize_resume_version(row) for row in rows]


def get_resume_version(db: Database, resume_version_id: str) -> dict[str, object]:
    with db.connect() as connection:
        row = connection.execute(
            "SELECT * FROM fj_resume_versions WHERE id = ?", (resume_version_id,)
        ).fetchone()
    if row is None:
        raise _not_found("简历版本不存在。")
    return _serialize_resume_version(row)


def create_resume_version(
    db: Database,
    profile_id: str,
    payload: ResumeVersionPayload,
) -> dict[str, object]:
    get_profile(db, profile_id)
    if payload.source_id:
        source = get_source(db, payload.source_id)
        if source["profile_id"] != profile_id:
            raise AppError(422, "VALIDATION_FAILED", "简历版本资料不属于当前档案。")
    family = None
    if payload.resume_family_id:
        with db.connect() as connection:
            family = connection.execute(
                "SELECT profile_id, base_version_id FROM fj_resume_families WHERE id = ?",
                (payload.resume_family_id,),
            ).fetchone()
        if family is None or str(family["profile_id"]) != profile_id:
            raise AppError(422, "VALIDATION_FAILED", "简历版本简历组不属于当前档案。")
        if payload.version_type == "base" and family["base_version_id"]:
            raise AppError(
                status_code=422,
                error_category="VALIDATION_FAILED",
                error_message="当前简历组已经有基础简历，请使用“设为基础简历”。",
            )
    if payload.parent_version_id:
        parent = get_resume_version(db, payload.parent_version_id)
        if parent["resume_family_id"] != payload.resume_family_id:
            raise AppError(422, "VALIDATION_FAILED", "父版本与当前简历组不一致。")
    resume_version_id = new_id()
    now = utc_now()
    with db.connect() as connection:
        if payload.is_default:
            connection.execute(
                "UPDATE fj_resume_versions SET is_default = 0, updated_at = ? WHERE profile_id = ?",
                (now, profile_id),
            )
        connection.execute(
            """
            INSERT INTO fj_resume_versions (
              id, profile_id, resume_family_id, parent_version_id, name, role_family,
              version_type, target_job_id, derived_reason, based_on_content_version,
              campaign_id, source_id, content, fact_ids_json, is_default, status,
              current_role, origin_type, derived_from_version_id,
              target_job_snapshot_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft', ?, ?, ?, ?, ?, ?)
            """,
            (
                resume_version_id,
                profile_id,
                payload.resume_family_id,
                payload.parent_version_id,
                payload.name.strip(),
                payload.role_family.strip(),
                payload.version_type,
                payload.target_job_id,
                payload.derived_reason.strip(),
                payload.based_on_content_version,
                payload.campaign_id,
                payload.source_id,
                payload.content,
                _dump(payload.fact_ids),
                1 if payload.is_default else 0,
                payload.current_role or ("base" if payload.version_type == "base" else "derived"),
                payload.origin_type or (
                    "upload_base" if payload.version_type == "base"
                    else "upload_derived" if payload.source_id
                    else "ai_derived" if payload.version_type == "jd_tailored"
                    else "manual_copy"
                ),
                payload.derived_from_version_id or payload.parent_version_id,
                _dump(payload.target_job_snapshot),
                now,
                now,
            ),
        )
        if payload.resume_family_id and payload.version_type == "base":
            connection.execute(
                """
                UPDATE fj_resume_families
                SET base_version_id = ?, updated_at = ?
                WHERE id = ?
                """,
                (resume_version_id, now, payload.resume_family_id),
            )
        if payload.source_id:
            connection.execute(
                "UPDATE fj_profile_sources SET resume_version_id = ?, updated_at = ? WHERE id = ?",
                (resume_version_id, now, payload.source_id),
            )
    bump_versions(db, profile_id, "context_version")
    return get_resume_version(db, resume_version_id)


def update_resume_version(
    db: Database,
    resume_version_id: str,
    payload: ResumeVersionUpdate,
) -> dict[str, object]:
    current = get_resume_version(db, resume_version_id)
    if int(current["content_version"]) != payload.expected_content_version:
        raise _version_error("简历版本已经变化，请重新读取。")
    if payload.version_type != current["version_type"] and (
        payload.version_type == "base" or current["version_type"] == "base"
    ):
        raise AppError(
            status_code=422,
            error_category="VALIDATION_FAILED",
            error_message="基础简历变更请使用“设为基础简历”。",
        )
    now = utc_now()
    with db.connect() as connection:
        if payload.is_default:
            connection.execute(
                "UPDATE fj_resume_versions SET is_default = 0, updated_at = ? WHERE profile_id = ?",
                (now, current["profile_id"]),
            )
        connection.execute(
            """
            UPDATE fj_resume_versions SET
              resume_family_id = ?, parent_version_id = ?, name = ?, role_family = ?,
              version_type = ?, target_job_id = ?, derived_reason = ?, based_on_content_version = ?,
              campaign_id = ?, source_id = ?, content = ?,
              fact_ids_json = ?, is_default = ?, status = 'draft', confirmed_at = NULL,
              origin_type = ?, derived_from_version_id = ?, target_job_snapshot_json = ?,
              content_version = content_version + 1, updated_at = ?
            WHERE id = ?
            """,
            (
                payload.resume_family_id,
                payload.parent_version_id,
                payload.name.strip(),
                payload.role_family.strip(),
                payload.version_type,
                payload.target_job_id,
                payload.derived_reason.strip(),
                payload.based_on_content_version,
                payload.campaign_id,
                payload.source_id,
                payload.content,
                _dump(payload.fact_ids),
                1 if payload.is_default else 0,
                payload.origin_type or current["origin_type"],
                payload.derived_from_version_id or payload.parent_version_id,
                _dump(payload.target_job_snapshot),
                now,
                resume_version_id,
            ),
        )
        if payload.source_id:
            connection.execute(
                "UPDATE fj_profile_sources SET resume_version_id = ?, updated_at = ? WHERE id = ?",
                (resume_version_id, now, payload.source_id),
            )
    bump_versions(db, str(current["profile_id"]), "context_version")
    return get_resume_version(db, resume_version_id)


def confirm_resume_version(db: Database, resume_version_id: str) -> dict[str, object]:
    current = get_resume_version(db, resume_version_id)
    now = utc_now()
    with db.connect() as connection:
        connection.execute(
            "UPDATE fj_resume_versions SET status = 'confirmed', confirmed_at = ?, updated_at = ? WHERE id = ?",
            (now, now, resume_version_id),
        )
    bump_versions(db, str(current["profile_id"]), "context_version")
    return get_resume_version(db, resume_version_id)


def delete_resume_version(db: Database, resume_version_id: str) -> None:
    current = get_resume_version(db, resume_version_id)
    if current["resume_family_id"]:
        with db.connect() as connection:
            family = connection.execute(
                "SELECT base_version_id FROM fj_resume_families WHERE id = ?",
                (current["resume_family_id"],),
            ).fetchone()
        if family is not None and str(family["base_version_id"] or "") == resume_version_id:
            raise AppError(
                status_code=422,
                error_category="VALIDATION_FAILED",
                error_message="基础简历不能直接删除，请先设置新的基础简历。",
            )
    with db.connect() as connection:
        connection.execute("DELETE FROM fj_resume_versions WHERE id = ?", (resume_version_id,))
    bump_versions(db, str(current["profile_id"]), "context_version")


def set_resume_version_as_base(
    db: Database,
    resume_version_id: str,
) -> dict[str, object]:
    """将组内派生版本提升为基础简历，并保持每组只有一个基础版本。"""
    current = get_resume_version(db, resume_version_id)
    family_id = str(current["resume_family_id"] or "")
    if not family_id:
        raise AppError(
            status_code=422,
            error_category="VALIDATION_FAILED",
            error_message="未关联简历组的版本不能设为基础简历。",
        )
    with db.connect() as connection:
        family = connection.execute(
            "SELECT * FROM fj_resume_families WHERE id = ?",
            (family_id,),
        ).fetchone()
    if family is None:
        raise AppError(
            status_code=404,
            error_category="RESUME_FAMILY_NOT_FOUND",
            error_message="简历组不存在。",
        )
    old_base_id = str(family["base_version_id"] or "")
    if old_base_id == resume_version_id:
        return current

    now = utc_now()
    with db.connect() as connection:
        connection.execute(
            """
            UPDATE fj_resume_versions
            SET current_role = 'derived', updated_at = ?
            WHERE resume_family_id = ? AND id <> ? AND current_role = 'base'
            """,
            (now, family_id, resume_version_id),
        )
        connection.execute(
            """
            UPDATE fj_resume_versions
            SET current_role = 'base', updated_at = ?
            WHERE id = ?
            """,
            (now, resume_version_id),
        )
        connection.execute(
            """
            UPDATE fj_resume_families
            SET base_version_id = ?, updated_at = ?
            WHERE id = ?
            """,
            (resume_version_id, now, family_id),
        )
    bump_versions(db, str(current["profile_id"]), "context_version")
    return get_resume_version(db, resume_version_id)


def list_facts(db: Database, profile_id: str) -> tuple[list[dict[str, object]], int]:
    profile = get_profile(db, profile_id)
    with db.connect() as connection:
        rows = connection.execute(
            """
            SELECT * FROM fj_profile_facts WHERE profile_id = ?
            ORDER BY domain, sort_order, entity_id, field_key, created_at
            """,
            (profile_id,),
        ).fetchall()
        link_rows = connection.execute(
            """
            SELECT l.fact_id, l.resume_version_id
            FROM fj_fact_resume_links l
            JOIN fj_profile_facts f ON f.id = l.fact_id
            WHERE f.profile_id = ?
            ORDER BY l.created_at, l.resume_version_id
            """,
            (profile_id,),
        ).fetchall()
    links: dict[str, list[str]] = {}
    for link in link_rows:
        links.setdefault(str(link["fact_id"]), []).append(str(link["resume_version_id"]))
    items = [_serialize_fact(row) for row in rows]
    for item in items:
        item["resume_version_ids"] = links.get(str(item["id"]), [])
    return items, int(profile["versions"]["facts_version"])  # type: ignore[index]


def evaluation_facts(
    db: Database,
    profile_id: str,
    resume_version_id: str | None = None,
) -> list[dict[str, object]]:
    facts, _ = list_facts(db, profile_id)
    return [
        {
            "id": fact["id"],
            "fact_type": fact["domain"],
            "fact_key": fact["field_key"],
            "fact_value": fact["value"] if isinstance(fact["value"], str) else _dump(fact["value"]),
            "confidence": fact["confidence"],
            "source_text": "",
            "user_confirmed": True,
            "sensitive": fact["sensitivity"] != "normal",
        }
        for fact in facts
        if fact["status"] == "confirmed"
        and (
            resume_version_id is None
            or bool(fact.get("applies_to_all_resumes"))
            or resume_version_id in fact.get("resume_version_ids", [])
        )
    ]


def get_fact(db: Database, fact_id: str) -> dict[str, object]:
    with db.connect() as connection:
        row = connection.execute("SELECT * FROM fj_profile_facts WHERE id = ?", (fact_id,)).fetchone()
        links = connection.execute(
            "SELECT resume_version_id FROM fj_fact_resume_links WHERE fact_id = ? ORDER BY created_at, resume_version_id",
            (fact_id,),
        ).fetchall()
    if row is None:
        raise _not_found("候选人事实不存在。")
    item = _serialize_fact(row)
    item["resume_version_ids"] = [str(link["resume_version_id"]) for link in links]
    return item


def create_fact(db: Database, profile_id: str, payload: ProfileFactPayload) -> dict[str, object]:
    get_profile(db, profile_id)
    fact_id = new_id()
    now = utc_now()
    with db.connect() as connection:
        _insert_fact(connection, fact_id, profile_id, payload.model_dump(), now)
        _replace_resume_links(
            connection,
            table="fj_fact_resume_links",
            owner_column="fact_id",
            owner_id=fact_id,
            profile_id=profile_id,
            resume_version_ids=payload.resume_version_ids,
            linked_by="user" if payload.source_type == "manual" else "ai_extraction",
            now=now,
        )
    bump_versions(db, profile_id, "facts_version", "context_version")
    return get_fact(db, fact_id)


def update_fact(db: Database, fact_id: str, payload: ProfileFactUpdate) -> dict[str, object]:
    current = get_fact(db, fact_id)
    profile_id = str(current["profile_id"])
    if version_vector(db, profile_id)["facts_version"] != payload.expected_facts_version:
        raise _version_error("候选人事实已经变化，请重新读取。")
    values = payload.model_dump(exclude={"expected_facts_version"})
    now = utc_now()
    with db.connect() as connection:
        connection.execute(
            """
            UPDATE fj_profile_facts SET
              scope_type = ?, scope_id = ?, domain = ?, entity_type = ?, entity_id = ?, field_key = ?, value_json = ?,
              source_type = ?, sort_order = ?, valid_from = ?, valid_to = ?, date_precision = ?,
              is_current = ?, confidence = ?, status = ?, conflict_group_id = ?, sensitivity = ?,
              external_use = ?, disclosure_policy_json = ?, valid_until = ?, confirmed_by = ?,
              analysis_operation_run_id = ?, source_content_version = ?, applies_to_all_resumes = ?, updated_at = ?
            WHERE id = ?
            """,
            _fact_values(values, now, fact_id),
        )
        _replace_resume_links(
            connection,
            table="fj_fact_resume_links",
            owner_column="fact_id",
            owner_id=fact_id,
            profile_id=profile_id,
            resume_version_ids=payload.resume_version_ids,
            linked_by="user",
            now=now,
        )
    bump_versions(db, profile_id, "facts_version", "context_version")
    return get_fact(db, fact_id)


def delete_fact(db: Database, fact_id: str) -> None:
    current = get_fact(db, fact_id)
    with db.connect() as connection:
        connection.execute("DELETE FROM fj_profile_facts WHERE id = ?", (fact_id,))
    bump_versions(db, str(current["profile_id"]), "facts_version", "context_version")


def list_evidence(db: Database, fact_id: str) -> list[dict[str, object]]:
    get_fact(db, fact_id)
    with db.connect() as connection:
        rows = connection.execute(
            "SELECT * FROM fj_profile_fact_evidence WHERE fact_id = ? ORDER BY created_at, id",
            (fact_id,),
        ).fetchall()
    return [_serialize_evidence(row) for row in rows]


def create_evidence(db: Database, fact_id: str, payload: FactEvidencePayload) -> dict[str, object]:
    fact = get_fact(db, fact_id)
    evidence_id = new_id()
    now = utc_now()
    with db.connect() as connection:
        connection.execute(
            """
            INSERT INTO fj_profile_fact_evidence (
              id, fact_id, source_type, source_id, source_excerpt,
              extraction_method, confidence, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                evidence_id,
                fact_id,
                payload.source_type,
                payload.source_id,
                payload.source_excerpt,
                payload.extraction_method,
                payload.confidence,
                now,
            ),
        )
    bump_versions(db, str(fact["profile_id"]), "facts_version", "context_version")
    return next(item for item in list_evidence(db, fact_id) if item["id"] == evidence_id)


def delete_evidence(db: Database, evidence_id: str) -> None:
    with db.connect() as connection:
        row = connection.execute(
            """
            SELECT e.id, f.profile_id FROM fj_profile_fact_evidence e
            JOIN fj_profile_facts f ON f.id = e.fact_id WHERE e.id = ?
            """,
            (evidence_id,),
        ).fetchone()
        if row is None:
            raise _not_found("事实依据不存在。")
        connection.execute("DELETE FROM fj_profile_fact_evidence WHERE id = ?", (evidence_id,))
    bump_versions(db, str(row["profile_id"]), "facts_version", "context_version")


def list_questions(db: Database, profile_id: str) -> tuple[list[dict[str, object]], int]:
    profile = get_profile(db, profile_id)
    with db.connect() as connection:
        rows = connection.execute(
            """
            SELECT * FROM fj_profile_questions WHERE profile_id = ?
            ORDER BY enabled DESC,
              CASE priority WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END,
              updated_at DESC
            """,
            (profile_id,),
        ).fetchall()
        link_rows = connection.execute(
            """
            SELECT l.question_id, l.resume_version_id
            FROM fj_question_resume_links l
            JOIN fj_profile_questions q ON q.id = l.question_id
            WHERE q.profile_id = ?
            ORDER BY l.created_at, l.resume_version_id
            """,
            (profile_id,),
        ).fetchall()
    links: dict[str, list[str]] = {}
    for link in link_rows:
        links.setdefault(str(link["question_id"]), []).append(str(link["resume_version_id"]))
    items = [_serialize_question(row) for row in rows]
    for item in items:
        item["resume_version_ids"] = links.get(str(item["id"]), [])
    return items, int(profile["versions"]["questions_version"])  # type: ignore[index]


def get_question(db: Database, question_id: str) -> dict[str, object]:
    with db.connect() as connection:
        row = connection.execute(
            "SELECT * FROM fj_profile_questions WHERE id = ?", (question_id,)
        ).fetchone()
        links = connection.execute(
            "SELECT resume_version_id FROM fj_question_resume_links WHERE question_id = ? ORDER BY created_at, resume_version_id",
            (question_id,),
        ).fetchall()
    if row is None:
        raise _not_found("QA 问题不存在。")
    item = _serialize_question(row)
    item["resume_version_ids"] = [str(link["resume_version_id"]) for link in links]
    return item


def list_question_revisions(db: Database, question_id: str) -> list[dict[str, object]]:
    get_question(db, question_id)
    with db.connect() as connection:
        rows = connection.execute(
            """
            SELECT * FROM fj_profile_qa_revisions
            WHERE question_id = ? ORDER BY revision DESC
            """,
            (question_id,),
        ).fetchall()
    return [
        {
            "id": str(row["id"]),
            "question_id": str(row["question_id"]),
            "revision": int(row["revision"]),
            "answer": _load(row["answer_json"], None),
            "source_type": str(row["source_type"]),
            "status": str(row["status"]),
            "created_at": str(row["created_at"]),
        }
        for row in rows
    ]


def record_question_revision(
    db: Database,
    question_id: str,
    answer: object,
    *,
    source_type: str,
) -> None:
    if answer is None:
        return
    with db.connect() as connection:
        _record_question_revision(
            connection, question_id, answer, source_type=source_type, now=utc_now()
        )


def create_question(
    db: Database,
    profile_id: str,
    payload: ProfileQuestionPayload,
) -> dict[str, object]:
    get_profile(db, profile_id)
    question_id = new_id()
    now = utc_now()
    with db.connect() as connection:
        _insert_question(connection, question_id, profile_id, payload.model_dump(), now)
        _record_question_revision(
            connection,
            question_id,
            payload.final_answer,
            source_type="user" if payload.origin == "user" else "ai_extraction",
            now=now,
        )
        _replace_resume_links(
            connection,
            table="fj_question_resume_links",
            owner_column="question_id",
            owner_id=question_id,
            profile_id=profile_id,
            resume_version_ids=payload.resume_version_ids,
            linked_by="user" if payload.origin == "user" else "ai_extraction",
            now=now,
        )
    bump_versions(db, profile_id, "questions_version", "context_version")
    return get_question(db, question_id)


def update_question(
    db: Database,
    question_id: str,
    payload: ProfileQuestionUpdate,
) -> dict[str, object]:
    current = get_question(db, question_id)
    profile_id = str(current["profile_id"])
    if version_vector(db, profile_id)["questions_version"] != payload.expected_questions_version:
        raise _version_error("QA 已经变化，请重新读取。")
    values = payload.model_dump(exclude={"expected_questions_version"})
    now = utc_now()
    with db.connect() as connection:
        connection.execute(
            """
            UPDATE fj_profile_questions SET
              scope_type = ?, scope_id = ?, scope_key = ?, question_key = ?, question_text = ?, reason = ?, origin = ?, answer_type = ?,
              required_stage = ?, priority = ?, proposed_answer_json = ?, final_answer_json = ?,
              status = ?, external_use = ?, valid_until = ?, source_id = ?, job_id = ?,
              writes_to_field = ?, enabled = ?, confirmed_by = ?, analysis_operation_run_id = ?,
              source_content_version = ?, applies_to_all_resumes = ?, updated_at = ?
            WHERE id = ?
            """,
            _question_values(values, now, question_id),
        )
        _record_question_revision(
            connection,
            question_id,
            payload.final_answer,
            source_type="user" if payload.confirmed_by == "user" else "ai_extraction",
            now=now,
        )
        _replace_resume_links(
            connection,
            table="fj_question_resume_links",
            owner_column="question_id",
            owner_id=question_id,
            profile_id=profile_id,
            resume_version_ids=payload.resume_version_ids,
            linked_by="user",
            now=now,
        )
    bump_versions(db, profile_id, "questions_version", "context_version")
    return get_question(db, question_id)


def delete_question(db: Database, question_id: str) -> None:
    current = get_question(db, question_id)
    with db.connect() as connection:
        connection.execute("DELETE FROM fj_profile_questions WHERE id = ?", (question_id,))
    bump_versions(db, str(current["profile_id"]), "questions_version", "answers_version", "context_version")


def list_answer_variants(db: Database, question_id: str) -> list[dict[str, object]]:
    get_question(db, question_id)
    with db.connect() as connection:
        rows = connection.execute(
            """
            SELECT * FROM fj_profile_answer_variants WHERE question_id = ?
            ORDER BY CASE scope_type WHEN 'job' THEN 1 WHEN 'role_family' THEN 2 ELSE 3 END,
                     updated_at DESC
            """,
            (question_id,),
        ).fetchall()
    return [_serialize_answer_variant(row) for row in rows]


def get_answer_variant(db: Database, variant_id: str) -> dict[str, object]:
    with db.connect() as connection:
        row = connection.execute(
            "SELECT * FROM fj_profile_answer_variants WHERE id = ?", (variant_id,)
        ).fetchone()
    if row is None:
        raise _not_found("回答版本不存在。")
    return _serialize_answer_variant(row)


def create_answer_variant(
    db: Database,
    question_id: str,
    payload: AnswerVariantPayload,
) -> dict[str, object]:
    question = get_question(db, question_id)
    variant_id = new_id()
    now = utc_now()
    with db.connect() as connection:
        _insert_answer_variant(connection, variant_id, question_id, payload.model_dump(), now)
    bump_versions(db, str(question["profile_id"]), "answers_version", "context_version")
    return get_answer_variant(db, variant_id)


def update_answer_variant(
    db: Database,
    variant_id: str,
    payload: AnswerVariantUpdate,
) -> dict[str, object]:
    current = get_answer_variant(db, variant_id)
    question = get_question(db, str(current["question_id"]))
    profile_id = str(question["profile_id"])
    if version_vector(db, profile_id)["answers_version"] != payload.expected_answers_version:
        raise _version_error("回答版本已经变化，请重新读取。")
    now = utc_now()
    values = payload.model_dump(exclude={"expected_answers_version"})
    with db.connect() as connection:
        connection.execute(
            """
            UPDATE fj_profile_answer_variants SET
              name = ?, scope_type = ?, scope_id = ?, answer_text = ?, internal_note = ?,
              usage_condition = ?, generated_by = ?, based_on_job_version = ?, external_use = ?,
              disclosure_policy_json = ?, status = 'draft', updated_at = ?
            WHERE id = ?
            """,
            _answer_variant_values(values, now, variant_id),
        )
    bump_versions(db, profile_id, "answers_version", "context_version")
    return get_answer_variant(db, variant_id)


def confirm_answer_variant(db: Database, variant_id: str) -> dict[str, object]:
    current = get_answer_variant(db, variant_id)
    question = get_question(db, str(current["question_id"]))
    now = utc_now()
    with db.connect() as connection:
        connection.execute(
            "UPDATE fj_profile_answer_variants SET status = 'confirmed', updated_at = ? WHERE id = ?",
            (now, variant_id),
        )
    bump_versions(db, str(question["profile_id"]), "answers_version", "context_version")
    return get_answer_variant(db, variant_id)


def delete_answer_variant(db: Database, variant_id: str) -> None:
    current = get_answer_variant(db, variant_id)
    question = get_question(db, str(current["question_id"]))
    with db.connect() as connection:
        connection.execute("DELETE FROM fj_profile_answer_variants WHERE id = ?", (variant_id,))
    bump_versions(db, str(question["profile_id"]), "answers_version", "context_version")


def get_analysis_run(db: Database, run_id: str) -> dict[str, object]:
    with db.connect() as connection:
        row = connection.execute(
            "SELECT * FROM fj_profile_analysis_runs WHERE id = ?", (run_id,)
        ).fetchone()
    if row is None:
        raise _not_found("资料分析任务不存在。")
    return _serialize_analysis_run(row)


def get_latest_analysis_run(db: Database, profile_id: str) -> dict[str, object]:
    get_profile(db, profile_id)
    with db.connect() as connection:
        row = connection.execute(
            """
            SELECT * FROM fj_profile_analysis_runs
            WHERE profile_id = ?
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            (profile_id,),
        ).fetchone()
    if row is None:
        raise _not_found("当前档案还没有资料分析任务。")
    return _serialize_analysis_run(row)


def list_analysis_items(db: Database, run_id: str) -> list[dict[str, object]]:
    get_analysis_run(db, run_id)
    with db.connect() as connection:
        rows = connection.execute(
            "SELECT * FROM fj_profile_analysis_items WHERE analysis_run_id = ? ORDER BY created_at, id",
            (run_id,),
        ).fetchall()
    return [_serialize_analysis_item(row) for row in rows]


def get_analysis_item(db: Database, item_id: str) -> dict[str, object]:
    with db.connect() as connection:
        row = connection.execute(
            "SELECT * FROM fj_profile_analysis_items WHERE id = ?", (item_id,)
        ).fetchone()
    if row is None:
        raise _not_found("分析项不存在。")
    return _serialize_analysis_item(row)


def set_analysis_item_status(
    db: Database,
    item_id: str,
    *,
    expected_status: str,
    status: str,
    decision_note: str | None,
    payload: dict[str, Any] | None = None,
) -> dict[str, object]:
    current = get_analysis_item(db, item_id)
    if current["status"] != expected_status:
        raise _version_error("分析项状态已经变化，请重新读取。")
    now = utc_now()
    with db.connect() as connection:
        connection.execute(
            """
            UPDATE fj_profile_analysis_items
            SET status = ?, payload_json = ?, decision_note = ?, decided_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                status,
                _dump(payload if payload is not None else current["payload"]),
                decision_note,
                now,
                now,
                item_id,
            ),
        )
    return get_analysis_item(db, item_id)


def list_campaigns(db: Database, profile_id: str) -> list[dict[str, object]]:
    get_profile(db, profile_id)
    with db.connect() as connection:
        rows = connection.execute(
            "SELECT * FROM fj_search_campaigns WHERE profile_id = ? ORDER BY updated_at DESC, id",
            (profile_id,),
        ).fetchall()
    return [_serialize_campaign(db, row) for row in rows]


def get_campaign(db: Database, campaign_id: str) -> dict[str, object]:
    with db.connect() as connection:
        row = connection.execute(
            "SELECT * FROM fj_search_campaigns WHERE id = ?", (campaign_id,)
        ).fetchone()
    if row is None:
        raise _not_found("求职活动不存在。")
    return _serialize_campaign(db, row)


def create_campaign(
    db: Database,
    profile_id: str,
    payload: SearchCampaignPayload,
) -> dict[str, object]:
    get_profile(db, profile_id)
    campaign_id = new_id()
    now = utc_now()
    with db.connect() as connection:
        _insert_campaign(connection, campaign_id, profile_id, payload.model_dump(), now)
    bump_versions(db, profile_id, "strategy_version", "context_version")
    return get_campaign(db, campaign_id)


def update_campaign(
    db: Database,
    campaign_id: str,
    payload: SearchCampaignUpdate,
) -> dict[str, object]:
    current = get_campaign(db, campaign_id)
    if int(current["campaign_version"]) != payload.expected_campaign_version:
        raise _version_error("求职活动已经变化，请重新读取。")
    values = payload.model_dump(exclude={"expected_campaign_version"})
    now = utc_now()
    with db.connect() as connection:
        connection.execute(
            """
            UPDATE fj_search_campaigns SET
              name = ?, target_titles_json = ?, role_families_json = ?, cities_json = ?,
              districts_json = ?, work_modes_json = ?, salary_json = ?, industries_json = ?,
              company_scales_json = ?, resume_version_id = ?, filter_strategy_id = ?,
              recommendation_strategy_id = ?, delivery_strategy_id = ?, excluded_terms_json = ?,
              campaign_version = campaign_version + 1, confirmed_at = NULL, updated_at = ?
            WHERE id = ?
            """,
            _campaign_update_values(values, now, campaign_id),
        )
    bump_versions(db, str(current["profile_id"]), "strategy_version", "context_version")
    return get_campaign(db, campaign_id)


def delete_campaign(db: Database, campaign_id: str) -> None:
    current = get_campaign(db, campaign_id)
    with db.connect() as connection:
        connection.execute("DELETE FROM fj_search_campaigns WHERE id = ?", (campaign_id,))
    bump_versions(db, str(current["profile_id"]), "strategy_version", "context_version")


def replace_search_queries(
    db: Database,
    campaign_id: str,
    payload: SearchQueriesReplaceRequest,
) -> dict[str, object]:
    campaign = get_campaign(db, campaign_id)
    if int(campaign["campaign_version"]) != payload.expected_campaign_version:
        raise _version_error("求职活动已经变化，请重新读取。")
    now = utc_now()
    with db.connect() as connection:
        connection.execute("DELETE FROM fj_search_queries WHERE campaign_id = ?", (campaign_id,))
        for query in payload.queries:
            values = query.model_dump()
            connection.execute(
                """
                INSERT INTO fj_search_queries (
                  id, campaign_id, name, role_family, platform, keyword, cities_json,
                  work_modes_json, positive_terms_json, excluded_terms_json, priority,
                  reason, enabled, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    new_id(),
                    campaign_id,
                    values["name"],
                    values["role_family"],
                    values["platform"],
                    values["keyword"],
                    _dump(values["cities"]),
                    _dump(values["work_modes"]),
                    _dump(values["positive_terms"]),
                    _dump(values["excluded_terms"]),
                    values["priority"],
                    values["reason"],
                    1 if values["enabled"] else 0,
                    now,
                    now,
                ),
            )
        connection.execute(
            "UPDATE fj_search_campaigns SET campaign_version = campaign_version + 1, updated_at = ? WHERE id = ?",
            (now, campaign_id),
        )
    bump_versions(db, str(campaign["profile_id"]), "strategy_version", "context_version")
    return get_campaign(db, campaign_id)


def migration_preview(db: Database) -> dict[str, object]:
    with db.connect() as connection:
        resume_count = int(connection.execute("SELECT COUNT(*) FROM fj_resumes").fetchone()[0])
        confirmed_fact_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM fj_resume_facts WHERE user_confirmed = 1"
            ).fetchone()[0]
        )
        intent_count = int(connection.execute("SELECT COUNT(*) FROM fj_job_intents").fetchone()[0])
    return {
        "legacy_resumes": resume_count,
        "confirmed_legacy_facts": confirmed_fact_count,
        "legacy_intents": intent_count,
        "convertible_sources": resume_count,
        "convertible_facts": confirmed_fact_count,
        "convertible_resume_versions": resume_count,
        "convertible_campaigns": intent_count,
        "skipped": [],
    }


def apply_legacy_migration(db: Database, profile_id: str) -> dict[str, object]:
    get_profile(db, profile_id)
    now = utc_now()
    counts = {
        "created_sources": 0,
        "created_facts": 0,
        "created_resume_versions": 0,
        "created_campaigns": 0,
        "created_queries": 0,
    }
    skipped: list[dict[str, Any]] = []
    with db.connect() as connection:
        resumes = connection.execute("SELECT * FROM fj_resumes ORDER BY created_at, id").fetchall()
        for resume in resumes:
            source_id = f"legacy_source_{resume['id']}"
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO fj_profile_sources (
                  id, profile_id, source_type, title, file_path, raw_text, recognized_text,
                  recognizer_name, status, enabled, created_at, updated_at
                ) VALUES (?, ?, 'pdf', ?, ?, ?, ?, ?, 'ready', 1, ?, ?)
                """,
                (
                    source_id,
                    profile_id,
                    resume["name"],
                    resume["file_path"],
                    resume["raw_text"],
                    resume["markdown_text"] or resume["raw_text"],
                    resume["parser_name"],
                    now,
                    now,
                ),
            )
            counts["created_sources"] += max(0, cursor.rowcount)
            version_id = f"legacy_resume_version_{resume['id']}"
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO fj_resume_versions (
                  id, profile_id, name, source_id, content, fact_ids_json,
                  is_default, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, '[]', 0, 'draft', ?, ?)
                """,
                (
                    version_id,
                    profile_id,
                    resume["name"],
                    source_id,
                    resume["markdown_text"] or resume["raw_text"],
                    now,
                    now,
                ),
            )
            counts["created_resume_versions"] += max(0, cursor.rowcount)
            legacy_facts = connection.execute(
                "SELECT * FROM fj_resume_facts WHERE resume_id = ? AND user_confirmed = 1",
                (resume["id"],),
            ).fetchall()
            for legacy_fact in legacy_facts:
                fact_id = f"legacy_fact_{legacy_fact['id']}"
                cursor = connection.execute(
                    """
                    INSERT OR IGNORE INTO fj_profile_facts (
                      id, profile_id, domain, entity_type, entity_id, field_key, value_json,
                      source_type, confidence, status, sensitivity, external_use,
                      disclosure_policy_json, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 'document', ?, 'confirmed', ?, 'prohibited', '{}', ?, ?)
                    """,
                    (
                        fact_id,
                        profile_id,
                        legacy_fact["fact_type"],
                        legacy_fact["fact_type"],
                        f"legacy_{legacy_fact['fact_type']}_{legacy_fact['id']}",
                        legacy_fact["fact_key"],
                        _dump(legacy_fact["fact_value"]),
                        legacy_fact["confidence"],
                        "sensitive" if legacy_fact["sensitive"] else "normal",
                        now,
                        now,
                    ),
                )
                if cursor.rowcount > 0:
                    counts["created_facts"] += 1
                    connection.execute(
                        """
                        INSERT INTO fj_profile_fact_evidence (
                          id, fact_id, source_type, source_id, source_excerpt,
                          extraction_method, confidence, created_at
                        ) VALUES (?, ?, 'document', ?, ?, 'legacy_migration', ?, ?)
                        """,
                        (
                            f"legacy_evidence_{legacy_fact['id']}",
                            fact_id,
                            source_id,
                            legacy_fact["source_text"] or "",
                            legacy_fact["confidence"],
                            now,
                        ),
                    )

        intents = connection.execute("SELECT * FROM fj_job_intents ORDER BY created_at, id").fetchall()
        for intent in intents:
            campaign_id = f"legacy_campaign_{intent['id']}"
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO fj_search_campaigns (
                  id, profile_id, name, target_titles_json, cities_json, work_modes_json,
                  salary_json, excluded_terms_json, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)
                """,
                (
                    campaign_id,
                    profile_id,
                    str(intent["target_title"] or "旧求职意向"),
                    _dump([intent["target_title"]] if intent["target_title"] else []),
                    intent["cities_json"],
                    _dump([] if intent["work_mode"] == "any" else [intent["work_mode"]]),
                    _dump({"min": intent["salary_min"], "max": intent["salary_max"]}),
                    intent["excluded_keywords_json"],
                    now,
                    now,
                ),
            )
            counts["created_campaigns"] += max(0, cursor.rowcount)
            keywords = [*_load_list(intent["keywords_json"]), *_load_list(intent["expanded_keywords_json"])]
            for index, keyword in enumerate(dict.fromkeys(str(value) for value in keywords if str(value).strip())):
                cursor = connection.execute(
                    """
                    INSERT OR IGNORE INTO fj_search_queries (
                      id, campaign_id, name, platform, keyword, priority, reason,
                      enabled, created_at, updated_at
                    ) VALUES (?, ?, ?, 'boss', ?, ?, '从旧求职意向迁移', 1, ?, ?)
                    """,
                    (
                        f"legacy_query_{intent['id']}_{index}",
                        campaign_id,
                        keyword,
                        keyword,
                        len(keywords) - index,
                        now,
                        now,
                    ),
                )
                counts["created_queries"] += max(0, cursor.rowcount)
    if any(counts.values()):
        bump_versions(
            db,
            profile_id,
            "sources_version",
            "facts_version",
            "strategy_version",
            "context_version",
        )
    return {"profile_id": profile_id, **counts, "skipped": skipped}


def _insert_default_questions(connection, profile_id: str, now: str) -> None:
    """将系统预设问题写入模板库，避免生成空的正式 QA。"""
    for sort_order, item in enumerate(DEFAULT_QUESTIONS):
        connection.execute(
            """
            INSERT OR IGNORE INTO fj_profile_qa_templates (
              id, profile_id, question_key, question_text, reason, answer_type,
              required_stage, priority, writes_to_field, enabled, sort_order,
              source_type, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, 'system', ?, ?)
            """,
            (
                new_id(),
                profile_id,
                item["question_key"],
                item["question_text"],
                item["reason"],
                item.get("answer_type", "text"),
                item["required_stage"],
                item["priority"],
                item["writes_to_field"],
                sort_order,
                now,
                now,
            ),
        )


def _replace_resume_links(
    connection,
    *,
    table: str,
    owner_column: str,
    owner_id: str,
    profile_id: str,
    resume_version_ids: Iterable[str],
    linked_by: str,
    now: str,
) -> None:
    """校验具体简历归属后，一次替换事实或 QA 的适用关联。"""
    allowed = {
        ("fj_fact_resume_links", "fact_id"),
        ("fj_question_resume_links", "question_id"),
    }
    if (table, owner_column) not in allowed:
        raise ValueError("Unsupported resume link table")
    normalized = list(dict.fromkeys(str(item).strip() for item in resume_version_ids if str(item).strip()))
    if normalized:
        placeholders = ",".join("?" for _ in normalized)
        rows = connection.execute(
            f"SELECT id FROM fj_resume_versions WHERE profile_id = ? AND deleted_at IS NULL AND id IN ({placeholders})",
            (profile_id, *normalized),
        ).fetchall()
        found = {str(row["id"]) for row in rows}
        if found != set(normalized):
            raise AppError(422, "VALIDATION_FAILED", "关联简历不存在或不属于当前候选人档案。")
    connection.execute(f"DELETE FROM {table} WHERE {owner_column} = ?", (owner_id,))
    connection.executemany(
        f"INSERT INTO {table} ({owner_column}, resume_version_id, linked_by, created_at) VALUES (?, ?, ?, ?)",
        [(owner_id, resume_version_id, linked_by, now) for resume_version_id in normalized],
    )


def _insert_fact(connection, fact_id: str, profile_id: str, values: dict[str, Any], now: str) -> None:
    fact_values = _fact_values(values, now, None)
    connection.execute(
        """
        INSERT INTO fj_profile_facts (
          id, profile_id, scope_type, scope_id, domain, entity_type, entity_id, field_key, value_json,
          source_type, sort_order, valid_from, valid_to, date_precision, is_current,
          confidence, status, conflict_group_id, sensitivity, external_use,
          disclosure_policy_json, valid_until, confirmed_by, analysis_operation_run_id,
          source_content_version, applies_to_all_resumes, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (fact_id, profile_id, *fact_values[:-2], now, now),
    )


def _fact_values(values: dict[str, Any], now: str, fact_id: str | None) -> tuple[object, ...]:
    return (
        values.get("scope_type") or "general",
        values.get("scope_id"),
        values["domain"],
        values["entity_type"],
        values["entity_id"],
        values["field_key"],
        _dump(values["value"]),
        values["source_type"],
        int(values.get("sort_order") or 0),
        values.get("valid_from"),
        values.get("valid_to"),
        values.get("date_precision") or "unknown",
        1 if values.get("is_current") else 0,
        float(values.get("confidence") or 0),
        values.get("status") or "proposed",
        values.get("conflict_group_id"),
        values.get("sensitivity") or "normal",
        values.get("external_use") or "prohibited",
        _dump(values.get("disclosure_policy") or {}),
        values.get("valid_until"),
        values.get("confirmed_by"),
        values.get("analysis_operation_run_id"),
        values.get("source_content_version"),
        1 if values.get("applies_to_all_resumes") else 0,
        now,
        fact_id,
    )


def _insert_question(connection, question_id: str, profile_id: str, values: dict[str, Any], now: str) -> None:
    question_values = _question_values(values, now, None)
    connection.execute(
        """
        INSERT INTO fj_profile_questions (
          id, profile_id, scope_type, scope_id, scope_key, question_key, question_text, reason, origin, answer_type,
          required_stage, priority, proposed_answer_json, final_answer_json, status,
          external_use, valid_until, source_id, job_id, writes_to_field, enabled,
          confirmed_by, analysis_operation_run_id, source_content_version,
          applies_to_all_resumes, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (question_id, profile_id, *question_values[:-2], now, now),
    )


def _question_values(values: dict[str, Any], now: str, question_id: str | None) -> tuple[object, ...]:
    scope_type = values.get("scope_type") or "general"
    scope_id = values.get("scope_id")
    return (
        scope_type,
        scope_id,
        str(scope_id) if scope_type == "resume_family" and scope_id else "general",
        values["question_key"],
        values["question_text"],
        values.get("reason") or "",
        values.get("origin") or "user",
        values.get("answer_type") or "text",
        values.get("required_stage") or "chat",
        values.get("priority") or "medium",
        _dump(values["proposed_answer"]) if values.get("proposed_answer") is not None else None,
        _dump(values["final_answer"]) if values.get("final_answer") is not None else None,
        values.get("status") or "pending",
        values.get("external_use") or "prohibited",
        values.get("valid_until"),
        values.get("source_id"),
        values.get("job_id"),
        values.get("writes_to_field"),
        1 if values.get("enabled", True) else 0,
        values.get("confirmed_by"),
        values.get("analysis_operation_run_id"),
        values.get("source_content_version"),
        1 if values.get("applies_to_all_resumes") else 0,
        now,
        question_id,
    )


def _insert_answer_variant(connection, variant_id: str, question_id: str, values: dict[str, Any], now: str) -> None:
    variant_values = _answer_variant_values(values, now, None)
    connection.execute(
        """
        INSERT INTO fj_profile_answer_variants (
          id, question_id, name, scope_type, scope_id, answer_text, internal_note,
          usage_condition, status, generated_by, based_on_job_version, external_use,
          disclosure_policy_json, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'draft', ?, ?, ?, ?, ?, ?)
        """,
        (variant_id, question_id, *variant_values[:10], now, now),
    )


def _answer_variant_values(values: dict[str, Any], now: str, variant_id: str | None) -> tuple[object, ...]:
    return (
        values["name"],
        values.get("scope_type") or "general",
        values.get("scope_id"),
        values["answer_text"],
        values.get("internal_note") or "",
        values.get("usage_condition") or "",
        values.get("generated_by") or "user",
        values.get("based_on_job_version"),
        values.get("external_use") or "prohibited",
        _dump(values.get("disclosure_policy") or {}),
        now,
        variant_id,
    )


def _insert_campaign(connection, campaign_id: str, profile_id: str, values: dict[str, Any], now: str) -> None:
    connection.execute(
        """
        INSERT INTO fj_search_campaigns (
          id, profile_id, name, target_titles_json, role_families_json, cities_json,
          districts_json, work_modes_json, salary_json, industries_json,
          company_scales_json, resume_version_id, filter_strategy_id,
          recommendation_strategy_id, delivery_strategy_id, excluded_terms_json,
          status, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)
        """,
        (campaign_id, profile_id, *_campaign_values(values), now, now),
    )


def _campaign_values(values: dict[str, Any]) -> tuple[object, ...]:
    return (
        values["name"],
        _dump(values.get("target_titles") or []),
        _dump(values.get("role_families") or []),
        _dump(values.get("cities") or []),
        _dump(values.get("districts") or []),
        _dump(values.get("work_modes") or []),
        _dump(values.get("salary") or {}),
        _dump(values.get("industries") or []),
        _dump(values.get("company_scales") or []),
        values.get("resume_version_id"),
        values.get("filter_strategy_id"),
        values.get("recommendation_strategy_id"),
        values.get("delivery_strategy_id"),
        _dump(values.get("excluded_terms") or []),
    )


def _campaign_update_values(values: dict[str, Any], now: str, campaign_id: str) -> tuple[object, ...]:
    return (*_campaign_values(values), now, campaign_id)


def _serialize_profile(row) -> dict[str, object]:
    return {
        "id": str(row["id"]),
        "display_name": str(row["display_name"]),
        "status": str(row["status"]),
        "versions": {
            "sources_version": int(row["sources_version"]),
            "facts_version": int(row["facts_version"]),
            "questions_version": int(row["questions_version"]),
            "answers_version": int(row["answers_version"]),
            "strategy_version": int(row["strategy_version"]),
            "context_version": int(row["context_version"]),
        },
        "created_at": str(row["created_at"]),
        "updated_at": str(row["updated_at"]),
    }


def _serialize_source(row) -> dict[str, object]:
    return {
        "id": str(row["id"]),
        "profile_id": str(row["profile_id"]),
        "resume_family_id": str(row["resume_family_id"]) if row["resume_family_id"] else None,
        "resume_version_id": str(row["resume_version_id"]) if row["resume_version_id"] else None,
        "source_type": str(row["source_type"]),
        "title": str(row["title"]),
        "file_path": str(row["file_path"]) if row["file_path"] else None,
        "raw_text": str(row["raw_text"] or ""),
        "recognized_text": str(row["recognized_text"] or ""),
        "editable_text": str(row["editable_text"] or ""),
        # 抽屉展示 AI 清洗结果，草稿和正式制品都允许查看。
        "normalized_markdown": str(row["normalized_markdown"] or ""),
        "recognizer_name": str(row["recognizer_name"]) if row["recognizer_name"] else None,
        "status": str(row["status"]),
        "active_analysis_run_id": str(row["active_analysis_run_id"]) if row["active_analysis_run_id"] else None,
        "enabled": bool(row["enabled"]),
        "source_version": int(row["source_version"]),
        "created_at": str(row["created_at"]),
        "updated_at": str(row["updated_at"]),
    }


def _serialize_resume_version(row) -> dict[str, object]:
    return {
        "id": str(row["id"]),
        "profile_id": str(row["profile_id"]),
        "resume_family_id": str(row["resume_family_id"]) if row["resume_family_id"] else None,
        "parent_version_id": str(row["parent_version_id"]) if row["parent_version_id"] else None,
        "name": str(row["name"]),
        "role_family": str(row["role_family"]),
        "version_type": str(row["version_type"]),
        "target_job_id": str(row["target_job_id"]) if row["target_job_id"] else None,
        "derived_reason": str(row["derived_reason"] or ""),
        "based_on_content_version": int(row["based_on_content_version"]),
        "campaign_id": str(row["campaign_id"]) if row["campaign_id"] else None,
        "source_id": str(row["source_id"]) if row["source_id"] else None,
        "content": str(row["content"] or ""),
        "fact_ids": _load_list(row["fact_ids_json"]),
        "is_default": bool(row["is_default"]),
        "current_role": str(row["current_role"]),
        "origin_type": str(row["origin_type"]),
        "derived_from_version_id": str(row["derived_from_version_id"]) if row["derived_from_version_id"] else None,
        "target_job_snapshot": _load(row["target_job_snapshot_json"], {}),
        "status": str(row["status"]),
        "content_version": int(row["content_version"]),
        "confirmed_at": str(row["confirmed_at"]) if row["confirmed_at"] else None,
        "created_at": str(row["created_at"]),
        "updated_at": str(row["updated_at"]),
        "deleted_at": str(row["deleted_at"]) if row["deleted_at"] else None,
    }


def _serialize_fact(row) -> dict[str, object]:
    return {
        "id": str(row["id"]),
        "profile_id": str(row["profile_id"]),
        "scope_type": str(row["scope_type"]),
        "scope_id": str(row["scope_id"]) if row["scope_id"] else None,
        "domain": str(row["domain"]),
        "entity_type": str(row["entity_type"]),
        "entity_id": str(row["entity_id"]),
        "field_key": str(row["field_key"]),
        "value": _load(row["value_json"], None),
        "source_type": str(row["source_type"]),
        "sort_order": int(row["sort_order"]),
        "valid_from": str(row["valid_from"]) if row["valid_from"] else None,
        "valid_to": str(row["valid_to"]) if row["valid_to"] else None,
        "date_precision": str(row["date_precision"]),
        "is_current": bool(row["is_current"]),
        "confidence": float(row["confidence"]),
        "status": str(row["status"]),
        "conflict_group_id": str(row["conflict_group_id"]) if row["conflict_group_id"] else None,
        "sensitivity": str(row["sensitivity"]),
        "external_use": str(row["external_use"]),
        "disclosure_policy": _load(row["disclosure_policy_json"], {}),
        "valid_until": str(row["valid_until"]) if row["valid_until"] else None,
        "confirmed_by": str(row["confirmed_by"]) if row["confirmed_by"] else None,
        "analysis_operation_run_id": str(row["analysis_operation_run_id"]) if row["analysis_operation_run_id"] else None,
        "source_content_version": int(row["source_content_version"]) if row["source_content_version"] else None,
        "applies_to_all_resumes": bool(row["applies_to_all_resumes"]),
        "resume_version_ids": [],
        "created_at": str(row["created_at"]),
        "updated_at": str(row["updated_at"]),
    }


def _serialize_evidence(row) -> dict[str, object]:
    return {
        "id": str(row["id"]),
        "fact_id": str(row["fact_id"]),
        "source_type": str(row["source_type"]),
        "source_id": str(row["source_id"]) if row["source_id"] else None,
        "source_excerpt": str(row["source_excerpt"] or ""),
        "extraction_method": str(row["extraction_method"]),
        "confidence": float(row["confidence"]),
        "created_at": str(row["created_at"]),
    }


def _serialize_question(row) -> dict[str, object]:
    return {
        "id": str(row["id"]),
        "profile_id": str(row["profile_id"]),
        "scope_type": str(row["scope_type"]),
        "scope_id": str(row["scope_id"]) if row["scope_id"] else None,
        "question_key": str(row["question_key"]),
        "question_text": str(row["question_text"]),
        "reason": str(row["reason"] or ""),
        "origin": str(row["origin"]),
        "answer_type": str(row["answer_type"]),
        "required_stage": str(row["required_stage"]),
        "priority": str(row["priority"]),
        "proposed_answer": _load(row["proposed_answer_json"], None),
        "final_answer": _load(row["final_answer_json"], None),
        "status": str(row["status"]),
        "external_use": str(row["external_use"]),
        "valid_until": str(row["valid_until"]) if row["valid_until"] else None,
        "source_id": str(row["source_id"]) if row["source_id"] else None,
        "job_id": str(row["job_id"]) if row["job_id"] else None,
        "writes_to_field": str(row["writes_to_field"]) if row["writes_to_field"] else None,
        "enabled": bool(row["enabled"]),
        "confirmed_by": str(row["confirmed_by"]) if row["confirmed_by"] else None,
        "analysis_operation_run_id": str(row["analysis_operation_run_id"]) if row["analysis_operation_run_id"] else None,
        "source_content_version": int(row["source_content_version"]) if row["source_content_version"] else None,
        "applies_to_all_resumes": bool(row["applies_to_all_resumes"]),
        "resume_version_ids": [],
        "created_at": str(row["created_at"]),
        "updated_at": str(row["updated_at"]),
    }


def _serialize_answer_variant(row) -> dict[str, object]:
    return {
        "id": str(row["id"]),
        "question_id": str(row["question_id"]),
        "name": str(row["name"]),
        "scope_type": str(row["scope_type"]),
        "scope_id": str(row["scope_id"]) if row["scope_id"] else None,
        "answer_text": str(row["answer_text"]),
        "internal_note": str(row["internal_note"] or ""),
        "usage_condition": str(row["usage_condition"] or ""),
        "status": str(row["status"]),
        "generated_by": str(row["generated_by"]),
        "based_on_job_version": int(row["based_on_job_version"]) if row["based_on_job_version"] else None,
        "external_use": str(row["external_use"]),
        "disclosure_policy": _load(row["disclosure_policy_json"], {}),
        "created_at": str(row["created_at"]),
        "updated_at": str(row["updated_at"]),
    }


def _serialize_analysis_run(row) -> dict[str, object]:
    return {
        "id": str(row["id"]),
        "profile_id": str(row["profile_id"]),
        "source_ids": _load_list(row["source_ids_json"]),
        "input_versions": _load(row["input_versions_json"], {}),
        "ai_model": str(row["ai_model"]) if row["ai_model"] else None,
        "prompt_version": str(row["prompt_version"]),
        "status": str(row["status"]),
        "quality": _load(row["quality_json"], {}),
        "error_category": str(row["error_category"]) if row["error_category"] else None,
        "error_message": str(row["error_message"]) if row["error_message"] else None,
        "started_at": str(row["started_at"]) if row["started_at"] else None,
        "completed_at": str(row["completed_at"]) if row["completed_at"] else None,
        "created_at": str(row["created_at"]),
        "updated_at": str(row["updated_at"]),
    }


def _serialize_analysis_item(row) -> dict[str, object]:
    return {
        "id": str(row["id"]),
        "analysis_run_id": str(row["analysis_run_id"]),
        "item_type": str(row["item_type"]),
        "source_refs": _load_list(row["source_refs_json"]),
        "payload": _load(row["payload_json"], {}),
        "status": str(row["status"]),
        "result_resource_type": str(row["result_resource_type"]) if row["result_resource_type"] else None,
        "result_resource_id": str(row["result_resource_id"]) if row["result_resource_id"] else None,
        "decision_note": str(row["decision_note"]) if row["decision_note"] else None,
        "decided_at": str(row["decided_at"]) if row["decided_at"] else None,
        "created_at": str(row["created_at"]),
        "updated_at": str(row["updated_at"]),
    }


def _serialize_campaign(db: Database, row) -> dict[str, object]:
    with db.connect() as connection:
        query_rows = connection.execute(
            "SELECT * FROM fj_search_queries WHERE campaign_id = ? ORDER BY priority DESC, created_at, id",
            (row["id"],),
        ).fetchall()
    return {
        "id": str(row["id"]),
        "profile_id": str(row["profile_id"]),
        "name": str(row["name"]),
        "target_titles": _load_list(row["target_titles_json"]),
        "role_families": _load_list(row["role_families_json"]),
        "cities": _load_list(row["cities_json"]),
        "districts": _load_list(row["districts_json"]),
        "work_modes": _load_list(row["work_modes_json"]),
        "salary": _load(row["salary_json"], {}),
        "industries": _load_list(row["industries_json"]),
        "company_scales": _load_list(row["company_scales_json"]),
        "resume_version_id": str(row["resume_version_id"]) if row["resume_version_id"] else None,
        "filter_strategy_id": str(row["filter_strategy_id"]) if row["filter_strategy_id"] else None,
        "recommendation_strategy_id": str(row["recommendation_strategy_id"]) if row["recommendation_strategy_id"] else None,
        "delivery_strategy_id": str(row["delivery_strategy_id"]) if row["delivery_strategy_id"] else None,
        "excluded_terms": _load_list(row["excluded_terms_json"]),
        "status": str(row["status"]),
        "campaign_version": int(row["campaign_version"]),
        "confirmed_at": str(row["confirmed_at"]) if row["confirmed_at"] else None,
        "queries": [_serialize_search_query(query) for query in query_rows],
        "created_at": str(row["created_at"]),
        "updated_at": str(row["updated_at"]),
    }


def _serialize_search_query(row) -> dict[str, object]:
    return {
        "id": str(row["id"]),
        "campaign_id": str(row["campaign_id"]),
        "name": str(row["name"]),
        "role_family": str(row["role_family"]),
        "platform": str(row["platform"]),
        "keyword": str(row["keyword"]),
        "cities": _load_list(row["cities_json"]),
        "work_modes": _load_list(row["work_modes_json"]),
        "positive_terms": _load_list(row["positive_terms_json"]),
        "excluded_terms": _load_list(row["excluded_terms_json"]),
        "priority": int(row["priority"]),
        "reason": str(row["reason"] or ""),
        "enabled": bool(row["enabled"]),
        "created_at": str(row["created_at"]),
        "updated_at": str(row["updated_at"]),
    }


def _record_question_revision(
    connection,
    question_id: str,
    answer: object,
    *,
    source_type: str,
    now: str,
) -> None:
    if answer is None:
        return
    answer_json = _dump(answer)
    current = connection.execute(
        """
        SELECT id, answer_json FROM fj_profile_qa_revisions
        WHERE question_id = ? AND status = 'current'
        ORDER BY revision DESC LIMIT 1
        """,
        (question_id,),
    ).fetchone()
    if current is not None and str(current["answer_json"]) == answer_json:
        return
    connection.execute(
        "UPDATE fj_profile_qa_revisions SET status = 'history' WHERE question_id = ? AND status = 'current'",
        (question_id,),
    )
    revision = int(
        connection.execute(
            "SELECT COALESCE(MAX(revision), 0) + 1 FROM fj_profile_qa_revisions WHERE question_id = ?",
            (question_id,),
        ).fetchone()[0]
    )
    connection.execute(
        """
        INSERT INTO fj_profile_qa_revisions (
          id, question_id, revision, answer_json, source_type, status, created_at
        ) VALUES (?, ?, ?, ?, ?, 'current', ?)
        """,
        (new_id(), question_id, revision, answer_json, source_type, now),
    )


def _dump(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _load(value: object, default: Any) -> Any:
    if value in (None, ""):
        return default
    try:
        return json.loads(str(value))
    except json.JSONDecodeError:
        return default


def _load_list(value: object) -> list[Any]:
    parsed = _load(value, [])
    return parsed if isinstance(parsed, list) else []


def _not_found(message: str) -> AppError:
    return AppError(status_code=404, error_category="NOT_FOUND", error_message=message)


def _version_error(message: str) -> AppError:
    return AppError(status_code=409, error_category="CONTEXT_VERSION_CHANGED", error_message=message)
