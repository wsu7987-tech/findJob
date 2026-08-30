from __future__ import annotations

import json
from collections import defaultdict
from typing import Any

import httpx

from backend.app.config import AppConfig
from backend.app.db import Database
from backend.app.errors import AppError
from backend.app.schemas.fine_job.profile_v3 import (
    AIDerivedResumePreviewRequest,
    ContextView,
    IssueChangeSetUpdate,
    QAAnswerPreviewRequest,
    QATemplatePayload,
    ResumeLinksUpdate,
    ResumeDeleteRequest,
)
from backend.app.schemas.fine_job.profiles import (
    FactEvidencePayload,
    ProfileFactPayload,
    ProfileFactUpdate,
    ProfileQuestionPayload,
    ProfileQuestionUpdate,
)
from backend.app.services.fine_job import profile_store
from backend.app.services.reasoning.codex_exec import run_codex_exec
from backend.app.utils import new_id, utc_now


def preview_ai_derived_resume(
    db: Database,
    config: AppConfig,
    profile_id: str,
    request: AIDerivedResumePreviewRequest,
) -> dict[str, object]:
    source = _require_resume(db, profile_id, request.source_resume_version_id)
    source_content = str(source.get("content") or "").strip()
    if not source_content:
        raise AppError(422, "RESUME_CONTENT_EMPTY", "来源简历没有可用于派生的正文。")
    job_title = str(request.target_job_snapshot.get("title") or request.target_job_id or "目标岗位")
    derived_reason = request.instructions.strip() or f"针对{job_title}调整表达"
    if config.reasoning_executor == "llm" and (config.llm_provider or "").strip().lower() == "stub-llm":
        content = source_content
    else:
        prompt = (
            "你是 FineJob 简历派生编辑器。根据来源简历与目标 JD 生成 Markdown 简历草稿。"
            "保留来源简历支持的真实事实，不得新增未经支持的经历、时间、技能或业绩。"
            "可以调整顺序、压缩表达并突出与岗位相关的已有证据。只返回 JSON。\n"
            f"来源简历：\n{source_content}\n\n"
            f"目标 JD：\n{request.jd_text.strip()}\n\n"
            f"用户要求：{request.instructions.strip() or '按岗位相关性优化'}"
        )
        if config.reasoning_executor == "codex-cli":
            result = run_codex_exec(
                cli_path=config.codex_cli_path,
                prompt=prompt,
                output_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {"content": {"type": "string"}},
                    "required": ["content"],
                },
                model=config.codex_model,
                reasoning_effort=config.codex_reasoning_effort,
                timeout_seconds=config.codex_timeout_seconds,
            )
            payload = result.output
        else:
            if config.reasoning_executor != "llm" or not config.llm_model or not config.llm_api_key:
                raise AppError(400, "CONFIG_INVALID", "AI 派生需要可用的 LLM 或 Codex 执行器。")
            base_url = (config.llm_base_url or "https://api.openai.com/v1").rstrip("/")
            try:
                response = httpx.post(
                    f"{base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {config.llm_api_key}"},
                    json={
                        "model": config.llm_model,
                        "temperature": 0.1,
                        "response_format": {"type": "json_object"},
                        "messages": [
                            {"role": "system", "content": "严格基于来源简历生成派生简历 JSON。"},
                            {"role": "user", "content": prompt},
                        ],
                    },
                    timeout=config.llm_timeout_seconds,
                )
                response.raise_for_status()
                payload = json.loads(response.json()["choices"][0]["message"]["content"])
            except (httpx.HTTPError, KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
                raise AppError(502, "RESUME_DERIVE_FAILED", f"AI 派生简历失败：{exc}") from exc
        content = str(payload.get("content") or "").strip()
        if not content:
            raise AppError(502, "RESUME_DERIVE_OUTPUT_INVALID", "AI 没有返回有效的简历正文。")
    return {
        "source_resume_version_id": request.source_resume_version_id,
        "suggested_name": f"{source['name']}-{job_title}",
        "content": content,
        "derived_reason": derived_reason,
        "target_job_id": request.target_job_id,
        "target_job_snapshot": request.target_job_snapshot,
    }


def list_qa_templates(db: Database, profile_id: str) -> list[dict[str, object]]:
    profile_store.get_profile(db, profile_id)
    with db.connect() as connection:
        rows = connection.execute(
            """
            SELECT * FROM fj_profile_qa_templates
            WHERE profile_id = ?
            ORDER BY enabled DESC, sort_order, created_at, id
            """,
            (profile_id,),
        ).fetchall()
    return [_serialize_template(row) for row in rows]


def create_qa_template(
    db: Database,
    profile_id: str,
    payload: QATemplatePayload,
) -> dict[str, object]:
    profile_store.get_profile(db, profile_id)
    template_id = new_id()
    now = utc_now()
    try:
        with db.connect() as connection:
            connection.execute(
                """
                INSERT INTO fj_profile_qa_templates (
                  id, profile_id, question_key, question_text, reason, answer_type,
                  required_stage, priority, writes_to_field, enabled, sort_order,
                  source_type, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'user', ?, ?)
                """,
                (
                    template_id,
                    profile_id,
                    payload.question_key.strip(),
                    payload.question_text.strip(),
                    payload.reason.strip(),
                    payload.answer_type,
                    payload.required_stage,
                    payload.priority,
                    payload.writes_to_field,
                    1 if payload.enabled else 0,
                    payload.sort_order,
                    now,
                    now,
                ),
            )
    except Exception as exc:
        if "UNIQUE constraint failed" in str(exc):
            raise AppError(409, "QA_TEMPLATE_EXISTS", "相同问题标识的 QA 模板已经存在。") from exc
        raise
    profile_store.bump_versions(db, profile_id, "questions_version", "context_version")
    return get_qa_template(db, template_id)


def get_qa_template(db: Database, template_id: str) -> dict[str, object]:
    with db.connect() as connection:
        row = connection.execute(
            "SELECT * FROM fj_profile_qa_templates WHERE id = ?", (template_id,)
        ).fetchone()
    if row is None:
        raise AppError(404, "QA_TEMPLATE_NOT_FOUND", "QA 模板不存在。")
    return _serialize_template(row)


def update_qa_template(
    db: Database,
    template_id: str,
    payload: QATemplatePayload,
) -> dict[str, object]:
    current = get_qa_template(db, template_id)
    now = utc_now()
    try:
        with db.connect() as connection:
            connection.execute(
                """
                UPDATE fj_profile_qa_templates SET question_key = ?, question_text = ?,
                  reason = ?, answer_type = ?, required_stage = ?, priority = ?,
                  writes_to_field = ?, enabled = ?, sort_order = ?, source_type = 'user',
                  updated_at = ?
                WHERE id = ?
                """,
                (
                    payload.question_key.strip(),
                    payload.question_text.strip(),
                    payload.reason.strip(),
                    payload.answer_type,
                    payload.required_stage,
                    payload.priority,
                    payload.writes_to_field,
                    1 if payload.enabled else 0,
                    payload.sort_order,
                    now,
                    template_id,
                ),
            )
    except Exception as exc:
        if "UNIQUE constraint failed" in str(exc):
            raise AppError(409, "QA_TEMPLATE_EXISTS", "相同问题标识的 QA 模板已经存在。") from exc
        raise
    profile_store.bump_versions(
        db, str(current["profile_id"]), "questions_version", "context_version"
    )
    return get_qa_template(db, template_id)


def delete_qa_template(db: Database, template_id: str) -> None:
    current = get_qa_template(db, template_id)
    with db.connect() as connection:
        connection.execute("DELETE FROM fj_profile_qa_templates WHERE id = ?", (template_id,))
    profile_store.bump_versions(
        db, str(current["profile_id"]), "questions_version", "context_version"
    )


def preview_qa_answer(
    db: Database,
    config: AppConfig,
    profile_id: str,
    question_id: str,
    request: QAAnswerPreviewRequest,
) -> dict[str, object]:
    question = profile_store.get_question(db, question_id)
    if question["profile_id"] != profile_id:
        raise AppError(404, "PROFILE_QUESTION_NOT_FOUND", "QA 不存在。")
    _require_resume(db, profile_id, request.resume_version_id)
    if (
        not question.get("applies_to_all_resumes")
        and request.resume_version_id not in question.get("resume_version_ids", [])
    ):
        raise AppError(422, "QA_RESUME_NOT_LINKED", "该 QA 没有关联所选简历。")
    current_answer = question.get("final_answer")
    if config.reasoning_executor == "llm" and (config.llm_provider or "").strip().lower() == "stub-llm":
        answer = current_answer if isinstance(current_answer, str) else _dump(current_answer)
    else:
        context = _render_context(db, profile_id, request.resume_version_id, "full")
        prompt = (
            "你是 FineJob 规范 QA 编辑器。依据候选人上下文重新整理当前问题的规范答案。"
            "只使用上下文支持的信息，不得补充未经确认的事实。只返回 JSON。\n"
            f"问题：{question['question_text']}\n"
            f"当前答案：{_dump(current_answer)}\n"
            f"用户要求：{request.instructions.strip() or '使答案准确、简洁、适合后续按场景改写'}\n"
            f"候选人上下文：\n{context}"
        )
        if config.reasoning_executor == "codex-cli":
            result = run_codex_exec(
                cli_path=config.codex_cli_path,
                prompt=prompt,
                output_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {"answer": {"type": "string"}},
                    "required": ["answer"],
                },
                model=config.codex_model,
                reasoning_effort=config.codex_reasoning_effort,
                timeout_seconds=config.codex_timeout_seconds,
            )
            payload = result.output
        else:
            if config.reasoning_executor != "llm" or not config.llm_model or not config.llm_api_key:
                raise AppError(400, "CONFIG_INVALID", "AI 整理 QA 需要可用的 LLM 或 Codex 执行器。")
            base_url = (config.llm_base_url or "https://api.openai.com/v1").rstrip("/")
            try:
                response = httpx.post(
                    f"{base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {config.llm_api_key}"},
                    json={
                        "model": config.llm_model,
                        "temperature": 0.1,
                        "response_format": {"type": "json_object"},
                        "messages": [
                            {"role": "system", "content": "严格基于已确认资料整理规范 QA。"},
                            {"role": "user", "content": prompt},
                        ],
                    },
                    timeout=config.llm_timeout_seconds,
                )
                response.raise_for_status()
                payload = json.loads(response.json()["choices"][0]["message"]["content"])
            except (httpx.HTTPError, KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
                raise AppError(502, "QA_ANSWER_PREVIEW_FAILED", f"AI 整理 QA 失败：{exc}") from exc
        answer = str(payload.get("answer") or "").strip()
    if not answer.strip():
        raise AppError(422, "QA_ANSWER_EMPTY", "当前 QA 没有可整理的规范答案。")
    return {
        "question_id": question_id,
        "resume_version_id": request.resume_version_id,
        "answer": answer,
    }


def update_fact_resume_links(
    db: Database,
    profile_id: str,
    fact_id: str,
    payload: ResumeLinksUpdate,
) -> dict[str, object]:
    current = profile_store.get_fact(db, fact_id)
    if current["profile_id"] != profile_id:
        raise AppError(404, "PROFILE_FACT_NOT_FOUND", "候选人事实不存在。")
    values = {
        key: value
        for key, value in current.items()
        if key not in {"id", "profile_id", "created_at", "updated_at"}
    }
    values["resume_version_ids"] = payload.resume_version_ids
    values["applies_to_all_resumes"] = payload.applies_to_all_resumes
    values["expected_facts_version"] = profile_store.version_vector(db, profile_id)["facts_version"]
    return profile_store.update_fact(db, fact_id, ProfileFactUpdate(**values))


def update_question_resume_links(
    db: Database,
    profile_id: str,
    question_id: str,
    payload: ResumeLinksUpdate,
) -> dict[str, object]:
    current = profile_store.get_question(db, question_id)
    if current["profile_id"] != profile_id:
        raise AppError(404, "PROFILE_QUESTION_NOT_FOUND", "QA 不存在。")
    values = {
        key: value
        for key, value in current.items()
        if key not in {"id", "profile_id", "created_at", "updated_at"}
    }
    values["resume_version_ids"] = payload.resume_version_ids
    values["applies_to_all_resumes"] = payload.applies_to_all_resumes
    values["expected_questions_version"] = profile_store.version_vector(db, profile_id)["questions_version"]
    return profile_store.update_question(db, question_id, ProfileQuestionUpdate(**values))


def list_issues(
    db: Database,
    profile_id: str,
    *,
    status: str | None = None,
) -> list[dict[str, object]]:
    profile_store.get_profile(db, profile_id)
    sql = "SELECT * FROM fj_profile_issues_v3 WHERE profile_id = ?"
    params: list[object] = [profile_id]
    if status:
        sql += " AND status = ?"
        params.append(status)
    sql += " ORDER BY CASE status WHEN 'pending' THEN 0 WHEN 'awaiting_confirmation' THEN 1 ELSE 2 END, updated_at DESC"
    with db.connect() as connection:
        rows = connection.execute(sql, params).fetchall()
    return [_serialize_issue(db, row) for row in rows]


def get_issue(db: Database, issue_id: str) -> dict[str, object]:
    with db.connect() as connection:
        row = connection.execute(
            "SELECT * FROM fj_profile_issues_v3 WHERE id = ?", (issue_id,)
        ).fetchone()
    if row is None:
        raise AppError(404, "PROFILE_ISSUE_NOT_FOUND", "待处理事项不存在。")
    return _serialize_issue(db, row)


def answer_and_organize_issue(
    db: Database,
    config: AppConfig,
    issue_id: str,
    answer_text: str,
) -> dict[str, object]:
    issue = get_issue(db, issue_id)
    if issue["status"] in {"resolved", "dismissed"}:
        raise AppError(409, "PROFILE_ISSUE_FINISHED", "当前待处理事项已经结束。")
    answer_id = new_id()
    now = utc_now()
    with db.connect() as connection:
        connection.execute(
            "INSERT INTO fj_profile_issue_answers (id, issue_id, answer_text, created_at) VALUES (?, ?, ?, ?)",
            (answer_id, issue_id, answer_text.strip(), now),
        )
        connection.execute(
            "UPDATE fj_profile_issues_v3 SET status = 'organizing', updated_at = ? WHERE id = ?",
            (now, issue_id),
        )
    try:
        changes = _organize_issue_changes(config, issue, answer_text.strip())
    except Exception:
        with db.connect() as connection:
            connection.execute(
                "UPDATE fj_profile_issues_v3 SET status = 'pending', updated_at = ? WHERE id = ?",
                (utc_now(), issue_id),
            )
        raise
    change_set_id = new_id()
    now = utc_now()
    with db.connect() as connection:
        connection.execute(
            """
            INSERT INTO fj_profile_issue_change_sets (
              id, issue_id, answer_id, changes_json, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, 'draft', ?, ?)
            """,
            (change_set_id, issue_id, answer_id, _dump(changes), now, now),
        )
        connection.execute(
            """
            UPDATE fj_profile_issues_v3
            SET status = 'awaiting_confirmation', updated_at = ? WHERE id = ?
            """,
            (now, issue_id),
        )
    return get_issue(db, issue_id)


def update_issue_change_set(
    db: Database,
    change_set_id: str,
    payload: IssueChangeSetUpdate,
) -> dict[str, object]:
    with db.connect() as connection:
        row = connection.execute(
            "SELECT * FROM fj_profile_issue_change_sets WHERE id = ?", (change_set_id,)
        ).fetchone()
        if row is None:
            raise AppError(404, "ISSUE_CHANGE_SET_NOT_FOUND", "待应用变更不存在。")
        if row["status"] != "draft":
            raise AppError(409, "ISSUE_CHANGE_SET_FINISHED", "待应用变更已经结束。")
        connection.execute(
            "UPDATE fj_profile_issue_change_sets SET changes_json = ?, updated_at = ? WHERE id = ?",
            (_dump(payload.changes), utc_now(), change_set_id),
        )
        issue_id = str(row["issue_id"])
    return get_issue(db, issue_id)


def apply_issue_change_set(db: Database, change_set_id: str) -> dict[str, object]:
    with db.connect() as connection:
        row = connection.execute(
            """
            SELECT c.*, i.profile_id, i.resume_version_id
            FROM fj_profile_issue_change_sets c
            JOIN fj_profile_issues_v3 i ON i.id = c.issue_id
            WHERE c.id = ?
            """,
            (change_set_id,),
        ).fetchone()
    if row is None:
        raise AppError(404, "ISSUE_CHANGE_SET_NOT_FOUND", "待应用变更不存在。")
    if row["status"] != "draft":
        raise AppError(409, "ISSUE_CHANGE_SET_FINISHED", "待应用变更已经结束。")
    changes = _load(row["changes_json"], {})
    if not isinstance(changes, dict):
        raise AppError(422, "ISSUE_CHANGE_SET_INVALID", "待应用变更格式不正确。")
    _apply_fact_changes(
        db,
        str(row["profile_id"]),
        str(row["resume_version_id"] or ""),
        list(changes.get("facts") or []),
        str(row["answer_id"]),
    )
    _apply_question_changes(
        db,
        str(row["profile_id"]),
        str(row["resume_version_id"] or ""),
        list(changes.get("questions") or []),
    )
    now = utc_now()
    with db.connect() as connection:
        connection.execute(
            "UPDATE fj_profile_issue_change_sets SET status = 'applied', applied_at = ?, updated_at = ? WHERE id = ?",
            (now, now, change_set_id),
        )
        connection.execute(
            "UPDATE fj_profile_issues_v3 SET status = 'resolved', resolved_at = ?, updated_at = ? WHERE id = ?",
            (now, now, row["issue_id"]),
        )
    return get_issue(db, str(row["issue_id"]))


def update_issue_status(db: Database, issue_id: str, status: str) -> dict[str, object]:
    issue = get_issue(db, issue_id)
    now = utc_now()
    with db.connect() as connection:
        connection.execute(
            "UPDATE fj_profile_issues_v3 SET status = ?, resolved_at = NULL, updated_at = ? WHERE id = ?",
            (status, now, issue_id),
        )
    return get_issue(db, issue_id)


def get_context_head(
    db: Database,
    profile_id: str,
    resume_version_id: str,
    view: ContextView,
    *,
    include_history: bool = True,
) -> dict[str, object]:
    resume = _require_resume(db, profile_id, resume_version_id)
    dependencies = _context_dependencies(db, profile_id, resume_version_id)
    with db.connect() as connection:
        head = connection.execute(
            """
            SELECT * FROM fj_profile_context_heads
            WHERE profile_id = ? AND resume_version_id = ? AND view_type = ?
            """,
            (profile_id, resume_version_id, view),
        ).fetchone()
        if head is None:
            return {
                "id": "",
                "profile_id": profile_id,
                "resume_version_id": resume_version_id,
                "view": view,
                "stale": False,
                "dependency_versions": dependencies,
                "current_revision": None,
                "draft_revision": None,
                "history": [],
                "created_at": str(resume["created_at"]),
                "updated_at": str(resume["updated_at"]),
            }
        head_id = str(head["id"])
        revisions = connection.execute(
            "SELECT * FROM fj_profile_context_revisions WHERE head_id = ? ORDER BY revision DESC",
            (head_id,),
        ).fetchall()
    stored_dependencies = _load(head["dependency_versions_json"], {})
    stale = head["current_revision_id"] is not None and stored_dependencies != dependencies
    current = next(
        (row for row in revisions if str(row["id"]) == str(head["current_revision_id"])), None
    )
    draft = next((row for row in revisions if row["status"] == "draft"), None)
    history = [row for row in revisions if row["status"] == "history"] if include_history else []
    return {
        "id": head_id,
        "profile_id": profile_id,
        "resume_version_id": resume_version_id,
        "view": view,
        "stale": stale,
        "dependency_versions": dependencies,
        "current_revision": _serialize_context_revision(current) if current else None,
        "draft_revision": _serialize_context_revision(draft) if draft else None,
        "history": [_serialize_context_revision(row) for row in history],
        "created_at": str(head["created_at"]),
        "updated_at": str(head["updated_at"]),
    }


def generate_context_draft(
    db: Database,
    profile_id: str,
    resume_version_id: str,
    view: ContextView,
) -> dict[str, object]:
    content = _render_context(db, profile_id, resume_version_id, view)
    _save_context_revision(
        db,
        profile_id,
        resume_version_id,
        view,
        content,
        source_type="generated",
        make_current=False,
    )
    return get_context_head(db, profile_id, resume_version_id, view)


def update_context_draft(
    db: Database,
    profile_id: str,
    resume_version_id: str,
    view: ContextView,
    content: str,
) -> dict[str, object]:
    head = get_context_head(db, profile_id, resume_version_id, view, include_history=False)
    draft = head.get("draft_revision")
    now = utc_now()
    if draft:
        with db.connect() as connection:
            connection.execute(
                """
                UPDATE fj_profile_context_revisions
                SET content = ?, source_type = 'user_edit', updated_at = ?
                WHERE id = ? AND status = 'draft'
                """,
                (content.strip(), now, draft["id"]),
            )
    else:
        _save_context_revision(
            db,
            profile_id,
            resume_version_id,
            view,
            content.strip(),
            source_type="user_edit",
            make_current=False,
        )
    return get_context_head(db, profile_id, resume_version_id, view)


def save_context(
    db: Database,
    profile_id: str,
    resume_version_id: str,
    view: ContextView,
    content: str,
) -> dict[str, object]:
    _save_context_revision(
        db,
        profile_id,
        resume_version_id,
        view,
        content.strip(),
        source_type="user_edit",
        make_current=True,
    )
    return get_context_head(db, profile_id, resume_version_id, view)


def restore_context_revision(
    db: Database,
    profile_id: str,
    resume_version_id: str,
    view: ContextView,
    revision_id: str,
) -> dict[str, object]:
    head = get_context_head(db, profile_id, resume_version_id, view, include_history=False)
    with db.connect() as connection:
        row = connection.execute(
            "SELECT content FROM fj_profile_context_revisions WHERE id = ? AND head_id = ?",
            (revision_id, head["id"]),
        ).fetchone()
    if row is None:
        raise AppError(404, "CONTEXT_REVISION_NOT_FOUND", "上下文历史版本不存在。")
    _save_context_revision(
        db,
        profile_id,
        resume_version_id,
        view,
        str(row["content"]),
        source_type="restored",
        make_current=True,
    )
    return get_context_head(db, profile_id, resume_version_id, view)


def delete_context_draft(
    db: Database,
    profile_id: str,
    resume_version_id: str,
    view: ContextView,
    revision_id: str,
) -> dict[str, object]:
    head = get_context_head(db, profile_id, resume_version_id, view, include_history=False)
    draft = head.get("draft_revision")
    if not draft or draft["id"] != revision_id:
        raise AppError(404, "CONTEXT_DRAFT_NOT_FOUND", "上下文草稿不存在。")
    with db.connect() as connection:
        connection.execute(
            "DELETE FROM fj_profile_context_revisions WHERE id = ? AND status = 'draft'",
            (revision_id,),
        )
    return get_context_head(db, profile_id, resume_version_id, view)


def resolve_task_context(
    db: Database,
    profile_id: str,
    resume_version_id: str,
    view: ContextView,
    stale_action: str | None,
) -> dict[str, object]:
    head = get_context_head(db, profile_id, resume_version_id, view)
    if head["current_revision"] is None:
        content = _render_context(db, profile_id, resume_version_id, view)
        _save_context_revision(
            db,
            profile_id,
            resume_version_id,
            view,
            content,
            source_type="generated",
            make_current=True,
        )
        return {
            "status": "ready",
            "context": get_context_head(db, profile_id, resume_version_id, view),
        }
    if not head["stale"]:
        return {"status": "ready", "context": head}
    if stale_action is None:
        return {"status": "confirmation_required", "context": head}
    if stale_action == "cancel":
        return {"status": "cancelled", "context": head}
    if stale_action == "regenerate":
        content = _render_context(db, profile_id, resume_version_id, view)
        _save_context_revision(
            db,
            profile_id,
            resume_version_id,
            view,
            content,
            source_type="generated",
            make_current=True,
        )
        head = get_context_head(db, profile_id, resume_version_id, view)
    return {"status": "ready", "context": head}


def resume_delete_impact(db: Database, resume_version_id: str) -> dict[str, object]:
    resume = profile_store.get_resume_version(db, resume_version_id)
    family_id = str(resume.get("resume_family_id") or "")
    with db.connect() as connection:
        derived = connection.execute(
            """
            SELECT id, name, current_role, origin_type FROM fj_resume_versions
            WHERE resume_family_id = ? AND deleted_at IS NULL AND id <> ?
            ORDER BY current_role, created_at, id
            """,
            (family_id, resume_version_id),
        ).fetchall() if family_id else []
        fact_rows = connection.execute(
            """
            SELECT f.id, f.applies_to_all_resumes, COUNT(all_links.resume_version_id) AS link_count
            FROM fj_fact_resume_links target
            JOIN fj_profile_facts f ON f.id = target.fact_id
            LEFT JOIN fj_fact_resume_links all_links ON all_links.fact_id = f.id
            WHERE target.resume_version_id = ?
            GROUP BY f.id
            """,
            (resume_version_id,),
        ).fetchall()
        question_rows = connection.execute(
            """
            SELECT q.id, q.applies_to_all_resumes, COUNT(all_links.resume_version_id) AS link_count
            FROM fj_question_resume_links target
            JOIN fj_profile_questions q ON q.id = target.question_id
            LEFT JOIN fj_question_resume_links all_links ON all_links.question_id = q.id
            WHERE target.resume_version_id = ?
            GROUP BY q.id
            """,
            (resume_version_id,),
        ).fetchall()
    exclusive_facts, shared_facts = _split_link_impact(fact_rows)
    exclusive_questions, shared_questions = _split_link_impact(question_rows)
    return {
        "resume_version_id": resume_version_id,
        "resume_family_id": family_id or None,
        "is_base": resume.get("current_role") == "base",
        "source_id": resume.get("source_id"),
        "derived_versions": [dict(row) for row in derived],
        "exclusive_fact_ids": exclusive_facts,
        "exclusive_question_ids": exclusive_questions,
        "shared_fact_ids": shared_facts,
        "shared_question_ids": shared_questions,
    }


def delete_resume_version(
    db: Database,
    resume_version_id: str,
    payload: ResumeDeleteRequest,
) -> dict[str, object]:
    resume = profile_store.get_resume_version(db, resume_version_id)
    profile_id = str(resume["profile_id"])
    family_id = str(resume.get("resume_family_id") or "")
    with db.connect() as connection:
        active_rows = connection.execute(
            "SELECT id, current_role, source_id FROM fj_resume_versions WHERE resume_family_id = ? AND deleted_at IS NULL",
            (family_id,),
        ).fetchall() if family_id else []
    active_ids = [str(row["id"]) for row in active_rows]
    is_base = resume.get("current_role") == "base"
    if payload.action == "delete_family":
        targets = active_ids
    else:
        targets = [resume_version_id]
    promoted_id: str | None = None
    if is_base and len(active_ids) > 1:
        if payload.action == "delete_version":
            raise AppError(409, "BASE_RESUME_HAS_DERIVED", "基础简历仍有派生版本，请先选择接任基础简历或删除整个简历组。")
        if payload.action == "promote_then_delete":
            promoted_id = str(payload.promote_resume_version_id or "")
            if promoted_id not in active_ids or promoted_id == resume_version_id:
                raise AppError(422, "PROMOTE_RESUME_INVALID", "请选择同一简历组中的派生简历接任基础简历。")
            profile_store.set_resume_version_as_base(db, promoted_id)
    elif payload.action == "promote_then_delete":
        raise AppError(422, "PROMOTE_RESUME_NOT_REQUIRED", "当前简历无需先设置接任基础简历。")

    link_impact = _resume_targets_profile_data(db, targets)
    pending_issue_ids: list[str] = []
    deleted_fact_ids: list[str] = []
    deleted_question_ids: list[str] = []
    if payload.profile_data_action == "move_to_pending":
        for item_type, rows in (
            ("fact", link_impact["exclusive_facts"]),
            ("question", link_impact["exclusive_questions"]),
        ):
            for row in rows:
                pending_issue_ids.append(
                    _create_orphan_issue(
                        db,
                        profile_id,
                        promoted_id,
                        item_type,
                        dict(row),
                    )
                )
    with db.connect() as connection:
        connection.executemany(
            "DELETE FROM fj_fact_resume_links WHERE fact_id = ? AND resume_version_id IN ({})".format(
                ",".join("?" for _ in targets)
            ),
            [(row["id"], *targets) for row in link_impact["all_facts"]],
        ) if targets else None
        connection.executemany(
            "DELETE FROM fj_question_resume_links WHERE question_id = ? AND resume_version_id IN ({})".format(
                ",".join("?" for _ in targets)
            ),
            [(row["id"], *targets) for row in link_impact["all_questions"]],
        ) if targets else None
        for row in link_impact["exclusive_facts"]:
            connection.execute("DELETE FROM fj_profile_facts WHERE id = ?", (row["id"],))
            deleted_fact_ids.append(str(row["id"]))
        for row in link_impact["exclusive_questions"]:
            connection.execute("DELETE FROM fj_profile_questions WHERE id = ?", (row["id"],))
            deleted_question_ids.append(str(row["id"]))
        now = utc_now()
        placeholders = ",".join("?" for _ in targets)
        connection.execute(
            f"UPDATE fj_resume_versions SET deleted_at = ?, status = 'archived', is_default = 0, updated_at = ? WHERE id IN ({placeholders})",
            (now, now, *targets),
        )
        source_ids = [
            str(row["source_id"])
            for row in active_rows
            if str(row["id"]) in targets and row["source_id"]
        ]
        for source_id in source_ids:
            remaining = connection.execute(
                "SELECT 1 FROM fj_resume_versions WHERE source_id = ? AND deleted_at IS NULL LIMIT 1",
                (source_id,),
            ).fetchone()
            if remaining is None:
                connection.execute(
                    "UPDATE fj_profile_sources SET enabled = 0, status = 'archived', updated_at = ? WHERE id = ?",
                    (now, source_id),
                )
        if family_id and set(targets) == set(active_ids):
            connection.execute(
                """
                UPDATE fj_resume_families SET status = 'archived', base_version_id = NULL,
                  default_delivery_version_id = NULL, updated_at = ? WHERE id = ?
                """,
                (now, family_id),
            )
    profile_store.bump_versions(
        db,
        profile_id,
        "sources_version",
        "facts_version",
        "questions_version",
        "context_version",
    )
    return {
        "deleted_resume_version_ids": targets,
        "deleted_source_ids": source_ids,
        "deleted_fact_ids": deleted_fact_ids,
        "deleted_question_ids": deleted_question_ids,
        "pending_issue_ids": pending_issue_ids,
        "promoted_resume_version_id": promoted_id,
    }


def _organize_issue_changes(
    config: AppConfig,
    issue: dict[str, object],
    answer_text: str,
) -> dict[str, Any]:
    if config.reasoning_executor == "llm" and (config.llm_provider or "").strip().lower() == "stub-llm":
        return _stub_issue_changes(issue, answer_text)
    prompt = (
        "你是 FineJob 求职资料整理器。把用户对待处理事项的回答整理为待确认变更，不得补充回答未支持的事实。\n"
        "只返回 JSON：changes_json 是一个 JSON 字符串，字符串内容结构为 "
        '{"facts":[{"action":"create|update|delete","id":null,"data":{}}],'
        '"questions":[{"action":"create|update|delete","id":null,"data":{}}]}。'
        "create 的事实 data 使用正式事实字段；create 的问题 data 使用正式 QA 字段。更新允许只返回变化字段。\n"
        f"待处理事项：{json.dumps(issue, ensure_ascii=False, default=str)}\n"
        f"用户原始回答：{answer_text}"
    )
    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "changes_json": {"type": "string"},
            "summary": {"type": "string"},
        },
        "required": ["changes_json", "summary"],
    }
    if config.reasoning_executor == "codex-cli":
        result = run_codex_exec(
            cli_path=config.codex_cli_path,
            prompt=prompt,
            output_schema=schema,
            model=config.codex_model,
            reasoning_effort=config.codex_reasoning_effort,
            timeout_seconds=config.codex_timeout_seconds,
        )
        payload = result.output
    else:
        if config.reasoning_executor != "llm" or not config.llm_model or not config.llm_api_key:
            raise AppError(400, "CONFIG_INVALID", "整理回答需要可用的 LLM 或 Codex 执行器。")
        base_url = (config.llm_base_url or "https://api.openai.com/v1").rstrip("/")
        try:
            response = httpx.post(
                f"{base_url}/chat/completions",
                headers={"Authorization": f"Bearer {config.llm_api_key}"},
                json={
                    "model": config.llm_model,
                    "temperature": 0.1,
                    "response_format": {"type": "json_object"},
                    "messages": [
                        {"role": "system", "content": "严格整理用户回答并输出 JSON。"},
                        {"role": "user", "content": prompt},
                    ],
                },
                timeout=config.llm_timeout_seconds,
            )
            response.raise_for_status()
            payload = json.loads(response.json()["choices"][0]["message"]["content"])
        except (httpx.HTTPError, KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise AppError(502, "ISSUE_ORGANIZE_FAILED", f"回答整理失败：{exc}") from exc
    try:
        changes = json.loads(str(payload["changes_json"]))
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise AppError(502, "ISSUE_ORGANIZE_OUTPUT_INVALID", "回答整理结果格式不正确。") from exc
    if not isinstance(changes, dict):
        raise AppError(502, "ISSUE_ORGANIZE_OUTPUT_INVALID", "回答整理结果格式不正确。")
    return {"facts": list(changes.get("facts") or []), "questions": list(changes.get("questions") or [])}


def _stub_issue_changes(issue: dict[str, object], answer_text: str) -> dict[str, Any]:
    payload = dict(issue.get("payload") or {})
    if issue["issue_type"] in {"missing_qa", "qa_conflict", "missing_information"} and payload.get("question_key"):
        return {
            "facts": [],
            "questions": [
                {
                    "action": "create",
                    "id": None,
                    "data": {
                        "question_key": payload["question_key"],
                        "question_text": issue["title"],
                        "reason": issue.get("description") or "",
                        "final_answer": answer_text,
                        "status": "confirmed",
                        "origin": "user",
                    },
                }
            ],
        }
    fact_data = dict(payload.get("fact") or payload)
    fact_data["value"] = answer_text
    return {"facts": [{"action": "create", "id": None, "data": fact_data}], "questions": []}


def _apply_fact_changes(
    db: Database,
    profile_id: str,
    resume_version_id: str,
    changes: list[object],
    answer_id: str,
) -> None:
    for raw in changes:
        if not isinstance(raw, dict):
            raise AppError(422, "ISSUE_CHANGE_SET_INVALID", "事实变更格式不正确。")
        action = str(raw.get("action") or "")
        fact_id = str(raw.get("id") or "")
        data = dict(raw.get("data") or {})
        if action == "delete":
            profile_store.delete_fact(db, fact_id)
            continue
        if action == "update":
            current = profile_store.get_fact(db, fact_id)
            merged = {key: value for key, value in current.items() if key not in {"id", "profile_id", "created_at", "updated_at"}}
            merged.update(data)
            merged["expected_facts_version"] = profile_store.version_vector(db, profile_id)["facts_version"]
            profile_store.update_fact(db, fact_id, ProfileFactUpdate(**merged))
            continue
        if action != "create":
            raise AppError(422, "ISSUE_CHANGE_SET_INVALID", "不支持的事实变更动作。")
        data.setdefault("domain", "profile")
        data.setdefault("entity_type", "candidate")
        data.setdefault("entity_id", profile_id)
        data.setdefault("field_key", "additional_information")
        data.setdefault("source_type", "user_answer")
        data.setdefault("status", "confirmed")
        data.setdefault("confirmed_by", "user")
        data.setdefault("resume_version_ids", [resume_version_id] if resume_version_id else [])
        created = profile_store.create_fact(db, profile_id, ProfileFactPayload(**data))
        profile_store.create_evidence(
            db,
            str(created["id"]),
            FactEvidencePayload(
                source_type="question_answer",
                source_id=answer_id,
                source_excerpt=str(data.get("value") or ""),
                extraction_method="ai_organized_user_answer",
                confidence=1,
            ),
        )


def _apply_question_changes(
    db: Database,
    profile_id: str,
    resume_version_id: str,
    changes: list[object],
) -> None:
    for raw in changes:
        if not isinstance(raw, dict):
            raise AppError(422, "ISSUE_CHANGE_SET_INVALID", "QA 变更格式不正确。")
        action = str(raw.get("action") or "")
        question_id = str(raw.get("id") or "")
        data = dict(raw.get("data") or {})
        if action == "delete":
            profile_store.delete_question(db, question_id)
            continue
        if action == "update":
            current = profile_store.get_question(db, question_id)
            merged = {key: value for key, value in current.items() if key not in {"id", "profile_id", "created_at", "updated_at"}}
            merged.update(data)
            merged["expected_questions_version"] = profile_store.version_vector(db, profile_id)["questions_version"]
            profile_store.update_question(db, question_id, ProfileQuestionUpdate(**merged))
            continue
        if action != "create":
            raise AppError(422, "ISSUE_CHANGE_SET_INVALID", "不支持的 QA 变更动作。")
        data.setdefault("question_key", f"user_answer_{new_id()}")
        data.setdefault("question_text", "补充信息")
        data.setdefault("origin", "user")
        data.setdefault("status", "confirmed")
        data.setdefault("confirmed_by", "user")
        data.setdefault("resume_version_ids", [resume_version_id] if resume_version_id else [])
        profile_store.create_question(db, profile_id, ProfileQuestionPayload(**data))


def _ensure_context_head(
    db: Database,
    profile_id: str,
    resume_version_id: str,
    view: ContextView,
) -> str:
    with db.connect() as connection:
        row = connection.execute(
            """
            SELECT id FROM fj_profile_context_heads
            WHERE profile_id = ? AND resume_version_id = ? AND view_type = ?
            """,
            (profile_id, resume_version_id, view),
        ).fetchone()
        if row is not None:
            return str(row["id"])
        head_id = new_id()
        now = utc_now()
        connection.execute(
            """
            INSERT INTO fj_profile_context_heads (
              id, profile_id, resume_version_id, view_type, dependency_versions_json,
              stale, created_at, updated_at
            ) VALUES (?, ?, ?, ?, '{}', 0, ?, ?)
            """,
            (head_id, profile_id, resume_version_id, view, now, now),
        )
    return head_id


def _save_context_revision(
    db: Database,
    profile_id: str,
    resume_version_id: str,
    view: ContextView,
    content: str,
    *,
    source_type: str,
    make_current: bool,
) -> str:
    if not content.strip():
        raise AppError(422, "CONTEXT_EMPTY", "上下文内容不能为空。")
    head_id = _ensure_context_head(db, profile_id, resume_version_id, view)
    dependencies = _context_dependencies(db, profile_id, resume_version_id)
    revision_id = new_id()
    now = utc_now()
    with db.connect() as connection:
        revision = int(
            connection.execute(
                "SELECT COALESCE(MAX(revision), 0) + 1 FROM fj_profile_context_revisions WHERE head_id = ?",
                (head_id,),
            ).fetchone()[0]
        )
        if make_current:
            connection.execute(
                "UPDATE fj_profile_context_revisions SET status = 'history', updated_at = ? WHERE head_id = ? AND status = 'current'",
                (now, head_id),
            )
        else:
            connection.execute(
                "DELETE FROM fj_profile_context_revisions WHERE head_id = ? AND status = 'draft'",
                (head_id,),
            )
        connection.execute(
            """
            INSERT INTO fj_profile_context_revisions (
              id, head_id, revision, content, source_type, status,
              dependency_versions_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                revision_id,
                head_id,
                revision,
                content.strip(),
                source_type,
                "current" if make_current else "draft",
                _dump(dependencies),
                now,
                now,
            ),
        )
        if make_current:
            connection.execute(
                "DELETE FROM fj_profile_context_revisions WHERE head_id = ? AND status = 'draft'",
                (head_id,),
            )
            connection.execute(
                """
                UPDATE fj_profile_context_heads SET current_revision_id = ?,
                  dependency_versions_json = ?, stale = 0, updated_at = ? WHERE id = ?
                """,
                (revision_id, _dump(dependencies), now, head_id),
            )
    return revision_id


def _context_dependencies(
    db: Database,
    profile_id: str,
    resume_version_id: str,
) -> dict[str, object]:
    profile = profile_store.get_profile(db, profile_id)
    resume = profile_store.get_resume_version(db, resume_version_id)
    versions = dict(profile["versions"])  # type: ignore[arg-type]
    return {
        "resume_version_id": resume_version_id,
        "resume_content_version": int(resume["content_version"]),
        "sources_version": int(versions["sources_version"]),
        "facts_version": int(versions["facts_version"]),
        "questions_version": int(versions["questions_version"]),
        "answers_version": int(versions["answers_version"]),
        "strategy_version": int(versions["strategy_version"]),
    }


def _render_context(
    db: Database,
    profile_id: str,
    resume_version_id: str,
    view: ContextView,
) -> str:
    profile = profile_store.get_profile(db, profile_id)
    resume = _require_resume(db, profile_id, resume_version_id)
    facts, _ = profile_store.list_facts(db, profile_id)
    questions, _ = profile_store.list_questions(db, profile_id)
    facts = [
        item
        for item in facts
        if item["status"] == "confirmed"
        and (item["applies_to_all_resumes"] or resume_version_id in item["resume_version_ids"])
        and _fact_visible(item, view)
    ]
    questions = [
        item
        for item in questions
        if item["enabled"]
        and item["status"] in {"answered", "confirmed"}
        and (item["applies_to_all_resumes"] or resume_version_id in item["resume_version_ids"])
        and _question_visible(item, view)
    ]
    sections = [
        f"# 候选人上下文：{profile['display_name']}",
        f"上下文用途：{view}",
        f"关联简历：{resume['name']}（{resume_version_id}）",
    ]
    if view in {"full", "evaluation"}:
        sections.extend(["## 简历正文", str(resume.get("content") or "暂无简历正文。")])
    grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
    for fact in facts:
        grouped[str(fact["domain"])].append(fact)
    sections.append("## 已确认事实")
    if not grouped:
        sections.append("暂无适用于该简历的正式事实。")
    for domain, items in grouped.items():
        sections.append(f"### {domain}")
        for item in items:
            value = item["value"] if isinstance(item["value"], str) else _dump(item["value"])
            sections.append(f"- {item['entity_type']}.{item['field_key']}：{value}")
    sections.append("## 已确认问答")
    if not questions:
        sections.append("暂无适用于该简历的正式问答。")
    for question in questions:
        answer = question.get("final_answer")
        if answer is not None:
            rendered = answer if isinstance(answer, str) else _dump(answer)
            sections.extend([f"### {question['question_text']}", rendered])
    strategies = _context_strategies(db, profile_id, resume_version_id)
    if strategies:
        sections.append("## 当前策略")
        sections.extend(strategies)
    return "\n\n".join(str(section) for section in sections).strip() + "\n"


def _context_strategies(db: Database, profile_id: str, resume_version_id: str) -> list[str]:
    with db.connect() as connection:
        filter_rows = connection.execute(
            """
            SELECT * FROM fj_job_filter_strategies
            WHERE candidate_profile_id = ? AND resume_version_id = ?
            ORDER BY enabled DESC, updated_at DESC
            """,
            (profile_id, resume_version_id),
        ).fetchall()
        recommendation_rows = connection.execute(
            """
            SELECT * FROM fj_job_recommendation_strategies
            WHERE candidate_profile_id = ? AND resume_version_id = ?
            ORDER BY enabled DESC, updated_at DESC
            """,
            (profile_id, resume_version_id),
        ).fetchall()
        keyword_rows = connection.execute(
            """
            SELECT k.keyword, k.reason FROM fj_filter_strategy_search_keywords k
            JOIN fj_job_filter_strategies s ON s.id = k.filter_strategy_id
            WHERE s.candidate_profile_id = ? AND s.resume_version_id = ? AND k.enabled = 1
            ORDER BY s.enabled DESC, s.updated_at DESC, k.sort_order, k.created_at
            """,
            (profile_id, resume_version_id),
        ).fetchall()
    results: list[str] = []
    if filter_rows:
        results.append(f"- 岗位筛选策略：{filter_rows[0]['name']}")
    if recommendation_rows:
        results.append(f"- 建议投递策略：{recommendation_rows[0]['name']}")
    if keyword_rows:
        results.append("- 搜索词组：" + "、".join(str(row["keyword"]) for row in keyword_rows))
    return results


def _require_resume(
    db: Database,
    profile_id: str,
    resume_version_id: str,
) -> dict[str, object]:
    resume = profile_store.get_resume_version(db, resume_version_id)
    if resume["profile_id"] != profile_id or resume.get("deleted_at"):
        raise AppError(422, "VALIDATION_FAILED", "简历不存在或不属于当前候选人档案。")
    return resume


def _fact_visible(item: dict[str, object], view: ContextView) -> bool:
    if view == "full":
        return True
    if view == "chat" and item["external_use"] == "prohibited":
        return False
    domain = str(item["domain"])
    if view == "search":
        return domain in {"basic", "intent", "skill", "education", "work", "project"}
    if view == "evaluation":
        return domain in {"basic", "intent", "skill", "education", "work", "project", "achievement"}
    return True


def _question_visible(item: dict[str, object], view: ContextView) -> bool:
    if view == "chat" and item["external_use"] == "prohibited":
        return False
    if view == "search":
        return item["required_stage"] == "search"
    if view == "evaluation":
        return item["required_stage"] in {"search", "application", "interview"}
    return True


def _resume_targets_profile_data(db: Database, targets: list[str]) -> dict[str, list[Any]]:
    if not targets:
        return {"all_facts": [], "exclusive_facts": [], "all_questions": [], "exclusive_questions": []}
    placeholders = ",".join("?" for _ in targets)
    with db.connect() as connection:
        fact_rows = connection.execute(
            f"""
            SELECT f.*, COUNT(DISTINCT all_links.resume_version_id) AS total_links,
                   COUNT(DISTINCT target_links.resume_version_id) AS target_links
            FROM fj_profile_facts f
            JOIN fj_fact_resume_links target_links ON target_links.fact_id = f.id
              AND target_links.resume_version_id IN ({placeholders})
            LEFT JOIN fj_fact_resume_links all_links ON all_links.fact_id = f.id
            GROUP BY f.id
            """,
            targets,
        ).fetchall()
        question_rows = connection.execute(
            f"""
            SELECT q.*, COUNT(DISTINCT all_links.resume_version_id) AS total_links,
                   COUNT(DISTINCT target_links.resume_version_id) AS target_links
            FROM fj_profile_questions q
            JOIN fj_question_resume_links target_links ON target_links.question_id = q.id
              AND target_links.resume_version_id IN ({placeholders})
            LEFT JOIN fj_question_resume_links all_links ON all_links.question_id = q.id
            GROUP BY q.id
            """,
            targets,
        ).fetchall()
    exclusive_facts = [
        row for row in fact_rows
        if not bool(row["applies_to_all_resumes"]) and int(row["total_links"]) <= len(targets)
    ]
    exclusive_questions = [
        row for row in question_rows
        if not bool(row["applies_to_all_resumes"]) and int(row["total_links"]) <= len(targets)
    ]
    return {
        "all_facts": fact_rows,
        "exclusive_facts": exclusive_facts,
        "all_questions": question_rows,
        "exclusive_questions": exclusive_questions,
    }


def _create_orphan_issue(
    db: Database,
    profile_id: str,
    resume_version_id: str | None,
    item_type: str,
    item: dict[str, object],
) -> str:
    issue_id = new_id()
    now = utc_now()
    payload = {
        "item_type": item_type,
        "record": {key: item[key] for key in item.keys() if key not in {"total_links", "target_links"}},
    }
    with db.connect() as connection:
        connection.execute(
            """
            INSERT INTO fj_profile_issues_v3 (
              id, profile_id, resume_version_id, issue_type, title, description,
              payload_json, status, created_at, updated_at
            ) VALUES (?, ?, ?, 'orphaned_profile_data', ?, ?, ?, 'pending', ?, ?)
            """,
            (
                issue_id,
                profile_id,
                resume_version_id,
                "简历删除后需重新关联的正式资料",
                "请选择新的关联简历，或确认删除该资料。",
                _dump(payload),
                now,
                now,
            ),
        )
    return issue_id


def _split_link_impact(rows: list[Any]) -> tuple[list[str], list[str]]:
    exclusive: list[str] = []
    shared: list[str] = []
    for row in rows:
        target = shared if bool(row["applies_to_all_resumes"]) or int(row["link_count"]) > 1 else exclusive
        target.append(str(row["id"]))
    return exclusive, shared


def _serialize_template(row: Any) -> dict[str, object]:
    return {
        "id": str(row["id"]),
        "profile_id": str(row["profile_id"]),
        "question_key": str(row["question_key"]),
        "question_text": str(row["question_text"]),
        "reason": str(row["reason"]),
        "answer_type": str(row["answer_type"]),
        "required_stage": str(row["required_stage"]),
        "priority": str(row["priority"]),
        "writes_to_field": row["writes_to_field"],
        "enabled": bool(row["enabled"]),
        "sort_order": int(row["sort_order"]),
        "source_type": str(row["source_type"]),
        "created_at": str(row["created_at"]),
        "updated_at": str(row["updated_at"]),
    }


def _serialize_issue(db: Database, row: Any) -> dict[str, object]:
    with db.connect() as connection:
        answers = connection.execute(
            "SELECT * FROM fj_profile_issue_answers WHERE issue_id = ? ORDER BY created_at DESC, id",
            (row["id"],),
        ).fetchall()
        change_sets = connection.execute(
            "SELECT * FROM fj_profile_issue_change_sets WHERE issue_id = ? ORDER BY created_at DESC, id",
            (row["id"],),
        ).fetchall()
    return {
        "id": str(row["id"]),
        "profile_id": str(row["profile_id"]),
        "resume_version_id": row["resume_version_id"],
        "source_id": row["source_id"],
        "operation_run_id": row["operation_run_id"],
        "issue_type": str(row["issue_type"]),
        "title": str(row["title"]),
        "description": str(row["description"]),
        "source_excerpt": str(row["source_excerpt"]),
        "payload": _load(row["payload_json"], {}),
        "status": str(row["status"]),
        "answers": [
            {
                "id": str(answer["id"]),
                "issue_id": str(answer["issue_id"]),
                "answer_text": str(answer["answer_text"]),
                "created_at": str(answer["created_at"]),
            }
            for answer in answers
        ],
        "change_sets": [
            {
                "id": str(change["id"]),
                "issue_id": str(change["issue_id"]),
                "answer_id": str(change["answer_id"]),
                "changes": _load(change["changes_json"], {}),
                "status": str(change["status"]),
                "created_at": str(change["created_at"]),
                "updated_at": str(change["updated_at"]),
                "applied_at": change["applied_at"],
            }
            for change in change_sets
        ],
        "created_at": str(row["created_at"]),
        "updated_at": str(row["updated_at"]),
        "resolved_at": row["resolved_at"],
    }


def _serialize_context_revision(row: Any) -> dict[str, object]:
    return {
        "id": str(row["id"]),
        "revision": int(row["revision"]),
        "content": str(row["content"]),
        "source_type": str(row["source_type"]),
        "status": str(row["status"]),
        "dependency_versions": _load(row["dependency_versions_json"], {}),
        "created_at": str(row["created_at"]),
        "updated_at": str(row["updated_at"]),
    }


def _dump(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _load(value: object, fallback: Any) -> Any:
    if value in {None, ""}:
        return fallback
    try:
        return json.loads(str(value))
    except (TypeError, json.JSONDecodeError):
        return fallback
