from __future__ import annotations

from backend.app.errors import AppError
from backend.app.services.fine_job.boss_capture_tasks import boss_capture_task_manager
from backend.app.services.fine_job.collection_readiness import ensure_ready
from backend.app.services.fine_job.collection_start_operations import bind_existing, current_operation
from backend.app.services.fine_job.filter_exclusions import assert_job_action_allowed
from backend.app.services.fine_job.strategies import get_filter_strategy


def prepare_custom_phase(db, task_id, *, pages=None, job_ids=None, force=False, manual_override=False):
    task = boss_capture_task_manager.get_task(task_id)
    if task.get("capture_source") == "smart":
        raise AppError(409, "SMART_CAPTURE_CONTROL_REQUIRED", "请通过智能采集控制入口操作此任务。")
    if task["status"] in {"queued", "running"}:
        raise AppError(409, "TASK_RUNNING", "当前采集任务仍在运行。")
    expected = None
    if pages is not None:
        if not 1 <= pages <= 10:
            raise AppError(422, "VALIDATION_FAILED", "续采页数必须在 1 到 10 之间。")
        if task.get("has_more") is False:
            raise AppError(409, "CAPTURE_REACHED_END", "当前搜索结果已经没有更多岗位。")
        if not task.get("jobs_path"):
            raise AppError(409, "CAPTURE_NOT_READY", "首次采集结果尚未准备完成。")
        expected = boss_capture_task_manager.get_original_target(task_id)
    if job_ids is not None:
        selected = [job for job in task.get("jobs", []) if job.get("job_id") in job_ids and (force or job.get("detail_status") != "completed")]
        if not selected:
            raise AppError(422, "VALIDATION_FAILED", "请选择可采集详情的岗位。")
        for job in selected:
            if job.get("history_record_id"):
                strategy_id = job.get("filter_strategy_id")
                assert_job_action_allowed(db, str(job["history_record_id"]), strategy=get_filter_strategy(db, str(strategy_id)) if strategy_id else None, action="detail", allow_manual_override=manual_override)
    ensure_ready(expected_target_id=expected)
    latest = boss_capture_task_manager.get_task(task_id)
    if latest["updated_at"] != task["updated_at"]:
        raise AppError(409, "START_OWNER_CHANGED", "检查期间任务状态已变化，请重新确认。")
    # 先记录稳定阶段引用，后续状态写入沿用已有执行器的短事务。
    bind_existing(db, "custom", task_id, task_id)


def prepare_smart(db, capture, *, resume=False, parent_resume=False):
    from backend.app.services.fine_job import smart_capture_engine, smart_captures
    capture_id = str(capture["smart_capture_id"])
    workflow_id = str(capture.get("workflow_run_id") or "")
    parent = None
    if workflow_id:
        with db.connect() as connection:
            parent = connection.execute("SELECT * FROM fj_workflow_runs WHERE id = ?", (workflow_id,)).fetchone()
        if parent is None or parent["status"] in {"cancelled", "failed", "completed", "completed_with_errors"}:
            raise AppError(409, "WORKFLOW_NOT_RESUMABLE", "父任务已结束，不能启动采集。")
        allowed_parent_states = {"active", "waiting_child_paused", "waiting_child_interrupted"} if resume else {"active"}
        if not parent_resume and (parent["control_state"] not in allowed_parent_states or parent["control_state"] == "paused"):
            raise AppError(409, "WORKFLOW_CONTROL_REQUIRED", "请先恢复父任务，再启动采集。")
    expected = None
    operation_ref = smart_capture_engine.active_pipeline_operation_id(db, capture_id) if resume else ""
    pending_detail = pending_prefetch = None
    if resume:
        with db.connect() as connection:
            pending_detail = connection.execute("SELECT 1 FROM fj_workflow_tasks WHERE smart_capture_id = ? AND task_type = 'deep_job_search_jd' AND status IN ('pending', 'running') LIMIT 1", (capture_id,)).fetchone()
            pending_prefetch = connection.execute("SELECT 1 FROM fj_workflow_prefetch_items WHERE smart_capture_id = ? AND status IN ('pending', 'collecting') AND lifecycle_status NOT IN ('cancelled', 'abandoned') LIMIT 1", (capture_id,)).fetchone()
    operation_ref = operation_ref or ("" if pending_detail or pending_prefetch else str(capture.get("current_batch_id") or ""))
    if resume and operation_ref:
        try:
            task = boss_capture_task_manager.get_task(operation_ref)
        except AppError:
            if not smart_capture_engine.active_pipeline_operation_id(db, capture_id):
                raise AppError(409, "CAPTURE_PAGE_NOT_REUSABLE", "原搜索页执行身份已丢失，请新建采集；已有结果已保留。") from None
        else:
            if task["status"] in {"queued", "running"}:
                raise AppError(409, "CAPTURE_PAUSING", "采集仍在安全暂停中，请稍后继续。")
            if str(task.get("stage") or "").startswith("list"):
                expected = boss_capture_task_manager.get_original_target(operation_ref)
    needs_browser = not resume or bool(expected)
    if resume:
        needs_browser = needs_browser or bool(pending_detail or pending_prefetch) or not operation_ref
    if needs_browser:
        ensure_ready(expected_target_id=expected)
    latest = smart_captures.get_smart_capture(db, capture_id)
    if latest["state_version"] != capture["state_version"]:
        raise AppError(409, "START_OWNER_CHANGED", "检查期间采集状态已变化，请重新确认。")
    if parent is not None:
        with db.connect() as connection:
            latest_parent = connection.execute("SELECT * FROM fj_workflow_runs WHERE id = ?", (workflow_id,)).fetchone()
        if latest_parent is None or dict(parent) != dict(latest_parent):
            raise AppError(409, "START_OWNER_CHANGED", "检查期间父任务状态已变化，请重新确认。")
    operation = current_operation()
    if operation is not None:
        operation["checked_owner"] = (capture_id, capture["state_version"], dict(parent) if parent else None)
    return operation_ref


def guard_owner_in_connection(connection):
    operation = current_operation()
    checked = operation.get("checked_owner") if operation else None
    if not checked:
        return
    capture_id, version, parent = checked
    row = connection.execute("SELECT state_version FROM fj_smart_captures WHERE id = ?", (capture_id,)).fetchone()
    current_parent = connection.execute("SELECT * FROM fj_workflow_runs WHERE id = ?", (parent["id"],)).fetchone() if parent else None
    # 检查后的首个状态事务再次确认归属，期间的停止或父级控制优先生效。
    if row is None or row["state_version"] != version or (parent and (current_parent is None or dict(current_parent) != parent)):
        raise AppError(409, "START_OWNER_CHANGED", "检查期间父子任务已变化，请重新确认。")
    from backend.app.services.fine_job.collection_start_operations import bind_in_connection
    bind_in_connection(connection, "smart", capture_id)
    operation.pop("checked_owner")


def has_pending_details(db, capture_id):
    with db.connect() as connection:
        formal = connection.execute("SELECT 1 FROM fj_workflow_tasks WHERE smart_capture_id = ? AND task_type = 'deep_job_search_jd' AND (status IN ('pending', 'running') OR (status = 'failed' AND retryable = 1)) LIMIT 1", (capture_id,)).fetchone()
        prefetch = connection.execute("SELECT 1 FROM fj_workflow_prefetch_items WHERE smart_capture_id = ? AND status IN ('pending', 'collecting') AND lifecycle_status NOT IN ('cancelled', 'abandoned') LIMIT 1", (capture_id,)).fetchone()
    return bool(formal or prefetch)
