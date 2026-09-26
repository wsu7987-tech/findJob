from __future__ import annotations

import json
import logging
from collections import defaultdict


def _summary(scope_id, rows, *, prefetch=False):
    counts = dict(succeeded=0, failed=0, pending=0, running=0, cancelled=0)
    active = None
    for row in rows:
        status = str(row["status"])
        if prefetch and (row["detail_status"] == "completed" or status == "ready"):
            key = "succeeded"
        elif prefetch and status != "failed" and row["lifecycle_status"] in {"cancelled", "abandoned"}:
            key = "cancelled"
        elif status in {"running", "collecting"}:
            key = "running"
        elif status in {"skipped", "cancelled", "abandoned"}:
            key = "cancelled"
        elif status in {"succeeded", "failed"}:
            key = status
        else:
            key = "pending"
        counts[key] += 1
        if key == "running":
            active = {"id": row["job_id"], "title": row["title"] or ""}
    stage = "running" if counts["running"] else "pending" if counts["pending"] else "completed"
    return {"scope_id": scope_id, "unit": "job", "stage": stage, "total": len(rows),
            "processed": counts["succeeded"] + counts["failed"], **counts, "active_job": active}


def get_collection_progress(db, smart_capture_id, current_task, saved_progress):
    result = {"schema_version": 1, "list": None, "formal_jd": None, "prefetch": None}
    source = current_task or saved_progress
    scope_id = source.get("list_phase_id")
    if scope_id:
        result["list"] = {"scope_id": scope_id, "unit": "page", "stage": source.get("stage", ""),
                          "processed": int(source.get("list_phase_processed") or 0),
                          "total": source.get("list_planned_pages", source.get("pages")),
                          "succeeded": None, "failed": None, "pending": None, "running": None,
                          "cancelled": None, "active_job": None}
    with db.connect() as connection:
        rows = connection.execute("""SELECT t.*, json_extract(t.payload_json, '$.job_id') AS job_id, j.title
            FROM fj_workflow_tasks t LEFT JOIN fj_boss_jobs j ON j.id = json_extract(t.payload_json, '$.job_id')
            WHERE t.smart_capture_id = ? AND t.task_type = 'deep_job_search_jd'
            ORDER BY t.created_at, t.id""", (smart_capture_id,)).fetchall()
        groups = defaultdict(list)
        for row in rows:
            scope = json.loads(row["payload_json"]).get("jd_batch_id")
            if scope:
                groups[scope].append(row)
        active = [scope for scope, items in groups.items() if any(item["status"] in {"pending", "running"} for item in items)]
        if len(active) > 1:
            logging.getLogger(__name__).warning("Smart Capture %s 存在多个活动 JD 批次: %s", smart_capture_id, active)
        if groups:
            scope = active[-1] if active else list(groups)[-1]
            result["formal_jd"] = _summary(scope, groups[scope])
        batch = connection.execute("SELECT * FROM fj_workflow_prefetch_batches WHERE smart_capture_id = ? ORDER BY created_at DESC, id DESC LIMIT 1", (smart_capture_id,)).fetchone()
        if batch:
            items = connection.execute("SELECT i.*, j.title FROM fj_workflow_prefetch_items i LEFT JOIN fj_boss_jobs j ON j.id = i.job_id WHERE i.prefetch_batch_id = ?", (batch["id"],)).fetchall()
            result["prefetch"] = _summary(batch["id"], items, prefetch=True)
            result["prefetch"]["stage"] = batch["status"]
    return result


def advance_version(connection, smart_capture_id):
    from backend.app.utils import utc_now
    # 进度事实与版本同事务提交，终态的迟到回调不推进版本。
    connection.execute("UPDATE fj_smart_captures SET state_version = state_version + 1, updated_at = ? WHERE id = ? AND status NOT IN ('completed', 'stopped', 'failed')", (utc_now(), smart_capture_id))


def publish_progress(db, smart_capture_id):
    from backend.app.services.fine_job.smart_captures import publish_smart_capture_snapshot
    publish_smart_capture_snapshot(db, smart_capture_id)
