from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from typing import Literal
from urllib.parse import urlparse

from backend.app.db import Database
from backend.app.errors import AppError
from backend.app.services.fine_job.boss_capture_history import build_job_dedupe_key
from backend.app.utils import new_id, utc_now


ReviewStatus = Literal["pending", "approved", "rejected", "dismissed"]
ActionStatus = Literal[
    "queued", "leased", "succeeded", "failed", "blocked", "unknown", "cancelled"
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
    """保存不可变评估，并按已确认的自动化策略路由到审批或执行队列。"""
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
    decision: str | None = None,
    query: str = "",
    execution_state: str | None = None,
    created_from: str | None = None,
    created_to: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> dict[str, object]:
    conditions: list[str] = []
    values: list[object] = []
    if status:
        conditions.append("r.status = ?")
        values.append(status)
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
                   j.source_job_id, j.encrypt_job_id,
                   e.evaluation_json,
                   a.id AS action_id, a.status AS action_status,
                   a.execution_state, a.last_error AS action_last_error
            FROM fj_review_items r
            JOIN fj_boss_jobs j ON j.id = r.job_id
            JOIN fj_job_evaluations e ON e.id = r.evaluation_id
            LEFT JOIN fj_automation_actions a ON a.review_item_id = r.id
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
            error_message="该岗位原结论为不建议；确认仍要沟通后才能加入动作队列。",
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


def restore_review_item(db: Database, review_item_id: str) -> dict[str, object]:
    row = _get_review_row(db, review_item_id)
    # 只恢复用户主动归档的事项，保留“新评估替代旧事项”的业务终态。
    if row["status"] != "dismissed" or not str(row["resolution_note"] or "").startswith("用户归档"):
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


def claim_next_action(
    db: Database,
    *,
    worker_id: str,
    lease_seconds: int,
) -> dict[str, object] | None:
    now = utc_now()
    lease_expires_at = (
        datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)
    ).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    with db.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            """
            SELECT id
            FROM fj_automation_actions
            WHERE action_type = 'start_conversation'
              AND (
                status = 'queued'
                OR (status = 'leased' AND lease_expires_at IS NOT NULL AND lease_expires_at <= ?)
              )
            ORDER BY created_at ASC, id ASC
            LIMIT 1
            """,
            (now,),
        ).fetchone()
        if row is None:
            return None
        connection.execute(
            """
            UPDATE fj_automation_actions
            SET status = 'leased', lease_owner = ?, lease_expires_at = ?,
                attempt_count = attempt_count + 1, updated_at = ?
            WHERE id = ?
            """,
            (worker_id.strip(), lease_expires_at, now, row["id"]),
        )
    return _get_action(db, str(row["id"]))


def complete_action(
    db: Database,
    action_id: str,
    *,
    worker_id: str,
    status: Literal["succeeded", "failed", "blocked", "unknown"],
    message: str,
) -> dict[str, object]:
    action = _get_action(db, action_id)
    if action["status"] != "leased" or action.get("lease_owner") != worker_id.strip():
        raise AppError(
            status_code=409,
            error_category="INVALID_LEASE",
            error_message="动作租约已失效或不属于当前执行器。",
        )
    now = utc_now()
    with db.connect() as connection:
        connection.execute(
            """
            UPDATE fj_automation_actions
            SET status = ?, last_error = ?, lease_owner = NULL,
                lease_expires_at = NULL, updated_at = ?, completed_at = ?
            WHERE id = ?
            """,
            (
                status,
                None if status == "succeeded" else (message.strip() or None),
                now,
                now,
                action_id,
            ),
        )
    level = "info" if status == "succeeded" else "warning"
    _log(
        db,
        f"action_{status}",
        message.strip() or f"动作状态更新为 {status}。",
        level=level,
        detail={"job_id": action["job_id"], "action_id": action_id},
    )
    return _get_action(db, action_id)


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
                error_message="岗位缺少可验证的BOSS加密岗位标识，不能加入打招呼队列。",
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
        if existing is None:
            action_id = new_id()
            connection.execute(
                """
                INSERT INTO fj_automation_actions (
                  id, job_id, evaluation_id, review_item_id, action_type,
                  status, idempotency_key, payload_json, execution_state,
                  queue_position, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'BOSS_DEFAULT_GREETING', 'queued', ?, ?, 'queued',
                  COALESCE((SELECT MAX(queue_position) + 1 FROM fj_automation_actions), 1), ?, ?)
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
                ),
            )
        else:
            action_id = str(existing["id"])
            if existing["status"] == "queued":
                # 尚未执行时允许用用户最新确认的话术更新动作快照。
                connection.execute(
                    """
                    UPDATE fj_automation_actions
                    SET evaluation_id = ?, review_item_id = ?, payload_json = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (evaluation_id, review_item_id, _json(payload), now, action_id),
                )
            elif (
                existing["status"] == "cancelled"
                and existing["last_status_code"] == "MANUAL_CONFIRMED_NOT_CONTACTED"
            ):
                # 只有用户人工确认“尚未沟通”并再次批准后，才允许复用原幂等动作。
                # 重新批准只恢复队列状态，不会在本事务内发起任何BOSS请求。
                connection.execute(
                    """
                    UPDATE fj_automation_actions
                    SET evaluation_id = ?, review_item_id = ?, payload_json = ?,
                        status = 'queued', execution_state = 'queued',
                        execution_epoch = execution_epoch + 1,
                        queue_position = COALESCE((SELECT MAX(queue_position) + 1 FROM fj_automation_actions), 1),
                        lease_owner = NULL, lease_expires_at = NULL,
                        page_deadline_at = NULL, dispatch_started_at = NULL,
                        request_accepted_at = NULL, verification_state = 'not_required',
                        verification_method = 'none', verification_delay_seconds = NULL,
                        verification_due_at = NULL, verification_started_at = NULL,
                        verification_completed_at = NULL, verification_attempts = 0,
                        cooldown_seconds = NULL, next_eligible_at = NULL,
                        last_status_code = 'REAPPROVED_AFTER_MANUAL_NOT_CONTACTED',
                        last_error = NULL, result_json = '{}', completed_at = NULL,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (evaluation_id, review_item_id, _json(payload), now, action_id),
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
                   e.evaluation_json, e.filter_strategy_id,
                   a.id AS action_id, a.status AS action_status,
                   a.execution_state, a.last_error AS action_last_error
            FROM fj_review_items r
            JOIN fj_boss_jobs j ON j.id = r.job_id
            JOIN fj_job_evaluations e ON e.id = r.evaluation_id
            LEFT JOIN fj_automation_actions a ON a.review_item_id = r.id
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
        "evaluation": _load_json(row["evaluation_json"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "resolved_at": row["resolved_at"],
        "action_id": row["action_id"] if "action_id" in row.keys() else None,
        "action_status": row["action_status"] if "action_status" in row.keys() else None,
        "execution_state": row["execution_state"] if "execution_state" in row.keys() else None,
        "action_last_error": row["action_last_error"] if "action_last_error" in row.keys() else None,
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
        "lease_owner": row["lease_owner"],
        "lease_expires_at": row["lease_expires_at"],
        "attempt_count": row["attempt_count"],
        "last_error": row["last_error"],
        "job_title": row["job_title"],
        "company_name": row["company_name"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "completed_at": row["completed_at"],
        "execution_state": row["execution_state"],
        "execution_epoch": row["execution_epoch"],
        "queue_position": row["queue_position"],
        "page_open_attempts": row["page_open_attempts"],
        "page_deadline_at": row["page_deadline_at"],
        "dispatch_started_at": row["dispatch_started_at"],
        "request_accepted_at": row["request_accepted_at"],
        "verification_state": row["verification_state"],
        "verification_method": row["verification_method"],
        "verification_delay_seconds": row["verification_delay_seconds"],
        "verification_due_at": row["verification_due_at"],
        "verification_started_at": row["verification_started_at"],
        "verification_completed_at": row["verification_completed_at"],
        "verification_attempts": row["verification_attempts"],
        "cooldown_seconds": row["cooldown_seconds"],
        "next_eligible_at": row["next_eligible_at"],
        "last_status_code": row["last_status_code"],
        "result": _load_json(row["result_json"]),
        "navigation_task_id": row["navigation_task_id"],
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
