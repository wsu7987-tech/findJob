from __future__ import annotations

import json
from typing import Any

from backend.app.config import AppConfig
from backend.app.db import Database
from backend.app.errors import AppError
from backend.app.services.fine_job.boss_capture_tasks import boss_capture_task_manager
from backend.app.services.fine_job.boss_scraper.service import (
    BossCaptureRequest,
    boss_scraper_service,
)
from backend.app.utils import new_id, utc_now


ACTIVE_STATUSES = {"running", "pausing", "paused", "waiting_next_batch", "interrupted"}
TERMINAL_STATUSES = {"completed", "stopped", "failed"}


def recover_interrupted_smart_captures(db: Database) -> None:
    """应用启动时把失去进程执行器的任务收敛到可继续状态。"""
    now = utc_now()
    with db.connect() as connection:
        connection.execute(
            """
            UPDATE fj_smart_captures
            SET status = 'interrupted', stage = 'interrupted',
                message = '应用重启后采集执行已中断，请点击继续恢复当前任务。',
                updated_at = ?
            WHERE status IN ('running', 'pausing', 'waiting_next_batch')
            """,
            (now,),
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
        current = connection.execute(
            """
            SELECT c.id, c.status
            FROM fj_smart_capture_current current_task
            JOIN fj_smart_captures c ON c.id = current_task.smart_capture_id
            WHERE current_task.slot = 1
            """
        ).fetchone()
        placeholders = ",".join("?" for _ in ACTIVE_STATUSES)
        active = connection.execute(
            f"SELECT id, status FROM fj_smart_captures WHERE status IN ({placeholders}) "
            "ORDER BY updated_at DESC, created_at DESC LIMIT 1",
            tuple(ACTIVE_STATUSES),
        ).fetchone()
        selected = current if current is not None and str(current["status"]) in ACTIVE_STATUSES else active
        if selected is None:
            selected = current or connection.execute(
                "SELECT id, status FROM fj_smart_captures ORDER BY updated_at DESC, created_at DESC LIMIT 1"
            ).fetchone()
        if selected is not None:
            # 当前活动任务优先；没有活动任务时保留最近一次最终快照供页面恢复。
            connection.execute(
                """
                INSERT INTO fj_smart_capture_current (slot, smart_capture_id, updated_at)
                VALUES (1, ?, ?)
                ON CONFLICT(slot) DO UPDATE SET
                  smart_capture_id = excluded.smart_capture_id,
                  updated_at = excluded.updated_at
                """,
                (str(selected["id"]), now),
            )


def create_smart_capture(
    db: Database,
    *,
    source: str,
    workflow_run_id: str | None,
    search_config: dict[str, Any],
    target_count: int | None = None,
) -> dict[str, object]:
    """创建独立岗位采集身份，并由服务端切换当前任务。"""
    if source not in {"task_cockpit", "boss_capture"}:
        raise AppError(422, "VALIDATION_FAILED", "岗位采集任务来源无效。")
    capture_id = new_id()
    now = utc_now()
    with db.connect() as connection:
        connection.execute(
            """
            INSERT INTO fj_smart_captures (
              id, source, workflow_run_id, status, search_config_json,
              target_count, stage, message, created_at, updated_at
            ) VALUES (?, ?, ?, 'pending', ?, ?, 'created', ?, ?, ?)
            """,
            (
                capture_id,
                source,
                workflow_run_id,
                json.dumps(search_config, ensure_ascii=False),
                target_count,
                "岗位采集任务已创建，等待启动首个批次。",
                now,
                now,
            ),
        )
        placeholders = ",".join("?" for _ in ACTIVE_STATUSES)
        active_row = connection.execute(
            f"SELECT id FROM fj_smart_captures WHERE status IN ({placeholders}) "
            "ORDER BY updated_at DESC, created_at DESC LIMIT 1",
            tuple(ACTIVE_STATUSES),
        ).fetchone()
        if source == "boss_capture" or active_row is None:
            # 活动采集保持当前任务身份；驾驶舱关联任务在真正启动批次时接管当前指针。
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
    return get_smart_capture(db, capture_id)


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
    with db.connect() as connection:
        row = connection.execute(
            "SELECT smart_capture_id FROM fj_smart_capture_current WHERE slot = 1"
        ).fetchone()
    return get_smart_capture(db, str(row["smart_capture_id"])) if row is not None else None


def get_active_smart_capture(db: Database) -> dict[str, object] | None:
    current = get_current_smart_capture(db)
    if current is not None and str(current["status"]) in ACTIVE_STATUSES:
        return current
    with db.connect() as connection:
        placeholders = ",".join("?" for _ in ACTIVE_STATUSES)
        row = connection.execute(
            f"SELECT id FROM fj_smart_captures WHERE status IN ({placeholders}) "
            "ORDER BY updated_at DESC, created_at DESC LIMIT 1",
            tuple(ACTIVE_STATUSES),
        ).fetchone()
    return get_smart_capture(db, str(row["id"])) if row is not None else None


def start_independent_capture(
    db: Database,
    config: AppConfig,
    payload: dict[str, Any],
) -> dict[str, object]:
    """从岗位采集页创建不关联 Workflow Run 的任务并启动首批采集。"""
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
    )
    return _start_new_batch(db, config, str(capture["smart_capture_id"]), payload)


def bind_batch(db: Database, smart_capture_id: str, batch_id: str) -> None:
    now = utc_now()
    with db.connect() as connection:
        connection.execute(
            "UPDATE fj_boss_capture_batches SET smart_capture_id = ? WHERE id = ?",
            (smart_capture_id, batch_id),
        )
        connection.execute(
            """
            UPDATE fj_smart_captures
            SET current_batch_id = ?, status = 'running', stage = 'capturing',
                message = '正在采集岗位。', updated_at = ?, error_message = NULL
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
    try:
        sync_capture_snapshot(db, boss_capture_task_manager.get_task(batch_id))
    except AppError:
        # 批次进程快照短暂不可读时，父任务仍可从后续监听事件继续同步。
        pass


def pause_smart_capture(
    db: Database,
    smart_capture_id: str,
    *,
    sync_workflow: bool = True,
) -> dict[str, object]:
    capture = get_smart_capture(db, smart_capture_id)
    if str(capture["status"]) in TERMINAL_STATUSES:
        raise AppError(409, "SMART_CAPTURE_NOT_PAUSABLE", "当前岗位采集任务已结束，不能暂停。")
    batch_id = str(capture.get("current_batch_id") or "")
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
        message=(
            "正在安全暂停当前采集批次。"
            if requested_batch_pause
            else "原采集执行进程已中断，岗位采集任务已暂停。"
            if executor_missing
            else "岗位采集任务已暂停。"
        ),
    )
    workflow_run_id = str(capture.get("workflow_run_id") or "")
    if sync_workflow and workflow_run_id:
        from backend.app.services.fine_job import workflow_runs

        workflow_runs.pause_deep_job_search_run(db, workflow_run_id, sync_capture=False)
    return get_smart_capture(db, smart_capture_id)


def resume_smart_capture(
    db: Database,
    config: AppConfig,
    smart_capture_id: str,
    *,
    sync_workflow: bool = True,
) -> dict[str, object]:
    capture = get_smart_capture(db, smart_capture_id)
    if str(capture["status"]) not in {"paused", "pausing", "waiting_next_batch", "interrupted"}:
        raise AppError(409, "SMART_CAPTURE_NOT_RESUMABLE", "当前岗位采集任务不在可继续状态。")
    if not boss_scraper_service.get_browser_status().running:
        raise AppError(409, "BROWSER_NOT_RUNNING", "FineJob 专用 Chrome 未启动，暂不能继续岗位采集。")
    workflow_run_id = str(capture.get("workflow_run_id") or "")
    batch_id = str(capture.get("current_batch_id") or "")
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
            message="岗位采集任务已继续。",
        )
    elif not workflow_run_id:
        config_payload = dict(capture.get("search_config") or {})
        capture = _start_new_batch(db, config, smart_capture_id, config_payload)
    elif executor_missing:
        _update_capture(
            db,
            smart_capture_id,
            status="interrupted",
            stage="interrupted",
            message="原采集执行进程已中断，正在重新启动当前搜索组合。",
        )
    else:
        _update_capture(
            db,
            smart_capture_id,
            status="running",
            stage="resume_requested",
            message="正在由驾驶舱重新启动中断的采集组合。",
        )
    if sync_workflow and workflow_run_id:
        from backend.app.services.fine_job import workflow_runs

        workflow_run = capture.get("workflow_run") or {}
        if str(workflow_run.get("status") or "") in {"paused", "waiting_for_user"}:
            workflow_runs.resume_deep_job_search_run(
                db,
                config,
                workflow_run_id,
                sync_capture=False,
            )
        if not resumed:
            workflow_runs.advance_deep_job_search(db, config, workflow_run_id)
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
    return refreshed


def stop_smart_capture(
    db: Database,
    smart_capture_id: str,
    *,
    sync_workflow: bool = True,
) -> dict[str, object]:
    capture = get_smart_capture(db, smart_capture_id)
    batch_id = str(capture.get("current_batch_id") or "")
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
        message="岗位采集任务已停止，已获得的岗位继续保留。",
        completed=True,
    )
    workflow_run_id = str(capture.get("workflow_run_id") or "")
    if sync_workflow and workflow_run_id:
        from backend.app.services.fine_job import workflow_runs

        workflow_runs.cancel_deep_job_search_run(db, workflow_run_id, sync_capture=False)
    return get_smart_capture(db, smart_capture_id)


def sync_capture_snapshot(db: Database, task: dict[str, object]) -> None:
    """把进程内批次快照汇总到岗位采集父任务。"""
    smart_capture_id = str(task.get("smart_capture_id") or "")
    if not smart_capture_id:
        return
    with db.connect() as connection:
        parent = connection.execute(
            "SELECT source, target_count, status FROM fj_smart_captures WHERE id = ?",
            (smart_capture_id,),
        ).fetchone()
    if parent is None:
        return
    if str(parent["status"]) in TERMINAL_STATUSES and str(task.get("status") or "") in {"queued", "running"}:
        return
    status = str(task.get("status") or "")
    stage = str(task.get("stage") or "")
    if status in {"queued", "running"}:
        parent_status = "pausing" if bool(task.get("pause_requested")) else "running"
    elif status == "failed":
        parent_status = "failed"
    elif stage.endswith("paused"):
        parent_status = "paused"
    elif stage.endswith("stopped"):
        parent_status = "stopped"
    else:
        reached_independent_target = (
            str(parent["source"]) == "boss_capture"
            and int(parent["target_count"] or 0) > 0
            and int(task.get("jobs_collected") or len(task.get("jobs") or []))
            >= int(parent["target_count"])
        )
        parent_status = (
            "completed"
            if reached_independent_target or not bool(task.get("has_more"))
            else "waiting_next_batch"
        )
    _update_capture(
        db,
        smart_capture_id,
        status=parent_status,
        stage=stage or status,
        message=str(task.get("message") or ""),
        error_message=str(task.get("error_message") or "") or None,
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
        message="关联岗位采集已完成，驾驶舱正在消费岗位结果。",
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
    return {
        "smart_capture_id": str(data["id"]),
        "source": str(data["source"]),
        "workflow_run_id": workflow_run_id or None,
        "status": str(data["status"]),
        "current_batch_id": current_batch_id or None,
        "search_config": _load_json(str(data.get("search_config_json") or "{}")),
        "target_count": data.get("target_count"),
        "stage": str(data.get("stage") or ""),
        "message": str(data.get("message") or ""),
        "error_message": data.get("error_message"),
        "created_at": str(data["created_at"]),
        "updated_at": str(data["updated_at"]),
        "completed_at": data.get("completed_at"),
        "batches": [dict(batch) for batch in batches],
        "current_batch": current_task or (dict(batches[-1]) if batches else None),
        "jobs": list(jobs_by_id.values()),
        "workflow_run": workflow_run,
    }


def _start_new_batch(
    db: Database,
    config: AppConfig,
    smart_capture_id: str,
    payload: dict[str, Any],
) -> dict[str, object]:
    if not boss_scraper_service.get_browser_status().running:
        raise AppError(409, "BROWSER_NOT_RUNNING", "FineJob 专用 Chrome 未启动，请先打开并完成 BOSS 登录。")
    keywords = [str(value) for value in payload.get("allowed_search_keywords") or [] if str(value)]
    cities = [str(value) for value in payload.get("allowed_cities") or [] if str(value)]
    keyword = str(payload.get("keyword") or (keywords[0] if keywords else "")).strip()
    city = str(payload.get("city") or (cities[0] if cities else "")).strip()
    if not keyword or not city:
        raise AppError(422, "VALIDATION_FAILED", "岗位采集任务缺少搜索词或城市。")
    task = boss_capture_task_manager.start_capture(
        BossCaptureRequest(
            keyword=keyword,
            city=city,
            pages=max(1, min(10, int(payload.get("pages") or payload.get("min_depth") or 1))),
            filters=dict(payload.get("filters") or {}),
            include_details=bool(payload.get("include_details", False)),
            prefer_current_page=bool(payload.get("prefer_current_page", True)),
            filter_strategy_id=str(payload.get("filter_strategy_id") or "") or None,
            capture_source="smart",
            smart_capture_id=smart_capture_id,
        ),
        output_dir=config.output_root / "fine-job" / "boss-capture",
        db=db,
    )
    bind_batch(db, smart_capture_id, str(task["id"]))
    return get_smart_capture(db, smart_capture_id)


def _update_capture(
    db: Database,
    smart_capture_id: str,
    *,
    status: str,
    stage: str,
    message: str,
    error_message: str | None = None,
    completed: bool = False,
) -> None:
    now = utc_now()
    with db.connect() as connection:
        connection.execute(
            """
            UPDATE fj_smart_captures
            SET status = ?, stage = ?, message = ?, error_message = ?,
                updated_at = ?, completed_at = CASE WHEN ? THEN COALESCE(completed_at, ?) ELSE NULL END
            WHERE id = ?
            """,
            (status, stage, message, error_message, now, int(completed), now, smart_capture_id),
        )


def _load_json(value: str) -> dict[str, Any]:
    try:
        loaded = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}
