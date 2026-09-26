from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from backend.app.config import AppConfig
from backend.app.db import Database
from backend.app.errors import AppError
from backend.app.services.fine_job.boss_capture_tasks import boss_capture_task_manager
from backend.app.services.fine_job.collection_capacity import (
    SMART_CAPTURE_ACTIVE_STATUSES,
    assert_collection_start_allowed,
    assert_collection_start_allowed_in_connection,
    recover_collection_capacity,
)
from backend.app.services.fine_job import workflow_children
from backend.app.services.fine_job import pipeline_owner
from backend.app.services.fine_job import cutover_guard
from backend.app.services.fine_job import smart_capture_engine
from backend.app.services.fine_job import strategies
from backend.app.schemas.fine_job.smart_capture_execution_config import (
    execution_config_validation_errors,
)
from backend.app.services.fine_job.boss_scraper.service import (
    BossCaptureRequest,
    boss_scraper_service,
)
from backend.app.services.fine_job.smart_capture_events import smart_capture_event_broker
from backend.app.services.fine_job.workflow_run_events import workflow_run_event_broker
from backend.app.utils import new_id, utc_now


ACTIVE_STATUSES = set(SMART_CAPTURE_ACTIVE_STATUSES)
TERMINAL_STATUSES = {"completed", "stopped", "failed"}
LEGACY_MANUAL_WAITING_REASONS = {
    "browser",
    "browser_not_running",
    "context_budget",
    "manual_decision",
    "codex",
    "child_control",
    "capture_interrupted",
}


def _on_capture_task_updated(capture: dict[str, object]) -> None:
    """把 Smart Capture 批次进度持久化到独立 snapshot。"""
    db = capture.get("_db")
    smart_capture_id = str(capture.get("smart_capture_id") or "")
    if not isinstance(db, Database) or not smart_capture_id:
        return
    with db.connect() as connection:
        parent = connection.execute(
            "SELECT status FROM fj_smart_captures WHERE id = ?",
            (smart_capture_id,),
        ).fetchone()
    if parent is not None and str(parent["status"]) in TERMINAL_STATUSES:
        # 终态后的迟到批次回调只释放启动占用，不再进入自动 Engine。
        cutover_guard.get_runtime_cutover_guard().release_live_start(
            child_ref=smart_capture_id
        )
        return
    # JD/Prefetch 详情是 Smart Capture 内部单元，其终态只接续 Pipeline，不释放 child 启动权。
    if str(capture.get("pipeline_unit_type") or "") in {"formal_jd", "prefetch"}:
        try:
            smart_capture_engine.process_detail_task_update(db, smart_capture_id, capture)
        except Exception as exc:
            _update_capture(
                db,
                smart_capture_id,
                status="interrupted",
                stage="pipeline_interrupted",
                waiting_reason="capture_interrupted",
                control_cause="recovery",
                message="Smart Capture 内部详情单元接续中断，可恢复后继续。",
                error_message=str(exc),
            )
            return
        return
    # linked 与 independent 在 BOSS 批次结束后都由同一 Engine 写入候选池。
    stage = str(capture.get("stage") or "")
    if (
        str(capture.get("status") or "") == "completed"
        and not stage.endswith("paused")
        and not stage.endswith("stopped")
    ):
        try:
            # 先落批次进度，再由 Engine 写入更高层 Pipeline 状态，避免批次终态覆盖 JD/Analysis。
            sync_capture_snapshot(db, capture)
            smart_capture_engine.process_completed_batch(db, smart_capture_id, capture)
            smart_capture_engine.advance_completed_batch(db, smart_capture_id, capture)
        except Exception as exc:
            _update_capture(
                db,
                smart_capture_id,
                status="interrupted",
                stage="pipeline_interrupted",
                waiting_reason="capture_interrupted",
                control_cause="recovery",
                message="Smart Capture 批次结果处理中断，可恢复后继续。",
                error_message=str(exc),
            )
            return
        cutover_guard.get_runtime_cutover_guard().release_live_start(
            child_ref=smart_capture_id
        )
        return
    if str(capture.get("status") or "") in {"stopped", "failed"}:
        cutover_guard.get_runtime_cutover_guard().release_live_start(
            child_ref=smart_capture_id
        )
    try:
        sync_capture_snapshot(db, capture)
    except Exception:
        # 后台进度同步失败时保留采集线程，下一次轮询仍可读取批次状态。
        return


boss_capture_task_manager.add_listener(_on_capture_task_updated)


def recover_interrupted_smart_captures(db: Database) -> None:
    """应用启动时把失去进程执行器的任务收敛到可继续状态。"""
    recover_collection_capacity(db)
    now = utc_now()
    recovery_transition_id = new_id()
    with db.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            UPDATE fj_smart_captures
            SET status = 'interrupted', stage = 'interrupted',
                message = '应用重启后采集执行已中断，请点击继续恢复当前任务。',
                waiting_reason = 'capture_interrupted', control_cause = 'recovery',
                transition_id = ?, state_version = state_version + 1,
                updated_at = ?
            WHERE status IN ('running', 'pausing')
               OR (
                 status = 'waiting_next_batch'
                 AND (
                   EXISTS (
                     SELECT 1 FROM fj_workflow_tasks task
                     WHERE task.smart_capture_id = fj_smart_captures.id
                       AND task.task_type = 'deep_job_search_jd' AND task.status = 'running'
                   )
                   OR EXISTS (
                     SELECT 1 FROM fj_workflow_prefetch_items item
                     WHERE item.smart_capture_id = fj_smart_captures.id
                       AND item.status = 'collecting'
                   )
                 )
               )
            """,
            (recovery_transition_id, now),
        )
        linked_runs = connection.execute(
            """
            SELECT workflow_run_id
            FROM fj_smart_captures
            WHERE status = 'interrupted' AND workflow_run_id IS NOT NULL
            """
        ).fetchall()
        for linked in linked_runs:
            workflow_run_id = str(linked["workflow_run_id"])
            connection.execute(
                """
                UPDATE fj_workflow_tasks
                SET status = 'waiting_for_user', updated_at = ?
                WHERE workflow_run_id = ? AND task_type = 'deep_job_search'
                  AND status IN ('pending', 'running')
                """,
                (now, workflow_run_id),
            )
            connection.execute(
                """
                UPDATE fj_workflow_runs
                SET status = 'waiting_for_user', current_step = 'waiting_for_user',
                    next_action = 'await_user_choice',
                    next_action_reason = '应用重启后采集执行已中断，请继续任务后重新开始当前搜索组合。',
                    waiting_for_user = 1, stop_reason = 'capture_interrupted', updated_at = ?
                WHERE id = ? AND status NOT IN ('completed', 'completed_with_errors', 'cancelled', 'failed')
                """,
                (now, workflow_run_id),
            )
        connection.execute(
            """
            UPDATE fj_boss_capture_batches
            SET control_status = 'interrupted', updated_at = ?
            WHERE status IN ('queued', 'running')
              AND smart_capture_id IS NOT NULL
            """,
            (now,),
        )
        # 进程重启后内部详情执行器已丢失，恢复为可重启的 pending 单元。
        connection.execute(
            """
            UPDATE fj_workflow_tasks
            SET status = 'pending', operation_ref_type = NULL, operation_ref_id = NULL,
                updated_at = ?
            WHERE task_type = 'deep_job_search_jd' AND status = 'running'
              AND smart_capture_id IN (
                SELECT id FROM fj_smart_captures WHERE status = 'interrupted'
              )
            """,
            (now,),
        )
        connection.execute(
            """
            UPDATE fj_workflow_prefetch_items
            SET status = 'pending', operation_ref_type = NULL, operation_ref_id = NULL,
                detail_status = CASE WHEN detail_status = 'collecting' THEN 'queued' ELSE detail_status END,
                updated_at = ?
            WHERE status = 'collecting'
              AND smart_capture_id IN (
                SELECT id FROM fj_smart_captures WHERE status = 'interrupted'
              )
            """,
            (now,),
        )
        linked_captures = connection.execute(
            "SELECT * FROM fj_smart_captures WHERE workflow_run_id IS NOT NULL"
        ).fetchall()
        for linked_capture in linked_captures:
            relation = connection.execute(
                """
                SELECT id FROM fj_workflow_children
                WHERE workflow_run_id = ? AND child_type = 'smart_capture' AND child_ref = ?
                """,
                (str(linked_capture["workflow_run_id"]), str(linked_capture["id"])),
            ).fetchone()
            if relation is not None:
                workflow_children.record_child_event_in_connection(
                    connection,
                    child_relation_id=str(relation["id"]),
                    child_type=workflow_children.SMART_CAPTURE_CHILD_TYPE,
                    child_ref=str(linked_capture["id"]),
                    child_status="interrupted",
                    transition_id=recovery_transition_id,
                    state_version=max(1, int(linked_capture["state_version"] or 1)),
                    waiting_reason="capture_interrupted",
                    control_cause="recovery",
                    result_summary=_load_json(str(linked_capture["result_summary_json"] or "{}")),
                    occurred_at=now,
                )
        current = connection.execute(
            """
            SELECT c.id, c.status
            FROM fj_smart_capture_current current_task
            JOIN fj_smart_captures c ON c.id = current_task.smart_capture_id
            WHERE current_task.slot = 1
            """
        ).fetchone()
        # current 指针是持久化身份，重启只收敛状态，不按更新时间改指向历史任务。
        if current is not None:
            connection.execute(
                "UPDATE fj_smart_capture_current SET updated_at = updated_at WHERE slot = 1"
            )


def create_smart_capture(
    db: Database,
    *,
    source: str,
    workflow_run_id: str | None,
    search_config: dict[str, Any],
    target_count: int | None = None,
    execution_config: dict[str, Any] | None = None,
) -> dict[str, object]:
    """创建岗位采集身份，并在同一写事务内占用 current slot。"""
    with db.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        capture_id = create_smart_capture_in_connection(
            connection,
            db,
            source=source,
            workflow_run_id=workflow_run_id,
            search_config=search_config,
            target_count=target_count,
            execution_config=execution_config,
        )
    return publish_smart_capture_snapshot(db, capture_id, current_pointer_changed=True) or {}


def create_smart_capture_in_connection(
    connection: Any,
    db: Database,
    *,
    source: str,
    workflow_run_id: str | None,
    search_config: dict[str, Any],
    target_count: int | None = None,
    execution_config: dict[str, Any] | None = None,
) -> str:
    """在调用方的写事务中创建 Smart Capture，保证关联对象与 current 同时提交。"""
    if source not in {"task_cockpit", "boss_capture"}:
        raise AppError(422, "VALIDATION_FAILED", "岗位采集任务来源无效。")
    capture_id = new_id()
    now = utc_now()
    assert_collection_start_allowed_in_connection(
        connection, db, requested_kind="smart"
    )
    placeholders = ",".join("?" for _ in ACTIVE_STATUSES)
    active_row = connection.execute(
        f"""
        SELECT capture.id
        FROM fj_smart_capture_current current_task
        JOIN fj_smart_captures capture
          ON capture.id = current_task.smart_capture_id
        WHERE current_task.slot = 1
          AND capture.status IN ({placeholders})
        LIMIT 1
        """,
        tuple(sorted(ACTIVE_STATUSES)),
    ).fetchone()
    if active_row is not None:
        raise AppError(
            409,
            "COLLECTION_TASK_ACTIVE",
            "当前岗位采集任务尚未结束，请先暂停后继续或停止当前任务。",
        )
    connection.execute(
        """
        INSERT INTO fj_smart_captures (
          id, source, workflow_run_id, status, search_config_json, execution_config_json,
          target_count, stage, waiting_reason, control_cause, state_version,
          progress_json, result_summary_json, message, created_at, updated_at
        ) VALUES (?, ?, ?, 'pending', ?, ?, ?, 'created', '', '', 1, '{}', '{}', ?, ?, ?)
        """,
        (
            capture_id,
            source,
            workflow_run_id,
            json.dumps(search_config, ensure_ascii=False),
            json.dumps(
                execution_config
                if execution_config is not None
                else _build_execution_config(search_config),
                ensure_ascii=False,
                sort_keys=True,
            ),
            target_count,
            "岗位采集任务已创建，等待启动首个批次。",
            now,
            now,
        ),
    )
    connection.execute(
        """
        INSERT INTO fj_smart_capture_current (slot, smart_capture_id, updated_at)
        VALUES (1, ?, ?)
        ON CONFLICT(slot) DO UPDATE SET
          smart_capture_id = excluded.smart_capture_id,
          updated_at = excluded.updated_at
        """,
        (capture_id, now),
    )
    if workflow_run_id:
        workflow_children.create_child_relation_in_connection(
            connection,
            workflow_run_id=workflow_run_id,
            child_ref=capture_id,
            status="pending",
            capabilities=_capabilities(
                status="pending",
                stage="created",
                waiting_reason="",
                control_cause="",
            ),
            child_state_version=1,
            created_at=now,
        )
    return capture_id


def get_by_workflow_run(db: Database, workflow_run_id: str) -> dict[str, object] | None:
    with db.connect() as connection:
        row = connection.execute(
            "SELECT id FROM fj_smart_captures WHERE workflow_run_id = ?",
            (workflow_run_id,),
        ).fetchone()
    return get_smart_capture(db, str(row["id"])) if row is not None else None


def require_by_workflow_run(db: Database, workflow_run_id: str) -> dict[str, object]:
    capture = get_by_workflow_run(db, workflow_run_id)
    if capture is None:
        raise AppError(404, "SMART_CAPTURE_NOT_FOUND", "当前驾驶舱任务没有关联岗位采集任务。")
    return capture


def get_current_smart_capture(db: Database) -> dict[str, object] | None:
    smart_capture_id = get_current_smart_capture_id(db)
    return get_smart_capture(db, smart_capture_id) if smart_capture_id else None


def get_current_smart_capture_id(db: Database) -> str | None:
    with db.connect() as connection:
        row = connection.execute(
            "SELECT smart_capture_id FROM fj_smart_capture_current WHERE slot = 1"
        ).fetchone()
    return str(row["smart_capture_id"]) if row is not None else None


def publish_smart_capture_snapshot(
    db: Database,
    smart_capture_id: str,
    *,
    current_pointer_changed: bool = False,
) -> dict[str, object] | None:
    """在事务提交后发布最新快照，避免 SSE 读取到未提交状态。"""
    try:
        snapshot = get_smart_capture(db, smart_capture_id)
    except AppError:
        return None
    smart_capture_event_broker.publish(smart_capture_id, snapshot)
    workflow_run_id = str(snapshot.get("workflow_run_id") or "")
    workflow_run = snapshot.get("workflow_run")
    if workflow_run_id and isinstance(workflow_run, dict):
        # linked child 更新后同步推送父快照，保证驾驶舱只依赖 Workflow SSE 也能及时看到状态变化。
        workflow_run_event_broker.publish(workflow_run_id, workflow_run)
    if current_pointer_changed:
        smart_capture_event_broker.publish_current(snapshot)
    return snapshot


def get_active_smart_capture(db: Database) -> dict[str, object] | None:
    current = get_current_smart_capture(db)
    if current is not None and str(current["status"]) in ACTIVE_STATUSES:
        return current
    return None


def _update_parent_control_in_connection(
    connection: Any,
    workflow_run_id: str,
    *,
    status: str,
    control_state: str,
    waiting_reason: str,
    control_cause: str,
    transition_id: str,
    next_action: str,
    next_action_reason: str,
    waiting_for_user: int,
    paused: int,
    completed_at: str | None = None,
) -> None:
    """在 child 控制事务内同步父层语义字段。"""
    connection.execute(
        """
        UPDATE fj_workflow_runs
        SET status = ?, control_state = ?, waiting_reason = ?, control_cause = ?,
            transition_id = ?, state_version = state_version + 1,
            current_step = ?, next_action = ?, next_action_reason = ?,
            waiting_for_user = ?, paused = ?, completed_at = COALESCE(?, completed_at),
            updated_at = ?
        WHERE id = ?
        """,
        (
            status,
            control_state,
            waiting_reason,
            control_cause,
            transition_id,
            control_state,
            next_action,
            next_action_reason,
            waiting_for_user,
            paused,
            completed_at,
            utc_now(),
            workflow_run_id,
        ),
    )


def _set_child_state_in_connection(
    connection: Any,
    smart_capture_id: str,
    *,
    status: str,
    stage: str,
    waiting_reason: str,
    control_cause: str,
    transition_id: str,
    message: str,
    completed: bool = False,
    result_summary: dict[str, object] | None = None,
) -> str:
    """更新 child lifecycle，并为 linked child 产生一次可消费事件。"""
    now = utc_now()
    row = connection.execute(
        "SELECT * FROM fj_smart_captures WHERE id = ?",
        (smart_capture_id,),
    ).fetchone()
    if row is None:
        raise AppError(404, "SMART_CAPTURE_NOT_FOUND", "岗位采集任务不存在。")
    next_status = _canonical_status(status, waiting_reason)
    # child 终态只允许保留原结果，迟到回调和父层控制都不能改写有效 outcome。
    if str(row["status"]) in TERMINAL_STATUSES:
        return str(row["current_batch_id"] or "")
    next_result = (
        str(row["result_summary_json"] or "{}")
        if result_summary is None
        else json.dumps(result_summary, ensure_ascii=False, sort_keys=True)
    )
    next_completed_at = row["completed_at"] or now if completed else None
    observable = (
        str(row["status"]), str(row["stage"]), str(row["waiting_reason"] or ""),
        str(row["control_cause"] or ""), str(row["message"] or ""),
        str(row["result_summary_json"] or "{}"), row["completed_at"],
    )
    next_observable = (
        next_status, stage, waiting_reason, control_cause, message, next_result,
        next_completed_at,
    )
    if observable == next_observable and str(row["transition_id"] or "") == transition_id:
        return str(row["current_batch_id"] or "")
    next_version = max(1, int(row["state_version"] or 1)) + 1
    connection.execute(
        """
        UPDATE fj_smart_captures
        SET status = ?, stage = ?, waiting_reason = ?, control_cause = ?,
            message = ?, result_summary_json = ?, completed_at = CASE WHEN ?
              THEN COALESCE(completed_at, ?) ELSE NULL END,
            transition_id = ?, state_version = ?, updated_at = ?
        WHERE id = ?
        """,
        (
            next_status,
            stage,
            waiting_reason,
            control_cause,
            message,
            next_result,
            int(completed),
            now,
            transition_id,
            next_version,
            now,
            smart_capture_id,
        ),
    )
    updated = connection.execute(
        "SELECT * FROM fj_smart_captures WHERE id = ?", (smart_capture_id,)
    ).fetchone()
    if updated is None:
        return ""
    relation = connection.execute(
        """
        SELECT id FROM fj_workflow_children
        WHERE workflow_run_id = ? AND child_type = 'smart_capture' AND child_ref = ?
        """,
        (str(updated["workflow_run_id"] or ""), smart_capture_id),
    ).fetchone()
    if relation is not None and next_status in {"completed", "stopped", "failed", "interrupted"}:
        workflow_children.record_child_event_in_connection(
            connection,
            child_relation_id=str(relation["id"]),
            child_type=workflow_children.SMART_CAPTURE_CHILD_TYPE,
            child_ref=smart_capture_id,
            child_status=next_status,
            transition_id=transition_id,
            state_version=next_version,
            waiting_reason=waiting_reason,
            control_cause=control_cause,
            result_summary=_load_json(next_result),
            occurred_at=now,
        )
    elif relation is not None:
        workflow_children.project_smart_capture_in_connection(
            connection,
            updated,
            capabilities=_capabilities(
                status=next_status,
                stage=stage,
                waiting_reason=waiting_reason,
                control_cause=control_cause,
            ),
            now=now,
        )
    return str(updated["current_batch_id"] or "")


def parent_pause_child_in_connection(
    connection: Any, workflow_run_id: str, transition_id: str
) -> str:
    """父暂停与 linked child 的 pausing/paused 在同一事务内落库。"""
    capture = connection.execute(
        "SELECT * FROM fj_smart_captures WHERE workflow_run_id = ?",
        (workflow_run_id,),
    ).fetchone()
    if capture is None:
        return ""
    batch_id = str(capture["current_batch_id"] or "")
    batch = connection.execute(
        "SELECT status FROM fj_boss_capture_batches WHERE id = ?", (batch_id,)
    ).fetchone() if batch_id else None
    child_status = "pausing" if batch is not None and str(batch["status"]) in {"queued", "running"} else "paused"
    _update_parent_control_in_connection(
        connection, workflow_run_id, status="running", control_state="paused",
        waiting_reason="parent_pause", control_cause="parent_pause",
        transition_id=transition_id, next_action="resume_workflow",
        next_action_reason="父任务已暂停，恢复后继续当前子任务。", waiting_for_user=0, paused=1,
    )
    # pending 或已终态 child 没有可暂停的执行器，保留其 lifecycle 原样。
    if str(capture["status"]) in {"pending", *TERMINAL_STATUSES}:
        return batch_id
    _set_child_state_in_connection(
        connection, str(capture["id"]), status=child_status,
        stage="pause_requested" if child_status == "pausing" else "paused",
        waiting_reason="child_control", control_cause="parent_pause",
        transition_id=transition_id, message="父任务已请求安全暂停当前岗位采集。",
    )
    return batch_id


def parent_resume_child_in_connection(
    connection: Any, workflow_run_id: str, transition_id: str
) -> str:
    """父恢复只解除 parent_pause，不清除其它 child 卡点。"""
    parent = connection.execute(
        "SELECT control_state, control_cause, status, paused FROM fj_workflow_runs WHERE id = ?", (workflow_run_id,)
    ).fetchone()
    capture = connection.execute(
        "SELECT * FROM fj_smart_captures WHERE workflow_run_id = ?", (workflow_run_id,)
    ).fetchone()
    if parent is None or capture is None:
        return ""
    if (
        str(parent["control_state"] or "") != "paused"
        or str(parent["control_cause"] or "") != "parent_pause"
    ):
        raise AppError(409, "WORKFLOW_NOT_PARENT_PAUSED", "当前父任务不是由 parent pause 造成的暂停。")
    child_status = str(capture["status"])
    if child_status == "failed":
        _update_parent_control_in_connection(
            connection, workflow_run_id, status="waiting_for_user",
            control_state="child_failed_waiting_decision", waiting_reason="child_failed",
            control_cause="child_failure", transition_id=transition_id,
            next_action="await_child_decision", next_action_reason="子任务执行失败，请选择跳过该子任务继续或结束父任务。",
            waiting_for_user=1, paused=0,
        )
        return ""
    if child_status == "stopped":
        _update_parent_control_in_connection(
            connection, workflow_run_id, status="waiting_for_user",
            control_state="child_cancelled_waiting_decision", waiting_reason="child_stopped",
            control_cause="child_user_stop", transition_id=transition_id,
            next_action="await_child_decision", next_action_reason="子任务已停止，请选择跳过该子任务继续或结束父任务。",
            waiting_for_user=1, paused=0,
        )
        return ""
    if child_status == "interrupted":
        _update_parent_control_in_connection(
            connection, workflow_run_id, status="waiting_for_user",
            control_state="waiting_child_interrupted",
            waiting_reason=str(capture["waiting_reason"] or "capture_interrupted"),
            control_cause="recovery", transition_id=transition_id,
            next_action="await_child_resume", next_action_reason="子任务执行已中断，请明确恢复后再继续。",
            waiting_for_user=1, paused=0,
        )
        return ""
    _update_parent_control_in_connection(
        connection, workflow_run_id, status="running", control_state="active",
        waiting_reason="", control_cause="recovery", transition_id=transition_id,
        next_action="continue_workflow", next_action_reason="父任务已恢复。", waiting_for_user=0, paused=0,
    )
    if str(capture["status"]) in {"pausing", "paused"} and str(capture["control_cause"] or "") == "parent_pause":
        _set_child_state_in_connection(
            connection, str(capture["id"]), status="running", stage="resume_requested",
            waiting_reason="", control_cause="recovery", transition_id=transition_id,
            message="父任务已恢复当前岗位采集。",
        )
    return str(capture["current_batch_id"] or "")


def parent_cancel_child_in_connection(
    connection: Any, workflow_run_id: str, transition_id: str
) -> str:
    """父取消先原子收敛 parent/child，再由提交后的 side effect 停止执行器。"""
    capture = connection.execute(
        "SELECT * FROM fj_smart_captures WHERE workflow_run_id = ?", (workflow_run_id,)
    ).fetchone()
    _update_parent_control_in_connection(
        connection, workflow_run_id, status="cancelled", control_state="active",
        waiting_reason="", control_cause="parent_cancel", transition_id=transition_id,
        next_action="", next_action_reason="父任务已取消，已获得的数据继续保留。",
        waiting_for_user=0, paused=0, completed_at=utc_now(),
    )
    if capture is None:
        return ""
    if str(capture["status"]) in TERMINAL_STATUSES:
        return ""
    return _set_child_state_in_connection(
        connection, str(capture["id"]), status="stopped", stage="stopped",
        waiting_reason="", control_cause="parent_cancel", transition_id=transition_id,
        message="父任务已取消，岗位采集已停止。", completed=True,
    )


def child_pause_in_connection(
    connection: Any, smart_capture_id: str, transition_id: str
) -> str:
    """child 自主暂停，同时把 parent 置为 waiting_child_paused。"""
    capture = connection.execute(
        "SELECT * FROM fj_smart_captures WHERE id = ?", (smart_capture_id,)
    ).fetchone()
    if capture is None:
        raise AppError(404, "SMART_CAPTURE_NOT_FOUND", "岗位采集任务不存在。")
    if str(capture["status"]) not in {"running", "pausing"}:
        raise AppError(409, "SMART_CAPTURE_NOT_PAUSABLE", "当前岗位采集任务不在可暂停状态。")
    workflow_run_id = str(capture["workflow_run_id"] or "")
    batch_id = str(capture["current_batch_id"] or "")
    batch = connection.execute(
        "SELECT status FROM fj_boss_capture_batches WHERE id = ?", (batch_id,)
    ).fetchone() if batch_id else None
    child_status = "pausing" if batch is not None and str(batch["status"]) in {"queued", "running"} else "paused"
    if workflow_run_id:
        parent = connection.execute(
            "SELECT control_state FROM fj_workflow_runs WHERE id = ?", (workflow_run_id,)
        ).fetchone()
        if parent is not None and str(parent["control_state"] or "active") != "active":
            raise AppError(409, "CHILD_PAUSE_BLOCKED_BY_PARENT", "父任务当前已有等待或暂停原因。")
        _update_parent_control_in_connection(
            connection, workflow_run_id, status="waiting_for_user",
            control_state="waiting_child_paused", waiting_reason="child_paused",
            control_cause="child_self_pause", transition_id=transition_id,
            next_action="await_child_resume", next_action_reason="子任务已请求暂停，请在子任务恢复后继续。",
            waiting_for_user=1, paused=0,
        )
    _set_child_state_in_connection(
        connection, smart_capture_id, status=child_status,
        stage="pause_requested" if child_status == "pausing" else "paused",
        waiting_reason="child_control", control_cause="child_self_pause",
        transition_id=transition_id, message="岗位采集子任务已请求暂停。",
    )
    return batch_id


def child_resume_in_connection(
    connection: Any, smart_capture_id: str, transition_id: str
) -> str:
    """child resume 只解除 child_self_pause 或 recovery。"""
    capture = connection.execute(
        "SELECT * FROM fj_smart_captures WHERE id = ?", (smart_capture_id,)
    ).fetchone()
    if capture is None:
        raise AppError(404, "SMART_CAPTURE_NOT_FOUND", "岗位采集任务不存在。")
    workflow_run_id = str(capture["workflow_run_id"] or "")
    if workflow_run_id:
        parent = connection.execute(
            "SELECT control_state FROM fj_workflow_runs WHERE id = ?", (workflow_run_id,)
        ).fetchone()
        if parent is not None and str(parent["control_state"] or "") == "paused":
            raise AppError(409, "CHILD_RESUME_BLOCKED_BY_PARENT", "父任务仍处于暂停状态，请先恢复父任务。")
        resumable_from_child = (
            str(capture["status"]) in {"pausing", "paused"}
            and str(capture["control_cause"] or "") == "child_self_pause"
        ) or str(capture["status"]) == "interrupted" or (
            str(capture["status"]) == "waiting_for_user"
            and str(capture["waiting_reason"] or "") in {"capture_interrupted", "browser_not_running"}
        )
        if not resumable_from_child:
            raise AppError(409, "CHILD_RESUME_NOT_ALLOWED", "当前子任务没有可恢复的暂停或中断原因。")
        if parent is not None and str(parent["control_state"] or "") in {"waiting_child_paused", "waiting_child_interrupted"}:
            _update_parent_control_in_connection(
                connection, workflow_run_id, status="running", control_state="active",
                waiting_reason="", control_cause="recovery", transition_id=transition_id,
                next_action="continue_workflow", next_action_reason="子任务已恢复，父任务可以继续。",
                waiting_for_user=0, paused=0,
            )
    _set_child_state_in_connection(
        connection, smart_capture_id, status="running", stage="resume_requested",
        waiting_reason="", control_cause="recovery", transition_id=transition_id,
        message="岗位采集子任务已恢复。",
    )
    return str(capture["current_batch_id"] or "")


def child_stop_in_connection(
    connection: Any, smart_capture_id: str, transition_id: str
) -> str:
    """child stop 进入父层人工决策等待，不取消 parent。"""
    capture = connection.execute(
        "SELECT * FROM fj_smart_captures WHERE id = ?", (smart_capture_id,)
    ).fetchone()
    if capture is None:
        raise AppError(404, "SMART_CAPTURE_NOT_FOUND", "岗位采集任务不存在。")
    workflow_run_id = str(capture["workflow_run_id"] or "")
    if workflow_run_id:
        _update_parent_control_in_connection(
            connection, workflow_run_id, status="waiting_for_user",
            control_state="child_cancelled_waiting_decision", waiting_reason="child_stopped",
            control_cause="child_user_stop", transition_id=transition_id,
            next_action="await_child_decision", next_action_reason="子任务已停止，请选择跳过该子任务继续或结束父任务。",
            waiting_for_user=1, paused=0,
        )
    return _set_child_state_in_connection(
        connection, smart_capture_id, status="stopped", stage="stopped",
        waiting_reason="", control_cause="child_user_stop", transition_id=transition_id,
        message="岗位采集子任务已停止，已获得的数据继续保留。", completed=True,
    )


def start_independent_capture(
    db: Database,
    config: AppConfig,
    payload: dict[str, Any],
) -> dict[str, object]:
    """从岗位采集页创建不关联 Workflow Run 的任务并启动首批采集。"""
    _validate_execution_config_for_start(db, payload)
    assert_collection_start_allowed(db, requested_kind="smart")
    if get_active_smart_capture(db) is not None:
        raise AppError(409, "COLLECTION_TASK_ACTIVE", "当前岗位采集任务尚未结束，请先暂停后继续或停止当前任务。")
    if not boss_scraper_service.get_browser_status().running:
        raise AppError(409, "BROWSER_NOT_RUNNING", "FineJob 专用 Chrome 未启动，请先打开并完成 BOSS 登录。")
    capture = create_smart_capture(
        db,
        source="boss_capture",
        workflow_run_id=None,
        search_config=payload,
        target_count=int(payload.get("candidate_target_count") or 0) or None,
        execution_config=_build_execution_config(payload),
    )
    return _start_new_batch(db, config, str(capture["smart_capture_id"]), payload)


def start_smart_capture(
    db: Database,
    config: AppConfig,
    smart_capture_id: str,
) -> dict[str, object]:
    """启动已创建的 pending Smart Capture，供恢复和 linked adapter 使用。"""
    capture = get_smart_capture(db, smart_capture_id)
    if str(capture["status"]) != "pending":
        raise AppError(409, "SMART_CAPTURE_NOT_STARTABLE", "当前岗位采集任务不在待启动状态。")
    payload = dict(capture.get("search_config") or {})
    return _start_new_batch(
        db,
        config,
        smart_capture_id,
        payload,
        requested_authority=cutover_guard.ExecutionAuthority.SMART_CAPTURE,
    )


def retry_smart_capture(
    db: Database,
    config: AppConfig,
    smart_capture_id: str,
) -> dict[str, object]:
    """只恢复 pending/interrupted，failed 终态保持不可重试。"""
    capture = get_smart_capture(db, smart_capture_id)
    status = str(capture["status"])
    if status == "failed":
        raise AppError(409, "SMART_CAPTURE_NOT_RETRYABLE", "失败的岗位采集任务已经结束，不能重试。")
    if status not in {"pending", "interrupted"}:
        raise AppError(409, "SMART_CAPTURE_NOT_RETRYABLE", "当前岗位采集任务不在可重试状态。")
    return _start_new_batch(
        db,
        config,
        smart_capture_id,
        dict(capture.get("search_config") or {}),
        requested_authority=cutover_guard.ExecutionAuthority.SMART_CAPTURE,
    )


def start_linked_capture_batch(
    db: Database,
    config: AppConfig,
    smart_capture_id: str,
    payload: dict[str, Any],
) -> dict[str, object]:
    """由岗位采集子任务启动驾驶舱已编排的当前搜索批次。"""
    capture = get_smart_capture(db, smart_capture_id)
    if not str(capture.get("workflow_run_id") or ""):
        raise AppError(409, "SMART_CAPTURE_NOT_LINKED", "当前岗位采集任务没有关联驾驶舱任务。")
    return _start_new_batch(
        db,
        config,
        smart_capture_id,
        payload,
        requested_authority=cutover_guard.ExecutionAuthority.WORKFLOW_PIPELINE,
    )


def bind_batch(
    db: Database,
    smart_capture_id: str,
    batch_id: str,
    *,
    search_task: dict[str, str] | None = None,
    search_payload: dict[str, object] | None = None,
) -> None:
    now = utc_now()
    with db.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            UPDATE fj_boss_capture_batches
            SET smart_capture_id = ?, capture_source = 'smart'
            WHERE id = ?
            """,
            (smart_capture_id, batch_id),
        )
        connection.execute(
            """
            UPDATE fj_smart_captures
            SET current_batch_id = ?, status = 'running', stage = 'capturing',
                waiting_reason = '', control_cause = '',
                message = '正在采集岗位。', error_message = NULL,
                state_version = state_version + 1, updated_at = ?
            WHERE id = ?
            """,
            (batch_id, now, smart_capture_id),
        )
        connection.execute(
            """
            INSERT INTO fj_smart_capture_current (slot, smart_capture_id, updated_at)
            VALUES (1, ?, ?)
            ON CONFLICT(slot) DO UPDATE SET
              smart_capture_id = excluded.smart_capture_id,
              updated_at = excluded.updated_at
            """,
            (smart_capture_id, now),
        )
        capture_row = connection.execute(
            "SELECT * FROM fj_smart_captures WHERE id = ?",
            (smart_capture_id,),
        ).fetchone()
        if capture_row is not None:
            workflow_children.project_smart_capture_in_connection(
                connection,
                capture_row,
                capabilities=_capabilities(
                    status="running",
                    stage="capturing",
                    waiting_reason="",
                    control_cause="",
                ),
                now=now,
            )
    if search_task is not None:
        smart_capture_engine.bind_capture_task(
            db,
            smart_capture_id=smart_capture_id,
            task_id=search_task["task_id"],
            batch_id=batch_id,
            payload=search_payload or {},
        )
    try:
        sync_capture_snapshot(db, boss_capture_task_manager.get_task(batch_id))
    except AppError:
        # 批次进程快照短暂不可读时，父任务仍可从后续监听事件继续同步。
        pass
    publish_smart_capture_snapshot(db, smart_capture_id, current_pointer_changed=True)


def pause_smart_capture(
    db: Database,
    smart_capture_id: str,
    *,
    sync_workflow: bool = True,
    transition_id: str | None = None,
) -> dict[str, object]:
    capture = get_smart_capture(db, smart_capture_id)
    if str(capture["status"]) in TERMINAL_STATUSES:
        raise AppError(409, "SMART_CAPTURE_NOT_PAUSABLE", "当前岗位采集任务已结束，不能暂停。")
    if str(capture.get("control_cause") or "") == "child_self_pause" and str(capture["status"]) in {"pausing", "paused"}:
        return capture
    pipeline_operation_id = smart_capture_engine.active_pipeline_operation_id(
        db, smart_capture_id
    )
    batch_id = pipeline_operation_id
    if not batch_id:
        batch_id = str(capture.get("current_batch_id") or "")
    transition_id = transition_id or new_id()
    workflow_run_id = str(capture.get("workflow_run_id") or "")
    if workflow_run_id and sync_workflow:
        with db.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            child_batch_id = child_pause_in_connection(
                connection, smart_capture_id, transition_id
            )
        batch_id = pipeline_operation_id or child_batch_id
        if batch_id:
            try:
                task = boss_capture_task_manager.get_task(batch_id)
                if task.get("status") in {"queued", "running"}:
                    boss_capture_task_manager.pause_capture(batch_id)
            except AppError:
                pass
        return publish_smart_capture_snapshot(db, smart_capture_id) or {}
    requested_batch_pause = False
    executor_missing = False
    if batch_id:
        try:
            task = boss_capture_task_manager.get_task(batch_id)
            if task.get("status") in {"queued", "running"}:
                boss_capture_task_manager.pause_capture(batch_id)
                requested_batch_pause = True
        except AppError:
            executor_missing = True
    _update_capture(
        db,
        smart_capture_id,
        status="pausing" if requested_batch_pause else "paused",
        stage="pause_requested" if requested_batch_pause else "paused",
        waiting_reason="manual_decision",
        control_cause="user_pause",
        transition_id=transition_id,
        message=(
            "正在安全暂停当前采集批次。"
            if requested_batch_pause
            else "原采集执行进程已中断，岗位采集任务已暂停。"
            if executor_missing
            else "岗位采集任务已暂停。"
        ),
    )
    return publish_smart_capture_snapshot(db, smart_capture_id) or {}


def resume_smart_capture(
    db: Database,
    config: AppConfig,
    smart_capture_id: str,
    *,
    sync_workflow: bool = True,
    transition_id: str | None = None,
) -> dict[str, object]:
    capture = get_smart_capture(db, smart_capture_id)
    resumable_waiting = str(capture.get("waiting_reason") or "") in {
        "capture_interrupted",
        "browser_not_running",
    }
    if str(capture["status"]) not in {
        "paused",
        "pausing",
        "waiting_next_batch",
        "interrupted",
    } and not (str(capture["status"]) == "waiting_for_user" and resumable_waiting):
        raise AppError(409, "SMART_CAPTURE_NOT_RESUMABLE", "当前岗位采集任务不在可继续状态。")
    if not boss_scraper_service.get_browser_status().running:
        raise AppError(409, "BROWSER_NOT_RUNNING", "FineJob 专用 Chrome 未启动，暂不能继续岗位采集。")
    workflow_run_id = str(capture.get("workflow_run_id") or "")
    pipeline_operation_id = smart_capture_engine.active_pipeline_operation_id(
        db, smart_capture_id
    )
    batch_id = pipeline_operation_id or str(capture.get("current_batch_id") or "")
    transition_id = transition_id or new_id()
    if workflow_run_id and sync_workflow:
        executor_missing = False
        if batch_id:
            try:
                task = boss_capture_task_manager.get_task(batch_id)
            except AppError:
                executor_missing = True
            else:
                if task.get("status") in {"queued", "running"}:
                    raise AppError(409, "CAPTURE_PAUSING", "当前采集仍在安全暂停中，请稍后继续。")
        if executor_missing:
            with db.connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                _set_child_state_in_connection(
                    connection, smart_capture_id, status="interrupted", stage="interrupted",
                    waiting_reason="capture_interrupted", control_cause="recovery",
                    transition_id=transition_id, message="原采集执行进程已中断，请明确恢复后继续。",
                )
            if pipeline_operation_id and smart_capture_engine.resume_pipeline(
                db,
                smart_capture_id,
                config.output_root / "fine-job" / "boss-capture",
            ):
                return publish_smart_capture_snapshot(db, smart_capture_id) or {}
            return publish_smart_capture_snapshot(db, smart_capture_id) or {}
        with db.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            child_batch_id = child_resume_in_connection(
                connection, smart_capture_id, transition_id
            )
        batch_id = pipeline_operation_id or child_batch_id
        if batch_id:
            try:
                task = boss_capture_task_manager.get_task(batch_id)
                pages = max(1, int(task.get("pages") or 1))
                if str(task.get("stage") or "").endswith("paused"):
                    boss_capture_task_manager.resume_paused_capture(batch_id, pages=pages)
            except AppError:
                pass
        if smart_capture_engine.resume_pipeline(
            db,
            smart_capture_id,
            config.output_root / "fine-job" / "boss-capture",
        ):
            return publish_smart_capture_snapshot(db, smart_capture_id) or {}
        return publish_smart_capture_snapshot(db, smart_capture_id) or {}
    resumed = False
    executor_missing = False
    if batch_id:
        try:
            task = boss_capture_task_manager.get_task(batch_id)
        except AppError:
            executor_missing = True
        else:
            if task.get("status") in {"queued", "running"}:
                raise AppError(409, "CAPTURE_PAUSING", "当前采集仍在安全暂停中，请稍后继续。")
            pages = max(1, int(task.get("pages") or 1))
            if str(task.get("stage") or "").endswith("paused"):
                boss_capture_task_manager.resume_paused_capture(batch_id, pages=pages)
                resumed = True
            elif not workflow_run_id and str(capture.get("status") or "") == "waiting_next_batch":
                boss_capture_task_manager.continue_capture(batch_id, pages=pages)
                resumed = True
    if resumed:
        _update_capture(
            db,
            smart_capture_id,
            status="running",
            stage="capturing",
            waiting_reason="",
            control_cause="",
            transition_id=transition_id,
            message="岗位采集任务已继续。",
        )
    elif smart_capture_engine.resume_pipeline(
        db,
        smart_capture_id,
        config.output_root / "fine-job" / "boss-capture",
    ):
        return publish_smart_capture_snapshot(db, smart_capture_id) or {}
    elif not workflow_run_id:
        config_payload = dict(capture.get("search_config") or {})
        capture = _start_new_batch(db, config, smart_capture_id, config_payload)
    elif executor_missing:
        _update_capture(
            db,
            smart_capture_id,
            status="interrupted",
            stage="interrupted",
            waiting_reason="capture_interrupted",
            control_cause="recovery",
            transition_id=transition_id,
            message="原采集执行进程已中断，正在重新启动当前搜索组合。",
        )
    else:
        _update_capture(
            db,
            smart_capture_id,
            status="running",
            stage="resume_requested",
            waiting_reason="",
            control_cause="recovery",
            transition_id=transition_id,
            message="正在由驾驶舱重新启动中断的采集组合。",
        )
    refreshed = get_smart_capture(db, smart_capture_id)
    if (
        workflow_run_id
        and not batch_id
        and not refreshed.get("current_batch_id")
        and str(refreshed["status"]) == "running"
    ):
        _update_capture(
            db,
            smart_capture_id,
            status="pending",
            stage="waiting_workflow",
            message="驾驶舱仍在等待进入岗位采集阶段。",
        )
        refreshed = get_smart_capture(db, smart_capture_id)
    return publish_smart_capture_snapshot(db, smart_capture_id) or refreshed


def stop_smart_capture(
    db: Database,
    smart_capture_id: str,
    *,
    sync_workflow: bool = True,
    transition_id: str | None = None,
) -> dict[str, object]:
    capture = get_smart_capture(db, smart_capture_id)
    if str(capture["status"]) == "failed":
        raise AppError(409, "SMART_CAPTURE_NOT_STOPPABLE", "失败的岗位采集任务已经结束。")
    if str(capture["status"]) == "completed":
        raise AppError(409, "SMART_CAPTURE_NOT_STOPPABLE", "已完成的岗位采集任务不能再次停止。")
    if str(capture["status"]) == "stopped":
        return capture
    pipeline_operation_id = smart_capture_engine.active_pipeline_operation_id(
        db, smart_capture_id
    )
    batch_id = pipeline_operation_id or str(capture.get("current_batch_id") or "")
    workflow_run_id = str(capture.get("workflow_run_id") or "")
    transition_id = transition_id or new_id()
    if workflow_run_id and sync_workflow:
        with db.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            child_batch_id = child_stop_in_connection(
                connection, smart_capture_id, transition_id
            )
        batch_id = pipeline_operation_id or child_batch_id
        if batch_id:
            try:
                task = boss_capture_task_manager.get_task(batch_id)
                if task.get("status") in {"queued", "running"}:
                    boss_capture_task_manager.stop_capture(batch_id)
            except AppError:
                pass
        return publish_smart_capture_snapshot(db, smart_capture_id) or {}
    if batch_id:
        try:
            task = boss_capture_task_manager.get_task(batch_id)
            if task.get("status") in {"queued", "running"}:
                boss_capture_task_manager.stop_capture(batch_id)
        except AppError:
            pass
    _update_capture(
        db,
        smart_capture_id,
        status="stopped",
        stage="stopped",
        waiting_reason="",
        control_cause="child_user_stop",
        transition_id=transition_id,
        message="岗位采集任务已停止，已获得的岗位继续保留。",
        completed=True,
    )
    return publish_smart_capture_snapshot(db, smart_capture_id) or {}


def sync_capture_snapshot(db: Database, task: dict[str, object]) -> None:
    """把进程内批次快照汇总到岗位采集父任务。"""
    smart_capture_id = str(task.get("smart_capture_id") or "")
    if not smart_capture_id:
        return
    with db.connect() as connection:
        parent = connection.execute(
            "SELECT status, control_cause FROM fj_smart_captures WHERE id = ?",
            (smart_capture_id,),
        ).fetchone()
    if parent is None:
        return
    if str(parent["status"]) == "cancelled" or str(parent["control_cause"] or "") == "parent_cancel":
        # 父取消的高优先级结果不接受执行器迟到回调覆盖。
        return
    if str(parent["status"]) in TERMINAL_STATUSES and str(task.get("status") or "") in {"queued", "running"}:
        return
    status = str(task.get("status") or "")
    stage = str(task.get("stage") or "")
    if status in {"queued", "running"}:
        parent_status = "pausing" if bool(task.get("pause_requested")) else "running"
        parent_control = str(parent["control_cause"] or "")
        waiting_reason = "child_control" if parent_control in {"parent_pause", "child_self_pause"} and bool(task.get("pause_requested")) else "manual_decision" if bool(task.get("pause_requested")) else ""
        control_cause = parent_control if parent_control in {"parent_pause", "child_self_pause"} and bool(task.get("pause_requested")) else "user_pause" if bool(task.get("pause_requested")) else ""
    elif status == "failed" and _is_browser_interruption(task):
        parent_status = "interrupted"
        waiting_reason = "browser_not_running"
        control_cause = "recovery"
    elif status == "failed":
        parent_status = "failed"
        waiting_reason = "child_failed"
        control_cause = "child_failure"
    elif status == "interrupted":
        parent_status = "interrupted"
        waiting_reason = str(task.get("waiting_reason") or "capture_interrupted")
        control_cause = "recovery"
    elif stage.endswith("paused"):
        parent_status = "paused"
        parent_control = str(parent["control_cause"] or "")
        waiting_reason = "child_control" if parent_control in {"parent_pause", "child_self_pause"} else "manual_decision"
        control_cause = parent_control if parent_control in {"parent_pause", "child_self_pause"} else "user_pause"
    elif stage.endswith("stopped"):
        parent_status = "stopped"
        waiting_reason = ""
        control_cause = "child_user_stop"
    else:
        # 批次快照只记录系统等待；OFF/ON 的完成条件统一交给 Engine 判定。
        parent_status = "waiting_next_batch"
        waiting_reason = "next_batch"
        control_cause = ""
    _update_capture(
        db,
        smart_capture_id,
        status=parent_status,
        stage=stage or status,
        waiting_reason=waiting_reason,
        control_cause=control_cause,
        message=str(task.get("message") or ""),
        error_message=str(task.get("error_message") or "") or None,
        progress=_progress_from_task(task),
        result_summary=(
            _result_summary_from_task(task)
            if parent_status in TERMINAL_STATUSES
            else None
        ),
        completed=parent_status in TERMINAL_STATUSES,
    )


def mark_workflow_capture_completed(db: Database, workflow_run_id: str) -> None:
    capture = get_by_workflow_run(db, workflow_run_id)
    if capture is None or str(capture["status"]) in TERMINAL_STATUSES:
        return
    _update_capture(
        db,
        str(capture["smart_capture_id"]),
        status="completed",
        stage="completed",
        waiting_reason="",
        control_cause="",
        message="关联岗位采集已完成，驾驶舱正在消费岗位结果。",
        result_summary={"workflow_run_id": workflow_run_id},
        completed=True,
    )


def get_smart_capture(db: Database, smart_capture_id: str) -> dict[str, object]:
    with db.connect() as connection:
        row = connection.execute(
            "SELECT * FROM fj_smart_captures WHERE id = ?",
            (smart_capture_id,),
        ).fetchone()
        if row is None:
            raise AppError(404, "SMART_CAPTURE_NOT_FOUND", "岗位采集任务不存在。")
        batches = connection.execute(
            "SELECT * FROM fj_boss_capture_batches WHERE smart_capture_id = ? ORDER BY created_at",
            (smart_capture_id,),
        ).fetchall()
        job_rows = connection.execute(
            """
            SELECT j.snapshot_json, j.job_id AS history_record_id,
                   j.was_previously_collected
            FROM fj_boss_capture_batch_jobs j
            JOIN fj_boss_capture_batches b ON b.id = j.capture_id
            WHERE b.smart_capture_id = ?
            ORDER BY j.collected_at
            """,
            (smart_capture_id,),
        ).fetchall()
    data = dict(row)
    jobs_by_id: dict[str, dict[str, object]] = {}
    for job_row in job_rows:
        try:
            job = json.loads(str(job_row["snapshot_json"] or "{}"))
        except json.JSONDecodeError:
            continue
        # 批次关系是新旧岗位判定的权威来源，旧快照没有保存该展示字段。
        job["history_record_id"] = str(job_row["history_record_id"])
        job["is_previously_collected"] = bool(job_row["was_previously_collected"])
        key = str(job.get("job_id") or job.get("history_record_id") or "")
        if key:
            jobs_by_id[key] = job
    current_batch_id = str(data.get("current_batch_id") or "")
    current_task: dict[str, object] | None = None
    if current_batch_id:
        try:
            current_task = boss_capture_task_manager.get_task(current_batch_id)
        except AppError:
            current_task = None
    workflow_run: dict[str, object] | None = None
    workflow_run_id = str(data.get("workflow_run_id") or "")
    if workflow_run_id:
        from backend.app.services.fine_job import workflow_runs

        try:
            workflow_run = workflow_runs.get_workflow_run(db, workflow_run_id)
        except AppError:
            workflow_run = None
    progress = _load_json(str(data.get("progress_json") or "{}"))
    if current_task is not None:
        progress = _progress_from_task(current_task)
    result_summary = _load_json(str(data.get("result_summary_json") or "{}"))
    status = str(data["status"])
    waiting_reason = str(data.get("waiting_reason") or "")
    control_cause = str(data.get("control_cause") or "")
    if status == "waiting_next_batch" and waiting_reason in LEGACY_MANUAL_WAITING_REASONS:
        # 旧数据读取时统一到人工阻塞的 canonical lifecycle。
        status = "waiting_for_user"
    snapshot = {
        "smart_capture_id": str(data["id"]),
        "source": str(data["source"]),
        "workflow_run_id": workflow_run_id or None,
        "status": status,
        "current_batch_id": current_batch_id or None,
        "search_config": _load_json(str(data.get("search_config_json") or "{}")),
        "execution_config": _load_json(
            str(data.get("execution_config_json") or data.get("search_config_json") or "{}")
        ),
        "target_count": data.get("target_count"),
        "stage": str(data.get("stage") or ""),
        "waiting_reason": waiting_reason,
        "control_cause": control_cause,
        "state_version": int(data.get("state_version") or 1),
        "transition_id": str(data.get("transition_id") or ""),
        "capabilities": _capabilities(
            status=status,
            stage=str(data.get("stage") or ""),
            waiting_reason=waiting_reason,
            control_cause=control_cause,
        ),
        "progress": progress,
        "result_summary": result_summary,
        "message": str(data.get("message") or ""),
        "error_message": data.get("error_message"),
        "created_at": str(data["created_at"]),
        "updated_at": str(data["updated_at"]),
        "completed_at": data.get("completed_at"),
        "batches": [dict(batch) for batch in batches],
        # Search/Candidate 结果从 Smart Capture owner 读取，供两个入口使用同一快照。
        "search_combinations": smart_capture_engine.list_search_combinations(db, smart_capture_id),
        "candidate_pool": smart_capture_engine.list_candidate_pool(db, smart_capture_id),
        "prefetch": smart_capture_engine.get_prefetch_summary(db, smart_capture_id),
        "current_batch": current_task or (dict(batches[-1]) if batches else None),
        "jobs": list(jobs_by_id.values()),
        "workflow_run": workflow_run,
    }
    return pipeline_owner.validate_smart_capture_snapshot_contract(snapshot)


def _start_new_batch(
    db: Database,
    config: AppConfig,
    smart_capture_id: str,
    payload: dict[str, Any],
    *,
    requested_authority: cutover_guard.ExecutionAuthority | None = None,
) -> dict[str, object]:
    # pending/recovery 启动仍需经过统一执行容量检查，防止 custom 在两次请求间插入。
    assert_collection_start_allowed(db, requested_kind="smart")
    if not boss_scraper_service.get_browser_status().running:
        raise AppError(409, "BROWSER_NOT_RUNNING", "FineJob 专用 Chrome 未启动，请先打开并完成 BOSS 登录。")
    keywords = [str(value) for value in payload.get("allowed_search_keywords") or [] if str(value)]
    cities = [str(value) for value in payload.get("allowed_cities") or [] if str(value)]
    keyword = str(payload.get("keyword") or (keywords[0] if keywords else "")).strip()
    city = str(payload.get("city") or (cities[0] if cities else "")).strip()
    if not keyword or not city:
        raise AppError(422, "VALIDATION_FAILED", "岗位采集任务缺少搜索词或城市。")
    capture = get_smart_capture(db, smart_capture_id)
    owner = pipeline_owner.get_pipeline_owner(db, smart_capture_id)
    authority = requested_authority or (
        cutover_guard.ExecutionAuthority.WORKFLOW_PIPELINE
        if owner.is_linked
        else cutover_guard.ExecutionAuthority.SMART_CAPTURE
    )
    runtime_guard = cutover_guard.get_runtime_cutover_guard()
    runtime_guard.claim_live_start(
        child_ref=owner.identity,
        requested_authority=authority,
        production_authority=(
            cutover_guard.ExecutionAuthority.WORKFLOW_PIPELINE
            if owner.is_linked and runtime_guard.phase == cutover_guard.CutoverPhase.PRE_CUTOVER
            else cutover_guard.ExecutionAuthority.SMART_CAPTURE
        ),
        allow_independent=owner.is_independent,
    )
    search_task: dict[str, str] | None = None
    started_task: dict[str, object] | None = None
    try:
        search_task = smart_capture_engine.prepare_search_execution(
            db,
            smart_capture_id,
            payload,
        )
        started_task = boss_capture_task_manager.start_capture(
            BossCaptureRequest(
                keyword=keyword,
                city=city,
                pages=max(1, min(10, int(payload.get("pages") or payload.get("min_depth") or 1))),
                filters=dict(payload.get("filters") or {}),
                include_details=bool(payload.get("include_details", False)),
                prefer_current_page=bool(payload.get("prefer_current_page", True)),
                force_search_navigation=bool(payload.get("force_search_navigation", False)),
                filter_strategy_id=str(payload.get("filter_strategy_id") or "") or None,
                capture_source="smart",
                workflow_run_id=owner.workflow_run_id,
                smart_capture_id=owner.identity,
            ),
            output_dir=config.output_root / "fine-job" / "boss-capture",
            db=db,
        )
        bind_batch(
            db,
            smart_capture_id,
            str(started_task["id"]),
            search_task=search_task,
            search_payload=payload,
        )
    except Exception:
        if started_task is None:
            runtime_guard.release_live_start(child_ref=owner.identity)
        else:
            # 绑定失败时先请求停止已创建的执行器，避免释放启动占用后重复启动同一 child。
            try:
                boss_capture_task_manager.stop_capture(str(started_task["id"]))
            except Exception:
                runtime_guard.release_live_start(child_ref=owner.identity)
        raise
    return publish_smart_capture_snapshot(db, smart_capture_id) or {}


def _update_capture(
    db: Database,
    smart_capture_id: str,
    *,
    status: str,
    stage: str,
    message: str,
    error_message: str | None = None,
    completed: bool = False,
    waiting_reason: str | None = None,
    control_cause: str | None = None,
    transition_id: str | None = None,
    current_batch_id: str | None = None,
    progress: dict[str, object] | None = None,
    result_summary: dict[str, object] | None = None,
) -> None:
    now = utc_now()
    with db.connect() as connection:
        row = connection.execute(
            """
            SELECT status, stage, waiting_reason, control_cause, current_batch_id,
            progress_json, result_summary_json, message, error_message,
                   completed_at, state_version, transition_id, workflow_run_id
            FROM fj_smart_captures WHERE id = ?
            """,
            (smart_capture_id,),
        ).fetchone()
        if row is None:
            raise AppError(404, "SMART_CAPTURE_NOT_FOUND", "岗位采集任务不存在。")
        next_waiting_reason = (
            str(row["waiting_reason"] or "")
            if waiting_reason is None
            else waiting_reason
        )
        next_control_cause = (
            str(row["control_cause"] or "")
            if control_cause is None
            else control_cause
        )
        next_transition_id = transition_id or new_id()
        status = _canonical_status(status, next_waiting_reason)
        # 进程迟到状态或重复回调不得把已确认的 terminal outcome 改成另一种结果。
        if str(row["status"]) in TERMINAL_STATUSES:
            return
        next_batch_id = (
            row["current_batch_id"] if current_batch_id is None else current_batch_id
        )
        next_progress_json = (
            str(row["progress_json"] or "{}")
            if progress is None
            else json.dumps(progress, ensure_ascii=False, sort_keys=True)
        )
        next_result_summary_json = (
            str(row["result_summary_json"] or "{}")
            if result_summary is None
            else json.dumps(result_summary, ensure_ascii=False, sort_keys=True)
        )
        next_completed_at = (
            row["completed_at"] or now
            if completed
            else None
        )
        current_observable = (
            str(row["status"]),
            str(row["stage"]),
            str(row["waiting_reason"] or ""),
            str(row["control_cause"] or ""),
            row["current_batch_id"],
            str(row["progress_json"] or "{}"),
            str(row["result_summary_json"] or "{}"),
            str(row["message"] or ""),
            row["error_message"],
            row["completed_at"],
        )
        next_observable = (
            status,
            stage,
            next_waiting_reason,
            next_control_cause,
            next_batch_id,
            next_progress_json,
            next_result_summary_json,
            message,
            error_message,
            next_completed_at,
        )
        if current_observable == next_observable:
            return
        state_version = max(1, int(row["state_version"] or 1)) + 1
        connection.execute(
            """
            UPDATE fj_smart_captures
            SET status = ?, stage = ?, waiting_reason = ?, control_cause = ?,
                current_batch_id = ?, progress_json = ?, result_summary_json = ?,
                message = ?, error_message = ?, state_version = ?,
                transition_id = ?, updated_at = ?, completed_at = CASE WHEN ? THEN COALESCE(completed_at, ?) ELSE NULL END
            WHERE id = ?
            """,
            (
                status,
                stage,
                next_waiting_reason,
                next_control_cause,
                next_batch_id,
                next_progress_json,
                next_result_summary_json,
                message,
                error_message,
                state_version,
                next_transition_id,
                now,
                int(completed),
                now,
                smart_capture_id,
            ),
        )
        updated_row = connection.execute(
            "SELECT * FROM fj_smart_captures WHERE id = ?",
            (smart_capture_id,),
        ).fetchone()
        if updated_row is not None:
            relation = connection.execute(
                """
                SELECT id FROM fj_workflow_children
                WHERE workflow_run_id = ? AND child_type = 'smart_capture' AND child_ref = ?
                """,
                (str(updated_row["workflow_run_id"] or ""), smart_capture_id),
            ).fetchone()
            if relation is not None and status in {"completed", "stopped", "failed", "interrupted"}:
                workflow_children.record_child_event_in_connection(
                    connection,
                    child_relation_id=str(relation["id"]),
                    child_type=workflow_children.SMART_CAPTURE_CHILD_TYPE,
                    child_ref=smart_capture_id,
                    child_status=status,
                    transition_id=next_transition_id,
                    state_version=state_version,
                    waiting_reason=next_waiting_reason,
                    control_cause=next_control_cause,
                    result_summary=_load_json(next_result_summary_json),
                    occurred_at=now,
                )
            elif relation is not None:
                workflow_children.project_smart_capture_in_connection(
                    connection,
                    updated_row,
                    capabilities=_capabilities(
                        status=status,
                        stage=stage,
                        waiting_reason=next_waiting_reason,
                        control_cause=next_control_cause,
                    ),
                    now=now,
                )
    publish_smart_capture_snapshot(db, smart_capture_id)


def touch_pipeline_snapshot(db: Database, smart_capture_id: str) -> None:
    """Pipeline 子表改变可观察快照时单独推进 child 版本。"""
    now = utc_now()
    with db.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "UPDATE fj_smart_captures SET state_version = state_version + 1, updated_at = ? WHERE id = ?",
            (now, smart_capture_id),
        )
        row = connection.execute(
            "SELECT * FROM fj_smart_captures WHERE id = ?", (smart_capture_id,)
        ).fetchone()
        if row is not None and str(row["workflow_run_id"] or ""):
            workflow_children.project_smart_capture_in_connection(
                connection,
                row,
                capabilities=_capabilities(
                    status=str(row["status"]),
                    stage=str(row["stage"] or ""),
                    waiting_reason=str(row["waiting_reason"] or ""),
                    control_cause=str(row["control_cause"] or ""),
                ),
                now=now,
            )
    publish_smart_capture_snapshot(db, smart_capture_id)


def _progress_from_task(task: dict[str, object]) -> dict[str, object]:
    """将批次进度转换为 Smart Capture snapshot 的稳定字段。"""
    return {
        "current": int(task.get("progress_current") or 0),
        "total": int(task.get("progress_total") or 0),
        "jobs_collected": int(task.get("jobs_collected") or len(task.get("jobs") or [])),
        "details_completed": int(task.get("details_completed") or 0),
        "details_failed": int(task.get("details_failed") or 0),
    }


def _is_browser_interruption(task: dict[str, object]) -> bool:
    """将浏览器执行器丢失归入可恢复中断，而不是 hard failure。"""
    message = " ".join(
        str(task.get(key) or "").lower()
        for key in ("error_message", "message", "stage")
    )
    return any(marker in message for marker in ("browser", "cdp", "chrome", "target closed"))


def _result_summary_from_task(task: dict[str, object]) -> dict[str, object]:
    """终态快照保留已采集数量和批次完成结果。"""
    return {
        "jobs_collected": int(task.get("jobs_collected") or len(task.get("jobs") or [])),
        "details_completed": int(task.get("details_completed") or 0),
        "details_failed": int(task.get("details_failed") or 0),
        "has_more": bool(task.get("has_more")),
    }


def _capabilities(
    *,
    status: str,
    stage: str,
    waiting_reason: str,
    control_cause: str,
) -> dict[str, bool]:
    """按 Smart Capture lifecycle 计算固定五项能力，不暴露 parent cancel。"""
    terminal = status in TERMINAL_STATUSES
    recoverable_waiting = waiting_reason in {"capture_interrupted", "browser_not_running"}
    return {
        "start": status == "pending",
        "pause": status in {"running", "pausing"},
        "resume": status in {"paused", "pausing", "waiting_next_batch", "interrupted"}
        or (status == "waiting_for_user" and recoverable_waiting),
        "retry": status in {"pending", "interrupted"}
        or (status == "waiting_for_user" and recoverable_waiting),
        "stop": not terminal,
    }


def _canonical_status(status: str, waiting_reason: str) -> str:
    """禁止新写入用 waiting_next_batch 表达人工阻塞。"""
    if status == "waiting_next_batch" and waiting_reason in LEGACY_MANUAL_WAITING_REASONS:
        return "waiting_for_user"
    return status


def _build_execution_config(payload: dict[str, Any]) -> dict[str, object]:
    """把 linked/independent 共用的完整执行配置固定在 Smart Capture owner。"""
    return {
        "search": {
            "filter_strategy_id": str(payload.get("filter_strategy_id") or ""),
            "keywords": list(payload.get("allowed_search_keywords") or []),
            "cities": list(payload.get("allowed_cities") or []),
            "pages": int(payload.get("pages") or payload.get("min_depth") or 1),
            "filters": dict(payload.get("filters") or {}),
            "include_details": bool(payload.get("include_details", False)),
            "prefer_current_page": bool(payload.get("prefer_current_page", True)),
        },
        "candidate_target_count": int(payload.get("candidate_target_count") or 0) or None,
        "delivery_target": {
            "enabled": bool(payload.get("delivery_target_enabled", False)),
            "recommendation_strategy_id": str(payload.get("recommendation_strategy_id") or ""),
            "recommend_target": payload.get("recommend_target") or payload.get("target_count"),
            "review_target": payload.get("review_target"),
            "target_mode": str(payload.get("target_mode") or "all"),
        },
        "jd_detail_policy": {
            "include_details": bool(payload.get("include_details", False)),
            "analyze_all_candidates": bool(payload.get("analyze_all_candidates", False)),
            "batch_size": int(payload.get("analysis_batch_size") or payload.get("jd_batch_size") or 5),
        },
        "analysis": {
            "codex_model": str(payload.get("codex_model") or ""),
            "codex_reasoning_effort": str(payload.get("codex_reasoning_effort") or ""),
            "guidance": str(payload.get("analysis_guidance") or ""),
            "handoff": str(payload.get("execution_policy_codex_handoff") or "auto"),
            "after_analysis_batch": str(payload.get("execution_policy_after_analysis_batch") or "auto_continue"),
            "analyze_all_candidates": bool(payload.get("analyze_all_candidates", False)),
            "stop_after_current_batch": bool(payload.get("stop_after_current_batch", False)),
            "batch_size": int(payload.get("analysis_batch_size") or payload.get("jd_batch_size") or 5),
        },
        "context_budget": int(payload.get("context_soft_budget_characters") or 12000),
        "stop_policy": {
            "min_depth": int(payload.get("min_depth") or payload.get("pages") or 1),
            "scroll_batch_size": int(payload.get("scroll_batch_size") or 3),
            "max_depth": int(payload.get("max_depth") or 20),
            "low_yield_streak_limit": int(payload.get("low_yield_streak_limit") or 3),
            "stop_after_current_batch": bool(payload.get("stop_after_current_batch", False)),
        },
    }


def _validate_execution_config_for_start(
    db: Database, payload: Mapping[str, Any] | dict[str, Any]
) -> None:
    """在独立入口再次执行与 Workflow 入口相同的条件校验。"""
    errors = execution_config_validation_errors(payload)
    if errors:
        raise AppError(422, "VALIDATION_FAILED", "；".join(errors))
    if bool(payload.get("delivery_target_enabled", False)):
        strategies.require_recommendation_strategy(
            db,
            recommendation_strategy_id=str(payload.get("recommendation_strategy_id") or ""),
            filter_strategy_id=str(payload.get("filter_strategy_id") or ""),
        )


def _load_json(value: str) -> dict[str, Any]:
    try:
        loaded = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}
