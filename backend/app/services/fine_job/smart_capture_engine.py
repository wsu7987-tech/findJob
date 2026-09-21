from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from backend.app.db import Database
from backend.app.errors import AppError
from backend.app.services.fine_job.adaptive_search_planner import (
    SearchWindowMetrics,
    build_metrics_for_window,
    canonicalize_platform_filters,
    combination_identity,
)
from backend.app.services.fine_job.boss_capture_history import (
    get_capture_history_job,
    update_capture_job_filter_result,
)
from backend.app.services.fine_job.boss_capture_tasks import boss_capture_task_manager
from backend.app.services.fine_job.filter_exclusions import apply_filter_exclusions
from backend.app.services.fine_job.job_evaluation import evaluate_filter_strategy
from backend.app.services.fine_job.strategies import get_filter_strategy
from backend.app.utils import new_id, utc_now


SEARCH_TASK_TYPE = "deep_job_search"
JD_TASK_TYPE = "deep_job_search_jd"
ANALYSIS_TASK_TYPE = "deep_job_search_analysis"


def prepare_search_execution(
    db: Database,
    smart_capture_id: str,
    payload: dict[str, object],
) -> dict[str, str]:
    """为 linked/independent 入口准备同一套组合和搜索任务身份。"""
    keywords = [str(value) for value in payload.get("allowed_search_keywords") or [] if str(value).strip()]
    cities = [str(value) for value in payload.get("allowed_cities") or [] if str(value).strip()]
    keyword = str(payload.get("keyword") or (keywords[0] if keywords else "")).strip()
    city = str(payload.get("city") or (cities[0] if cities else "")).strip()
    if not keyword or not city:
        raise AppError(422, "VALIDATION_FAILED", "岗位采集任务缺少搜索词或城市。")
    filters = canonicalize_platform_filters(payload.get("platform_filters") or payload.get("filters"))
    identity = combination_identity(keyword, city, filters)
    with db.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        capture = connection.execute(
            "SELECT workflow_run_id FROM fj_smart_captures WHERE id = ?",
            (smart_capture_id,),
        ).fetchone()
        if capture is None:
            raise AppError(404, "SMART_CAPTURE_NOT_FOUND", "岗位采集任务不存在。")
        workflow_run_id = str(capture["workflow_run_id"] or "") or None
        combination = connection.execute(
            """
            SELECT id FROM fj_workflow_search_combinations
            WHERE smart_capture_id = ? AND identity_json = ?
            """,
            (smart_capture_id, identity),
        ).fetchone()
        now = utc_now()
        if combination is None:
            combination_id = new_id()
            sequence = int(
                connection.execute(
                    """
                    SELECT COALESCE(MAX(sequence), 0)
                    FROM fj_workflow_search_combinations
                    WHERE smart_capture_id = ?
                    """,
                    (smart_capture_id,),
                ).fetchone()[0]
            ) + 1
            connection.execute(
                """
                INSERT INTO fj_workflow_search_combinations (
                  id, workflow_run_id, smart_capture_id, keyword, city,
                  platform_filters_json, identity_json, status, sequence,
                  transition_action, transition_reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, 'SWITCH_COMBINATION', 'engine')
                """,
                (
                    combination_id,
                    workflow_run_id,
                    smart_capture_id,
                    keyword,
                    city,
                    _dump(filters),
                    identity,
                    sequence,
                ),
            )
        else:
            combination_id = str(combination["id"])

        task = connection.execute(
            """
            SELECT id, payload_json, status
            FROM fj_workflow_tasks
            WHERE smart_capture_id = ? AND task_type = ?
              AND json_extract(payload_json, '$.search_combination_id') = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (smart_capture_id, SEARCH_TASK_TYPE, combination_id),
        ).fetchone()
        if task is None:
            task_id = new_id()
            task_payload = {
                "keyword": keyword,
                "city": city,
                "platform_filters": filters,
                "filter_strategy_id": str(payload.get("filter_strategy_id") or ""),
                "search_combination_id": combination_id,
                "is_baseline": sequence_is_first(connection, smart_capture_id, combination_id),
                "depth": 0,
                "low_yield_streak": 0,
                "low_novelty_streak": 0,
                "low_qualified_yield_streak": 0,
            }
            connection.execute(
                """
                INSERT INTO fj_workflow_tasks (
                  id, workflow_run_id, smart_capture_id, task_type, status,
                  payload_json, result_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'pending', ?, '{}', ?, ?)
                """,
                (
                    task_id,
                    workflow_run_id,
                    smart_capture_id,
                    SEARCH_TASK_TYPE,
                    _dump(task_payload),
                    now,
                    now,
                ),
            )
        else:
            task_id = str(task["id"])
            # linked 身份创建阶段可能还没有把筛选策略写入子任务，启动时补齐同一 owner 配置。
            task_payload = _load(task["payload_json"])
            task_payload.update(
                {
                    "keyword": keyword,
                    "city": city,
                    "platform_filters": filters,
                    "filter_strategy_id": str(
                        payload.get("filter_strategy_id")
                        or task_payload.get("filter_strategy_id")
                        or ""
                    ),
                    "search_combination_id": combination_id,
                }
            )
            connection.execute(
                "UPDATE fj_workflow_tasks SET payload_json = ?, updated_at = ? WHERE id = ?",
                (_dump(task_payload), now, task_id),
            )
    return {"smart_capture_id": smart_capture_id, "combination_id": combination_id, "task_id": task_id}


def sequence_is_first(connection: Any, smart_capture_id: str, combination_id: str) -> bool:
    row = connection.execute(
        "SELECT sequence FROM fj_workflow_search_combinations WHERE id = ? AND smart_capture_id = ?",
        (combination_id, smart_capture_id),
    ).fetchone()
    return row is not None and int(row["sequence"] or 0) == 1


def bind_capture_task(
    db: Database,
    *,
    smart_capture_id: str,
    task_id: str,
    batch_id: str,
    payload: dict[str, object],
) -> None:
    """把 BOSS batch 绑定到 owner-neutral 搜索任务，避免重复批次。"""
    now = utc_now()
    with db.connect() as connection:
        row = connection.execute(
            "SELECT payload_json, status FROM fj_workflow_tasks WHERE id = ? AND smart_capture_id = ? AND task_type = ?",
            (task_id, smart_capture_id, SEARCH_TASK_TYPE),
        ).fetchone()
        if row is None:
            raise AppError(404, "SMART_CAPTURE_SEARCH_TASK_MISSING", "Smart Capture 搜索任务不存在。")
        merged_payload = _load(row["payload_json"])
        merged_payload.update(payload)
        merged_payload.setdefault("search_combination_id", "")
        connection.execute(
            """
            UPDATE fj_workflow_tasks
            SET status = CASE WHEN status = 'succeeded' THEN status ELSE 'running' END,
                operation_ref_type = 'capture_task', operation_ref_id = ?, payload_json = ?,
                started_at = COALESCE(started_at, ?), updated_at = ?
            WHERE id = ? AND smart_capture_id = ? AND task_type = ?
            """,
            (
                batch_id,
                _dump(merged_payload),
                now,
                now,
                task_id,
                smart_capture_id,
                SEARCH_TASK_TYPE,
            ),
        )
        connection.execute(
            """
            UPDATE fj_workflow_search_combinations
            SET status = 'running', started_at = COALESCE(started_at, ?)
            WHERE id = (
              SELECT json_extract(payload_json, '$.search_combination_id')
              FROM fj_workflow_tasks WHERE id = ?
            ) AND smart_capture_id = ? AND status IN ('pending', 'running')
            """,
            (now, task_id, smart_capture_id),
        )


def process_completed_batch(
    db: Database,
    smart_capture_id: str,
    capture: dict[str, object],
    *,
    planner_policy: dict[str, object] | None = None,
    filter_strategy_id: str | None = None,
) -> dict[str, object]:
    """把完成的 BOSS batch 一次性写入组合、discovery 和候选池。"""
    batch_id = str(capture.get("id") or "")
    if not batch_id:
        raise AppError(422, "CAPTURE_BATCH_ID_REQUIRED", "BOSS 采集批次缺少标识。")
    task_info = _task_for_batch(db, smart_capture_id, batch_id)
    if task_info is None:
        task_info = _pending_task_for_capture(db, smart_capture_id)
    if task_info is None:
        task_info = prepare_search_execution(
            db,
            smart_capture_id,
            {
                "keyword": capture.get("keyword"),
                "city": capture.get("city"),
                "filters": capture.get("filters") or {},
            },
        )
    task_id = task_info["task_id"]
    with db.connect() as connection:
        task = connection.execute(
            "SELECT * FROM fj_workflow_tasks WHERE id = ? AND smart_capture_id = ?",
            (task_id, smart_capture_id),
        ).fetchone()
    if task is None:
        raise AppError(500, "SMART_CAPTURE_SEARCH_TASK_MISSING", "Smart Capture 搜索任务不存在。")
    task_payload = _load(task["payload_json"])
    if filter_strategy_id and not task_payload.get("filter_strategy_id"):
        task_payload["filter_strategy_id"] = filter_strategy_id
    jobs = [dict(job) for job in capture.get("jobs") or [] if isinstance(job, dict)]
    capture_pages = int(capture.get("total_pages_loaded") or 0)
    capture_job_count = len(jobs)
    previous_result = _load(task["result_json"])
    if previous_result.get("capture_task_id") == batch_id and task["status"] == "succeeded":
        # 续采会复用同一个 BOSS batch ID；只有页数或累计岗位数增长时才重新处理。
        previous_pages = int(previous_result.get("capture_pages") or 0)
        previous_job_count = int(previous_result.get("capture_job_count") or 0)
        if capture_pages <= previous_pages and capture_job_count <= previous_job_count:
            return dict(previous_result.get("metrics") or {})

    results = _evaluate_jobs(db, smart_capture_id, task_payload, jobs)
    result_by_id = {str(item.get("job_id") or ""): item for item in results}
    try:
        # 同步进程内快照，保留页面和后续详情流程对筛选结果的既有读取行为。
        boss_capture_task_manager.apply_filter_results(batch_id, results)
    except AppError:
        # 重启后只有持久化 batch 时，历史 discovery 仍可独立完成写入。
        pass
    strategy_id = str(task_payload.get("filter_strategy_id") or "")
    if strategy_id:
        _persist_filter_results(db, jobs, result_by_id)
    metrics = build_metrics_for_window(jobs, results)
    combination_id = str(task_payload.get("search_combination_id") or "")
    now = utc_now()
    with db.connect() as connection:
        for job in jobs:
            history_job_id = str(job.get("history_record_id") or "")
            source_job_id = str(job.get("job_id") or "")
            if not history_job_id or not source_job_id:
                continue
            filter_result = result_by_id.get(source_job_id, {})
            previous = connection.execute(
                """
                SELECT 1 FROM fj_workflow_job_discoveries
                WHERE smart_capture_id = ? AND job_id = ? LIMIT 1
                """,
                (smart_capture_id, history_job_id),
            ).fetchone()
            connection.execute(
                """
                INSERT OR IGNORE INTO fj_workflow_job_discoveries (
                  id, workflow_run_id, smart_capture_id, task_id, job_id,
                  search_keyword, city, search_combination_json, scroll_depth,
                  discovered_at, is_run_first_discovery, is_historical_duplicate,
                  is_filter_candidate
                ) VALUES (?, (SELECT workflow_run_id FROM fj_smart_captures WHERE id = ?), ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    new_id(),
                    smart_capture_id,
                    smart_capture_id,
                    task_id,
                    history_job_id,
                    str(task_payload.get("keyword") or capture.get("keyword") or ""),
                    str(task_payload.get("city") or capture.get("city") or ""),
                    _dump({
                        "search_combination_id": combination_id,
                        "platform_filters": task_payload.get("platform_filters") or {},
                        "filter_strategy_id": strategy_id,
                    }),
                    int(capture.get("total_pages_loaded") or 0),
                    now,
                    int(previous is None),
                    int(bool(job.get("is_previously_collected") or job.get("processing_state") == "duplicate")),
                    int(filter_result.get("status") in {"pass", "review"}),
                ),
            )
        _update_combination_metrics_in_connection(
            connection,
            combination_id,
            metrics,
            pages_seen=int(capture.get("total_pages_loaded") or 0),
            low_novelty_threshold=float((planner_policy or {}).get("low_novelty_threshold") or 0.25),
            low_qualified_yield_threshold=float((planner_policy or {}).get("low_qualified_yield_threshold") or 0.15),
        )
        task_payload["depth"] = int(capture.get("total_pages_loaded") or task_payload.get("depth") or 0)
        task_payload["low_novelty_streak"] = _latest_metric(
            connection, combination_id, "low_novelty_streak", metrics.low_novelty_streak
        )
        task_payload["low_qualified_yield_streak"] = _latest_metric(
            connection, combination_id, "low_qualified_yield_streak", metrics.low_qualified_yield_streak
        )
        metrics_dict = metrics.as_dict()
        metrics_dict["low_novelty_streak"] = task_payload["low_novelty_streak"]
        metrics_dict["low_qualified_yield_streak"] = task_payload["low_qualified_yield_streak"]
        if combination_id:
            connection.execute(
                "UPDATE fj_workflow_search_combinations SET status = 'completed', completed_at = COALESCE(completed_at, ?) WHERE id = ? AND smart_capture_id = ? AND status = 'running'",
                (now, combination_id, smart_capture_id),
            )
        connection.execute(
            """
            UPDATE fj_workflow_tasks
            SET status = 'succeeded', payload_json = ?, result_json = ?,
                completed_at = COALESCE(completed_at, ?), updated_at = ?
            WHERE id = ? AND smart_capture_id = ?
            """,
            (
                _dump(task_payload),
                _dump({
                    "new_jobs": metrics.run_fresh_jobs,
                    "duplicates": metrics.historical_duplicates,
                    "new_candidates": metrics.candidate_jobs,
                    "historical_duplicates": metrics.historical_duplicates,
                    "cooldown_excluded": metrics.cooldown_excluded,
                    "strategy_pass": metrics.strategy_pass,
                    "strategy_review": metrics.strategy_review,
                    "strategy_reject": metrics.strategy_reject,
                    "qualified_fresh_jobs": metrics.qualified_fresh_jobs,
                    "candidate_jobs": metrics.candidate_jobs,
                    "metrics": metrics_dict,
                    "capture_task_id": batch_id,
                    "capture_pages": capture_pages,
                    "capture_job_count": capture_job_count,
                }),
                now,
                now,
                task_id,
                smart_capture_id,
            ),
        )
    return metrics_dict


def count_candidates(db: Database, smart_capture_id: str) -> int:
    with db.connect() as connection:
        row = connection.execute(
            """
            SELECT COUNT(DISTINCT job_id) AS count
            FROM fj_workflow_job_discoveries
            WHERE smart_capture_id = ? AND is_run_first_discovery = 1
              AND is_historical_duplicate = 0 AND is_filter_candidate = 1
            """,
            (smart_capture_id,),
        ).fetchone()
    return int(row["count"] or 0)


def get_prefetch_summary(db: Database, smart_capture_id: str) -> dict[str, object]:
    """返回 Smart Capture owner 的最新 Prefetch 快照。"""
    with db.connect() as connection:
        batch = connection.execute(
            "SELECT * FROM fj_workflow_prefetch_batches WHERE smart_capture_id = ? ORDER BY created_at DESC, id DESC LIMIT 1",
            (smart_capture_id,),
        ).fetchone()
        if batch is None:
            return {
                "prefetch_batch_id": "",
                "source_analysis_batch_id": "",
                "status": "none",
                "target_count": 0,
                "pending_count": 0,
                "collecting_count": 0,
                "ready_count": 0,
                "failed_count": 0,
            }
        counts = connection.execute(
            """
            SELECT
              COALESCE(SUM(status = 'pending'), 0) AS pending_count,
              COALESCE(SUM(status = 'collecting'), 0) AS collecting_count,
              COALESCE(SUM(status = 'ready'), 0) AS ready_count,
              COALESCE(SUM(status = 'failed'), 0) AS failed_count
            FROM fj_workflow_prefetch_items WHERE prefetch_batch_id = ?
            """,
            (batch["id"],),
        ).fetchone()
    return {
        "prefetch_batch_id": str(batch["id"]),
        "source_analysis_batch_id": str(batch["source_analysis_batch_id"]),
        "status": str(batch["status"]),
        "target_count": int(batch["target_count"] or 0),
        "pending_count": int(counts["pending_count"] or 0),
        "collecting_count": int(counts["collecting_count"] or 0),
        "ready_count": int(counts["ready_count"] or 0),
        "failed_count": int(counts["failed_count"] or 0),
    }


def _capture_config(snapshot: dict[str, object]) -> dict[str, object]:
    value = snapshot.get("execution_config")
    return dict(value) if isinstance(value, dict) else {}


def _batch_size(config: dict[str, object]) -> int:
    policy = config.get("jd_detail_policy")
    if not isinstance(policy, dict):
        return 5
    return max(1, int(policy.get("batch_size") or 5))


def _workflow_link(snapshot: dict[str, object]) -> str | None:
    value = str(snapshot.get("workflow_run_id") or "")
    return value or None


def _select_available_candidates(
    connection: Any, smart_capture_id: str, limit: int
) -> list[Any]:
    return connection.execute(
        """
        SELECT d.job_id, j.detail_status
        FROM fj_workflow_job_discoveries d
        JOIN fj_boss_jobs j ON j.id = d.job_id
        WHERE d.smart_capture_id = ?
          AND d.is_run_first_discovery = 1
          AND d.is_historical_duplicate = 0
          AND d.is_filter_candidate = 1
          AND NOT EXISTS (
            SELECT 1 FROM fj_workflow_tasks t
            WHERE t.smart_capture_id = d.smart_capture_id
              AND t.task_type IN ('deep_job_search_jd', 'deep_job_search_analysis')
              AND json_extract(t.payload_json, '$.job_id') = d.job_id
          )
          AND NOT EXISTS (
            SELECT 1 FROM fj_workflow_candidate_reservations r
            WHERE r.job_id = d.job_id AND r.status = 'reserved'
          )
          AND NOT EXISTS (
            SELECT 1 FROM fj_workflow_prefetch_items pi
            JOIN fj_workflow_prefetch_batches pb ON pb.id = pi.prefetch_batch_id
            WHERE pi.smart_capture_id = d.smart_capture_id AND pi.job_id = d.job_id
              AND pb.status IN ('preparing', 'ready')
              AND pi.lifecycle_status IN ('preparing', 'ready')
          )
        ORDER BY d.discovered_at, d.job_id
        LIMIT ?
        """,
        (smart_capture_id, limit),
    ).fetchall()


def _ensure_formal_jd_batch(
    db: Database, smart_capture_id: str, snapshot: dict[str, object]
) -> int:
    """原子预留下一批正式 JD，防止与 Prefetch/其他 Smart Capture 重复。"""
    config = _capture_config(snapshot)
    workflow_run_id = _workflow_link(snapshot)
    now = utc_now()
    try:
        with db.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            candidates = _select_available_candidates(
                connection, smart_capture_id, _batch_size(config)
            )
            batch_id = new_id()
            for candidate in candidates:
                task_id = new_id()
                job_id = str(candidate["job_id"])
                connection.execute(
                    """
                    INSERT INTO fj_workflow_candidate_reservations (
                      id, workflow_run_id, smart_capture_id, job_id, owner_type,
                      owner_id, status, created_at
                    ) VALUES (?, ?, ?, ?, 'formal_jd', ?, 'reserved', ?)
                    """,
                    (new_id(), workflow_run_id, smart_capture_id, job_id, task_id, now),
                )
                connection.execute(
                    """
                    INSERT INTO fj_workflow_tasks (
                      id, workflow_run_id, smart_capture_id, task_type, status,
                      payload_json, result_json, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, 'pending', ?, '{}', ?, ?)
                    """,
                    (
                        task_id,
                        workflow_run_id,
                        smart_capture_id,
                        JD_TASK_TYPE,
                        _dump({"job_id": job_id, "jd_batch_id": batch_id}),
                        now,
                        now,
                    ),
                )
    except sqlite3.IntegrityError as exc:
        raise AppError(
            409,
            "CANDIDATE_RESERVATION_CONFLICT",
            "候选岗位已被其他活动 Smart Capture 占用。",
        ) from exc
    return len(candidates)


def _next_pipeline_unit(db: Database, smart_capture_id: str, unit_type: str) -> Any | None:
    table = "fj_workflow_tasks" if unit_type == "formal_jd" else "fj_workflow_prefetch_items"
    task_filter = "AND task_type = 'deep_job_search_jd'" if unit_type == "formal_jd" else ""
    with db.connect() as connection:
        return connection.execute(
            f"SELECT * FROM {table} WHERE smart_capture_id = ? {task_filter} AND status = 'pending' ORDER BY created_at, id LIMIT 1",
            (smart_capture_id,),
        ).fetchone()


def _start_pipeline_detail(
    db: Database,
    smart_capture_id: str,
    unit_type: str,
    unit: Any,
    output_dir: Path,
) -> None:
    job_id = str(
        _load(unit["payload_json"]).get("job_id")
        if unit_type == "formal_jd"
        else unit["job_id"]
    )
    job = get_capture_history_job(db, job_id)
    if str(job.get("detail_status") or "") == "completed":
        _finish_pipeline_detail(db, smart_capture_id, unit_type, unit, succeeded=True)
        _advance_pipeline_details(db, smart_capture_id, unit_type, output_dir)
        return
    now = utc_now()
    with db.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        table = "fj_workflow_tasks" if unit_type == "formal_jd" else "fj_workflow_prefetch_items"
        next_status = "running" if unit_type == "formal_jd" else "collecting"
        changed = connection.execute(
            f"UPDATE {table} SET status = ? WHERE id = ? AND smart_capture_id = ? AND status = 'pending'",
            (next_status, unit["id"], smart_capture_id),
        )
        if changed.rowcount != 1:
            return
        if unit_type == "formal_jd":
            connection.execute(
                "UPDATE fj_workflow_tasks SET started_at = COALESCE(started_at, ?), updated_at = ? WHERE id = ?",
                (now, now, unit["id"]),
            )
        else:
            connection.execute(
                "UPDATE fj_workflow_prefetch_items SET lifecycle_status = 'preparing', detail_status = 'queued', started_at = COALESCE(started_at, ?), updated_at = ? WHERE id = ?",
                (now, now, unit["id"]),
            )
    try:
        detail_task = boss_capture_task_manager.start_history_detail(
            job,
            output_dir=output_dir,
            db=db,
            capture_source="smart",
            workflow_run_id=_workflow_run_id(db, smart_capture_id),
            smart_capture_id=smart_capture_id,
            pipeline_unit_type=unit_type,
            pipeline_unit_id=str(unit["id"]),
        )
        table = "fj_workflow_tasks" if unit_type == "formal_jd" else "fj_workflow_prefetch_items"
        with db.connect() as connection:
            connection.execute(
                f"UPDATE {table} SET operation_ref_type = 'capture_task', operation_ref_id = ?, updated_at = ? WHERE id = ?",
                (str(detail_task["id"]), utc_now(), unit["id"]),
            )
        if unit_type == "prefetch":
            _touch_pipeline_snapshot(db, smart_capture_id)
    except Exception as exc:
        _finish_pipeline_detail(
            db, smart_capture_id, unit_type, unit, succeeded=False, error_message=str(exc)
        )
        _advance_pipeline_details(db, smart_capture_id, unit_type, output_dir)


def _workflow_run_id(db: Database, smart_capture_id: str) -> str | None:
    with db.connect() as connection:
        row = connection.execute(
            "SELECT workflow_run_id FROM fj_smart_captures WHERE id = ?",
            (smart_capture_id,),
        ).fetchone()
    value = str(row["workflow_run_id"] or "") if row is not None else ""
    return value or None


def _finish_pipeline_detail(
    db: Database,
    smart_capture_id: str,
    unit_type: str,
    unit: Any,
    *,
    succeeded: bool,
    error_message: str = "",
) -> None:
    now = utc_now()
    job_id = str(
        _load(unit["payload_json"]).get("job_id")
        if unit_type == "formal_jd"
        else unit["job_id"]
    )
    with db.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        if unit_type == "formal_jd":
            connection.execute(
                """
                UPDATE fj_workflow_tasks
                SET status = ?, result_json = ?, completed_at = ?, updated_at = ?
                WHERE id = ? AND smart_capture_id = ? AND status IN ('pending', 'running')
                """,
                (
                    "succeeded" if succeeded else "failed",
                    _dump({"job_id": job_id, "error": error_message}),
                    now,
                    now,
                    unit["id"],
                    smart_capture_id,
                ),
            )
        else:
            connection.execute(
                """
                UPDATE fj_workflow_prefetch_items
                SET status = ?, lifecycle_status = ?, detail_status = ?, error_message = ?,
                    completed_at = ?, updated_at = ?
                WHERE id = ? AND smart_capture_id = ? AND status IN ('pending', 'collecting', 'running')
                """,
                (
                    "ready" if succeeded else "failed",
                    "ready" if succeeded else "abandoned",
                    "completed" if succeeded else "failed",
                    error_message,
                    now,
                    now,
                    unit["id"],
                    smart_capture_id,
                ),
            )
        if not succeeded or unit_type == "formal_jd":
            connection.execute(
                """
                UPDATE fj_workflow_candidate_reservations
                SET status = ?, released_at = ?, terminal_at = ?
                WHERE smart_capture_id = ? AND owner_type = ? AND owner_id = ?
                  AND job_id = ? AND status = 'reserved'
                """,
                (
                    "released" if succeeded else "failed",
                    now,
                    now,
                    smart_capture_id,
                    unit_type,
                    unit["id"],
                    job_id,
                ),
            )
    if unit_type == "prefetch":
        _touch_pipeline_snapshot(db, smart_capture_id)


def _advance_pipeline_details(
    db: Database, smart_capture_id: str, unit_type: str, output_dir: Path
) -> None:
    unit = _next_pipeline_unit(db, smart_capture_id, unit_type)
    if unit is not None:
        snapshot = _smart_capture_snapshot(db, smart_capture_id)
        if str(snapshot["status"]) in {
            "pausing",
            "paused",
            "interrupted",
            "stopped",
            "failed",
            "completed",
        }:
            return
        _start_pipeline_detail(db, smart_capture_id, unit_type, unit, output_dir)
        return
    with db.connect() as connection:
        table = "fj_workflow_tasks" if unit_type == "formal_jd" else "fj_workflow_prefetch_items"
        task_filter = "AND task_type = 'deep_job_search_jd'" if unit_type == "formal_jd" else ""
        active = connection.execute(
            f"SELECT 1 FROM {table} WHERE smart_capture_id = ? {task_filter} AND status IN ('pending', 'running', 'collecting') LIMIT 1",
            (smart_capture_id,),
        ).fetchone()
    if active is not None:
        return
    if unit_type == "formal_jd":
        _create_analysis_from_formal_jd(db, smart_capture_id)
    else:
        _finalize_prefetch_batch(db, smart_capture_id)


def active_pipeline_operation_id(db: Database, smart_capture_id: str) -> str:
    """返回当前正在安全执行的 JD/Prefetch 详情单元。"""
    with db.connect() as connection:
        task = connection.execute(
            """
            SELECT operation_ref_id FROM fj_workflow_tasks
            WHERE smart_capture_id = ? AND task_type = 'deep_job_search_jd'
              AND status = 'running' AND operation_ref_id IS NOT NULL
            ORDER BY started_at, created_at LIMIT 1
            """,
            (smart_capture_id,),
        ).fetchone()
        if task is None:
            task = connection.execute(
                """
                SELECT operation_ref_id FROM fj_workflow_prefetch_items
                WHERE smart_capture_id = ? AND status = 'collecting'
                  AND operation_ref_id IS NOT NULL
                ORDER BY started_at, created_at LIMIT 1
                """,
                (smart_capture_id,),
            ).fetchone()
    return str(task["operation_ref_id"] or "") if task is not None else ""


def resume_pipeline(
    db: Database, smart_capture_id: str, output_dir: Path
) -> bool:
    """重启后将失去进程的详情单元收敛回 pending 并恢复。"""
    with db.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        formal = connection.execute(
            "SELECT 1 FROM fj_workflow_tasks WHERE smart_capture_id = ? AND task_type = 'deep_job_search_jd' AND status IN ('pending', 'running') LIMIT 1",
            (smart_capture_id,),
        ).fetchone()
        prefetch = connection.execute(
            "SELECT 1 FROM fj_workflow_prefetch_items WHERE smart_capture_id = ? AND status IN ('pending', 'collecting') LIMIT 1",
            (smart_capture_id,),
        ).fetchone()
        connection.execute(
            "UPDATE fj_workflow_tasks SET status = 'pending', operation_ref_type = NULL, operation_ref_id = NULL, updated_at = ? WHERE smart_capture_id = ? AND task_type = 'deep_job_search_jd' AND status = 'running'",
            (utc_now(), smart_capture_id),
        )
        connection.execute(
            "UPDATE fj_workflow_prefetch_items SET status = 'pending', operation_ref_type = NULL, operation_ref_id = NULL, updated_at = ? WHERE smart_capture_id = ? AND status = 'collecting'",
            (utc_now(), smart_capture_id),
        )
    unit_type = "formal_jd" if formal is not None else "prefetch" if prefetch is not None else ""
    if not unit_type:
        return False
    _set_capture_state(
        db,
        smart_capture_id,
        status="running",
        stage="collecting_jd" if unit_type == "formal_jd" else "analyzing_prefetch",
        waiting_reason="",
        message="正在恢复 Smart Capture 详情单元。",
    )
    _advance_pipeline_details(db, smart_capture_id, unit_type, output_dir)
    return True


def process_detail_task_update(
    db: Database, smart_capture_id: str, task: dict[str, object]
) -> None:
    """由详情任务事件接续 JD/Prefetch，不依赖前端轮询推进。"""
    status = str(task.get("status") or "")
    if status in {"queued", "running"}:
        return
    unit_type = str(task.get("pipeline_unit_type") or "")
    unit_id = str(task.get("pipeline_unit_id") or "")
    if unit_type not in {"formal_jd", "prefetch"} or not unit_id:
        return
    table = "fj_workflow_tasks" if unit_type == "formal_jd" else "fj_workflow_prefetch_items"
    with db.connect() as connection:
        unit = connection.execute(
            f"SELECT * FROM {table} WHERE id = ? AND smart_capture_id = ?",
            (unit_id, smart_capture_id),
        ).fetchone()
    if unit is None or str(unit["status"]) not in {"pending", "running", "collecting"}:
        return
    job_id = str(
        _load(unit["payload_json"]).get("job_id")
        if unit_type == "formal_jd"
        else unit["job_id"]
    )
    succeeded = False
    if status == "completed":
        try:
            succeeded = str(get_capture_history_job(db, job_id).get("detail_status") or "") == "completed"
        except AppError:
            succeeded = False
    _finish_pipeline_detail(
        db,
        smart_capture_id,
        unit_type,
        unit,
        succeeded=succeeded,
        error_message="" if succeeded else str(task.get("error_message") or "JD 采集未返回完整详情。"),
    )
    output_dir = task.get("_output_dir")
    if isinstance(output_dir, Path):
        _advance_pipeline_details(db, smart_capture_id, unit_type, output_dir)


def _create_analysis_from_formal_jd(db: Database, smart_capture_id: str) -> None:
    now = utc_now()
    batch_id = new_id()
    with db.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        rows = connection.execute(
            """
            SELECT * FROM fj_workflow_tasks jd
            WHERE jd.smart_capture_id = ? AND jd.task_type = 'deep_job_search_jd'
              AND jd.status = 'succeeded'
              AND NOT EXISTS (
                SELECT 1 FROM fj_workflow_tasks analysis
                WHERE analysis.smart_capture_id = jd.smart_capture_id
                  AND analysis.task_type = 'deep_job_search_analysis'
                  AND json_extract(analysis.payload_json, '$.jd_task_id') = jd.id
              )
            ORDER BY jd.created_at, jd.id
            """,
            (smart_capture_id,),
        ).fetchall()
        workflow_run_id = _workflow_run_id_in_connection(connection, smart_capture_id)
        for row in rows:
            job_id = str(_load(row["payload_json"]).get("job_id") or "")
            task_id = new_id()
            connection.execute(
                """
                INSERT INTO fj_workflow_tasks (
                  id, workflow_run_id, smart_capture_id, task_type, status,
                  payload_json, result_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'pending', ?, '{}', ?, ?)
                """,
                (
                    task_id,
                    workflow_run_id,
                    smart_capture_id,
                    ANALYSIS_TASK_TYPE,
                    _dump({"job_id": job_id, "jd_task_id": str(row["id"]), "analysis_batch_id": batch_id}),
                    now,
                    now,
                ),
            )
            connection.execute(
                """
                INSERT INTO fj_workflow_candidate_reservations (
                  id, workflow_run_id, smart_capture_id, job_id, owner_type,
                  owner_id, status, created_at
                ) VALUES (?, ?, ?, ?, 'formal_analysis', ?, 'reserved', ?)
                """,
                (new_id(), workflow_run_id, smart_capture_id, job_id, task_id, now),
            )
    if rows:
        _set_capture_state(
            db,
            smart_capture_id,
            status="running",
            stage="waiting_codex",
            waiting_reason="codex",
            message="本批 JD 已就绪，等待 Codex 开始分析。",
        )


def _workflow_run_id_in_connection(connection: Any, smart_capture_id: str) -> str | None:
    row = connection.execute(
        "SELECT workflow_run_id FROM fj_smart_captures WHERE id = ?", (smart_capture_id,)
    ).fetchone()
    value = str(row["workflow_run_id"] or "") if row is not None else ""
    return value or None


def _set_capture_state(
    db: Database,
    smart_capture_id: str,
    *,
    status: str,
    stage: str,
    waiting_reason: str,
    message: str,
    result_summary: dict[str, object] | None = None,
    completed: bool = False,
) -> None:
    from backend.app.services.fine_job import smart_captures

    smart_captures._update_capture(
        db,
        smart_capture_id,
        status=status,
        stage=stage,
        waiting_reason=waiting_reason,
        control_cause="",
        message=message,
        result_summary=result_summary,
        completed=completed,
    )


def _touch_pipeline_snapshot(db: Database, smart_capture_id: str) -> None:
    from backend.app.services.fine_job import smart_captures

    smart_captures.touch_pipeline_snapshot(db, smart_capture_id)


def start_prefetch_after_handoff(
    db: Database,
    smart_capture_id: str,
    source_analysis_batch_id: str,
    output_dir: Path,
) -> None:
    """Codex started ACK 后立即启动 N+1 Prefetch，与 Analysis N 并行。"""
    snapshot = _smart_capture_snapshot(db, smart_capture_id)
    if str(snapshot["status"]) in {"completed", "stopped", "failed"}:
        return
    batch_id = _ensure_prefetch_batch(
        db, smart_capture_id, source_analysis_batch_id, snapshot
    )
    if not batch_id:
        return
    summary = get_prefetch_summary(db, smart_capture_id)
    if str(summary["status"]) in {"preparing", "ready"}:
        _set_capture_state(
            db,
            smart_capture_id,
            status="running",
            stage="analyzing_prefetch",
            waiting_reason="codex",
            message="Codex 正在分析当前批，下一批 JD 已并行准备。",
        )
    if str(summary["status"]) == "preparing":
        _advance_pipeline_details(db, smart_capture_id, "prefetch", output_dir)


def _smart_capture_snapshot(db: Database, smart_capture_id: str) -> dict[str, object]:
    from backend.app.services.fine_job import smart_captures

    return smart_captures.get_smart_capture(db, smart_capture_id)


def _ensure_prefetch_batch(
    db: Database,
    smart_capture_id: str,
    source_analysis_batch_id: str,
    snapshot: dict[str, object],
) -> str | None:
    config = _capture_config(snapshot)
    target_count = _batch_size(config)
    workflow_run_id = _workflow_link(snapshot)
    now = utc_now()
    try:
        with db.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT id FROM fj_workflow_prefetch_batches WHERE smart_capture_id = ? AND source_analysis_batch_id = ?",
                (smart_capture_id, source_analysis_batch_id),
            ).fetchone()
            if existing is not None:
                return str(existing["id"])
            candidates = _select_available_candidates(
                connection, smart_capture_id, target_count
            )
            if not candidates:
                return None
            batch_id = new_id()
            connection.execute(
                """
                INSERT INTO fj_workflow_prefetch_batches (
                  id, workflow_run_id, smart_capture_id, source_analysis_batch_id,
                  target_count, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'preparing', ?, ?)
                """,
                (
                    batch_id,
                    workflow_run_id,
                    smart_capture_id,
                    source_analysis_batch_id,
                    target_count,
                    now,
                    now,
                ),
            )
            for candidate in candidates:
                item_id = new_id()
                job_id = str(candidate["job_id"])
                ready = str(candidate["detail_status"] or "") == "completed"
                connection.execute(
                    """
                    INSERT INTO fj_workflow_prefetch_items (
                      id, workflow_run_id, smart_capture_id, prefetch_batch_id,
                      job_id, status, lifecycle_status, detail_status,
                      completed_at, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        item_id,
                        workflow_run_id,
                        smart_capture_id,
                        batch_id,
                        job_id,
                        "ready" if ready else "pending",
                        "ready" if ready else "preparing",
                        "completed" if ready else str(candidate["detail_status"] or "not_collected"),
                        now if ready else None,
                        now,
                        now,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO fj_workflow_candidate_reservations (
                      id, workflow_run_id, smart_capture_id, job_id, owner_type,
                      owner_id, status, created_at
                    ) VALUES (?, ?, ?, ?, 'prefetch', ?, 'reserved', ?)
                    """,
                    (new_id(), workflow_run_id, smart_capture_id, job_id, item_id, now),
                )
            if all(str(candidate["detail_status"] or "") == "completed" for candidate in candidates):
                connection.execute(
                    "UPDATE fj_workflow_prefetch_batches SET status = 'ready', started_at = ?, completed_at = ?, updated_at = ? WHERE id = ?",
                    (now, now, now, batch_id),
                )
    except sqlite3.IntegrityError as exc:
        raise AppError(
            409,
            "CANDIDATE_RESERVATION_CONFLICT",
            "Prefetch 候选岗位已被其他活动 Smart Capture 占用。",
        ) from exc
    return batch_id


def _finalize_prefetch_batch(db: Database, smart_capture_id: str) -> None:
    now = utc_now()
    source_analysis_batch_id = ""
    with db.connect() as connection:
        batch = connection.execute(
            "SELECT * FROM fj_workflow_prefetch_batches WHERE smart_capture_id = ? AND status = 'preparing' ORDER BY created_at DESC, id DESC LIMIT 1",
            (smart_capture_id,),
        ).fetchone()
        if batch is None:
            return
        source_analysis_batch_id = str(batch["source_analysis_batch_id"])
        counts = connection.execute(
            """
            SELECT COALESCE(SUM(status = 'ready'), 0) AS ready_count,
                   COALESCE(SUM(status IN ('pending', 'running', 'collecting')), 0) AS active_count
            FROM fj_workflow_prefetch_items WHERE prefetch_batch_id = ?
            """,
            (batch["id"],),
        ).fetchone()
        if int(counts["active_count"] or 0):
            return
        connection.execute(
            """
            UPDATE fj_workflow_prefetch_batches
            SET status = ?, completed_at = ?, updated_at = ? WHERE id = ?
            """,
            ("ready" if int(counts["ready_count"] or 0) else "failed", now, now, batch["id"]),
        )
    _touch_pipeline_snapshot(db, smart_capture_id)
    with db.connect() as connection:
        unfinished_analysis = connection.execute(
            """
            SELECT 1 FROM fj_workflow_tasks
            WHERE smart_capture_id = ? AND task_type = 'deep_job_search_analysis'
              AND json_extract(payload_json, '$.analysis_batch_id') = ?
              AND status IN ('pending', 'running')
            LIMIT 1
            """,
            (smart_capture_id, source_analysis_batch_id),
        ).fetchone()
    # Analysis 先于 Prefetch 完成时，由最后一个详情事件完成交接。
    if unfinished_analysis is None and _promote_ready_prefetch(db, smart_capture_id) == "promoted":
        _set_capture_state(
            db,
            smart_capture_id,
            status="running",
            stage="waiting_codex",
            waiting_reason="codex",
            message="Prefetch 已收敛并提升为下一正式 Analysis Batch。",
        )


def analysis_item_saved(
    db: Database, smart_capture_id: str, analysis_batch_id: str
) -> None:
    """消费 Analysis N 结果：完成目标或原子提升已 ready 的 N+1。"""
    snapshot = _smart_capture_snapshot(db, smart_capture_id)
    if str(snapshot["status"]) in {"completed", "stopped", "failed"}:
        # OFF completed 后处理只保存 artifact，不重启 Engine。
        return
    with db.connect() as connection:
        rows = connection.execute(
            "SELECT status FROM fj_workflow_tasks WHERE smart_capture_id = ? AND task_type = ? AND json_extract(payload_json, '$.analysis_batch_id') = ?",
            (smart_capture_id, ANALYSIS_TASK_TYPE, analysis_batch_id),
        ).fetchall()
    if not rows or any(str(row["status"]) in {"pending", "running"} for row in rows):
        return
    now = utc_now()
    with db.connect() as connection:
        connection.execute(
            """
            UPDATE fj_workflow_analysis_handoffs
            SET status = 'completed', attempt_status = 'completed', completed_at = COALESCE(completed_at, ?)
            WHERE smart_capture_id = ? AND analysis_batch_id = ?
              AND attempt_status IN ('claimed', 'prompt_written', 'started')
            """,
            (now, smart_capture_id, analysis_batch_id),
        )
    config = _capture_config(snapshot)
    if _delivery_target_reached(db, smart_capture_id, config):
        _abandon_prefetch(db, smart_capture_id)
        counts = _analysis_decision_counts(db, smart_capture_id)
        _set_capture_state(
            db,
            smart_capture_id,
            status="completed",
            stage="completed",
            waiting_reason="",
            message="已达到建议投递目标。",
            result_summary={**counts, "completion_reason": "delivery_target_reached"},
            completed=True,
        )
        return
    promotion = _promote_ready_prefetch(db, smart_capture_id)
    if promotion == "promoted":
        _set_capture_state(
            db,
            smart_capture_id,
            status="running",
            stage="waiting_codex",
            waiting_reason="codex",
            message="已将 ready Prefetch 提升为下一正式 Analysis Batch。",
        )
    elif promotion == "waiting":
        _set_capture_state(
            db,
            smart_capture_id,
            status="waiting_next_batch",
            stage="waiting_prefetch",
            waiting_reason="next_batch",
            message="当前分析已完成，等待并行 Prefetch 收敛。",
        )
    else:
        _set_capture_state(
            db,
            smart_capture_id,
            status="waiting_next_batch",
            stage="candidate_ready",
            waiting_reason="next_batch",
            message="当前分析已完成，等待后续候选批次。",
        )


def _analysis_decision_counts(db: Database, smart_capture_id: str) -> dict[str, int]:
    counts = {"recommend_count": 0, "review_count": 0, "reject_count": 0}
    with db.connect() as connection:
        rows = connection.execute(
            "SELECT result_json FROM fj_workflow_tasks WHERE smart_capture_id = ? AND task_type = ? AND status = 'succeeded'",
            (smart_capture_id, ANALYSIS_TASK_TYPE),
        ).fetchall()
    jobs: dict[str, set[str]] = {"recommend": set(), "review": set(), "reject": set()}
    for row in rows:
        result = _load(row["result_json"])
        decision = str(result.get("decision") or "")
        job_id = str(result.get("job_id") or "")
        if decision in jobs and job_id:
            jobs[decision].add(job_id)
    for decision in jobs:
        counts[f"{decision}_count"] = len(jobs[decision])
    return counts


def _delivery_target_reached(
    db: Database, smart_capture_id: str, config: dict[str, object]
) -> bool:
    target = config.get("delivery_target")
    if not isinstance(target, dict) or not bool(target.get("enabled")):
        return False
    counts = _analysis_decision_counts(db, smart_capture_id)
    configured: list[bool] = []
    recommend_target = int(target.get("recommend_target") or 0)
    if recommend_target > 0:
        configured.append(counts["recommend_count"] >= recommend_target)
    review_target = target.get("review_target")
    if review_target is not None:
        configured.append(counts["review_count"] >= int(review_target))
    if not configured:
        return False
    return any(configured) if str(target.get("target_mode") or "all") == "any" else all(configured)


def _promote_ready_prefetch(db: Database, smart_capture_id: str) -> str:
    now = utc_now()
    analysis_batch_id = new_id()
    with db.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        batch = connection.execute(
            "SELECT * FROM fj_workflow_prefetch_batches WHERE smart_capture_id = ? AND status IN ('preparing', 'ready') ORDER BY created_at DESC, id DESC LIMIT 1",
            (smart_capture_id,),
        ).fetchone()
        if batch is None:
            return "none"
        active = connection.execute(
            "SELECT 1 FROM fj_workflow_prefetch_items WHERE prefetch_batch_id = ? AND status IN ('pending', 'running', 'collecting') LIMIT 1",
            (batch["id"],),
        ).fetchone()
        if active is not None:
            return "waiting"
        items = connection.execute(
            "SELECT * FROM fj_workflow_prefetch_items WHERE prefetch_batch_id = ? AND status = 'ready' ORDER BY created_at, id",
            (batch["id"],),
        ).fetchall()
        if not items:
            return "none"
        workflow_run_id = _workflow_run_id_in_connection(connection, smart_capture_id)
        for item in items:
            reservation = connection.execute(
                "SELECT id FROM fj_workflow_candidate_reservations WHERE smart_capture_id = ? AND owner_type = 'prefetch' AND owner_id = ? AND status = 'reserved'",
                (smart_capture_id, item["id"]),
            ).fetchone()
            if reservation is None:
                return "none"
        for item in items:
            task_id = new_id()
            connection.execute(
                """
                INSERT INTO fj_workflow_tasks (
                  id, workflow_run_id, smart_capture_id, task_type, status,
                  payload_json, result_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'pending', ?, '{}', ?, ?)
                """,
                (
                    task_id,
                    workflow_run_id,
                    smart_capture_id,
                    ANALYSIS_TASK_TYPE,
                    _dump({
                        "job_id": str(item["job_id"]),
                        "prefetch_item_id": str(item["id"]),
                        "analysis_batch_id": analysis_batch_id,
                    }),
                    now,
                    now,
                ),
            )
            connection.execute(
                "UPDATE fj_workflow_prefetch_items SET lifecycle_status = 'promoted', promoted_at = ?, updated_at = ? WHERE id = ?",
                (now, now, item["id"]),
            )
            connection.execute(
                "UPDATE fj_workflow_candidate_reservations SET status = 'promoted', released_at = ?, terminal_at = ? WHERE smart_capture_id = ? AND owner_type = 'prefetch' AND owner_id = ? AND status = 'reserved'",
                (now, now, smart_capture_id, item["id"]),
            )
            connection.execute(
                """
                INSERT INTO fj_workflow_candidate_reservations (
                  id, workflow_run_id, smart_capture_id, job_id, owner_type,
                  owner_id, status, created_at
                ) VALUES (?, ?, ?, ?, 'formal_analysis', ?, 'reserved', ?)
                """,
                (new_id(), workflow_run_id, smart_capture_id, item["job_id"], task_id, now),
            )
        connection.execute(
            "UPDATE fj_workflow_prefetch_batches SET status = 'promoted', promoted_at = ?, completed_at = COALESCE(completed_at, ?), updated_at = ? WHERE id = ?",
            (now, now, now, batch["id"]),
        )
    return "promoted"


def _abandon_prefetch(db: Database, smart_capture_id: str) -> None:
    now = utc_now()
    with db.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        batches = connection.execute(
            "SELECT id FROM fj_workflow_prefetch_batches WHERE smart_capture_id = ? AND status NOT IN ('promoted', 'abandoned', 'cancelled')",
            (smart_capture_id,),
        ).fetchall()
        for batch in batches:
            connection.execute(
                "UPDATE fj_workflow_prefetch_items SET lifecycle_status = 'abandoned', abandoned_at = ?, updated_at = ? WHERE prefetch_batch_id = ? AND lifecycle_status <> 'promoted'",
                (now, now, batch["id"]),
            )
            connection.execute(
                """
                UPDATE fj_workflow_candidate_reservations
                SET status = 'abandoned', released_at = ?, terminal_at = ?
                WHERE smart_capture_id = ? AND owner_type = 'prefetch' AND status = 'reserved'
                  AND owner_id IN (SELECT id FROM fj_workflow_prefetch_items WHERE prefetch_batch_id = ?)
                """,
                (now, now, smart_capture_id, batch["id"]),
            )
            connection.execute(
                "UPDATE fj_workflow_prefetch_batches SET status = 'abandoned', abandoned_at = ?, updated_at = ? WHERE id = ?",
                (now, now, batch["id"]),
            )


def advance_completed_batch(
    db: Database, smart_capture_id: str, capture_task: dict[str, object]
) -> None:
    """由 Smart Capture Engine 消费 BOSS 完成，不经 Workflow 推进内部 Pipeline。"""
    # 延迟导入避免 Engine 与 lifecycle service 的模块初始化循环。
    from backend.app.services.fine_job import smart_captures

    snapshot = smart_captures.get_smart_capture(db, smart_capture_id)
    if str(snapshot["status"]) in {"completed", "stopped", "failed"}:
        return
    config = snapshot.get("execution_config")
    config = config if isinstance(config, dict) else {}
    delivery_target = config.get("delivery_target")
    delivery_enabled = bool(delivery_target.get("enabled")) if isinstance(delivery_target, dict) else False
    candidate_count = count_candidates(db, smart_capture_id)
    target_count = int(snapshot.get("target_count") or 0)
    exhausted = not bool(capture_task.get("has_more"))
    # OFF 模式仅以候选目标为完成条件；搜索耗尽仍保留为可恢复的系统等待。
    if not delivery_enabled and target_count > 0 and candidate_count >= target_count:
        smart_captures._update_capture(
            db,
            smart_capture_id,
            status="completed",
            stage="completed",
            waiting_reason="",
            control_cause="",
            message="岗位采集已达到完成条件。",
            result_summary={
                "candidate_count": candidate_count,
                "last_batch_id": str(capture_task.get("id") or ""),
                "completion_reason": "candidate_target_reached",
            },
            completed=True,
        )
        return
    result_summary = {
        "candidate_count": candidate_count,
        "last_batch_id": str(capture_task.get("id") or ""),
        "search_exhausted": exhausted,
    }
    output_dir = capture_task.get("_output_dir")
    created = _ensure_formal_jd_batch(db, smart_capture_id, snapshot)
    if created and isinstance(output_dir, Path):
        smart_captures._update_capture(
            db,
            smart_capture_id,
            status="running",
            stage="collecting_jd",
            waiting_reason="",
            control_cause="",
            message="候选池已更新，正在准备第一批正式 JD。",
            result_summary=result_summary,
        )
        _advance_pipeline_details(db, smart_capture_id, "formal_jd", output_dir)
        return
    # 暂无可用候选时保留系统等待态，不将 ON 任务提前 completed。
    smart_captures._update_capture(
        db,
        smart_capture_id,
        status="waiting_next_batch",
        stage="candidate_ready",
        waiting_reason="next_batch",
        control_cause="",
        message="候选池已更新，等待可用的详情与分析批次。",
        result_summary=result_summary,
    )


def list_search_combinations(db: Database, smart_capture_id: str) -> list[dict[str, object]]:
    with db.connect() as connection:
        rows = connection.execute(
            "SELECT * FROM fj_workflow_search_combinations WHERE smart_capture_id = ? ORDER BY sequence, id",
            (smart_capture_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def list_candidate_pool(db: Database, smart_capture_id: str) -> list[dict[str, object]]:
    with db.connect() as connection:
        rows = connection.execute(
            """
            SELECT j.*, MIN(d.discovered_at) AS candidate_discovered_at
            FROM fj_workflow_job_discoveries d
            JOIN fj_boss_jobs j ON j.id = d.job_id
            WHERE d.smart_capture_id = ? AND d.is_run_first_discovery = 1
              AND d.is_historical_duplicate = 0 AND d.is_filter_candidate = 1
            GROUP BY j.id
            ORDER BY candidate_discovered_at, j.id
            """,
            (smart_capture_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def _task_for_batch(db: Database, smart_capture_id: str, batch_id: str) -> dict[str, str] | None:
    with db.connect() as connection:
        row = connection.execute(
            """
            SELECT id, json_extract(payload_json, '$.search_combination_id') AS combination_id
            FROM fj_workflow_tasks
            WHERE smart_capture_id = ? AND operation_ref_type = 'capture_task'
              AND operation_ref_id = ? AND task_type = ?
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            (smart_capture_id, batch_id, SEARCH_TASK_TYPE),
        ).fetchone()
    if row is None:
        return None
    return {"task_id": str(row["id"]), "combination_id": str(row["combination_id"] or "")}


def _pending_task_for_capture(db: Database, smart_capture_id: str) -> dict[str, str] | None:
    """兼容旧回调未绑定 operation_ref 时的当前搜索任务。"""
    with db.connect() as connection:
        row = connection.execute(
            """
            SELECT id, json_extract(payload_json, '$.search_combination_id') AS combination_id
            FROM fj_workflow_tasks
            WHERE smart_capture_id = ? AND task_type = ?
              AND status IN ('pending', 'running')
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (smart_capture_id, SEARCH_TASK_TYPE),
        ).fetchone()
    if row is None:
        return None
    return {"task_id": str(row["id"]), "combination_id": str(row["combination_id"] or "")}


def _evaluate_jobs(
    db: Database,
    smart_capture_id: str,
    payload: dict[str, object],
    jobs: list[dict[str, object]],
) -> list[dict[str, object]]:
    strategy_id = str(payload.get("filter_strategy_id") or "")
    if not strategy_id:
        return [
            {
                "job_id": str(job.get("job_id") or ""),
                "status": "review" if not job.get("is_blacklisted") else "reject",
                "final_filter_status": "review" if not job.get("is_blacklisted") else "reject",
                "strategy_filter_status": "review" if not job.get("is_blacklisted") else "reject",
                "failure_codes": [],
            }
            for job in jobs
        ]
    strategy = get_filter_strategy(db, strategy_id)
    results = evaluate_filter_strategy(jobs, strategy)
    _jobs, results = apply_filter_exclusions(db, strategy, jobs, results)
    return results


def _persist_filter_results(
    db: Database,
    jobs: list[dict[str, object]],
    result_by_id: dict[str, dict[str, object]],
) -> None:
    for job in jobs:
        result = result_by_id.get(str(job.get("job_id") or ""))
        if result:
            update_capture_job_filter_result(db, job=job, result=result)


def _update_combination_metrics_in_connection(
    connection: Any,
    combination_id: str,
    metrics: SearchWindowMetrics,
    *,
    pages_seen: int,
    low_novelty_threshold: float,
    low_qualified_yield_threshold: float,
) -> None:
    if not combination_id:
        return
    row = connection.execute(
        "SELECT * FROM fj_workflow_search_combinations WHERE id = ?",
        (combination_id,),
    ).fetchone()
    if row is None:
        return
    low_novelty = int(row["low_novelty_streak"] or 0) + 1 if metrics.jobs_seen == 0 or metrics.novelty_yield < low_novelty_threshold else 0
    low_qualified = int(row["low_qualified_yield_streak"] or 0) + 1 if metrics.run_fresh_jobs > 0 and metrics.qualified_novelty_yield < low_qualified_yield_threshold else 0
    jobs_seen = int(row["jobs_seen"] or 0) + metrics.jobs_seen
    fresh = int(row["run_fresh_jobs"] or 0) + metrics.run_fresh_jobs
    qualified = int(row["qualified_fresh_jobs"] or 0) + metrics.qualified_fresh_jobs
    duplicates = int(row["historical_duplicates"] or 0) + metrics.historical_duplicates
    connection.execute(
        """
        UPDATE fj_workflow_search_combinations
        SET batch_count = batch_count + 1, pages_seen = pages_seen + ?,
            jobs_seen = ?, run_fresh_jobs = ?, historical_duplicates = ?,
            cooldown_excluded = cooldown_excluded + ?, strategy_pass = strategy_pass + ?,
            strategy_review = strategy_review + ?, strategy_reject = strategy_reject + ?,
            qualified_fresh_jobs = ?, candidate_jobs = candidate_jobs + ?,
            novelty_yield = ?, qualified_novelty_yield = ?, duplicate_rate = ?,
            low_novelty_streak = ?, low_qualified_yield_streak = ?
        WHERE id = ?
        """,
        (
            max(0, pages_seen), jobs_seen, fresh, duplicates,
            metrics.cooldown_excluded, metrics.strategy_pass, metrics.strategy_review,
            metrics.strategy_reject, qualified, metrics.candidate_jobs,
            round(fresh / jobs_seen, 4) if jobs_seen else 0,
            round(qualified / fresh, 4) if fresh else 0,
            round(duplicates / jobs_seen, 4) if jobs_seen else 0,
            low_novelty, low_qualified, combination_id,
        ),
    )


def _latest_metric(connection: Any, combination_id: str, field: str, fallback: int) -> int:
    if not combination_id:
        return fallback
    row = connection.execute(
        f"SELECT {field} FROM fj_workflow_search_combinations WHERE id = ?",
        (combination_id,),
    ).fetchone()
    return int(row[field] or fallback) if row is not None else fallback


def _dump(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _load(value: object) -> dict[str, Any]:
    try:
        loaded = json.loads(str(value or "{}"))
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}
