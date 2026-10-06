from __future__ import annotations

from copy import deepcopy
import json

from backend.app.errors import AppError
from backend.app.services.fine_job.capture_pacing import default_pacing
from backend.app.services.fine_job.capture_runtime import ACTIVITY_FIELDS
from backend.app.services.fine_job.smart_capture_events import SmartCaptureEventBroker
from backend.app.utils import utc_now


boss_capture_event_broker = SmartCaptureEventBroker()
QUALITY_FIELDS = ("capture_validity", "validity_reason", "filters_confirmed", "range_complete",
                  "window_id", "attempted_pages", "succeeded_pages", "failed_pages")


def public_task(task):
    return deepcopy({key: value for key, value in task.items() if not key.startswith("_")})


def business_state(snapshot):
    return {key: value for key, value in snapshot.items() if key not in {"state_version", "updated_at", "server_now"}}


def persist_task(task):
    """快照、业务列和版本在同一个事务内提交，调用方随后发布事件。"""
    db = task.get("_db")
    current = public_task(task)
    previous = task.get("_published_snapshot")
    changed = previous is None or business_state(previous) != business_state(current)
    version = int(task.get("state_version") or 1) + (1 if changed and previous is not None else 0)
    if not changed and previous:
        task["updated_at"] = previous["updated_at"]
    task["state_version"] = version
    snapshot = public_task(task)
    recovery = {key: deepcopy(task.get(key)) for key in (
        "_runtime_state", "_list_data", "_capture_target_id", "_paused_detail_job_ids",
        "_detail_mode", "_capture_id", "_control_status",
    )}
    request = task.get("_request")
    if request:
        from dataclasses import asdict
        recovery["_request"] = asdict(request)
        recovery["_request"]["output_dir"] = str(request.output_dir) if request.output_dir else None
    saved = {**snapshot, "_recovery": recovery}
    if db is not None:
        with db.connect() as connection:
            connection.execute(
                """UPDATE fj_boss_capture_batches SET state_version=?, pacing_snapshot_json=?, progress_json=?,
                status=?, stage=?, message=?, error_message=?, jobs_collected=?, details_completed=?, details_failed=?,
                progress_current=?, progress_total=?, source_url=?, control_status=?, updated_at=?, finished_at=? WHERE id=?""",
                (version, json.dumps(task.get("capture_pacing") or default_pacing()),
                 json.dumps(saved, ensure_ascii=False), task["status"], task["stage"], task["message"], task.get("error_message"),
                 task.get("jobs_collected", 0), task.get("details_completed", 0), task.get("details_failed", 0),
                 task.get("progress_current", 0), task.get("progress_total", 0), task.get("source_url"),
                 task.get("_control_status", "active"), task["updated_at"], task.get("finished_at"), task["id"]),
            )
            # 历史单详情任务复用启动回执保存自己的身份和版本，不增加历史采集批次。
            connection.execute(
                """UPDATE fj_collection_start_operations SET result_summary_json=?, updated_at=?
                WHERE result_task_id=? OR result_phase_ref=?""",
                (json.dumps(saved, ensure_ascii=False), task["updated_at"], task["id"], task["id"]),
            )
    task["_published_snapshot"] = snapshot
    return changed, snapshot


def read_saved_task(db, task_id):
    with db.connect() as connection:
        row = connection.execute("SELECT * FROM fj_boss_capture_batches WHERE id=?", (task_id,)).fetchone()
        receipt = connection.execute(
            "SELECT * FROM fj_collection_start_operations WHERE result_task_id=? OR result_phase_ref=? ORDER BY updated_at DESC LIMIT 1",
            (task_id, task_id),
        ).fetchone()
        if row is None and receipt is None:
            raise AppError(404, "CAPTURE_TASK_NOT_FOUND", "采集任务不存在。")
        saved = json.loads(row["progress_json"] or "{}") if row else json.loads(receipt["result_summary_json"] or "{}")
        if not saved:
            saved = dict(row) if row else {}
        if "jobs" not in saved:
            jobs = connection.execute("SELECT snapshot_json FROM fj_boss_capture_batch_jobs WHERE capture_id=? ORDER BY collected_at", (task_id,)).fetchall()
            saved["jobs"] = [json.loads(job["snapshot_json"]) for job in jobs]
    saved.setdefault("id", task_id)
    saved.setdefault("state_version", int(row["state_version"]) if row else 1)
    saved.setdefault("capture_pacing", default_pacing(json.loads(row["pacing_snapshot_json"] or "{}") if row else None))
    for key, default in (("keyword", ""), ("city", ""), ("pages", 0), ("auto_details", False),
                         ("status", "failed"), ("stage", "interrupted"), ("message", "已保存结果可继续查看。"),
                         ("created_at", receipt["created_at"] if receipt else utc_now()), ("updated_at", utc_now())):
        saved.setdefault(key, default)
    if row:
        for field in ("progress_json", "pacing_snapshot_json"):
            saved.pop(field, None)
    return saved


def clear_activity(task):
    for key in ACTIVITY_FIELDS:
        task[key] = "" if key == "activity_message" else None


def recover_task(db, task_id):
    saved = read_saved_task(db, task_id)
    recovery = saved.pop("_recovery", {})
    task = {**saved, **recovery, "_db": db, "_published_snapshot": dict(saved)}
    # 原执行线程已经丢失，先保存恢复状态，GET/SSE 不重新启动浏览器。
    if task["status"] in {"queued", "running"}:
        task.update(status="failed", stage="list_interrupted" if not str(task["stage"]).startswith("details") else "details_interrupted",
                    message="采集执行进程已退出，已保存结果可继续查看。", recovery_reason="executor_lost",
                    updated_at=utc_now(), finished_at=utc_now(), _control_status="interrupted",
                    stop_requested=False, pause_requested=False)
        clear_activity(task)
        persist_task(task)
    if not recovery:
        task["recovery_reason"] = "历史任务缺少完整页面位置，请保留结果并重新开始。"
    return task
