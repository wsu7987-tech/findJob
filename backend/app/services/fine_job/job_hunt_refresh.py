from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from backend.app.config import AppConfig
from backend.app.db import Database
from backend.app.errors import AppError
from backend.app.services.fine_job.boss_capture_tasks import boss_capture_task_manager
from backend.app.services.fine_job.boss_chat import (
    prepare_chat_job,
    sync_friend_list,
    sync_history_messages,
)
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
)


def get_refresh_context(db: Database) -> dict[str, object]:
    """返回页面初始时间信息；本方法只读取现有数据。"""
    with db.connect() as connection:
        latest_local = connection.execute(
            "SELECT MAX(sent_at) FROM fj_chat_messages"
        ).fetchone()[0]
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
    }


def discover_scope(db: Database, selected_since_time: str) -> dict[str, object]:
    """刷新平台聊天列表，并持久化用户随后确认执行的固定范围。"""
    since = _parse_time(selected_since_time)
    captured = boss_scraper_service.capture_chat_friend_list()
    friend_list_result = sync_friend_list(
        db,
        account_uid=str(captured["account_uid"]),
        response=captured["response"],
        source_url=str(captured["url"]),
    )
    synced_session_ids = [str(value) for value in friend_list_result.get("session_ids", [])]
    new_session_ids = {
        str(value) for value in friend_list_result.get("created_session_ids", [])
    }
    with db.connect() as connection:
        latest_local = connection.execute(
            "SELECT MAX(sent_at) FROM fj_chat_messages"
        ).fetchone()[0]
        refreshed_sessions = _load_refreshed_sessions(connection, synced_session_ids)
        sessions_to_sync: list[dict[str, Any]] = []
        for session in refreshed_sessions:
            latest_at = _try_parse_time(session.get("platform_latest_message_at"))
            if latest_at is None or latest_at < since:
                continue
            is_new = str(session["id"]) in new_session_ids
            latest_missing_locally = bool(
                str(session.get("platform_latest_msg_id") or "").strip()
                and not bool(session.get("latest_message_loaded"))
            )
            if is_new or bool(session["message_update_required"]) or latest_missing_locally:
                sessions_to_sync.append(session)

        related_jobs: dict[str, dict[str, Any]] = {}
        unresolved_session_ids: list[str] = []
        for session in sessions_to_sync:
            identity, job = _related_job_identity(connection, session)
            if identity:
                related_jobs.setdefault(
                    identity,
                    {
                        "entity_id": identity,
                        "session_id": str(session["id"]),
                        "job_id": str(job["id"]) if job else None,
                        "encrypt_job_id": str(session.get("encrypt_job_id") or "") or None,
                    },
                )
            else:
                unresolved_session_ids.append(str(session["id"]))

        jobs_to_collect: list[dict[str, Any]] = []
        jobs_missing_jd: list[dict[str, Any]] = []
        jobs_missing_evaluation: list[dict[str, Any]] = []
        for relation in related_jobs.values():
            job = _job_for_relation(connection, relation)
            if job is None or _job_needs_refresh(job):
                jobs_to_collect.append(relation)
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
            "refreshed_sessions": len(refreshed_sessions),
            "sessions_to_sync": len(sessions_to_sync),
            "new_sessions_to_sync": sum(
                str(session["id"]) in new_session_ids for session in sessions_to_sync
            ),
            "related_jobs": len(related_jobs),
            "jobs_to_collect": len(jobs_to_collect),
            "jobs_missing_jd": len(jobs_missing_jd),
            "jobs_missing_evaluation": len(jobs_missing_evaluation),
            "unresolved_relations": len(unresolved_session_ids),
        }
        connection.execute(
            """
            INSERT INTO fj_job_hunt_refresh_scopes (
              id, selected_since_time, account_uid, source_url,
              friend_list_synced_at, scope_generated_at, latest_local_message_at,
              session_ids_json, new_session_ids_json, related_jobs_json,
              jobs_to_collect_json, jobs_missing_jd_json,
              jobs_missing_evaluation_json, unresolved_session_ids_json,
              counts_json, friend_list_result_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                scope_id,
                _iso(since),
                str(friend_list_result["account_uid"]),
                str(friend_list_result["source_url"]),
                str(friend_list_result["synced_at"]),
                generated_at,
                str(latest_local) if latest_local else None,
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
    first_step = (
        "waiting_chat_messages"
        if options["refresh_chat_messages"]
        else "waiting_related_jobs"
    )
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
                      ?, ?, ?, ?)
            """,
            (
                run_id,
                scope_id,
                scope["scope_generated_at"],
                scope["selected_since_time"],
                scope["latest_local_message_at"],
                _dump(options),
                scope["counts"]["refreshed_sessions"],
                scope["counts"]["sessions_to_sync"],
                scope["counts"]["related_jobs"],
                scope["counts"]["jobs_to_collect"],
                scope["counts"]["jobs_missing_jd"],
                scope["counts"]["jobs_missing_evaluation"],
                first_step,
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
    if current["status"] == "completed_with_errors" and not current["resume_available"]:
        raise AppError(409, "REFRESH_RUN_NOT_RESUMABLE", "当前任务没有可继续处理的项目。")
    now = utc_now()
    with db.connect() as connection:
        connection.execute(
            """
            UPDATE fj_job_hunt_refresh_runs
            SET codex_session_ref = ?,
                status = CASE WHEN status = 'completed_with_errors' THEN 'running' ELSE status END,
                current_step = CASE WHEN status = 'completed_with_errors' THEN 'waiting_codex' ELSE current_step END,
                completed_at = CASE WHEN status = 'completed_with_errors' THEN NULL ELSE completed_at END,
                updated_at = ? WHERE id = ?
            """,
            (codex_session_ref.strip() or None, now, run_id),
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
            """,
            (run_id, item_type),
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
    _assert_session_in_scope(db, run, str(item["session_id"]))
    _mark_item_running(db, run_id, item_id, "refresh_chat_messages")
    try:
        with db.connect() as connection:
            session = connection.execute(
                "SELECT * FROM fj_chat_sessions WHERE id = ?", (item["session_id"],)
            ).fetchone()
        if session is None:
            raise AppError(404, "CHAT_SESSION_NOT_FOUND", "聊天会话不存在。")
        result = _sync_session_messages_since(
            db,
            session_id=str(item["session_id"]),
            boss_id=str(session["encrypt_peer_uid"] or ""),
            security_id=str(session["security_id"] or ""),
            since=_parse_time(str(run["selected_since_time"])),
        )
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
    _assert_session_in_scope(db, run, str(item["session_id"]))

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

        prepared = prepare_chat_job(
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


def complete_run(db: Database, run_id: str) -> dict[str, object]:
    run = _require_run(db, run_id)
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
        if run["workflow_options"]["refresh_related_jobs"]:
            unresolved += int(run["scope"]["counts"].get("unresolved_relations") or 0)
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


def _load_refreshed_sessions(connection, session_ids: list[str]) -> list[dict[str, Any]]:
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


def _messages_since(messages: list[dict[str, Any]], since: datetime) -> list[dict[str, Any]]:
    threshold_ms = int(since.timestamp() * 1000)
    scoped: list[dict[str, Any]] = []
    for message in messages:
        try:
            sent_ms = int(message.get("time") or 0)
        except (TypeError, ValueError):
            continue
        if sent_ms >= threshold_ms:
            scoped.append(message)
    return scoped


def _sync_session_messages_since(
    db: Database,
    *,
    session_id: str,
    boss_id: str,
    security_id: str,
    since: datetime,
) -> dict[str, object]:
    """沿用现有历史接口逐页同步，读取到 selected_since_time 边界后停止。"""
    cursor = "0"
    seen_cursors: set[str] = set()
    platform_fetched = 0
    considered = 0
    inserted = 0
    final_has_more = False
    while True:
        captured = boss_scraper_service.capture_chat_history(
            boss_id=boss_id,
            security_id=security_id,
            max_message_id=cursor,
        )
        raw_messages = list(captured.get("messages") or [])
        scoped_messages = _messages_since(raw_messages, since)
        page_result = sync_history_messages(
            db,
            session_id=session_id,
            messages=scoped_messages,
            history_has_more=bool(captured.get("has_more")),
            history_next_cursor=str(captured.get("next_cursor") or ""),
        )
        platform_fetched += len(raw_messages)
        considered += len(scoped_messages)
        inserted += int(page_result["inserted_count"])
        final_has_more = bool(captured.get("has_more"))
        reached_boundary = any(
            _message_time(message) is not None and _message_time(message) < since
            for message in raw_messages
        )
        next_cursor = str(captured.get("next_cursor") or "")
        if not final_has_more or reached_boundary or not next_cursor or next_cursor in seen_cursors:
            break
        seen_cursors.add(next_cursor)
        cursor = next_cursor
    return {
        "session_id": session_id,
        "platform_fetched_count": platform_fetched,
        "fetched_count": considered,
        "inserted_count": inserted,
        "message_update_required": False,
        "has_more": final_has_more,
    }


def _message_time(message: dict[str, Any]) -> datetime | None:
    try:
        milliseconds = int(message.get("time") or 0)
    except (TypeError, ValueError):
        return None
    if milliseconds <= 0:
        return None
    return datetime.fromtimestamp(milliseconds / 1000, tz=UTC)


def _assert_session_in_scope(db: Database, run: dict[str, object], session_id: str) -> None:
    scope = run.get("scope") or get_scope(db, str(run.get("scope_id") or ""))
    scoped_ids = {str(value) for value in scope["session_ids_to_sync"]}
    if session_id not in scoped_ids:
        raise AppError(
            409,
            "SESSION_OUTSIDE_REFRESH_SCOPE",
            "聊天会话不在本次已确认的更新范围内。",
        )


def _normalize_workflows(options: dict[str, bool]) -> dict[str, bool]:
    normalized = {name: bool(options.get(name, False)) for name in SUPPORTED_WORKFLOWS}
    normalized["refresh_chat_list"] = True
    normalized["analyze_conversations"] = False
    normalized["generate_missing_suggestions"] = False
    if not (
        normalized["refresh_chat_messages"] or normalized["refresh_related_jobs"]
    ):
        raise AppError(
            422,
            "REFRESH_WORKFLOW_REQUIRED",
            "请至少选择聊天消息同步或关联岗位采集。",
        )
    return normalized


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


def _progress(items: list[dict[str, object]], run: dict[str, object]) -> dict[str, object]:
    chat = [item for item in items if item["item_type"] == "chat_session"]
    jobs = [item for item in items if item["item_type"] == "related_job"]
    return {
        "chat_list": {"status": run["chat_list_status"]},
        "chat_messages": {
            "total": len(chat),
            "completed": sum(item["status"] in {"succeeded", "failed", "skipped"} for item in chat),
            "succeeded": sum(item["status"] == "succeeded" for item in chat),
            "failed": sum(item["status"] == "failed" for item in chat),
        },
        "related_jobs": {
            "total": len(jobs),
            "completed": sum(item["status"] in {"succeeded", "failed", "skipped"} for item in jobs),
            "succeeded": sum(item["status"] == "succeeded" for item in jobs),
            "failed": sum(item["status"] == "failed" for item in jobs),
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
    result["session_ids_to_sync"] = _load(result.pop("session_ids_json"), [])
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
