from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from backend.app.config import AppConfig
from backend.app.db import Database
from backend.app.errors import AppError
from backend.app.services.fine_job import boss_chat
from backend.app.services.fine_job import job_hunt_analysis
from backend.app.services.fine_job.boss_capture_tasks import boss_capture_task_manager
from backend.app.services.fine_job.boss_chat import CHAT_BATCH_LIMIT
from backend.app.services.fine_job.boss_scraper.service import boss_scraper_service
from backend.app.services.fine_job.job_activity import reconcile_chat_session_activity
from backend.app.utils import new_id, utc_now


TERMINAL_RUN_STATUSES = {
    "completed",
    "completed_with_errors",
    "failed",
    "cancelled",
}
SUPPORTED_WORKFLOWS = (
    "refresh_chat_list",
    "refresh_chat_messages",
    "refresh_related_jobs",
    "analyze_conversations",
    "generate_missing_suggestions",
    "generate_reply_drafts",
    "generate_followup_recommendations",
)
FRIEND_LIST_FRESHNESS = timedelta(minutes=30)


def get_refresh_context(db: Database) -> dict[str, object]:
    """返回页面初始时间信息；本方法只读取现有数据。"""
    with db.connect() as connection:
        latest_local = _latest_local_platform_message_at(connection)
        latest_chat_list = _latest_chat_list_sync(connection)
        last_success = connection.execute(
            """
            SELECT completed_at
            FROM fj_job_hunt_refresh_runs
            WHERE status IN ('completed', 'completed_with_errors')
              AND completed_at IS NOT NULL
            ORDER BY completed_at DESC LIMIT 1
            """
        ).fetchone()
        latest_scope = connection.execute(
            """
            SELECT s.id
            FROM fj_job_hunt_refresh_scopes s
            LEFT JOIN fj_job_hunt_refresh_runs r ON r.scope_id = s.id
            WHERE r.id IS NULL
            ORDER BY s.scope_generated_at DESC LIMIT 1
            """
        ).fetchone()
    default_since = (
        str(last_success["completed_at"])
        if last_success is not None
        else _iso(datetime.now(UTC) - timedelta(hours=24))
    )
    return {
        "timezone": "Asia/Shanghai",
        "latest_local_message_at": str(latest_local) if latest_local else None,
        "last_successful_completed_at": (
            str(last_success["completed_at"]) if last_success is not None else None
        ),
        "default_since_time": default_since,
        "latest_unconsumed_scope_id": (
            str(latest_scope["id"]) if latest_scope is not None else None
        ),
        "chat_list_synced_at": (
            str(latest_chat_list["synced_at"]) if latest_chat_list else None
        ),
    }


def discover_scope(
    db: Database,
    selected_since_time: str,
    source_mode: str = "auto",
) -> dict[str, object]:
    """按来源模式取得聊天列表快照，并持久化用户确认执行的固定范围。"""
    since = _parse_time(selected_since_time)
    requested_mode = source_mode.strip().lower()
    if requested_mode not in {"auto", "local", "refresh"}:
        raise AppError(422, "REFRESH_SCOPE_SOURCE_INVALID", "更新范围来源模式无效。")
    now = datetime.now(UTC)
    with db.connect() as connection:
        latest_chat_list = _latest_chat_list_sync(connection)
    has_fresh_local_list = bool(
        latest_chat_list
        and (synced_at := _try_parse_time(latest_chat_list["synced_at"])) is not None
        and now - synced_at <= FRIEND_LIST_FRESHNESS
    )
    resolved_source = (
        "refresh"
        if requested_mode == "refresh"
        or (requested_mode == "auto" and not has_fresh_local_list)
        else "local"
    )

    if resolved_source == "refresh":
        captured = boss_scraper_service.capture_chat_friend_list()
        friend_list_result = boss_chat.sync_friend_list(
            db,
            account_uid=str(captured["account_uid"]),
            response=captured["response"],
            source_url=str(captured["url"]),
        )
        account_uid = str(friend_list_result["account_uid"])
        session_ids = [str(value) for value in friend_list_result.get("session_ids", [])]
        new_session_ids = {
            str(value) for value in friend_list_result.get("created_session_ids", [])
        }
        chat_list_synced_at = str(friend_list_result["synced_at"])
        source_url = str(friend_list_result["source_url"])
    else:
        with db.connect() as connection:
            account_uid = _local_scope_account_uid(connection, latest_chat_list)
            session_ids = _local_session_ids(
                connection,
                account_uid,
                str(latest_chat_list["synced_at"]) if latest_chat_list else None,
            )
        new_session_ids = set()
        chat_list_synced_at = (
            str(latest_chat_list["synced_at"]) if latest_chat_list else None
        )
        source_url = ""
        age_minutes = _age_minutes(chat_list_synced_at, now)
        friend_list_result = {
            "account_uid": account_uid,
            "count": len(session_ids),
            "created_count": 0,
            "changed_count": 0,
            "source_url": "",
            "synced_at": chat_list_synced_at,
            "session_ids": session_ids,
            "created_session_ids": [],
            "reused_local_snapshot": True,
            "age_minutes": age_minutes,
        }
    with db.connect() as connection:
        latest_local = _latest_local_platform_message_at(connection)
        available_sessions = _load_sessions(connection, session_ids)
        sessions_in_scope = [
            session
            for session in available_sessions
            if (latest_at := _try_parse_time(session.get("platform_latest_message_at")))
            is not None
            and latest_at >= since
        ]
        # 与自动代聊“批量更新聊天记录”共用同一候选集合。
        batch_candidates = {
            str(candidate["id"]): candidate
            for candidate in boss_chat.get_batch_candidates(db)
        }
        sessions_to_sync = [
            session
            for session in sessions_in_scope
            if str(session["id"]) in batch_candidates
        ]

        related_jobs: dict[str, dict[str, Any]] = {}
        chat_update_jobs: dict[str, dict[str, Any]] = {}
        extra_jobs: dict[str, dict[str, Any]] = {}
        unresolved_session_ids: list[str] = []
        for session in sessions_in_scope:
            identity, job = _related_job_identity(connection, session)
            if identity:
                relation = {
                    "entity_id": identity,
                    "session_id": str(session["id"]),
                    "job_id": str(job["id"]) if job else None,
                    "encrypt_job_id": str(session.get("encrypt_job_id") or "") or None,
                }
                related_jobs.setdefault(identity, relation)
                needs_detail = job is None or _job_needs_refresh(job)
                if not needs_detail:
                    continue
                candidate = batch_candidates.get(str(session["id"]))
                if candidate and str(candidate.get("job_detail_status") or "") != "completed":
                    chat_update_jobs.setdefault(identity, relation)
                else:
                    extra_jobs.setdefault(identity, relation)
            else:
                unresolved_session_ids.append(str(session["id"]))

        for identity in list(extra_jobs):
            if identity in chat_update_jobs:
                extra_jobs.pop(identity, None)

        # 稳定岗位身份优先使用已有关联 job_id，其次使用 encrypt_job_id。
        jobs_to_update = dict(chat_update_jobs)
        for identity, relation in extra_jobs.items():
            jobs_to_update.setdefault(identity, relation)
        jobs_to_collect = list(extra_jobs.values())
        jobs_missing_jd: list[dict[str, Any]] = []
        jobs_missing_evaluation: list[dict[str, Any]] = []
        for relation in related_jobs.values():
            job = _job_for_relation(connection, relation)
            if job is None or _job_missing_jd(job):
                jobs_missing_jd.append(relation)
            if job is None:
                jobs_missing_evaluation.append(relation)
                continue
            evaluation = connection.execute(
                "SELECT 1 FROM fj_job_evaluations WHERE job_id = ? LIMIT 1",
                (job["id"],),
            ).fetchone()
            if evaluation is None:
                jobs_missing_evaluation.append(relation)

        scope_id = new_id()
        generated_at = utc_now()
        counts = {
            "refreshed_sessions": len(available_sessions),
            "sessions_in_scope": len(sessions_in_scope),
            "sessions_to_sync": len(sessions_to_sync),
            "new_sessions_to_sync": sum(
                str(session["id"]) in new_session_ids for session in sessions_to_sync
            ),
            "related_jobs": len(related_jobs),
            "chat_update_jobs": len(chat_update_jobs),
            "extra_jobs": len(extra_jobs),
            "jobs_to_update": len(jobs_to_update),
            "jobs_to_collect": len(jobs_to_collect),
            "jobs_missing_jd": len(jobs_missing_jd),
            "jobs_missing_evaluation": len(jobs_missing_evaluation),
            "unresolved_relations": len(unresolved_session_ids),
        }
        connection.execute(
            """
            INSERT INTO fj_job_hunt_refresh_scopes (
              id, selected_since_time, requested_source_mode, scope_source,
              account_uid, source_url, friend_list_synced_at, chat_list_synced_at,
              scope_generated_at, latest_local_message_at,
              session_ids_in_scope_json, session_ids_json,
              new_session_ids_json, related_jobs_json,
              jobs_to_collect_json, jobs_missing_jd_json,
              jobs_missing_evaluation_json, unresolved_session_ids_json,
              counts_json, friend_list_result_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                scope_id,
                _iso(since),
                requested_mode,
                resolved_source,
                account_uid,
                source_url,
                chat_list_synced_at or generated_at,
                chat_list_synced_at,
                generated_at,
                str(latest_local) if latest_local else None,
                _dump([str(session["id"]) for session in sessions_in_scope]),
                _dump([str(session["id"]) for session in sessions_to_sync]),
                _dump(sorted(new_session_ids)),
                _dump(list(related_jobs.values())),
                _dump(jobs_to_collect),
                _dump(jobs_missing_jd),
                _dump(jobs_missing_evaluation),
                _dump(unresolved_session_ids),
                _dump(counts),
                _dump(friend_list_result),
                generated_at,
            ),
        )
    return get_scope(db, scope_id)


def get_scope(db: Database, scope_id: str) -> dict[str, object]:
    with db.connect() as connection:
        row = connection.execute(
            "SELECT * FROM fj_job_hunt_refresh_scopes WHERE id = ?", (scope_id,)
        ).fetchone()
    if row is None:
        raise AppError(404, "REFRESH_SCOPE_NOT_FOUND", "更新范围快照不存在。")
    return _serialize_scope(row)


def create_run(
    db: Database,
    *,
    scope_id: str,
    workflow_options: dict[str, bool],
    trigger_source: str = "page",
) -> dict[str, object]:
    options = _normalize_workflows(workflow_options)
    scope = get_scope(db, scope_id)
    run_id = new_id()
    now = utc_now()
    with db.connect() as connection:
        existing = connection.execute(
            "SELECT id FROM fj_job_hunt_refresh_runs WHERE scope_id = ?", (scope_id,)
        ).fetchone()
        if existing is not None:
            raise AppError(409, "REFRESH_SCOPE_ALREADY_USED", "该更新范围已经创建过任务。")
        connection.execute(
            """
            INSERT INTO fj_job_hunt_refresh_runs (
              id, scope_id, scope_generated_at, status,
              selected_since_time, latest_local_message_at,
              workflow_options_json, estimated_sessions, estimated_update_sessions,
              estimated_jobs, estimated_refresh_jobs, estimated_missing_jd,
              estimated_missing_suggestions, chat_list_status, chat_list_retryable,
              current_step, trigger_source, created_at, updated_at
            ) VALUES (?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?, ?, ?, ?, 'succeeded', 0,
                      'waiting_codex', ?, ?, ?)
            """,
            (
                run_id,
                scope_id,
                scope["scope_generated_at"],
                scope["selected_since_time"],
                scope["latest_local_message_at"],
                _dump(options),
                scope["counts"]["sessions_in_scope"],
                scope["counts"]["sessions_to_sync"],
                scope["counts"]["related_jobs"],
                scope["counts"]["jobs_to_update"],
                scope["counts"]["jobs_missing_jd"],
                scope["counts"]["jobs_missing_evaluation"],
                trigger_source,
                now,
                now,
            ),
        )
        _create_run_items_from_scope(connection, run_id, scope, options)
        _refresh_run_counts(connection, run_id)
    return get_run(db, run_id)


def attach_codex_session(db: Database, run_id: str, codex_session_ref: str) -> dict[str, object]:
    current = get_run(db, run_id)
    if current["status"] in {"completed", "failed", "cancelled"}:
        raise AppError(409, "REFRESH_RUN_TERMINAL", "当前更新任务已经结束。")
    if current["status"] == "completed_with_errors" and not current["resume_available"]:
        raise AppError(409, "REFRESH_RUN_NOT_RESUMABLE", "当前任务没有可继续处理的项目。")
    now = utc_now()
    with db.connect() as connection:
        connection.execute(
            """
            UPDATE fj_job_hunt_refresh_runs
            SET codex_session_ref = ?,
                current_step = 'waiting_codex',
                prompt_submitted_at = NULL,
                updated_at = ? WHERE id = ?
            """,
            (codex_session_ref.strip() or None, now, run_id),
        )
    return get_run(db, run_id)


def mark_prompt_submitted(db: Database, run_id: str) -> dict[str, object]:
    run = get_run(db, run_id)
    if run["status"] in {"completed", "failed", "cancelled"}:
        raise AppError(409, "REFRESH_RUN_TERMINAL", "当前更新任务已经结束。")
    if not str(run.get("codex_session_ref") or "").strip():
        raise AppError(409, "REFRESH_CODEX_SESSION_MISSING", "任务尚未关联 Codex 会话。")
    chat_pending = any(
        item["item_type"] == "chat_session"
        and (
            item["status"] in {"pending", "running"}
            or (item["status"] == "failed" and item["retryable"])
        )
        for item in run["items"]
    )
    job_pending = any(
        item["item_type"] == "related_job"
        and (
            item["status"] in {"pending", "running"}
            or (item["status"] == "failed" and item["retryable"])
        )
        for item in run["items"]
    )
    next_step = (
        "waiting_chat_messages"
        if chat_pending
        else "waiting_related_jobs"
        if job_pending
        else _analysis_wait_step(run.get("summary", {}))
        if _analysis_requested(run["workflow_options"])
        else "waiting_completion"
    )
    now = utc_now()
    with db.connect() as connection:
        connection.execute(
            """
            UPDATE fj_job_hunt_refresh_runs
            SET status = 'running',
                current_step = ?, prompt_submitted_at = ?, completed_at = NULL,
                error_summary = NULL, updated_at = ?
            WHERE id = ?
            """,
            (next_step, now, now, run_id),
        )
    return get_run(db, run_id)


def cancel_run(db: Database, run_id: str) -> dict[str, object]:
    run = get_run(db, run_id)
    if run["status"] in {"completed", "failed", "cancelled"}:
        return run
    now = utc_now()
    with db.connect() as connection:
        connection.execute(
            """
            UPDATE fj_job_hunt_refresh_items
            SET status = 'skipped', retryable = 0,
                error_category = 'RUN_CANCELLED', error_message = '用户取消更新任务。',
                operation_ref_type = NULL, operation_ref_id = NULL,
                completed_at = COALESCE(completed_at, ?), updated_at = ?
            WHERE run_id = ? AND status NOT IN ('succeeded', 'skipped')
            """,
            (now, now, run_id),
        )
        _refresh_run_counts(connection, run_id)
        connection.execute(
            """
            UPDATE fj_job_hunt_refresh_runs
            SET status = 'cancelled', current_step = 'cancelled',
                completed_at = ?, updated_at = ? WHERE id = ?
            """,
            (now, now, run_id),
        )
    return get_run(db, run_id)


def list_runs(db: Database, *, limit: int = 10) -> list[dict[str, object]]:
    with db.connect() as connection:
        rows = connection.execute(
            "SELECT id FROM fj_job_hunt_refresh_runs ORDER BY created_at DESC LIMIT ?",
            (max(1, min(limit, 50)),),
        ).fetchall()
    return [get_run(db, str(row["id"])) for row in rows]


def get_run(db: Database, run_id: str) -> dict[str, object]:
    _reconcile_running_run(db, run_id)
    with db.connect() as connection:
        row = connection.execute(
            "SELECT * FROM fj_job_hunt_refresh_runs WHERE id = ?", (run_id,)
        ).fetchone()
        if row is None:
            raise AppError(404, "REFRESH_RUN_NOT_FOUND", "求职数据更新任务不存在。")
        item_rows = connection.execute(
            """
            SELECT * FROM fj_job_hunt_refresh_items
            WHERE run_id = ? ORDER BY item_type, created_at, id
            """,
            (run_id,),
        ).fetchall()
    run = _serialize_run(row)
    if run.get("scope_id"):
        run["scope"] = get_scope(db, str(run["scope_id"]))
    items = [_serialize_item(item) for item in item_rows]
    run["items"] = items
    run["progress"] = _progress(items, run)
    run["resume_available"] = _resume_available(run, items)
    return run


def list_actionable_items(
    db: Database,
    run_id: str,
    *,
    item_type: str,
) -> list[dict[str, object]]:
    if item_type not in {"chat_session", "related_job"}:
        raise AppError(422, "REFRESH_ITEM_TYPE_INVALID", "更新任务项类型无效。")
    run = _require_run(db, run_id)
    if run["status"] in {"completed", "failed", "cancelled"}:
        return []
    if item_type == "related_job":
        _skip_chat_batch_covered_related_jobs(db, run_id)
    with db.connect() as connection:
        rows = connection.execute(
            """
            SELECT * FROM fj_job_hunt_refresh_items
            WHERE run_id = ? AND item_type = ?
              AND (
                status IN ('pending', 'running')
                OR (status = 'failed' AND retryable = 1)
            )
            ORDER BY created_at, id
            LIMIT ?
            """,
            (run_id, item_type, CHAT_BATCH_LIMIT),
        ).fetchall()
        if rows:
            _set_run_running(connection, run_id, f"listing_{item_type}")
    return [_serialize_item(row) for row in rows]


def refresh_chat_list(db: Database, run_id: str) -> dict[str, object]:
    """兼容旧调用；聊天列表已在 Scope Discovery 阶段完成。"""
    run = _require_run(db, run_id)
    return {
        "status": "succeeded",
        "terminal": True,
        "reused": True,
        "result": dict(run.get("scope", {}).get("friend_list_result", {})),
        "run": get_run(db, run_id),
    }


def refresh_chat_messages(db: Database, run_id: str, item_id: str) -> dict[str, object]:
    run, item = _require_item(db, run_id, item_id, "chat_session")
    if not run["workflow_options"]["refresh_chat_messages"]:
        raise AppError(409, "WORKFLOW_NOT_SELECTED", "本次任务未选择更新聊天消息。")
    if item["status"] in {"succeeded", "skipped"}:
        return {"status": item["status"], "terminal": True, "reused": True, "item": item}
    _assert_session_in_scope(
        db,
        run,
        str(item["session_id"]),
        scope_key="session_ids_to_sync",
    )
    _mark_item_running(db, run_id, item_id, "refresh_chat_messages")
    try:
        result = boss_chat.refresh_session_history(db, str(item["session_id"]))
        result["selected_since_time"] = run["selected_since_time"]
        _mark_item_succeeded(db, run_id, item_id, result=result)
    except Exception as exc:
        _mark_item_failed(db, run_id, item_id, exc, retryable=True)
        raise
    return {
        "status": "succeeded",
        "terminal": True,
        "reused": False,
        "item": get_item(db, run_id, item_id),
        "run": get_run(db, run_id),
    }


def refresh_chat_messages_batch(
    db: Database,
    config: AppConfig,
    run_id: str,
) -> dict[str, object]:
    """按 Run 中未完成聊天 item 启动原自动代聊批量更新能力。"""
    run = _require_run(db, run_id)
    if not run["workflow_options"]["refresh_chat_messages"]:
        raise AppError(409, "WORKFLOW_NOT_SELECTED", "本次任务未选择更新聊天消息。")
    if run["status"] in {"completed", "failed", "cancelled"}:
        return {
            "status": run["status"],
            "terminal": True,
            "reused": True,
            "run": get_run(db, run_id),
        }

    active = _active_chat_batch_operation(db, run_id)
    if active is not None:
        task_id = str(active["operation_ref_id"])
        try:
            task = boss_chat.boss_chat_batch_manager.get(task_id)
        except AppError:
            _reset_lost_chat_batch_operation(db, run_id, task_id)
        else:
            status = str(task.get("status") or "queued")
            if status in {"queued", "running"}:
                return {
                    "status": status,
                    "terminal": False,
                    "operation": {"type": "chat_batch", "id": task_id},
                    "data": task,
                }
            return _finish_chat_batch_operation(db, run_id, task_id, task)

    items = _chat_batch_items(db, run_id)
    if not items:
        return {
            "status": "succeeded",
            "terminal": True,
            "reused": True,
            "run": get_run(db, run_id),
        }

    session_ids = [str(item["session_id"]) for item in items]
    _prepare_chat_page_for_batch(db, run_id)
    candidates = {
        str(candidate["id"])
        for candidate in boss_chat.get_batch_candidates(db, session_ids=session_ids)
    }
    reusable_ids = [
        str(item["id"]) for item in items if str(item["session_id"]) not in candidates
    ]
    for item_id in reusable_ids:
        _mark_item_succeeded(
            db,
            run_id,
            item_id,
            result={
                "outcome": "reused",
                "source": "boss_chat_batch",
                "selected_since_time": run["selected_since_time"],
            },
        )
    pending_session_ids = [
        str(item["session_id"]) for item in items if str(item["session_id"]) in candidates
    ]
    if not pending_session_ids:
        return {
            "status": "succeeded",
            "terminal": True,
            "reused": True,
            "run": get_run(db, run_id),
        }

    task = boss_chat.boss_chat_batch_manager.start(
        db,
        config,
        batch_size=len(pending_session_ids),
        session_ids=pending_session_ids,
    )
    _set_chat_batch_operation(db, run_id, items, str(task["id"]), set(pending_session_ids))
    return {
        "status": str(task.get("status") or "queued"),
        "terminal": False,
        "operation": {"type": "chat_batch", "id": str(task["id"])},
        "data": task,
        "run": get_run(db, run_id),
    }


def refresh_related_job(
    db: Database,
    config: AppConfig,
    run_id: str,
    item_id: str,
) -> dict[str, object]:
    run, item = _require_item(db, run_id, item_id, "related_job")
    if not run["workflow_options"]["refresh_related_jobs"]:
        raise AppError(409, "WORKFLOW_NOT_SELECTED", "本次任务未选择采集关联岗位。")
    if item["status"] in {"succeeded", "skipped"}:
        return {"status": item["status"], "terminal": True, "reused": True, "item": item}
    if _skip_chat_batch_covered_related_jobs(db, run_id, item_id=item_id):
        return {
            "status": "skipped",
            "terminal": True,
            "reused": True,
            "item": get_item(db, run_id, item_id),
            "run": get_run(db, run_id),
        }
    _assert_session_in_scope(
        db,
        run,
        str(item["session_id"]),
        scope_key="session_ids_in_scope",
    )

    operation_id = str(item.get("operation_ref_id") or "")
    if item["status"] == "running" and operation_id:
        try:
            task = boss_capture_task_manager.get_task(operation_id)
        except AppError:
            # 应用重启后内存任务不存在；数据库中的未完成 Item 可以从现有岗位状态继续。
            _clear_item_operation(db, run_id, item_id)
        else:
            raw_status = str(task.get("status") or "queued")
            if raw_status in {"queued", "running"}:
                return {
                    "status": raw_status,
                    "terminal": False,
                    "operation": {"type": "capture_task", "id": operation_id},
                    "data": task,
                }
            if raw_status == "completed":
                result = {
                    **dict(item.get("result") or {}),
                    "outcome": dict(item.get("result") or {}).get("outcome", "refreshed"),
                    "detail_status": "completed",
                    "capture_task_id": operation_id,
                }
                reconcile_chat_session_activity(db, str(item["session_id"]))
                _mark_item_succeeded(db, run_id, item_id, result=result)
                return {
                    "status": "succeeded",
                    "terminal": True,
                    "item": get_item(db, run_id, item_id),
                    "run": get_run(db, run_id),
                }
            error = RuntimeError(str(task.get("error_message") or "岗位详情采集失败。"))
            _mark_item_failed(db, run_id, item_id, error, retryable=True)
            return {
                "status": "failed",
                "terminal": True,
                "item": get_item(db, run_id, item_id),
                "run": get_run(db, run_id),
            }

    _mark_item_running(db, run_id, item_id, "refresh_related_job")
    try:
        with db.connect() as connection:
            session = connection.execute(
                "SELECT * FROM fj_chat_sessions WHERE id = ?", (item["session_id"],)
            ).fetchone()
            if session is None:
                raise AppError(404, "CHAT_SESSION_NOT_FOUND", "聊天会话不存在。")
            prior_job = _resolve_related_job(connection, dict(session))
        if not str(session["encrypt_job_id"] or "") and prior_job is None:
            result = {"outcome": "unresolved", "reason": "unresolved_job_relation"}
            _mark_item_skipped(
                db,
                run_id,
                item_id,
                result=result,
                error_category="UNRESOLVED_JOB_RELATION",
                error_message="会话缺少可靠岗位标识。",
            )
            return {
                "status": "skipped",
                "terminal": True,
                "item": get_item(db, run_id, item_id),
                "run": get_run(db, run_id),
            }

        prepared = boss_chat.prepare_chat_job(
            db,
            str(item["session_id"]),
            can_fetch_details=boss_scraper_service.get_browser_status().running,
        )
        history_job_id = str(prepared["history_job_id"])
        reconcile_chat_session_activity(db, str(item["session_id"]))
        prepared_job = dict(prepared["job"])
        if prepared["action"] == "view" and not _job_needs_refresh(prepared_job):
            result = {
                "outcome": "reused",
                "history_job_id": history_job_id,
                "detail_status": "completed",
            }
            _mark_item_succeeded(db, run_id, item_id, job_id=history_job_id, result=result)
            return {
                "status": "succeeded",
                "terminal": True,
                "item": get_item(db, run_id, item_id),
                "run": get_run(db, run_id),
            }

        if not boss_scraper_service.get_browser_status().running:
            raise AppError(
                409,
                "BROWSER_NOT_RUNNING",
                "FineJob 专用 Chrome 未启动，请先打开并完成 BOSS 登录。",
            )
        outcome = "created" if prior_job is None else "refreshed"
        task = boss_capture_task_manager.start_history_detail(
            prepared["job"],
            output_dir=config.output_root / "fine-job" / "boss-capture",
            db=db,
        )
        _set_item_operation(
            db,
            run_id,
            item_id,
            job_id=history_job_id,
            operation_id=str(task["id"]),
            result={"outcome": outcome, "history_job_id": history_job_id},
        )
        return {
            "status": str(task.get("status") or "queued"),
            "terminal": False,
            "operation": {"type": "capture_task", "id": str(task["id"])},
            "data": task,
        }
    except Exception as exc:
        _mark_item_failed(db, run_id, item_id, exc, retryable=True)
        raise


def complete_run(
    db: Database,
    run_id: str,
    config: AppConfig | None = None,
) -> dict[str, object]:
    run = _require_run(db, run_id)
    if run["status"] in {"completed", "failed", "cancelled"}:
        return get_run(db, run_id)
    _settle_terminal_chat_batches_for_run(db, run_id)
    _skip_chat_batch_covered_related_jobs(db, run_id)
    with db.connect() as connection:
        unfinished = int(
            connection.execute(
                """
                SELECT COUNT(*) FROM fj_job_hunt_refresh_items
                WHERE run_id = ? AND status IN ('pending', 'running')
                """,
                (run_id,),
            ).fetchone()[0]
        )
        if unfinished or run["chat_list_status"] in {"pending", "running"}:
            raise AppError(
                409,
                "REFRESH_RUN_NOT_FINISHABLE",
                "更新任务仍有未完成项，请继续处理后再汇总。",
            )

    if _analysis_requested(run["workflow_options"]):
        job_hunt_analysis.ensure_analysis_ready_for_completion(db, run_id)

    with db.connect() as connection:
        _refresh_run_counts(connection, run_id)
        failed = int(
            connection.execute(
                """
                SELECT COUNT(*) FROM fj_job_hunt_refresh_items
                WHERE run_id = ? AND status = 'failed'
                """,
                (run_id,),
            ).fetchone()[0]
        )
        unresolved = int(
            connection.execute(
                """
                SELECT COUNT(*) FROM fj_job_hunt_refresh_items
                WHERE run_id = ? AND error_category = 'UNRESOLVED_JOB_RELATION'
                """,
                (run_id,),
            ).fetchone()[0]
        )
        if run["workflow_options"]["refresh_related_jobs"] and run.get("scope_id"):
            scope = get_scope(db, str(run["scope_id"]))
            unresolved += int(scope["counts"].get("unresolved_relations") or 0)
        has_chat_list_error = run["chat_list_status"] == "failed"
        status = "completed_with_errors" if failed or unresolved or has_chat_list_error else "completed"
        now = utc_now()
        connection.execute(
            """
            UPDATE fj_job_hunt_refresh_runs
            SET status = ?, current_step = 'completed', completed_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (status, now, now, run_id),
        )
    return get_run(db, run_id)


def settle_chat_batch_operation(
    db: Database,
    task_id: str,
    task: dict[str, object] | None = None,
) -> dict[str, object]:
    """把原批量聊天任务的终态同步回关联的 Refresh Run Item。"""
    if task is None:
        task = boss_chat.boss_chat_batch_manager.get(task_id)
    raw_status = str(task.get("status") or "queued")
    if raw_status in {"queued", "running"}:
        return {
            "status": raw_status,
            "terminal": False,
            "operation": {"type": "chat_batch", "id": task_id},
            "data": task,
        }
    with db.connect() as connection:
        rows = connection.execute(
            """
            SELECT DISTINCT run_id
            FROM fj_job_hunt_refresh_items
            WHERE item_type = 'chat_session'
              AND operation_ref_type = 'chat_batch'
              AND operation_ref_id = ?
            """,
            (task_id,),
        ).fetchall()
    run_ids = [str(row["run_id"]) for row in rows]
    if not run_ids:
        return {
            "status": {"completed": "succeeded"}.get(raw_status, raw_status),
            "terminal": True,
            "operation": {"type": "chat_batch", "id": task_id},
            "data": task,
        }
    settled = [
        _finish_chat_batch_operation(db, run_id, task_id, task)
        for run_id in run_ids
    ]
    failed_items = sum(int(item.get("failed_items") or 0) for item in settled)
    return {
        "status": "failed" if failed_items else "succeeded",
        "terminal": True,
        "operation": {"type": "chat_batch", "id": task_id},
        "data": task,
        "failed_items": failed_items,
        "runs": [item.get("run") for item in settled],
        "run": settled[-1].get("run") if settled else None,
    }


def get_item(db: Database, run_id: str, item_id: str) -> dict[str, object]:
    with db.connect() as connection:
        row = connection.execute(
            "SELECT * FROM fj_job_hunt_refresh_items WHERE id = ? AND run_id = ?",
            (item_id, run_id),
        ).fetchone()
    if row is None:
        raise AppError(404, "REFRESH_ITEM_NOT_FOUND", "更新任务项不存在。")
    return _serialize_item(row)


def _create_run_items_from_scope(
    connection,
    run_id: str,
    scope: dict[str, object],
    options: dict[str, bool],
) -> None:
    """只从持久化 Scope 复制 Item，保证确认后范围保持固定。"""
    now = utc_now()
    if options.get("refresh_chat_messages"):
        for session_id in scope["session_ids_to_sync"]:
            connection.execute(
                """
                INSERT OR IGNORE INTO fj_job_hunt_refresh_items (
                  id, run_id, item_type, entity_id, session_id, status, step,
                  retryable, created_at, updated_at
                ) VALUES (?, ?, 'chat_session', ?, ?, 'pending', 'refresh_chat_messages', 1, ?, ?)
                """,
                (new_id(), run_id, str(session_id), str(session_id), now, now),
            )
    if options.get("refresh_related_jobs"):
        for relation in scope["jobs_to_collect"]:
            connection.execute(
                """
                INSERT OR IGNORE INTO fj_job_hunt_refresh_items (
                  id, run_id, item_type, entity_id, session_id, job_id,
                  status, step, retryable, created_at, updated_at
                ) VALUES (?, ?, 'related_job', ?, ?, ?, 'pending', 'refresh_related_job', 1, ?, ?)
                """,
                (
                    new_id(),
                    run_id,
                    str(relation["entity_id"]),
                    str(relation["session_id"]),
                    str(relation["job_id"]) if relation.get("job_id") else None,
                    now,
                    now,
                ),
            )


def _reconcile_running_run(db: Database, run_id: str) -> None:
    with db.connect() as connection:
        row = connection.execute(
            """
            SELECT status, chat_list_status, prompt_submitted_at
            FROM fj_job_hunt_refresh_runs
            WHERE id = ?
            """,
            (run_id,),
        ).fetchone()
    if row is None:
        return
    status = str(row["status"])
    prompt_submitted = bool(str(row["prompt_submitted_at"] or "").strip())
    if status == "pending" and prompt_submitted:
        with db.connect() as connection:
            connection.execute(
                """
                UPDATE fj_job_hunt_refresh_runs
                SET status = 'running',
                    started_at = COALESCE(started_at, ?),
                    updated_at = ?
                WHERE id = ? AND status = 'pending'
                """,
                (utc_now(), utc_now(), run_id),
            )
    elif status != "running":
        return
    _settle_terminal_chat_batches_for_run(db, run_id)
    _skip_chat_batch_covered_related_jobs(db, run_id)
    with db.connect() as connection:
        refreshed = connection.execute(
            """
            SELECT status, chat_list_status, scope_id, summary_json, workflow_options_json
            FROM fj_job_hunt_refresh_runs
            WHERE id = ?
            """,
            (run_id,),
        ).fetchone()
        if refreshed is None or str(refreshed["status"]) != "running":
            return
        unfinished = int(
            connection.execute(
                """
                SELECT COUNT(*)
                FROM fj_job_hunt_refresh_items
                WHERE run_id = ? AND (
                  status IN ('pending', 'running')
                  OR (status = 'failed' AND retryable = 1)
                )
                """,
                (run_id,),
            ).fetchone()[0]
        )
        if unfinished or str(refreshed["chat_list_status"]) in {"pending", "running"}:
            return
        options = _load(refreshed["workflow_options_json"], {})
        if _analysis_requested(options):
            summary = _load(refreshed["summary_json"], {})
            next_step = _analysis_wait_step(summary)
            connection.execute(
                """
                UPDATE fj_job_hunt_refresh_runs
                SET current_step = ?, updated_at = ?
                WHERE id = ? AND current_step <> ?
                """,
                (next_step, utc_now(), run_id, next_step),
            )
            return
        # 所有数据项完成后等待显式 complete 汇总，避免只读取 Run 就提前终结任务。
        connection.execute(
            """
            UPDATE fj_job_hunt_refresh_runs SET current_step = 'waiting_completion', updated_at = ?
            WHERE id = ? AND status = 'running'
            """,
            (utc_now(), run_id),
        )


def _skip_chat_batch_covered_related_jobs(
    db: Database,
    run_id: str,
    *,
    item_id: str | None = None,
) -> int:
    with db.connect() as connection:
        run = connection.execute(
            """
            SELECT scope_id, workflow_options_json
            FROM fj_job_hunt_refresh_runs
            WHERE id = ?
            """,
            (run_id,),
        ).fetchone()
        if run is None or not run["scope_id"]:
            return 0
        options = _load(run["workflow_options_json"], {})
        if not bool(options.get("refresh_chat_messages")):
            return 0
        actionable_chat = int(
            connection.execute(
                """
                SELECT COUNT(*)
                FROM fj_job_hunt_refresh_items
                WHERE run_id = ? AND item_type = 'chat_session'
                  AND (
                    status IN ('pending', 'running')
                    OR (status = 'failed' AND retryable = 1)
                  )
                """,
                (run_id,),
            ).fetchone()[0]
        )
        if actionable_chat:
            return 0
        scope = connection.execute(
            """
            SELECT session_ids_json
            FROM fj_job_hunt_refresh_scopes
            WHERE id = ?
            """,
            (run["scope_id"],),
        ).fetchone()
        if scope is None:
            return 0
        session_ids = [
            str(value).strip()
            for value in _load(scope["session_ids_json"], [])
            if str(value).strip()
        ]
        if not session_ids:
            return 0
        placeholders = ",".join("?" for _ in session_ids)
        item_filter = "AND id = ?" if item_id else ""
        parameters: tuple[object, ...] = (
            run_id,
            *session_ids,
            *([item_id] if item_id else []),
        )
        now = utc_now()
        result = {
            "outcome": "covered_by_chat_batch",
            "source": "boss_chat_batch",
            "reason": "聊天批量入口已负责该会话关联岗位。",
        }
        cursor = connection.execute(
            f"""
            UPDATE fj_job_hunt_refresh_items
            SET status = 'skipped', retryable = 0, result_json = ?,
                error_category = 'RELATED_JOB_COVERED_BY_CHAT_BATCH',
                error_message = '聊天批量入口已负责该会话关联岗位。',
                operation_ref_type = NULL, operation_ref_id = NULL,
                completed_at = ?, updated_at = ?
            WHERE run_id = ? AND item_type = 'related_job'
              AND status NOT IN ('succeeded', 'skipped')
              AND session_id IN ({placeholders})
              {item_filter}
            """,
            (_dump(result), now, now, *parameters),
        )
        changed = int(cursor.rowcount)
        if changed:
            _refresh_run_counts(connection, run_id)
        return changed


def _prepare_chat_page_for_batch(db: Database, run_id: str) -> dict[str, object]:
    """复用自动代聊“更新信息”入口，让后续历史消息请求拥有可用聊天页。"""
    try:
        captured = boss_scraper_service.capture_chat_friend_list()
        result = boss_chat.sync_friend_list(
            db,
            account_uid=str(captured["account_uid"]),
            response=captured["response"],
            source_url=str(captured["url"]),
        )
    except ValueError as exc:
        raise AppError(
            400,
            "BOSS_CHAT_LIST_CAPTURE_INVALID",
            str(exc),
        ) from exc
    except RuntimeError as exc:
        raise AppError(
            409,
            "BOSS_CHAT_LIST_CAPTURE_FAILED",
            str(exc),
        ) from exc
    _record_chat_list_prepare_result(db, run_id, result)
    return result


def _record_chat_list_prepare_result(
    db: Database,
    run_id: str,
    result: dict[str, object],
) -> None:
    now = utc_now()
    with db.connect() as connection:
        row = connection.execute(
            "SELECT summary_json FROM fj_job_hunt_refresh_runs WHERE id = ?",
            (run_id,),
        ).fetchone()
        summary = _load(row["summary_json"] if row else "{}", {})
        summary["chat_list"] = {
            "status": "succeeded",
            "prepared_for_chat_batch": True,
            "result": result,
        }
        connection.execute(
            """
            UPDATE fj_job_hunt_refresh_runs
            SET chat_list_status = 'succeeded', chat_list_retryable = 0,
                summary_json = ?, updated_at = ?
            WHERE id = ?
            """,
            (_dump(summary), now, run_id),
        )


def _active_chat_batch_operation(
    db: Database,
    run_id: str,
) -> dict[str, object] | None:
    with db.connect() as connection:
        row = connection.execute(
            """
            SELECT * FROM fj_job_hunt_refresh_items
            WHERE run_id = ? AND item_type = 'chat_session'
              AND status = 'running'
              AND operation_ref_type = 'chat_batch'
              AND operation_ref_id IS NOT NULL
            ORDER BY updated_at DESC LIMIT 1
            """,
            (run_id,),
        ).fetchone()
    return _serialize_item(row) if row is not None else None


def _chat_batch_items(db: Database, run_id: str) -> list[dict[str, object]]:
    with db.connect() as connection:
        rows = connection.execute(
            """
            SELECT * FROM fj_job_hunt_refresh_items
            WHERE run_id = ? AND item_type = 'chat_session'
              AND (
                status IN ('pending', 'running')
                OR (status = 'failed' AND retryable = 1)
              )
            ORDER BY created_at, id
            LIMIT ?
            """,
            (run_id, CHAT_BATCH_LIMIT),
        ).fetchall()
        if rows:
            _set_run_running(connection, run_id, "refresh_chat_messages_batch")
    return [_serialize_item(row) for row in rows]


def _set_chat_batch_operation(
    db: Database,
    run_id: str,
    items: list[dict[str, object]],
    task_id: str,
    session_ids: set[str],
) -> None:
    now = utc_now()
    with db.connect() as connection:
        _set_run_running(connection, run_id, "refresh_chat_messages_batch")
        for item in items:
            if str(item["session_id"]) not in session_ids:
                continue
            connection.execute(
                """
                UPDATE fj_job_hunt_refresh_items
                SET status = 'running', step = 'refresh_chat_messages',
                    started_at = COALESCE(started_at, ?),
                    completed_at = NULL, error_category = NULL, error_message = NULL,
                    operation_ref_type = 'chat_batch', operation_ref_id = ?,
                    updated_at = ?
                WHERE id = ? AND run_id = ? AND status <> 'succeeded'
                """,
                (now, task_id, now, item["id"], run_id),
            )
        _refresh_run_counts(connection, run_id)


def _finish_chat_batch_operation(
    db: Database,
    run_id: str,
    task_id: str,
    task: dict[str, object],
) -> dict[str, object]:
    with db.connect() as connection:
        rows = connection.execute(
            """
            SELECT * FROM fj_job_hunt_refresh_items
            WHERE run_id = ? AND item_type = 'chat_session'
              AND operation_ref_type = 'chat_batch'
              AND operation_ref_id = ?
            ORDER BY created_at, id
            """,
            (run_id, task_id),
        ).fetchall()
    items = [_serialize_item(row) for row in rows]
    session_ids = [str(item["session_id"]) for item in items]
    remaining = {
        str(candidate["id"])
        for candidate in boss_chat.get_batch_candidates(db, session_ids=session_ids)
    }
    run = _require_run(db, run_id)
    failed_count = 0
    for item in items:
        session_id = str(item["session_id"])
        if session_id not in remaining:
            _mark_item_succeeded(
                db,
                run_id,
                str(item["id"]),
                result={
                    "outcome": "updated",
                    "source": "boss_chat_batch",
                    "batch_task_id": task_id,
                    "selected_since_time": run["selected_since_time"],
                },
            )
            continue
        failed_count += 1
        _mark_item_failed(
            db,
            run_id,
            str(item["id"]),
            AppError(
                409,
                "BOSS_CHAT_BATCH_ITEM_FAILED",
                str(task.get("message") or "原批量聊天更新未完成该会话。"),
            ),
            retryable=True,
        )
    return {
        "status": "failed" if failed_count else "succeeded",
        "terminal": True,
        "operation": {"type": "chat_batch", "id": task_id},
        "data": task,
        "failed_items": failed_count,
        "run": get_run(db, run_id),
    }


def _settle_terminal_chat_batches_for_run(db: Database, run_id: str) -> None:
    with db.connect() as connection:
        rows = connection.execute(
            """
            SELECT DISTINCT operation_ref_id
            FROM fj_job_hunt_refresh_items
            WHERE run_id = ? AND item_type = 'chat_session'
              AND status = 'running'
              AND operation_ref_type = 'chat_batch'
              AND operation_ref_id IS NOT NULL
            """,
            (run_id,),
        ).fetchall()
    for row in rows:
        task_id = str(row["operation_ref_id"])
        try:
            task = boss_chat.boss_chat_batch_manager.get(task_id)
        except AppError:
            _reset_lost_chat_batch_operation(db, run_id, task_id)
            continue
        if str(task.get("status") or "queued") not in {"queued", "running"}:
            _finish_chat_batch_operation(db, run_id, task_id, task)


def _reset_lost_chat_batch_operation(
    db: Database,
    run_id: str,
    task_id: str,
) -> None:
    now = utc_now()
    with db.connect() as connection:
        connection.execute(
            """
            UPDATE fj_job_hunt_refresh_items
            SET status = 'failed', retryable = 1,
                operation_ref_type = NULL, operation_ref_id = NULL,
                error_category = 'CHAT_BATCH_TASK_LOST',
                error_message = '批量聊天更新任务状态已丢失，可重新执行。',
                completed_at = ?, updated_at = ?
            WHERE run_id = ? AND item_type = 'chat_session'
              AND operation_ref_type = 'chat_batch'
              AND operation_ref_id = ?
            """,
            (now, now, run_id, task_id),
        )
        _refresh_run_counts(connection, run_id)


def _latest_local_platform_message_at(connection) -> str | None:
    """只统计已经拥有真实平台消息标识的聊天记录。"""
    row = connection.execute(
        """
        SELECT MAX(sent_at) AS latest_at
        FROM fj_chat_messages
        WHERE TRIM(platform_message_id) <> ''
          AND NOT (
            source = 'assistant'
            AND platform_message_id LIKE 'assistant:%'
          )
        """
    ).fetchone()
    return str(row["latest_at"]) if row and row["latest_at"] else None


def _latest_chat_list_sync(connection) -> dict[str, str] | None:
    row = connection.execute(
        """
        SELECT account_uid, MAX(platform_synced_at) AS synced_at
        FROM fj_chat_sessions
        WHERE platform_synced_at IS NOT NULL AND TRIM(platform_synced_at) <> ''
        GROUP BY account_uid
        ORDER BY synced_at DESC
        LIMIT 1
        """
    ).fetchone()
    if row is None or not row["synced_at"]:
        return None
    return {"account_uid": str(row["account_uid"]), "synced_at": str(row["synced_at"])}


def _local_scope_account_uid(connection, latest_sync: dict[str, str] | None) -> str:
    if latest_sync:
        return str(latest_sync["account_uid"])
    row = connection.execute(
        "SELECT account_uid FROM fj_chat_sessions ORDER BY updated_at DESC LIMIT 1"
    ).fetchone()
    return str(row["account_uid"]) if row is not None else ""


def _local_session_ids(
    connection,
    account_uid: str,
    latest_synced_at: str | None,
) -> list[str]:
    if not account_uid:
        return []
    # 有可靠同步时间时，只复用最近一次 friend-list 响应实际包含的会话。
    synced_filter = " AND platform_synced_at = ?" if latest_synced_at else ""
    parameters = (account_uid, latest_synced_at) if latest_synced_at else (account_uid,)
    rows = connection.execute(
        f"""
        SELECT id FROM fj_chat_sessions
        WHERE account_uid = ?
        {synced_filter}
        ORDER BY platform_list_index, id
        """,
        parameters,
    ).fetchall()
    return [str(row["id"]) for row in rows]


def _age_minutes(value: str | None, now: datetime) -> int | None:
    parsed = _try_parse_time(value)
    if parsed is None:
        return None
    return max(0, int((now - parsed).total_seconds() // 60))


def _load_sessions(connection, session_ids: list[str]) -> list[dict[str, Any]]:
    if not session_ids:
        return []
    placeholders = ",".join("?" for _ in session_ids)
    rows = connection.execute(
        f"""
        SELECT s.*,
               EXISTS(
                 SELECT 1 FROM fj_chat_messages m
                 WHERE m.session_id = s.id
                   AND m.platform_message_id = s.platform_latest_msg_id
               ) AS latest_message_loaded
        FROM fj_chat_sessions s
        WHERE s.id IN ({placeholders})
        ORDER BY s.platform_list_index, s.id
        """,
        tuple(session_ids),
    ).fetchall()
    return [dict(row) for row in rows]


def _related_job_identity(connection, session: dict[str, Any]) -> tuple[str | None, dict[str, Any] | None]:
    job = _resolve_related_job(connection, session)
    if job is not None:
        return f"job:{job['id']}", job
    encrypt_job_id = str(session.get("encrypt_job_id") or "").strip()
    if encrypt_job_id:
        return f"encrypt:{encrypt_job_id}", None
    return None, None


def _resolve_related_job(connection, session: dict[str, Any]) -> dict[str, Any] | None:
    job_id = str(session.get("job_id") or "").strip()
    if job_id:
        row = connection.execute(
            "SELECT * FROM fj_boss_jobs WHERE id = ?", (job_id,)
        ).fetchone()
        if row is not None:
            return dict(row)
    encrypt_job_id = str(session.get("encrypt_job_id") or "").strip()
    if encrypt_job_id:
        row = connection.execute(
            """
            SELECT * FROM fj_boss_jobs
            WHERE encrypt_job_id = ? ORDER BY last_collected_at DESC LIMIT 1
            """,
            (encrypt_job_id,),
        ).fetchone()
        if row is not None:
            return dict(row)
    return None


def _job_for_relation(connection, relation: dict[str, Any]) -> dict[str, Any] | None:
    job_id = str(relation.get("job_id") or "").strip()
    if not job_id:
        return None
    row = connection.execute("SELECT * FROM fj_boss_jobs WHERE id = ?", (job_id,)).fetchone()
    return dict(row) if row is not None else None


def _job_needs_refresh(job: dict[str, Any]) -> bool:
    company_name = job.get("company_name") or job.get("boss_name")
    return _job_missing_jd(job) or any(
        not str(value or "").strip()
        for value in (job.get("title"), company_name, job.get("salary"), job.get("location"))
    )


def _job_missing_jd(job: dict[str, Any]) -> bool:
    if str(job.get("detail_status") or "") != "completed":
        return True
    detail = job.get("detail")
    if not isinstance(detail, dict):
        detail = _load(job.get("detail_json"), {})
    if not isinstance(detail, dict):
        return True
    return not any(
        str(detail.get(field) or "").strip()
        for field in ("jd", "description", "job_description")
    )


def _assert_session_in_scope(
    db: Database,
    run: dict[str, object],
    session_id: str,
    *,
    scope_key: str,
) -> None:
    scope = run.get("scope") or get_scope(db, str(run.get("scope_id") or ""))
    scoped_ids = {str(value) for value in scope[scope_key]}
    if session_id not in scoped_ids:
        raise AppError(
            409,
            "SESSION_OUTSIDE_REFRESH_SCOPE",
            "聊天会话不在本次已确认的更新范围内。",
        )


def _normalize_workflows(options: dict[str, bool]) -> dict[str, bool]:
    normalized = {name: bool(options.get(name, False)) for name in SUPPORTED_WORKFLOWS}
    normalized["refresh_chat_list"] = True
    if (
        normalized["generate_reply_drafts"]
        or normalized["generate_followup_recommendations"]
    ):
        normalized["analyze_conversations"] = True
    if not (
        normalized["refresh_chat_messages"]
        or normalized["refresh_related_jobs"]
        or normalized["analyze_conversations"]
        or normalized["generate_missing_suggestions"]
        or normalized["generate_reply_drafts"]
        or normalized["generate_followup_recommendations"]
    ):
        raise AppError(
            422,
            "REFRESH_WORKFLOW_REQUIRED",
            "请至少选择一个可执行工作流。",
        )
    return normalized


def _analysis_requested(options: dict[str, object]) -> bool:
    return any(
        bool(options.get(name))
        for name in (
            "analyze_conversations",
            "generate_missing_suggestions",
            "generate_reply_drafts",
            "generate_followup_recommendations",
        )
    )


def _analysis_wait_step(summary: dict[str, object]) -> str:
    analysis = summary.get("analysis") if isinstance(summary.get("analysis"), dict) else {}
    analysis_status = str(analysis.get("status") or "")
    if analysis_status == "saved":
        return "waiting_completion"
    if analysis_status in {"prepared", "saved_partial"}:
        return "waiting_analysis_save"
    return "waiting_analysis_prepare"


def _require_run(db: Database, run_id: str) -> dict[str, object]:
    with db.connect() as connection:
        row = connection.execute(
            "SELECT * FROM fj_job_hunt_refresh_runs WHERE id = ?", (run_id,)
        ).fetchone()
    if row is None:
        raise AppError(404, "REFRESH_RUN_NOT_FOUND", "求职数据更新任务不存在。")
    return _serialize_run(row)


def _require_item(
    db: Database,
    run_id: str,
    item_id: str,
    item_type: str,
) -> tuple[dict[str, object], dict[str, object]]:
    run = _require_run(db, run_id)
    item = get_item(db, run_id, item_id)
    if item["item_type"] != item_type:
        raise AppError(422, "REFRESH_ITEM_TYPE_INVALID", "更新任务项类型不匹配。")
    if run["status"] in {"completed", "failed", "cancelled"}:
        raise AppError(409, "REFRESH_RUN_TERMINAL", "当前更新任务已经结束。")
    return run, item


def _set_run_running(connection, run_id: str, step: str) -> None:
    now = utc_now()
    connection.execute(
        """
        UPDATE fj_job_hunt_refresh_runs
        SET status = 'running', current_step = ?,
            started_at = COALESCE(started_at, ?), completed_at = NULL, updated_at = ?
        WHERE id = ? AND status NOT IN ('completed', 'failed', 'cancelled')
        """,
        (step, now, now, run_id),
    )


def _mark_item_running(db: Database, run_id: str, item_id: str, step: str) -> None:
    now = utc_now()
    with db.connect() as connection:
        _set_run_running(connection, run_id, step)
        connection.execute(
            """
            UPDATE fj_job_hunt_refresh_items
            SET status = 'running', step = ?, started_at = COALESCE(started_at, ?),
                completed_at = NULL, error_category = NULL, error_message = NULL,
                updated_at = ?
            WHERE id = ? AND run_id = ? AND status <> 'succeeded'
            """,
            (step, now, now, item_id, run_id),
        )


def _mark_item_succeeded(
    db: Database,
    run_id: str,
    item_id: str,
    *,
    result: dict[str, object],
    job_id: str | None = None,
) -> None:
    now = utc_now()
    with db.connect() as connection:
        connection.execute(
            """
            UPDATE fj_job_hunt_refresh_items
            SET status = 'succeeded', retryable = 0, job_id = COALESCE(?, job_id),
                result_json = ?, error_category = NULL, error_message = NULL,
                operation_ref_type = NULL, operation_ref_id = NULL,
                completed_at = ?, updated_at = ?
            WHERE id = ? AND run_id = ?
            """,
            (job_id, _dump(result), now, now, item_id, run_id),
        )
        _refresh_run_counts(connection, run_id)


def _mark_item_skipped(
    db: Database,
    run_id: str,
    item_id: str,
    *,
    result: dict[str, object],
    error_category: str,
    error_message: str,
) -> None:
    now = utc_now()
    with db.connect() as connection:
        connection.execute(
            """
            UPDATE fj_job_hunt_refresh_items
            SET status = 'skipped', retryable = 0, result_json = ?,
                error_category = ?, error_message = ?, completed_at = ?, updated_at = ?
            WHERE id = ? AND run_id = ?
            """,
            (_dump(result), error_category, error_message, now, now, item_id, run_id),
        )
        _refresh_run_counts(connection, run_id)


def _mark_item_failed(
    db: Database,
    run_id: str,
    item_id: str,
    error: Exception,
    *,
    retryable: bool,
) -> None:
    category = error.error_category if isinstance(error, AppError) else type(error).__name__
    message = error.error_message if isinstance(error, AppError) else str(error)
    now = utc_now()
    with db.connect() as connection:
        connection.execute(
            """
            UPDATE fj_job_hunt_refresh_items
            SET status = 'failed', retryable = ?, error_category = ?, error_message = ?,
                completed_at = ?, updated_at = ?
            WHERE id = ? AND run_id = ?
            """,
            (int(retryable), str(category)[:120], str(message)[:500], now, now, item_id, run_id),
        )
        _refresh_run_counts(connection, run_id)


def _set_item_operation(
    db: Database,
    run_id: str,
    item_id: str,
    *,
    job_id: str,
    operation_id: str,
    result: dict[str, object],
) -> None:
    with db.connect() as connection:
        connection.execute(
            """
            UPDATE fj_job_hunt_refresh_items
            SET status = 'running', job_id = ?, operation_ref_type = 'capture_task',
                operation_ref_id = ?, result_json = ?, updated_at = ?
            WHERE id = ? AND run_id = ?
            """,
            (job_id, operation_id, _dump(result), utc_now(), item_id, run_id),
        )
        _refresh_run_counts(connection, run_id)


def _clear_item_operation(db: Database, run_id: str, item_id: str) -> None:
    with db.connect() as connection:
        connection.execute(
            """
            UPDATE fj_job_hunt_refresh_items
            SET status = 'pending', operation_ref_type = NULL, operation_ref_id = NULL,
                updated_at = ? WHERE id = ? AND run_id = ?
            """,
            (utc_now(), item_id, run_id),
        )


def _refresh_run_counts(connection, run_id: str) -> None:
    rows = connection.execute(
        "SELECT item_type, status, result_json, error_category FROM fj_job_hunt_refresh_items WHERE run_id = ?",
        (run_id,),
    ).fetchall()
    chat_rows = [row for row in rows if row["item_type"] == "chat_session"]
    job_rows = [row for row in rows if row["item_type"] == "related_job"]
    old_summary_row = connection.execute(
        "SELECT summary_json, scope_id FROM fj_job_hunt_refresh_runs WHERE id = ?", (run_id,)
    ).fetchone()
    old_summary = _load(old_summary_row["summary_json"] if old_summary_row else "{}", {})
    scope_unresolved = 0
    if old_summary_row and old_summary_row["scope_id"]:
        scope_row = connection.execute(
            "SELECT counts_json FROM fj_job_hunt_refresh_scopes WHERE id = ?",
            (old_summary_row["scope_id"],),
        ).fetchone()
        if scope_row:
            scope_unresolved = int(
                _load(scope_row["counts_json"], {}).get("unresolved_relations") or 0
            )
    summary = {
        "chat_list": old_summary.get("chat_list", {}),
        "analysis": old_summary.get("analysis", {}),
        "sessions_total": len(chat_rows),
        "sessions_succeeded": sum(row["status"] == "succeeded" for row in chat_rows),
        "sessions_failed": sum(row["status"] == "failed" for row in chat_rows),
        "new_messages": sum(
            int(_load(row["result_json"], {}).get("inserted_count") or 0)
            for row in chat_rows
        ),
        "related_jobs_total": len(job_rows),
        "jobs_succeeded": sum(row["status"] == "succeeded" for row in job_rows),
        "jobs_failed": sum(row["status"] == "failed" for row in job_rows),
        "jobs_created": sum(
            _load(row["result_json"], {}).get("outcome") == "created" for row in job_rows
        ),
        "jobs_refreshed": sum(
            _load(row["result_json"], {}).get("outcome") == "refreshed" for row in job_rows
        ),
        "jobs_reused": sum(
            _load(row["result_json"], {}).get("outcome") == "reused" for row in job_rows
        ),
        "unresolved_jobs": scope_unresolved + sum(
            row["error_category"] == "UNRESOLVED_JOB_RELATION" for row in job_rows
        ),
    }
    analysis = summary["analysis"] if isinstance(summary.get("analysis"), dict) else {}
    progress_summary = _scope_progress_summary(connection, old_summary_row)
    summary.update(
        {
            "conversations_analyzed": int(analysis.get("analyzed") or 0),
            "conversations_skipped": int(analysis.get("skipped") or 0),
            "conversation_analysis_failed": int(analysis.get("failed") or 0),
            "activities_written": int(analysis.get("activities_created") or 0),
            "reply_drafts_generated": int(analysis.get("generated_reply_draft") or 0),
            "missing_suggestions_total": int(analysis.get("evaluation_jobs_total") or 0),
            "missing_suggestions_generated": int(analysis.get("generated_evaluation") or 0),
            "missing_suggestions_skipped": int(analysis.get("evaluation_jobs_skipped") or 0),
            "progress_updates": int(analysis.get("updated_pipeline") or 0),
            "waiting_for_recruiter": progress_summary["waiting_for_recruiter"],
            "waiting_for_candidate": progress_summary["waiting_for_candidate"],
            "followup_recommended": progress_summary["followup_recommended"],
            "resume_viewed": progress_summary["resume_viewed"],
            "under_review": progress_summary["under_review"],
            "rejections_detected": int(analysis.get("rejection_detected") or 0),
            "jobs_closed": progress_summary["jobs_closed"],
        }
    )
    connection.execute(
        """
        UPDATE fj_job_hunt_refresh_runs
        SET processed_sessions = ?, processed_jobs = ?, failed_sessions = ?, failed_jobs = ?,
            summary_json = ?, updated_at = ? WHERE id = ?
        """,
        (
            sum(row["status"] in {"succeeded", "failed", "skipped"} for row in chat_rows),
            sum(row["status"] in {"succeeded", "failed", "skipped"} for row in job_rows),
            summary["sessions_failed"],
            summary["jobs_failed"],
            _dump(summary),
            utc_now(),
            run_id,
        ),
    )


def _scope_progress_summary(connection, run_row) -> dict[str, int]:
    result = {
        "waiting_for_recruiter": 0,
        "waiting_for_candidate": 0,
        "followup_recommended": 0,
        "resume_viewed": 0,
        "under_review": 0,
        "jobs_closed": 0,
    }
    if not run_row or not run_row["scope_id"]:
        return result
    scope = connection.execute(
        """
        SELECT session_ids_in_scope_json, session_ids_json
        FROM fj_job_hunt_refresh_scopes WHERE id = ?
        """,
        (run_row["scope_id"],),
    ).fetchone()
    session_ids = _load(scope["session_ids_in_scope_json"], []) if scope else []
    if not session_ids and scope:
        session_ids = _load(scope["session_ids_json"], [])
    session_ids = [str(value) for value in session_ids if str(value)]
    if not session_ids:
        return result
    placeholders = ",".join("?" for _ in session_ids)
    rows = connection.execute(
        f"""
        SELECT s.id AS session_id, s.job_id, p.stage, p.waiting_on,
               a.attention_status
        FROM fj_chat_sessions s
        LEFT JOIN fj_job_pipeline_snapshots p ON p.job_id = s.job_id
        LEFT JOIN fj_chat_attention_states a ON a.session_id = s.id
        WHERE s.id IN ({placeholders})
        """,
        tuple(session_ids),
    ).fetchall()
    result["waiting_for_recruiter"] = sum(row["waiting_on"] == "recruiter" for row in rows)
    result["waiting_for_candidate"] = sum(row["waiting_on"] == "candidate" for row in rows)
    result["followup_recommended"] = sum(row["attention_status"] == "needs_followup" for row in rows)
    jobs = {str(row["job_id"]): str(row["stage"] or "") for row in rows if row["job_id"]}
    result["resume_viewed"] = sum(stage == "resume_viewed" for stage in jobs.values())
    result["under_review"] = sum(stage == "under_review" for stage in jobs.values())
    result["jobs_closed"] = sum(stage == "closed" for stage in jobs.values())
    return result


def _progress(items: list[dict[str, object]], run: dict[str, object]) -> dict[str, object]:
    chat = [item for item in items if item["item_type"] == "chat_session"]
    jobs = [item for item in items if item["item_type"] == "related_job"]
    scope = run.get("scope") if isinstance(run.get("scope"), dict) else {}
    scope_counts = scope.get("counts") if isinstance(scope.get("counts"), dict) else {}
    related_job_total = int(scope_counts.get("jobs_to_update") or len(jobs))
    chat_status_by_session = {
        str(item["session_id"]): str(item["status"])
        for item in chat
        if item.get("session_id")
    }
    extra_entities = {
        str(item.get("entity_id"))
        for item in scope.get("jobs_to_collect", [])
        if isinstance(item, dict) and item.get("entity_id")
    }
    related_entities = {
        str(item.get("entity_id")): item
        for item in scope.get("related_jobs", [])
        if isinstance(item, dict) and item.get("entity_id")
    }
    completed_related_entities: set[str] = set()
    failed_related_entities: set[str] = set()
    for entity_id, relation in related_entities.items():
        session_id = str(relation.get("session_id") or "")
        if entity_id in extra_entities:
            continue
        chat_status = chat_status_by_session.get(session_id)
        if chat_status in {"succeeded", "failed", "skipped"}:
            completed_related_entities.add(entity_id)
        if chat_status == "failed":
            failed_related_entities.add(entity_id)
    for item in jobs:
        entity_id = str(item.get("entity_id") or "")
        if not entity_id:
            continue
        if item["status"] in {"succeeded", "failed", "skipped"}:
            completed_related_entities.add(entity_id)
        if item["status"] == "failed":
            failed_related_entities.add(entity_id)
    related_completed = min(related_job_total, len(completed_related_entities))
    related_failed = min(related_job_total, len(failed_related_entities))
    return {
        "chat_list": {"status": run["chat_list_status"]},
        "chat_messages": {
            "total": len(chat),
            "completed": sum(item["status"] in {"succeeded", "failed", "skipped"} for item in chat),
            "succeeded": sum(item["status"] == "succeeded" for item in chat),
            "failed": sum(item["status"] == "failed" for item in chat),
        },
        "related_jobs": {
            "total": related_job_total,
            "completed": related_completed,
            "succeeded": max(0, related_completed - related_failed),
            "failed": related_failed,
            "skipped": sum(item["status"] == "skipped" for item in jobs),
        },
    }


def _resume_available(run: dict[str, object], items: list[dict[str, object]]) -> bool:
    if run["status"] in {"completed", "failed", "cancelled"}:
        return False
    if run["chat_list_status"] in {"pending", "running"}:
        return True
    if run["chat_list_status"] == "failed" and run["chat_list_retryable"]:
        return True
    return any(
        item["status"] in {"pending", "running"}
        or (item["status"] == "failed" and item["retryable"])
        for item in items
    )


def _serialize_run(row) -> dict[str, object]:
    result = dict(row)
    result["workflow_options"] = _load(result.pop("workflow_options_json"), {})
    result["summary"] = _load(result.pop("summary_json"), {})
    result["chat_list_retryable"] = bool(result["chat_list_retryable"])
    return result


def _serialize_scope(row) -> dict[str, object]:
    result = dict(row)
    result["session_ids_in_scope"] = _load(
        result.pop("session_ids_in_scope_json"), []
    )
    result["session_ids_to_sync"] = _load(result.pop("session_ids_json"), [])
    if not result["session_ids_in_scope"]:
        result["session_ids_in_scope"] = list(result["session_ids_to_sync"])
    result["new_session_ids"] = _load(result.pop("new_session_ids_json"), [])
    result["related_jobs"] = _load(result.pop("related_jobs_json"), [])
    result["jobs_to_collect"] = _load(result.pop("jobs_to_collect_json"), [])
    result["jobs_missing_jd"] = _load(result.pop("jobs_missing_jd_json"), [])
    result["jobs_missing_evaluation"] = _load(
        result.pop("jobs_missing_evaluation_json"), []
    )
    result["unresolved_session_ids"] = _load(
        result.pop("unresolved_session_ids_json"), []
    )
    result["counts"] = _load(result.pop("counts_json"), {})
    result["counts"].setdefault(
        "sessions_in_scope", len(result["session_ids_in_scope"])
    )
    result["counts"].setdefault(
        "chat_update_jobs", int(result["counts"].get("jobs_to_collect") or 0)
    )
    result["counts"].setdefault("extra_jobs", 0)
    result["counts"].setdefault(
        "jobs_to_update", int(result["counts"].get("jobs_to_collect") or 0)
    )
    result["friend_list_result"] = _load(result.pop("friend_list_result_json"), {})
    result["related_job_ids"] = [
        str(item["job_id"])
        for item in result["related_jobs"]
        if item.get("job_id")
    ]
    result["encrypt_job_ids"] = [
        str(item["encrypt_job_id"])
        for item in result["related_jobs"]
        if item.get("encrypt_job_id")
    ]
    return result


def _serialize_item(row) -> dict[str, object]:
    result = dict(row)
    result["result"] = _load(result.pop("result_json"), {})
    result["retryable"] = bool(result["retryable"])
    return result


def _parse_time(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise AppError(422, "SELECTED_SINCE_TIME_INVALID", "更新时间格式无效。") from exc
    if parsed.tzinfo is None:
        raise AppError(422, "SELECTED_SINCE_TIME_INVALID", "更新时间必须包含时区。")
    parsed = parsed.astimezone(UTC)
    if parsed > datetime.now(UTC) + timedelta(minutes=1):
        raise AppError(422, "SELECTED_SINCE_TIME_INVALID", "更新时间不能晚于当前时间。")
    return parsed


def _try_parse_time(value: object) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(UTC)
    except (TypeError, ValueError):
        return None


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _dump(value: object) -> str:
    return json.dumps(value, ensure_ascii=False)


def _load(value: object, default: Any) -> Any:
    try:
        return json.loads(str(value or ""))
    except (json.JSONDecodeError, TypeError):
        return default
