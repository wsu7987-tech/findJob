from __future__ import annotations

import json
import re
from typing import Literal
from urllib.parse import urlparse

from backend.app.db import Database
from backend.app.errors import AppError
from backend.app.services.fine_job.boss_capture_history import build_job_dedupe_key
from backend.app.utils import new_id, utc_now


ReviewStatus = Literal["pending", "approved", "rejected", "dismissed"]
ReviewExecutionView = Literal["running", "executed"]
ActionStatus = Literal[
    "queued", "running", "leased", "succeeded", "failed", "blocked", "unknown", "cancelled"
]


def record_evaluation_and_route(
    db: Database,
    *,
    job: dict[str, object],
    evaluation: dict[str, object],
    recommendation_strategy: dict[str, object],
    filter_strategy: dict[str, object] | None,
    resume_id: str | None,
    delivery_strategy: dict[str, object] | None,
    candidate_profile: dict[str, object] | None = None,
    resume_version_id: str | None = None,
    context_revision_id: str | None = None,
    context_dependency_versions: dict[str, object] | None = None,
) -> dict[str, object] | None:
    """保存不可变评估，并按已确认的自动化策略路由到审批或执行任务。"""
    job_id = _resolve_history_job_id(db, job)
    if not job_id:
        # 单元测试或未持久化的临时岗位仍可返回评估，但不创建悬空审批记录。
        return None

    now = utc_now()
    evaluation_id = new_id()
    decision = str(evaluation.get("decision") or "review")
    source = str(evaluation.get("source") or "rules")
    candidate_snapshot = {
        "candidate_profile_id": _optional_text((candidate_profile or {}).get("id")),
        "profile_versions": (candidate_profile or {}).get("versions") or {},
        "resume_version_id": _optional_text(resume_version_id),
        "context_revision_id": _optional_text(context_revision_id),
        "context_dependencies": context_dependency_versions or {},
        "filter_strategy": {
            "id": _optional_text((filter_strategy or {}).get("id")),
            "version": (filter_strategy or {}).get("strategy_version"),
        },
        "recommendation_strategy": {
            "id": _optional_text(recommendation_strategy.get("id")),
            "version": recommendation_strategy.get("strategy_version"),
        },
    }
    profile_versions = (candidate_profile or {}).get("versions") or {}
    with db.connect() as connection:
        connection.execute(
            """
            INSERT INTO fj_job_evaluations (
              id, job_id, evaluation_version, recommendation_strategy_id,
              filter_strategy_id, resume_id, source, decision, confidence,
              evaluation_json, created_at, candidate_profile_id,
              profile_context_version, resume_version_id, structure_version,
              context_revision_id, filter_strategy_version,
              recommendation_strategy_version, profile_facts_version,
              profile_questions_version, candidate_snapshot_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                evaluation_id,
                job_id,
                str(evaluation.get("evaluation_version") or "2.0"),
                _optional_text(recommendation_strategy.get("id")),
                _optional_text((filter_strategy or {}).get("id")),
                _optional_text(resume_id),
                source,
                decision,
                float(evaluation.get("confidence") or 0),
                _json(evaluation),
                now,
                _optional_text((candidate_profile or {}).get("id")),
                _profile_context_version(candidate_profile),
                _optional_text(resume_version_id),
                2 if candidate_profile else 1,
                _optional_text(context_revision_id),
                (filter_strategy or {}).get("strategy_version"),
                recommendation_strategy.get("strategy_version"),
                profile_versions.get("facts_version") if isinstance(profile_versions, dict) else None,
                profile_versions.get("questions_version") if isinstance(profile_versions, dict) else None,
                _json(candidate_snapshot),
            ),
        )
    # 评估记录落库后再刷新组合冷却，确保详情与建议两个事实同时可见。
    from backend.app.services.fine_job.filter_exclusions import record_job_event

    record_job_event(db, "evaluation", job_id, now)

    greeting = evaluation.get("greeting_draft")
    draft_message = (
        str(greeting.get("text") or "").strip() if isinstance(greeting, dict) else ""
    )
    if decision != "reject" and not draft_message:
        draft_message = _generic_greeting(job)

    auto_approved = bool(
        decision == "recommend"
        and delivery_strategy
        and delivery_strategy.get("ready")
        and delivery_strategy.get("automation_level") == "auto_greeting"
        and delivery_strategy.get("auto_greeting_enabled")
    )
    status: ReviewStatus
    if decision == "reject":
        status = "rejected"
    elif auto_approved:
        status = "approved"
    else:
        status = "pending"

    review_id = new_id()
    resolved_at = now if status != "pending" else None
    with db.connect() as connection:
        # 同一岗位的新评估替代尚未执行的旧结论，避免待确认池出现多份相互冲突的事项。
        connection.execute(
            """
            UPDATE fj_review_items
            SET status = 'dismissed', resolution_note = '已由新的岗位评估替代',
                updated_at = ?, resolved_at = ?
            WHERE job_id = ? AND action_type = 'start_conversation'
              AND status IN ('pending', 'rejected')
            """,
            (now, now, job_id),
        )
        connection.execute(
            """
            INSERT INTO fj_review_items (
              id, job_id, evaluation_id, action_type, status, ai_decision,
              draft_message, final_message, resolution_note, auto_approved,
              created_at, updated_at, resolved_at, candidate_profile_id,
              profile_context_version, resume_version_id, context_revision_id
            ) VALUES (?, ?, ?, 'start_conversation', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                review_id,
                job_id,
                evaluation_id,
                status,
                decision,
                draft_message,
                draft_message if auto_approved else "",
                "命中已确认的自动打招呼策略" if auto_approved else "",
                1 if auto_approved else 0,
                now,
                now,
                resolved_at,
                _optional_text((candidate_profile or {}).get("id")),
                _profile_context_version(candidate_profile),
                _optional_text(resume_version_id),
                _optional_text(context_revision_id),
            ),
        )

    action = None
    if auto_approved:
        action = _enqueue_action(
            db,
            job_id=job_id,
            evaluation_id=evaluation_id,
            review_item_id=review_id,
            message=draft_message,
        )
    return {
        "evaluation_id": evaluation_id,
        "review_item_id": review_id,
        "review_status": status,
        "action": action,
    }


def list_review_items(
    db: Database,
    *,
    status: ReviewStatus | None = None,
    execution_view: ReviewExecutionView | None = None,
    decision: str | None = None,
    query: str = "",
    execution_state: str | None = None,
    created_from: str | None = None,
    created_to: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> dict[str, object]:
    # 测试岗位仅服务于运行状态页的任务验证，不进入人工待确认流程。
    conditions: list[str] = ["j.is_test = 0"]
    values: list[object] = []
    if status:
        conditions.append("r.status = ?")
        values.append(status)
    if execution_view:
        # 执行视图只呈现已批准且已经创建动作的事项，避免和待确认、归档状态混在一起。
        conditions.append("r.status = 'approved'")
        if execution_view == "running":
            conditions.append("a.execution_state IN ('queued', 'running')")
        else:
            conditions.append("a.execution_state IN ('succeeded', 'failed', 'blocked', 'unknown', 'cancelled')")
    if decision:
        conditions.append("r.ai_decision = ?")
        values.append(decision)
    search = query.strip()
    if search:
        conditions.append("(j.title LIKE ? OR j.company_name LIKE ? OR r.resolution_note LIKE ?)")
        wildcard = f"%{search}%"
        values.extend([wildcard, wildcard, wildcard])
    if execution_state:
        conditions.append("a.execution_state = ?")
        values.append(execution_state)
    if created_from:
        conditions.append("r.created_at >= ?")
        values.append(created_from)
    if created_to:
        conditions.append("r.created_at <= ?")
        values.append(created_to)
    condition = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    offset = (page - 1) * page_size
    with db.connect() as connection:
        total = int(
            connection.execute(
                f"""
                SELECT COUNT(*)
                FROM fj_review_items r
                JOIN fj_boss_jobs j ON j.id = r.job_id
                LEFT JOIN fj_automation_actions a ON a.review_item_id = r.id
                {condition}
                """,
                values,
            ).fetchone()[0]
        )
        rows = connection.execute(
            f"""
            SELECT r.*, j.title AS job_title, j.company_name, j.job_link,
                   j.source_job_id, j.encrypt_job_id, j.company_id,
                   c.company_type,
                   e.evaluation_json,
                   a.id AS action_id, a.status AS action_status,
                   a.execution_state, a.last_error AS action_last_error,
                   (
                     SELECT s.id
                     FROM fj_chat_sessions s
                     JOIN fj_boss_jobs chat_job ON chat_job.id = s.job_id
                     WHERE chat_job.company_id = j.company_id
                     ORDER BY s.updated_at DESC, s.id DESC
                     LIMIT 1
                   ) AS company_chat_session_id,
                   (
                     SELECT s.id
                     FROM fj_chat_sessions s
                     WHERE s.job_id = r.job_id
                        OR (j.encrypt_job_id <> '' AND s.encrypt_job_id = j.encrypt_job_id)
                     ORDER BY s.updated_at DESC, s.id DESC
                     LIMIT 1
                   ) AS job_chat_session_id
            FROM fj_review_items r
            JOIN fj_boss_jobs j ON j.id = r.job_id
            JOIN fj_job_evaluations e ON e.id = r.evaluation_id
            LEFT JOIN fj_automation_actions a ON a.review_item_id = r.id
            LEFT JOIN fj_companies c ON c.id = j.company_id
            {condition}
            ORDER BY r.created_at DESC, r.id DESC
            LIMIT ? OFFSET ?
            """,
            [*values, page_size, offset],
        ).fetchall()
    return {
        "items": [_serialize_review(row) for row in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


def approve_review_item(
    db: Database,
    review_item_id: str,
    *,
    message: str,
    allow_override: bool,
) -> tuple[dict[str, object], dict[str, object]]:
    row = _get_review_row(db, review_item_id)
    from backend.app.services.fine_job.filter_exclusions import assert_job_action_allowed
    from backend.app.services.fine_job.strategies import get_filter_strategy

    filter_strategy_id = str(row["filter_strategy_id"] or "")
    filter_strategy = get_filter_strategy(db, filter_strategy_id) if filter_strategy_id else None
    assert_job_action_allowed(
        db, str(row["job_id"]), strategy=filter_strategy, action="application"
    )
    if row["status"] == "rejected" and not allow_override:
        raise AppError(
            status_code=409,
            error_category="CONFIRMATION_REQUIRED",
            error_message="该岗位原结论为不建议；确认仍要沟通后才能创建执行任务。",
        )
    if row["status"] not in {"pending", "rejected"}:
        raise AppError(
            status_code=409,
            error_category="INVALID_STATE",
            error_message="该待确认事项已经处理。",
        )
    final_message = message.strip() or str(row["draft_message"] or "").strip()
    if not final_message:
        final_message = _generic_greeting(
            {"title": row["job_title"], "boss_name": row["company_name"]}
        )
    now = utc_now()
    with db.connect() as connection:
        connection.execute(
            """
            UPDATE fj_review_items
            SET status = 'approved', final_message = ?, resolution_note = ?,
                auto_approved = 0, updated_at = ?, resolved_at = ?
            WHERE id = ?
            """,
            (
                final_message,
                "用户覆盖 AI 不建议结论并批准" if row["status"] == "rejected" else "用户批准",
                now,
                now,
                review_item_id,
            ),
        )
    action = _enqueue_action(
        db,
        job_id=str(row["job_id"]),
        evaluation_id=str(row["evaluation_id"]),
        review_item_id=review_item_id,
        message=final_message,
    )
    _log(
        db,
        "review_approved",
        f"已批准岗位“{row['job_title']}”的打招呼动作。",
        detail={"job_id": row["job_id"], "review_item_id": review_item_id, "action_id": action["id"]},
    )
    return _serialize_review(_get_review_row(db, review_item_id)), action


def reject_review_item(
    db: Database,
    review_item_id: str,
    *,
    note: str,
) -> dict[str, object]:
    row = _get_review_row(db, review_item_id)
    if row["status"] != "pending":
        raise AppError(
            status_code=409,
            error_category="INVALID_STATE",
            error_message="只有待确认事项可以拒绝。",
        )
    now = utc_now()
    with db.connect() as connection:
        connection.execute(
            """
            UPDATE fj_review_items
            SET status = 'rejected', resolution_note = ?, updated_at = ?, resolved_at = ?
            WHERE id = ?
            """,
            (note.strip(), now, now, review_item_id),
        )
    _log(
        db,
        "review_rejected",
        f"已拒绝岗位“{row['job_title']}”的打招呼动作。",
        detail={"job_id": row["job_id"], "review_item_id": review_item_id},
    )
    return _serialize_review(_get_review_row(db, review_item_id))


def archive_review_item(
    db: Database,
    review_item_id: str,
    *,
    note: str = "",
) -> dict[str, object]:
    row = _get_review_row(db, review_item_id)
    if row["status"] not in {"pending", "rejected"}:
        raise AppError(
            status_code=409,
            error_category="INVALID_STATE",
            error_message="只有待确认或已拒绝事项可以归档。",
        )
    now = utc_now()
    resolution_note = "用户归档" + (f"：{note.strip()}" if note.strip() else "")
    with db.connect() as connection:
        connection.execute(
            """
            UPDATE fj_review_items
            SET status = 'dismissed', resolution_note = ?, updated_at = ?, resolved_at = ?
            WHERE id = ?
            """,
            (resolution_note, now, now, review_item_id),
        )
    _log(
        db,
        "review_archived",
        f"已归档岗位“{row['job_title']}”的待确认事项。",
        detail={"job_id": row["job_id"], "review_item_id": review_item_id},
    )
    return _serialize_review(_get_review_row(db, review_item_id))


def _link_review_item_chat(
    db: Database,
    row,
) -> str | None:
    """关联单条待确认事项的同岗位聊天会话，命中后归档。"""
    session_id = _find_job_chat_session_id(db, row)
    if session_id is None:
        return None
    now = utc_now()
    with db.connect() as connection:
        connection.execute(
            """
            UPDATE fj_review_items
            SET status = 'dismissed', resolution_note = '用户关联已有聊天会话后归档',
                updated_at = ?, resolved_at = ?
            WHERE id = ?
            """,
            (now, now, row["id"]),
        )
    _log(
        db,
        "review_archived",
        f"已关联岗位“{row['job_title']}”的聊天会话并归档待确认事项。",
        detail={"job_id": row["job_id"], "review_item_id": row["id"], "chat_session_id": session_id},
    )
    return session_id


def _find_job_chat_session_id(db: Database, row) -> str | None:
    """按岗位身份查找最近的 BOSS 聊天会话。"""
    with db.connect() as connection:
        session = connection.execute(
            """
            SELECT id FROM fj_chat_sessions
            WHERE job_id = ?
               OR (? <> '' AND encrypt_job_id = ?)
            ORDER BY CASE WHEN job_id = ? THEN 0 ELSE 1 END, updated_at DESC, id DESC
            LIMIT 1
            """,
            (row["job_id"], row["encrypt_job_id"], row["encrypt_job_id"], row["job_id"]),
        ).fetchone()
        if session is None:
            return None
    return str(session["id"])


def link_review_items_chat(
    db: Database,
    *,
    status: Literal["pending", "rejected", "approved"],
    execution_view: Literal["running"] | None = None,
    decision: str | None = None,
    query: str = "",
    execution_state: str | None = None,
    created_from: str | None = None,
    created_to: str | None = None,
) -> dict[str, int]:
    """对当前筛选范围内的全部待确认事项关联同岗位聊天会话。"""
    conditions = ["r.status = ?"]
    values: list[object] = [status]
    if execution_view == "running":
        conditions.append("a.execution_state IN ('queued', 'running')")
    if decision:
        conditions.append("r.ai_decision = ?")
        values.append(decision)
    if query.strip():
        wildcard = f"%{query.strip()}%"
        conditions.append("(j.title LIKE ? OR j.company_name LIKE ? OR r.resolution_note LIKE ?)")
        values.extend([wildcard, wildcard, wildcard])
    if execution_state:
        conditions.append("a.execution_state = ?")
        values.append(execution_state)
    if created_from:
        conditions.append("r.created_at >= ?")
        values.append(created_from)
    if created_to:
        conditions.append("r.created_at <= ?")
        values.append(created_to)
    with db.connect() as connection:
        rows = connection.execute(
            f"""
            SELECT r.*, j.title AS job_title, j.company_name, j.job_link,
                   j.encrypt_job_id, j.company_id, c.company_type,
                   e.evaluation_json, a.id AS action_id, a.status AS action_status,
                   a.execution_state, a.last_error AS action_last_error
            FROM fj_review_items r
            JOIN fj_boss_jobs j ON j.id = r.job_id
            JOIN fj_job_evaluations e ON e.id = r.evaluation_id
            LEFT JOIN fj_automation_actions a ON a.review_item_id = r.id
            LEFT JOIN fj_companies c ON c.id = j.company_id
            WHERE {' AND '.join(conditions)}
            ORDER BY r.created_at DESC, r.id DESC
            """,
            values,
        ).fetchall()
    archived = 0
    confirmed = 0
    for row in rows:
        if status == "approved":
            # 同岗位出现会话不能确认某个执行动作，等待 action-specific 直接 Evidence。
            continue
        elif _link_review_item_chat(db, row):
            archived += 1
    matched = archived + confirmed
    return {"matched": matched, "archived": archived, "confirmed": confirmed, "unmatched": len(rows) - matched}


def restore_review_item(db: Database, review_item_id: str) -> dict[str, object]:
    row = _get_review_row(db, review_item_id)
    # 只恢复用户主动归档的事项，保留“新评估替代旧事项”的业务终态。
    user_archived = str(row["resolution_note"] or "").startswith(("用户归档", "用户关联已有聊天会话后归档"))
    if row["status"] != "dismissed" or not user_archived:
        raise AppError(
            status_code=409,
            error_category="INVALID_STATE",
            error_message="该事项由新评估替代或当前未归档，不能恢复。",
        )
    now = utc_now()
    with db.connect() as connection:
        connection.execute(
            """
            UPDATE fj_review_items
            SET status = 'pending', resolution_note = '由归档恢复',
                updated_at = ?, resolved_at = NULL
            WHERE id = ?
            """,
            (now, review_item_id),
        )
    _log(
        db,
        "review_restored",
        f"已恢复岗位“{row['job_title']}”的待确认事项。",
        detail={"job_id": row["job_id"], "review_item_id": review_item_id},
    )
    return _serialize_review(_get_review_row(db, review_item_id))


def batch_review_items(
    db: Database,
    *,
    review_item_ids: list[str],
    operation: str,
    note: str = "",
    allow_override: bool = False,
) -> dict[str, object]:
    results: list[dict[str, object]] = []
    # 保留用户勾选顺序，同时避免同一事项重复执行。
    unique_ids = list(dict.fromkeys(item.strip() for item in review_item_ids if item.strip()))
    for review_item_id in unique_ids:
        try:
            # 单项失败不阻断其余事项，前端可据此展示部分成功结果。
            if operation == "approve":
                approve_review_item(
                    db,
                    review_item_id,
                    message="",
                    allow_override=allow_override,
                )
            elif operation == "reject":
                reject_review_item(db, review_item_id, note=note)
            elif operation == "archive":
                archive_review_item(db, review_item_id, note=note)
            else:
                raise AppError(400, "VALIDATION_FAILED", "不支持的批量操作。")
            results.append({"review_item_id": review_item_id, "success": True, "error_message": ""})
        except AppError as exc:
            results.append(
                {
                    "review_item_id": review_item_id,
                    "success": False,
                    "error_message": exc.error_message,
                }
            )
    succeeded = sum(1 for item in results if item["success"])
    return {"results": results, "succeeded": succeeded, "failed": len(results) - succeeded}


def list_automation_actions(
    db: Database,
    *,
    status: ActionStatus | None = None,
    limit: int = 200,
) -> dict[str, object]:
    condition = "WHERE a.status = ?" if status else ""
    values: list[object] = [status] if status else []
    with db.connect() as connection:
        total = int(
            connection.execute(
                f"SELECT COUNT(*) FROM fj_automation_actions a {condition}", values
            ).fetchone()[0]
        )
        rows = connection.execute(
            f"""
            SELECT a.*, j.title AS job_title, j.company_name
            FROM fj_automation_actions a
            JOIN fj_boss_jobs j ON j.id = a.job_id
            {condition}
            ORDER BY a.created_at DESC, a.id DESC
            LIMIT ?
            """,
            [*values, limit],
        ).fetchall()
    return {"actions": [_serialize_action(row) for row in rows], "total": total}


def _enqueue_action(
    db: Database,
    *,
    job_id: str,
    evaluation_id: str,
    review_item_id: str,
    message: str,
) -> dict[str, object]:
    idempotency_key = f"boss:{job_id}:BOSS_DEFAULT_GREETING"
    now = utc_now()
    with db.connect() as connection:
        job = connection.execute(
            """
            SELECT id, source_job_id, encrypt_job_id, title, company_name, job_link
            FROM fj_boss_jobs WHERE id = ?
            """,
            (job_id,),
        ).fetchone()
        assert job is not None
        encrypt_job_id = _resolve_encrypt_job_id(job)
        if not encrypt_job_id:
            raise AppError(
                status_code=409,
                error_category="JOB_ID_MISSING",
                error_message="岗位缺少可验证的BOSS加密岗位标识，不能创建打招呼执行任务。",
            )
        if not str(job["encrypt_job_id"] or ""):
            # 兼容早期仅保存详情页链接的岗位，提取后回填正式身份列。
            connection.execute(
                "UPDATE fj_boss_jobs SET encrypt_job_id = ? WHERE id = ?",
                (encrypt_job_id, job_id),
            )
        payload = {
            "platform": "boss",
            "action_type": "BOSS_DEFAULT_GREETING",
            "history_job_id": job_id,
            "source_job_id": job["source_job_id"],
            "encrypt_job_id": encrypt_job_id,
            "job_title": job["title"],
            "company_name": job["company_name"],
            "job_link": job["job_link"],
            # 仅为旧桌面端响应兼容保留审批文本；插件按动作类型固定使用 BOSS 默认招呼语，
            # 绝不能把这个字段作为发送内容。
            "message": message,
            "evaluation_id": evaluation_id,
            "review_item_id": review_item_id,
        }
        existing = connection.execute(
            "SELECT id, status, last_status_code FROM fj_automation_actions WHERE idempotency_key = ?",
            (idempotency_key,),
        ).fetchone()
        requested_epoch: int | None = None
        if existing is None:
            action_id = new_id()
            requested_epoch = 0
            connection.execute(
                """
                INSERT INTO fj_automation_actions (
                  id, job_id, evaluation_id, review_item_id, action_type,
                  status, idempotency_key, payload_json, execution_state,
                  canonical_status, canonical_updated_at, canonical_reason,
                  created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'BOSS_DEFAULT_GREETING', 'queued', ?, ?, 'queued',
                          'pending', ?, '等待执行', ?, ?)
                """,
                (
                    action_id,
                    job_id,
                    evaluation_id,
                    review_item_id,
                    idempotency_key,
                    _json(payload),
                    now,
                    now,
                    now,
                ),
            )
        else:
            action_id = str(existing["id"])
            if existing["status"] == "queued":
                # 尚未执行时允许用用户最新确认的话术更新任务内容。
                connection.execute(
                    """
                    UPDATE fj_automation_actions
                    SET evaluation_id = ?, review_item_id = ?, payload_json = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (evaluation_id, review_item_id, _json(payload), now, action_id),
                )
            elif existing["status"] == "cancelled":
                # 当前事项再次获得批准后，统一恢复对应任务状态。
                # 重新批准只恢复任务状态，不会在本事务内发起任何 BOSS 请求。
                connection.execute(
                    """
                    UPDATE fj_automation_actions
                    SET evaluation_id = ?, review_item_id = ?, payload_json = ?,
                        status = 'queued', execution_state = 'queued',
                        execution_epoch = execution_epoch + 1,
                        last_status_code = 'REAPPROVED',
                        last_error = NULL, result_json = '{}', completed_at = NULL,
                        canonical_status = 'pending', canonical_updated_at = ?,
                        canonical_reason = '用户重新批准，等待执行',
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (evaluation_id, review_item_id, _json(payload), now, now, action_id),
                )
                epoch_row = connection.execute(
                    "SELECT execution_epoch FROM fj_automation_actions WHERE id = ?",
                    (action_id,),
                ).fetchone()
                requested_epoch = int(epoch_row["execution_epoch"])
        if requested_epoch is not None:
            from backend.app.services.fine_job.job_activity import append_job_activity_with_connection

            append_job_activity_with_connection(
                connection,
                job_id=job_id,
                event_type="greeting_requested",
                occurred_at=now,
                source="workflow",
                source_ref_type="automation_action",
                source_ref_id=action_id,
                evidence_level="direct",
                payload={"execution_epoch": requested_epoch},
                dedupe_key=f"automation_action:{action_id}:epoch:{requested_epoch}:greeting_requested",
            )
    return _get_action(db, action_id)


def _resolve_history_job_id(db: Database, job: dict[str, object]) -> str | None:
    candidate = _optional_text(job.get("history_record_id") or job.get("id"))
    with db.connect() as connection:
        if candidate:
            row = connection.execute(
                "SELECT id FROM fj_boss_jobs WHERE id = ?", (candidate,)
            ).fetchone()
            if row:
                return str(row["id"])
        dedupe_key = build_job_dedupe_key(job)
        row = connection.execute(
            "SELECT id FROM fj_boss_jobs WHERE dedupe_key = ?", (dedupe_key,)
        ).fetchone()
    return str(row["id"]) if row else None


def _get_review_row(db: Database, review_item_id: str):
    with db.connect() as connection:
        row = connection.execute(
            """
            SELECT r.*, j.title AS job_title, j.company_name, j.job_link,
                   j.encrypt_job_id, j.company_id, c.company_type,
                   e.evaluation_json, e.filter_strategy_id,
                   a.id AS action_id, a.status AS action_status,
                   a.execution_state, a.last_error AS action_last_error
            FROM fj_review_items r
            JOIN fj_boss_jobs j ON j.id = r.job_id
            JOIN fj_job_evaluations e ON e.id = r.evaluation_id
            LEFT JOIN fj_automation_actions a ON a.review_item_id = r.id
            LEFT JOIN fj_companies c ON c.id = j.company_id
            WHERE r.id = ?
            """,
            (review_item_id,),
        ).fetchone()
    if row is None:
        raise AppError(
            status_code=404,
            error_category="NOT_FOUND",
            error_message="待确认事项不存在。",
        )
    return row


def _get_action(db: Database, action_id: str) -> dict[str, object]:
    with db.connect() as connection:
        row = connection.execute(
            """
            SELECT a.*, j.title AS job_title, j.company_name
            FROM fj_automation_actions a
            JOIN fj_boss_jobs j ON j.id = a.job_id
            WHERE a.id = ?
            """,
            (action_id,),
        ).fetchone()
    if row is None:
        raise AppError(
            status_code=404,
            error_category="NOT_FOUND",
            error_message="自动化动作不存在。",
        )
    return _serialize_action(row)


def _serialize_review(row) -> dict[str, object]:
    return {
        "id": row["id"],
        "job_id": row["job_id"],
        "evaluation_id": row["evaluation_id"],
        "action_type": row["action_type"],
        "status": row["status"],
        "ai_decision": row["ai_decision"],
        "draft_message": row["draft_message"],
        "final_message": row["final_message"],
        "resolution_note": row["resolution_note"],
        "auto_approved": bool(row["auto_approved"]),
        "job_title": row["job_title"],
        "company_name": row["company_name"],
        "job_link": row["job_link"],
        "company_id": row["company_id"] if "company_id" in row.keys() else None,
        "company_type": row["company_type"] if "company_type" in row.keys() else None,
        "evaluation": _normalize_evaluation(_load_json(row["evaluation_json"])),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "resolved_at": row["resolved_at"],
        "action_id": row["action_id"] if "action_id" in row.keys() else None,
        "action_status": row["action_status"] if "action_status" in row.keys() else None,
        "execution_state": row["execution_state"] if "execution_state" in row.keys() else None,
        "action_last_error": row["action_last_error"] if "action_last_error" in row.keys() else None,
        "company_chat_session_id": row["company_chat_session_id"] if "company_chat_session_id" in row.keys() else None,
        "job_chat_session_id": row["job_chat_session_id"] if "job_chat_session_id" in row.keys() else None,
    }


def _serialize_action(row) -> dict[str, object]:
    return {
        "id": row["id"],
        "job_id": row["job_id"],
        "evaluation_id": row["evaluation_id"],
        "review_item_id": row["review_item_id"],
        "action_type": row["action_type"],
        "status": row["status"],
        "idempotency_key": row["idempotency_key"],
        "payload": _load_json(row["payload_json"]),
        "last_error": row["last_error"],
        "job_title": row["job_title"],
        "company_name": row["company_name"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "completed_at": row["completed_at"],
        "execution_state": row["execution_state"],
        "execution_epoch": row["execution_epoch"],
        "last_status_code": row["last_status_code"],
        "result": _load_json(row["result_json"]),
    }


def _generic_greeting(job: dict[str, object]) -> str:
    title = str(job.get("title") or "该岗位").strip() or "该岗位"
    return f"您好，我对贵司的{title}很感兴趣，希望有机会进一步沟通，谢谢。"


def _resolve_encrypt_job_id(job) -> str:
    direct = str(job["encrypt_job_id"] or "").strip()
    if direct:
        return direct
    link = str(job["job_link"] or "").strip()
    parsed = urlparse(link)
    if parsed.scheme != "https" or parsed.hostname not in {"www.zhipin.com", "zhipin.com"}:
        return ""
    matched = re.search(r"/job_detail/([^/]+?)\.html(?:/|$)", parsed.path)
    return matched.group(1) if matched else ""


def _log(
    db: Database,
    action_type: str,
    message: str,
    *,
    level: str = "info",
    detail: dict[str, object] | None = None,
) -> None:
    with db.connect() as connection:
        connection.execute(
            """
            INSERT INTO fj_action_logs (id, run_id, level, action_type, message, detail_json, created_at)
            VALUES (?, NULL, ?, ?, ?, ?, ?)
            """,
            (new_id(), level, action_type, message, _json(detail or {}), utc_now()),
        )


def _optional_text(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def _profile_context_version(profile: dict[str, object] | None) -> int | None:
    if not profile:
        return None
    versions = profile.get("versions")
    if not isinstance(versions, dict):
        return None
    value = versions.get("context_version")
    return int(value) if value is not None else None


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False)


def _load_json(value: object) -> dict[str, object]:
    try:
        parsed = json.loads(str(value or "{}"))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _normalize_evaluation(value: dict[str, object]) -> dict[str, object]:
    """补齐历史评估记录缺失的列表字段，保持待确认接口契约稳定。"""
    evaluation = dict(value)
    for field in ("reasons", "strengths", "gaps", "risks", "hard_requirements"):
        if not isinstance(evaluation.get(field), list):
            evaluation[field] = []
    if not isinstance(evaluation.get("summary"), str):
        evaluation["summary"] = ""
    if not isinstance(evaluation.get("confidence"), (int, float)):
        evaluation["confidence"] = 0
    return evaluation
