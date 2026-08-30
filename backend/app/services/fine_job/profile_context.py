from __future__ import annotations

import json
from collections import defaultdict
from typing import Any, Literal

from backend.app.db import Database
from backend.app.errors import AppError
from backend.app.services.fine_job import profile_store
from backend.app.utils import new_id, utc_now


ContextView = Literal["full", "search", "evaluation", "chat"]


def get_profile_context(
    db: Database,
    profile_id: str = profile_store.DEFAULT_PROFILE_ID,
    *,
    view: ContextView = "full",
    job_id: str | None = None,
    role_family: str | None = None,
    resume_family_id: str | None = None,
    persist_artifact: bool = True,
) -> dict[str, object]:
    if view not in {"full", "search", "evaluation", "chat"}:
        raise AppError(422, "VALIDATION_FAILED", "不支持的候选人上下文视图。")
    profile = profile_store.get_profile(db, profile_id)
    selected_family_id = _resolve_resume_family_id(db, profile_id, resume_family_id)
    facts, _ = profile_store.list_facts(db, profile_id)
    questions, _ = profile_store.list_questions(db, profile_id)
    campaigns = profile_store.list_campaigns(db, profile_id)
    resume_versions = profile_store.list_resume_versions(db, profile_id)
    facts = [fact for fact in facts if _in_resume_scope(fact, selected_family_id)]
    questions = [question for question in questions if _in_resume_scope(question, selected_family_id)]
    if selected_family_id:
        resume_versions = [
            resume for resume in resume_versions
            if resume.get("resume_family_id") == selected_family_id
        ]
    strategies = _list_applied_strategies(db, profile_id, selected_family_id)
    search_keywords = _list_resume_search_keywords(db, selected_family_id)
    normalized_documents = _list_official_normalized_documents(db, profile_id, selected_family_id)

    visible_facts = [fact for fact in facts if _fact_visible(fact, view)]
    visible_questions = [question for question in questions if _question_visible(question, view)]
    answers = _selected_answers(
        db,
        visible_questions,
        view=view,
        job_id=job_id,
        role_family=role_family,
    )
    markdown = _render_markdown(
        profile=profile,
        facts=visible_facts,
        questions=visible_questions,
        answers=answers,
        campaigns=campaigns,
        resume_versions=resume_versions,
        strategies=strategies,
        search_keywords=search_keywords,
        normalized_documents=normalized_documents,
        view=view,
    )
    artifact_version = int(profile["versions"]["context_version"])  # type: ignore[index]
    generated_at = utc_now()
    if persist_artifact:
        _save_artifact(
            db,
            profile_id=profile_id,
            view=view,
            content=markdown,
            version=artifact_version,
            context_scope_id=selected_family_id,
            created_at=generated_at,
        )
    return {
        "profile_id": profile_id,
        "resume_family_id": selected_family_id,
        "view": view,
        "versions": profile["versions"],
        "artifact_version": artifact_version,
        "markdown": markdown,
        "generated_at": generated_at,
    }


def _fact_visible(fact: dict[str, object], view: ContextView) -> bool:
    if fact["status"] != "confirmed":
        return False
    if view == "full":
        return True
    if fact["external_use"] == "prohibited" and view == "chat":
        return False
    domain = str(fact["domain"])
    if view == "search":
        return domain in {"basic", "intent", "skill", "education", "work", "project"}
    if view == "evaluation":
        return domain in {"basic", "intent", "skill", "education", "work", "project", "achievement"}
    return True


def _question_visible(question: dict[str, object], view: ContextView) -> bool:
    if not question["enabled"] or question["status"] not in {"answered", "confirmed"}:
        return False
    if view == "chat" and question["external_use"] == "prohibited":
        return False
    if view == "search":
        return question["required_stage"] == "search"
    if view == "evaluation":
        return question["required_stage"] in {"search", "application", "interview"}
    return True


def _in_resume_scope(item: dict[str, object], resume_family_id: str | None) -> bool:
    scope_type = str(item.get("scope_type") or "general")
    if scope_type == "general":
        return True
    return scope_type == "resume_family" and item.get("scope_id") == resume_family_id


def _selected_answers(
    db: Database,
    questions: list[dict[str, object]],
    *,
    view: ContextView,
    job_id: str | None,
    role_family: str | None,
) -> dict[str, dict[str, object]]:
    selected: dict[str, dict[str, object]] = {}
    for question in questions:
        variants = profile_store.list_answer_variants(db, str(question["id"]))
        confirmed = [variant for variant in variants if variant["status"] == "confirmed"]
        if view == "chat":
            confirmed = [variant for variant in confirmed if variant["external_use"] != "prohibited"]
        ranked = sorted(
            confirmed,
            key=lambda variant: _answer_rank(variant, job_id=job_id, role_family=role_family),
            reverse=True,
        )
        if ranked and _answer_rank(ranked[0], job_id=job_id, role_family=role_family) > 0:
            selected[str(question["id"])] = ranked[0]
    return selected


def _answer_rank(
    variant: dict[str, object],
    *,
    job_id: str | None,
    role_family: str | None,
) -> int:
    scope_type = variant["scope_type"]
    scope_id = variant["scope_id"]
    if scope_type == "job" and job_id and scope_id == job_id:
        return 30
    if scope_type == "role_family" and role_family and scope_id == role_family:
        return 20
    if scope_type == "general":
        return 10
    return 0


def _render_markdown(
    *,
    profile: dict[str, object],
    facts: list[dict[str, object]],
    questions: list[dict[str, object]],
    answers: dict[str, dict[str, object]],
    campaigns: list[dict[str, object]],
    resume_versions: list[dict[str, object]],
    strategies: list[dict[str, object]],
    search_keywords: list[dict[str, object]],
    normalized_documents: list[str],
    view: ContextView,
) -> str:
    sections = [f"# 候选人上下文：{profile['display_name']}", f"上下文视图：{view}"]
    if normalized_documents and view in {"full", "evaluation"}:
        sections.append("## 已确认资料 Markdown")
        sections.extend(normalized_documents)
    grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
    for fact in facts:
        grouped[str(fact["domain"])].append(fact)
    sections.append("## 已确认事实")
    if not grouped:
        sections.append("暂无已确认事实。")
    for domain, domain_facts in grouped.items():
        sections.append(f"### {domain}")
        for fact in domain_facts:
            value = json.dumps(fact["value"], ensure_ascii=False) if not isinstance(fact["value"], str) else fact["value"]
            sections.append(f"- {fact['entity_type']}.{fact['field_key']}：{value}")

    sections.append("## 已确认 QA")
    answered = False
    for question in questions:
        answer = answers.get(str(question["id"]))
        final_answer = answer["answer_text"] if answer else question.get("final_answer")
        if final_answer is None:
            continue
        answered = True
        rendered = json.dumps(final_answer, ensure_ascii=False) if not isinstance(final_answer, str) else final_answer
        sections.extend([f"### {question['question_text']}", rendered])
    if not answered:
        sections.append("暂无可用回答。")

    if view in {"full", "search", "evaluation"}:
        sections.append("## 搜索关键词")
        if search_keywords:
            for keyword in search_keywords:
                sections.append(f"- {keyword['keyword']}：{keyword['reason']}")
        else:
            sections.append("暂无当前简历组的搜索关键词。")

        # 兼容尚未迁入简历组的旧求职活动搜索词。
        if not search_keywords:
            for campaign in campaigns:
                if campaign["status"] != "active":
                    continue
                for query in campaign["queries"]:  # type: ignore[union-attr]
                    if query["enabled"]:
                        sections.append(f"- {query['keyword']}（{query['role_family'] or '未指定岗位族'}）")

    if view in {"full", "evaluation"}:
        sections.append("## 已确认简历版本")
        confirmed_resumes = [resume for resume in resume_versions if resume["status"] == "confirmed"]
        if not confirmed_resumes:
            sections.append("暂无已确认简历版本。")
        for resume in confirmed_resumes:
            sections.extend([f"### {resume['name']}", str(resume["content"])])

    if strategies and view in {"full", "search", "evaluation", "chat"}:
        sections.append("## 已确认策略")
        for strategy in strategies:
            sections.append(f"- {strategy['name']}：{json.dumps(strategy['content'], ensure_ascii=False)}")
    return "\n\n".join(sections).strip() + "\n"


def _list_applied_strategies(
    db: Database,
    profile_id: str,
    resume_family_id: str | None,
) -> list[dict[str, object]]:
    with db.connect() as connection:
        v2_rows = connection.execute(
            """
            SELECT name, content_json FROM fj_resume_strategies
            WHERE profile_id = ? AND resume_family_id = ? AND status = 'current'
            ORDER BY strategy_type, version DESC
            """,
            (profile_id, resume_family_id),
        ).fetchall() if resume_family_id else []
        rows = connection.execute(
            """
            SELECT i.payload_json FROM fj_profile_analysis_items i
            JOIN fj_profile_analysis_runs r ON r.id = i.analysis_run_id
            WHERE r.profile_id = ? AND i.item_type = 'strategy' AND i.status = 'applied'
            ORDER BY i.updated_at DESC
            """,
            (profile_id,),
        ).fetchall()
    results = []
    for row in v2_rows:
        try:
            content = json.loads(str(row["content_json"]))
        except json.JSONDecodeError:
            continue
        results.append({"name": str(row["name"]), "content": content})
    if results:
        return results
    for row in rows:
        try:
            payload = json.loads(str(row["payload_json"]))
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            results.append(payload)
    return results


def _list_resume_search_keywords(
    db: Database,
    resume_family_id: str | None,
) -> list[dict[str, object]]:
    if not resume_family_id:
        return []
    with db.connect() as connection:
        rows = connection.execute(
            """
            SELECT keyword, reason FROM fj_resume_search_keywords
            WHERE resume_family_id = ? AND status = 'current' AND enabled = 1
            ORDER BY sort_order, created_at
            """,
            (resume_family_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def _list_official_normalized_documents(
    db: Database,
    profile_id: str,
    resume_family_id: str | None,
) -> list[str]:
    with db.connect() as connection:
        if resume_family_id:
            rows = connection.execute(
                """
                SELECT a.content FROM fj_profile_artifacts a
                JOIN fj_profile_sources s ON s.id = a.source_id
                WHERE a.profile_id = ? AND s.resume_family_id = ?
                  AND a.artifact_type = 'normalized_resume_markdown' AND a.status = 'official'
                ORDER BY a.version DESC, a.created_at DESC
                """,
                (profile_id, resume_family_id),
            ).fetchall()
        else:
            rows = connection.execute(
                """
                SELECT content FROM fj_profile_artifacts
                WHERE profile_id = ? AND artifact_type = 'normalized_resume_markdown' AND status = 'official'
                ORDER BY version DESC, created_at DESC
                """,
                (profile_id,),
            ).fetchall()
    return [str(row["content"]) for row in rows if str(row["content"]).strip()]


def _resolve_resume_family_id(
    db: Database,
    profile_id: str,
    requested_id: str | None,
) -> str | None:
    with db.connect() as connection:
        if requested_id:
            row = connection.execute(
                "SELECT id FROM fj_resume_families WHERE id = ? AND profile_id = ?",
                (requested_id, profile_id),
            ).fetchone()
            if row is None:
                raise AppError(404, "RESUME_FAMILY_NOT_FOUND", "简历组不存在。")
            return str(row["id"])
        row = connection.execute(
            """
            SELECT f.id FROM fj_resume_families f
            LEFT JOIN fj_resume_versions v
              ON v.resume_family_id = f.id AND v.is_default = 1
            WHERE f.profile_id = ? AND f.status <> 'archived'
            ORDER BY CASE WHEN v.id IS NULL THEN 1 ELSE 0 END, f.updated_at DESC
            LIMIT 1
            """,
            (profile_id,),
        ).fetchone()
    return str(row["id"]) if row else None


def _save_artifact(
    db: Database,
    *,
    profile_id: str,
    view: ContextView,
    content: str,
    version: int,
    context_scope_id: str | None,
    created_at: str,
) -> None:
    artifact_type = f"candidate_context_{view}"
    with db.connect() as connection:
        existing = connection.execute(
            """
            SELECT id FROM fj_profile_artifacts
            WHERE profile_id = ? AND artifact_type = ? AND version = ?
              AND COALESCE(context_scope_id, '') = COALESCE(?, '') AND status = 'official'
            LIMIT 1
            """,
            (profile_id, artifact_type, version, context_scope_id),
        ).fetchone()
        if existing is not None:
            return
        connection.execute(
            """
            UPDATE fj_profile_artifacts SET status = 'stale'
            WHERE profile_id = ? AND artifact_type = ?
              AND COALESCE(context_scope_id, '') = COALESCE(?, '') AND status = 'official'
            """,
            (profile_id, artifact_type, context_scope_id),
        )
        connection.execute(
            """
            INSERT INTO fj_profile_artifacts (
              id, profile_id, context_scope_id, artifact_type, content, version, status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'official', ?)
            """,
            (new_id(), profile_id, context_scope_id, artifact_type, content, version, created_at),
        )
