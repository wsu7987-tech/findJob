from __future__ import annotations

from backend.app.services.fine_job.collection_progress import advance_version, publish_progress

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
    plan_next_combination,
    platform_filter_code,
)
from backend.app.services.fine_job.boss_capture_history import (
    get_capture_history_job,
    update_capture_job_filter_result,
)
from backend.app.services.fine_job.boss_capture_tasks import boss_capture_task_manager
from backend.app.services.fine_job.filter_exclusions import apply_filter_exclusions, ensure_exclusion_state
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
    filter_strategy_id = str(payload.get("filter_strategy_id") or "").strip()
    if filter_strategy_id:
        # 每个搜索批次启动前刷新排除清单，保证筛选条件切换后仍使用最新冷却状态。
        ensure_exclusion_state(db, get_filter_strategy(db, filter_strategy_id), force=True)
    filters = canonicalize_platform_filters(payload.get("platform_filters") or payload.get("filters"))
    identity = combination_identity(keyword, city, filters)
    with db.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        from backend.app.services.fine_job.collection_starts import guard_owner_in_connection
        guard_owner_in_connection(connection)
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
    previous_pages = 0
    previous_job_count = 0
    window_jobs = jobs
    if previous_result.get("capture_task_id") == batch_id and task["status"] == "succeeded":
        # 续采会复用同一个 BOSS batch ID；只有页数或累计岗位数增长时才重新处理。
        previous_pages = int(previous_result.get("capture_pages") or 0)
        previous_job_count = int(previous_result.get("capture_job_count") or 0)
        if capture_pages <= previous_pages and capture_job_count <= previous_job_count:
            return dict(previous_result.get("metrics") or {})
        # Capture 快照是累计集合，窗口结算只消费本次新增岗位，避免续采重复累计指标。
        window_jobs = jobs[previous_job_count:]

    results = _evaluate_jobs(db, smart_capture_id, task_payload, window_jobs)
    result_by_id = {str(item.get("job_id") or ""): item for item in results}
    try:
        # 同步进程内快照，保留页面和后续详情流程对筛选结果的既有读取行为。
        if window_jobs:
            boss_capture_task_manager.apply_filter_results(batch_id, results)
    except AppError:
        # 重启后只有持久化 batch 时，历史 discovery 仍可独立完成写入。
        pass
    strategy_id = str(task_payload.get("filter_strategy_id") or "")
    if strategy_id:
        _persist_filter_results(db, window_jobs, result_by_id)
    metrics = build_metrics_for_window(window_jobs, results)
    combination_id = str(task_payload.get("search_combination_id") or "")
    now = utc_now()
    with db.connect() as connection:
        marginal_jobs_seen = len(window_jobs)
        marginal_first_discovery = 0
        for job in window_jobs:
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
            if previous is None:
                marginal_first_discovery += 1
        pages_seen = capture_pages
        if previous_result.get("capture_task_id") == batch_id:
            pages_seen = max(0, capture_pages - previous_pages)
        _update_combination_metrics_in_connection(
            connection,
            combination_id,
            metrics,
            pages_seen=pages_seen,
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
        metrics_dict["marginal_jobs_seen"] = marginal_jobs_seen
        metrics_dict["marginal_first_discovery_jobs"] = marginal_first_discovery
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


def count_candidates(
    db: Database,
    smart_capture_id: str,
    filter_strategy_id: str | None = None,
) -> int:
    strategy_id = str(filter_strategy_id or "").strip()
    if strategy_id:
        # 目标岗位数使用当前筛选策略的有效岗位/公司冷却状态。
        ensure_exclusion_state(db, get_filter_strategy(db, strategy_id))
    now = utc_now()
    with db.connect() as connection:
        row = connection.execute(
            """
            SELECT COUNT(DISTINCT d.job_id) AS count
            FROM fj_workflow_job_discoveries d
            JOIN fj_boss_jobs j ON j.id = d.job_id
            WHERE d.smart_capture_id = ?
              AND d.is_filter_candidate = 1
              AND (? = '' OR NOT EXISTS (
                SELECT 1
                FROM fj_filter_exclusion_entries exclusion
                WHERE exclusion.strategy_id = ?
                  AND (exclusion.excluded_until IS NULL OR exclusion.excluded_until > ?)
                  AND (
                    (exclusion.entity_type = 'job' AND exclusion.entity_id = j.id)
                    OR (exclusion.entity_type = 'company' AND exclusion.entity_id = j.company_id)
                  )
              ))
            """,
            (smart_capture_id, strategy_id, strategy_id, now),
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
            SELECT DISTINCT d.job_id, j.detail_status
        FROM fj_workflow_job_discoveries d
        JOIN fj_boss_jobs j ON j.id = d.job_id
        WHERE d.smart_capture_id = ?
          AND d.is_filter_candidate = 1
          AND j.detail_status = 'not_collected'
          AND NOT EXISTS (
            SELECT 1 FROM fj_workflow_tasks t
            WHERE t.smart_capture_id = d.smart_capture_id
              AND t.task_type IN ('deep_job_search_jd', 'deep_job_search_analysis')
                  AND t.status IN ('pending', 'running', 'succeeded')
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
            if candidates:
                advance_version(connection, smart_capture_id)
    except sqlite3.IntegrityError as exc:
        raise AppError(
            409,
            "CANDIDATE_RESERVATION_CONFLICT",
            "候选岗位已被其他活动 Smart Capture 占用。",
        ) from exc
    if candidates:
        publish_progress(db, smart_capture_id)
    return len(candidates)


def _next_pipeline_unit(db: Database, smart_capture_id: str, unit_type: str) -> Any | None:
    table = "fj_workflow_tasks" if unit_type == "formal_jd" else "fj_workflow_prefetch_items"
    task_filter = "AND task_type = 'deep_job_search_jd'" if unit_type == "formal_jd" else "AND lifecycle_status NOT IN ('abandoned', 'cancelled')"
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
    detail_task_id = new_id()
    with db.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        table = "fj_workflow_tasks" if unit_type == "formal_jd" else "fj_workflow_prefetch_items"
        next_status = "running" if unit_type == "formal_jd" else "collecting"
        changed = connection.execute(
            f"UPDATE {table} SET status = ? WHERE id = ? AND smart_capture_id = ? AND status = 'pending' AND EXISTS (SELECT 1 FROM fj_smart_captures WHERE id = ? AND status = 'running')",
            (next_status, unit["id"], smart_capture_id, smart_capture_id),
        )
        if changed.rowcount != 1:
            return
        # 先提交本单元的执行身份，极速回调和旧执行器迟到结果均可核对归属。
        connection.execute(f"UPDATE {table} SET operation_ref_type = 'capture_task', operation_ref_id = ? WHERE id = ?", (detail_task_id, unit["id"]))
        from backend.app.services.fine_job.collection_start_operations import bind_in_connection
        bind_in_connection(connection, "smart", smart_capture_id, detail_task_id)
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
        advance_version(connection, smart_capture_id)
    publish_progress(db, smart_capture_id)
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
            task_id=detail_task_id,
        )
        table = "fj_workflow_tasks" if unit_type == "formal_jd" else "fj_workflow_prefetch_items"
        with db.connect() as connection:
            connection.execute(
                f"UPDATE {table} SET operation_ref_type = 'capture_task', operation_ref_id = ?, updated_at = ? WHERE id = ?",
                (str(detail_task["id"]), utc_now(), unit["id"]),
            )
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
        owner = connection.execute("SELECT status FROM fj_smart_captures WHERE id = ?", (smart_capture_id,)).fetchone()
        if owner is None or owner["status"] in {"completed", "stopped", "failed"}:
            return
        if unit_type == "formal_jd":
            changed = connection.execute(
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
            changed = connection.execute(
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
        if changed.rowcount != 1:
            return
        advance_version(connection, smart_capture_id)
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
    publish_progress(db, smart_capture_id)


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
        task_filter = "AND task_type = 'deep_job_search_jd'" if unit_type == "formal_jd" else "AND lifecycle_status NOT IN ('abandoned', 'cancelled')"
        active = connection.execute(
            f"SELECT 1 FROM {table} WHERE smart_capture_id = ? {task_filter} AND status IN ('pending', 'running', 'collecting') LIMIT 1",
            (smart_capture_id,),
        ).fetchone()
        retryable_failed = connection.execute(
            "SELECT result_json FROM fj_workflow_tasks WHERE smart_capture_id = ? AND task_type = 'deep_job_search_jd' AND status = 'failed' AND retryable = 1 LIMIT 1",
            (smart_capture_id,),
        ).fetchone() if unit_type == "formal_jd" else None
    if active is not None:
        return
    if retryable_failed is not None:
        # 当前详情批次失败后进入可恢复状态，由继续采集统一重置失败单元并重试。
        from backend.app.services.fine_job import smart_captures

        error_message = _load(retryable_failed["result_json"]).get("error") or "JD 详情采集失败。"
        smart_captures._update_capture(
            db,
            smart_capture_id,
            status="interrupted",
            stage="pipeline_interrupted",
            waiting_reason="capture_interrupted",
            control_cause="recovery",
            message="JD 详情采集失败，请点击继续采集重试。",
            error_message=str(error_message),
        )
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
            "SELECT 1 FROM fj_workflow_tasks WHERE smart_capture_id = ? AND task_type = 'deep_job_search_jd' AND (status IN ('pending', 'running') OR (status = 'failed' AND retryable = 1)) LIMIT 1",
            (smart_capture_id,),
        ).fetchone()
        prefetch = connection.execute(
            "SELECT 1 FROM fj_workflow_prefetch_items WHERE smart_capture_id = ? AND status IN ('pending', 'collecting') AND lifecycle_status NOT IN ('abandoned', 'cancelled') LIMIT 1",
            (smart_capture_id,),
        ).fetchone()
        connection.execute(
            "UPDATE fj_workflow_tasks SET status = 'pending', operation_ref_type = NULL, operation_ref_id = NULL, completed_at = NULL, updated_at = ? WHERE smart_capture_id = ? AND task_type = 'deep_job_search_jd' AND status IN ('running', 'failed') AND retryable = 1",
            (utc_now(), smart_capture_id),
        )
        connection.execute(
            "UPDATE fj_workflow_prefetch_items SET status = 'pending', operation_ref_type = NULL, operation_ref_id = NULL, updated_at = ? WHERE smart_capture_id = ? AND status = 'collecting' AND lifecycle_status NOT IN ('abandoned', 'cancelled')",
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
    if unit["operation_ref_id"] and str(unit["operation_ref_id"]) != str(task.get("id") or ""):
        return
    if str(task.get("stage") or "").endswith("paused"):
        with db.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            changed = connection.execute(f"UPDATE {table} SET status = 'pending', updated_at = ? WHERE id = ? AND status IN ('running', 'collecting') AND EXISTS (SELECT 1 FROM fj_smart_captures WHERE id = ? AND status NOT IN ('completed', 'stopped', 'failed'))", (utc_now(), unit_id, smart_capture_id)).rowcount
            if not changed:
                return
            advance_version(connection, smart_capture_id)
        publish_progress(db, smart_capture_id)
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
            advance_version(connection, smart_capture_id)
    except sqlite3.IntegrityError as exc:
        raise AppError(
            409,
            "CANDIDATE_RESERVATION_CONFLICT",
            "Prefetch 候选岗位已被其他活动 Smart Capture 占用。",
        ) from exc
    publish_progress(db, smart_capture_id)
    return batch_id


def _finalize_prefetch_batch(db: Database, smart_capture_id: str) -> None:
    now = utc_now()
    source_analysis_batch_id = ""
    with db.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
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
        advance_version(connection, smart_capture_id)
    publish_progress(db, smart_capture_id)
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
    if unfinished_analysis is None:
        promotion = _promote_ready_prefetch(db, smart_capture_id)
        if promotion == "promoted":
            _set_capture_state(
                db,
                smart_capture_id,
                status="running",
                stage="waiting_codex",
                waiting_reason="codex",
                message="Prefetch 已收敛并提升为下一正式 Analysis Batch。",
            )
        elif promotion == "none":
            # Prefetch 没有可用岗位时，继续搜索以补充下一批候选。
            snapshot = _smart_capture_snapshot(db, smart_capture_id)
            config = _capture_config(snapshot)
            _continue_after_analysis_batch(db, smart_capture_id, snapshot, config)


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
        # 分析目标未达成且没有可用 Prefetch 时，继续既有的列表推进流程。
        if _continue_after_analysis_batch(db, smart_capture_id, snapshot, config):
            return
        _set_capture_state(
            db,
            smart_capture_id,
            status="waiting_next_batch",
            stage="candidate_ready",
            waiting_reason="next_batch",
            message="当前分析已完成，等待后续候选批次。",
        )


def _continue_after_analysis_batch(
    db: Database,
    smart_capture_id: str,
    snapshot: dict[str, object],
    execution_config: dict[str, object],
) -> bool:
    """分析批次未达目标时，接续当前搜索或切换下一搜索组合。"""
    analysis_config = execution_config.get("analysis")
    analysis_config = analysis_config if isinstance(analysis_config, dict) else {}
    if str(analysis_config.get("after_analysis_batch") or "auto_continue") != "auto_continue":
        return False
    if bool(analysis_config.get("stop_after_current_batch")):
        return False

    current_batch_id = str(snapshot.get("current_batch_id") or "")
    if not current_batch_id:
        return False
    try:
        # 允许服务重启后从持久化批次恢复当前列表采集任务。
        capture_task = boss_capture_task_manager.get_task(current_batch_id, db=db)
    except AppError:
        return False
    if str(capture_task.get("status") or "") in {"queued", "running"}:
        # 当前列表采集仍在执行，等待它自己的完成回调推进，避免重复启动。
        return False

    task_info = _task_for_batch(db, smart_capture_id, current_batch_id)
    if task_info is None:
        return False
    with db.connect() as connection:
        task = connection.execute(
            "SELECT result_json FROM fj_workflow_tasks WHERE id = ? AND smart_capture_id = ?",
            (task_info["task_id"], smart_capture_id),
        ).fetchone()
    if task is None:
        return False
    metrics = _load(task["result_json"]).get("metrics") or {}
    if not isinstance(metrics, dict):
        metrics = {}

    # 搜索切换时复用 Smart Capture 的统一输出目录，保证重启恢复后仍可创建新批次。
    from backend.app.config import load_config

    capture_task["_output_dir"] = load_config().output_root / "fine-job" / "boss-capture"
    return _advance_search_progression(
        db,
        smart_capture_id,
        capture_task,
        metrics,
        execution_config,
    )


def resume_after_analysis_batch(db: Database, smart_capture_id: str) -> bool:
    """恢复已进入等待态的任务，补执行一次分析完成后的自动推进。"""
    snapshot = _smart_capture_snapshot(db, smart_capture_id)
    if str(snapshot.get("status") or "") in {"completed", "stopped", "failed"}:
        return False
    config = _capture_config(snapshot)
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
        return True
    if promotion == "waiting":
        return False
    return _continue_after_analysis_batch(db, smart_capture_id, snapshot, config)


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
        advance_version(connection, smart_capture_id)
    publish_progress(db, smart_capture_id)
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
        if batches:
            advance_version(connection, smart_capture_id)
    if batches:
        publish_progress(db, smart_capture_id)


def advance_completed_batch(
    db: Database,
    smart_capture_id: str,
    capture_task: dict[str, object],
    *,
    metrics: dict[str, object] | None = None,
) -> bool:
    """由 Smart Capture Engine 消费 BOSS 完成，不经 Workflow 推进内部 Pipeline。"""
    # 延迟导入避免 Engine 与 lifecycle service 的模块初始化循环。
    from backend.app.services.fine_job import smart_captures

    snapshot = smart_captures.get_smart_capture(db, smart_capture_id)
    if str(snapshot["status"]) in {"completed", "stopped", "failed"}:
        return False
    config = snapshot.get("execution_config")
    config = config if isinstance(config, dict) else {}
    auto_jd_details = bool(config.get("auto_jd_detail_collection_enabled", True))
    search_config = config.get("search")
    search_config = search_config if isinstance(search_config, dict) else {}
    candidate_count = count_candidates(
        db,
        smart_capture_id,
        str(search_config.get("filter_strategy_id") or ""),
    )
    target_count = int(snapshot.get("target_count") or 0)
    exhausted = not bool(capture_task.get("has_more"))
    # 采集目标岗位数始终是列表进入 JD 详情的门槛，投递目标只在分析阶段判断完成。
    target_reached = target_count > 0 and candidate_count >= target_count
    # 手动 JD 模式达到目标后结束列表采集；自动 JD 模式达到目标后直接进入详情批次。
    if target_reached and not auto_jd_details:
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
        return False
    if not target_reached and _advance_search_progression(
        db,
        smart_capture_id,
        capture_task,
        metrics or {},
        config,
    ):
        return True
    result_summary = {
        "candidate_count": candidate_count,
        "last_batch_id": str(capture_task.get("id") or ""),
        "search_exhausted": exhausted,
    }
    if not auto_jd_details:
        # 关闭自动 JD 详情时，列表采集完成后交给岗位列表的手动采集入口处理详情。
        smart_captures._update_capture(
            db,
            smart_capture_id,
            status="completed",
            stage="completed",
            waiting_reason="",
            control_cause="",
            message="岗位列表采集完成，等待手动采集 JD 详情。",
            result_summary={**result_summary, "completion_reason": "list_completed_waiting_manual_details"},
            completed=True,
        )
        return False
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
        return False
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
    return False


def _continue_capture_batch(
    db: Database,
    smart_capture_id: str,
    capture_task: dict[str, object],
    metrics: dict[str, object],
    execution_config: dict[str, object],
) -> bool:
    """按现有搜索深度和低产出规则继续当前搜索组合。"""
    if not bool(capture_task.get("continuation_available") and capture_task.get("has_more")):
        return False
    stop_policy = execution_config.get("stop_policy")
    if not isinstance(stop_policy, dict):
        return False
    depth = int(capture_task.get("total_pages_loaded") or 0)
    max_depth = int(stop_policy.get("max_depth") or 20)
    if depth >= max_depth:
        return False
    qualified = int(metrics.get("qualified_fresh_jobs") or 0)
    low_novelty_streak = int(metrics.get("low_novelty_streak") or 0)
    low_qualified_yield_streak = int(metrics.get("low_qualified_yield_streak") or 0)
    low_yield_limit = int(stop_policy.get("low_yield_streak_limit") or 3)
    should_continue = qualified > 0 or (
        low_novelty_streak < low_yield_limit
        and low_qualified_yield_streak < low_yield_limit
    )
    if not should_continue:
        return False
    batch_pages = min(
        int(stop_policy.get("scroll_batch_size") or 3),
        max_depth - depth,
        10,
    )
    if batch_pages < 1:
        return False
    boss_capture_task_manager.continue_capture(
        str(capture_task["id"]),
        pages=batch_pages,
    )
    from backend.app.services.fine_job import smart_captures

    # 当前批次有继续价值时立即进入下一批，目标未达成前保持同一搜索组合。
    smart_captures._update_capture(
        db,
        smart_capture_id,
        status="running",
        stage="capturing",
        waiting_reason="",
        control_cause="",
        message="当前搜索组合仍有有效产出，正在自动继续采集。",
    )
    return True


def _advance_search_progression(
    db: Database,
    smart_capture_id: str,
    capture_task: dict[str, object],
    metrics: dict[str, object],
    execution_config: dict[str, object],
) -> bool:
    """迁移 Workflow 的批次后续推进：续采当前组合或切换到下一组合。"""
    if _continue_capture_batch(
        db,
        smart_capture_id,
        capture_task,
        metrics,
        execution_config,
    ):
        return True

    task_info = _task_for_batch(db, smart_capture_id, str(capture_task.get("id") or ""))
    if task_info is None:
        task_info = _pending_task_for_capture(db, smart_capture_id)
    if task_info is None:
        return False
    with db.connect() as connection:
        task = connection.execute(
            "SELECT payload_json, result_json FROM fj_workflow_tasks WHERE id = ? AND smart_capture_id = ?",
            (task_info["task_id"], smart_capture_id),
        ).fetchone()
        combination = connection.execute(
            "SELECT * FROM fj_workflow_search_combinations WHERE id = ? AND smart_capture_id = ?",
            (task_info["combination_id"], smart_capture_id),
        ).fetchone() if task_info["combination_id"] else None
    if task is None:
        return False

    payload = _load(task["payload_json"])
    result = _load(task["result_json"])
    window = metrics or result.get("metrics") or {}
    window_metrics = SearchWindowMetrics(
        jobs_seen=int(window.get("jobs_seen") or 0),
        run_fresh_jobs=int(window.get("run_fresh_jobs") or window.get("new_jobs") or 0),
        historical_duplicates=int(window.get("historical_duplicates") or window.get("duplicates") or 0),
        cooldown_excluded=int(window.get("cooldown_excluded") or 0),
        strategy_pass=int(window.get("strategy_pass") or 0),
        strategy_review=int(window.get("strategy_review") or 0),
        strategy_reject=int(window.get("strategy_reject") or 0),
        qualified_fresh_jobs=int(window.get("qualified_fresh_jobs") or window.get("candidate_jobs") or 0),
        candidate_jobs=int(window.get("candidate_jobs") or window.get("new_candidates") or 0),
        novelty_yield=float(window.get("novelty_yield") or 0),
        qualified_novelty_yield=float(window.get("qualified_novelty_yield") or 0),
        duplicate_rate=float(window.get("duplicate_rate") or 0),
        low_novelty_streak=int(window.get("low_novelty_streak") or payload.get("low_novelty_streak") or 0),
        low_qualified_yield_streak=int(window.get("low_qualified_yield_streak") or payload.get("low_qualified_yield_streak") or 0),
        failure_code_counts={
            str(key): int(value)
            for key, value in (window.get("failure_code_counts") or {}).items()
        },
    )
    stop_policy = execution_config.get("stop_policy")
    stop_policy = stop_policy if isinstance(stop_policy, dict) else {}
    search_config = execution_config.get("search")
    search_config = search_config if isinstance(search_config, dict) else {}
    keyword = str(payload.get("keyword") or capture_task.get("keyword") or "").strip()
    city = str(payload.get("city") or capture_task.get("city") or "").strip()
    strategy_id = str(payload.get("filter_strategy_id") or search_config.get("filter_strategy_id") or "")
    if not keyword or not city or not strategy_id:
        return False

    can_continue = bool(capture_task.get("continuation_available") and capture_task.get("has_more"))
    depth = int(payload.get("depth") or capture_task.get("total_pages_loaded") or 0)
    max_depth = int(stop_policy.get("max_depth") or 20)
    current_filters = canonicalize_platform_filters(
        payload.get("platform_filters")
        or (_load(combination["platform_filters_json"]) if combination is not None else {})
    )
    parent_filters: dict[str, str] | None = None
    if combination is not None and combination["parent_combination_id"]:
        with db.connect() as connection:
            parent = connection.execute(
                "SELECT platform_filters_json FROM fj_workflow_search_combinations WHERE id = ? AND smart_capture_id = ?",
                (str(combination["parent_combination_id"]), smart_capture_id),
            ).fetchone()
        if parent is not None:
            parent_filters = canonicalize_platform_filters(_load(parent["platform_filters_json"]))
    current_combination_empty = bool(
        combination is not None and int(combination["jobs_seen"] or 0) == 0
    )
    evidence_combination_id = (
        str(combination["parent_combination_id"])
        if current_combination_empty and combination is not None and combination["parent_combination_id"]
        else task_info["combination_id"]
    )
    positive_distribution = _positive_filter_distribution(
        db,
        smart_capture_id,
        evidence_combination_id,
    )
    if (
        not current_combination_empty
        and combination is not None
        and combination["parent_combination_id"]
    ):
        positive_distribution = _merge_filter_distributions(
            positive_distribution,
            _positive_filter_distribution(
                db,
                smart_capture_id,
                str(combination["parent_combination_id"]),
            ),
        )
    decision = plan_next_combination(
        current_filters=current_filters,
        strategy=get_filter_strategy(db, strategy_id),
        metrics=window_metrics,
        attempted_filters=_list_combination_filters(db, smart_capture_id, keyword, city),
        duplicate_distribution=_historical_duplicate_distribution(db, smart_capture_id, keyword, city),
        force_transition=not can_continue or depth >= max_depth,
        low_yield_streak_limit=int(stop_policy.get("low_yield_streak_limit") or 3),
        low_novelty_threshold=0.25,
        low_qualified_yield_threshold=0.15,
        duplicate_skew_threshold=0.6,
        combination_safety_limit=24,
        parent_filters=parent_filters,
        positive_distribution=positive_distribution,
        current_combination_empty=current_combination_empty,
        scope_empty_streak=_scope_empty_streak(db, smart_capture_id, keyword, city),
        scope_empty_limit=2,
    )
    _save_planner_decision(db, smart_capture_id, task_info["combination_id"], decision)
    if decision.action == "SCOPE_EXHAUSTED":
        _mark_search_combination_exhausted(
            db,
            task_info["combination_id"],
            decision.switch_reason or "approved_platform_search_space_exhausted",
        )
        next_task = _create_next_approved_scope(
            db,
            smart_capture_id,
            keyword,
            city,
            search_config,
            task_info["combination_id"],
        )
    else:
        transition_parent_id = (
            str(combination["parent_combination_id"])
            if current_combination_empty and combination is not None and combination["parent_combination_id"]
            else task_info["combination_id"]
        )
        next_task = _create_search_combination_task(
            db,
            smart_capture_id,
            workflow_run_id=str(_smart_capture_workflow_id(db, smart_capture_id) or "") or None,
            keyword=keyword,
            city=city,
            platform_filters=decision.platform_filters,
            parent_combination_id=transition_parent_id or None,
            transition_action=decision.action,
            transition_reason=decision.switch_reason,
            selected_axis=decision.selected_axis,
            evidence=decision.evidence or {},
        )
    if next_task is None:
        return False
    output_dir = capture_task.get("_output_dir")
    if not isinstance(output_dir, Path):
        return False
    next_payload = {
        "keyword": next_task["keyword"],
        "city": next_task["city"],
        "allowed_search_keywords": list(search_config.get("keywords") or []),
        "allowed_cities": list(search_config.get("cities") or []),
        "platform_filters": next_task["platform_filters"],
        "filters": next_task["platform_filters"],
        "filter_strategy_id": strategy_id,
        "min_depth": int(stop_policy.get("min_depth") or 1),
        "prefer_current_page": bool(search_config.get("prefer_current_page", True)),
        "force_search_navigation": True,
    }
    from backend.app.services.fine_job import smart_captures

    smart_captures.start_search_combination_batch(
        db,
        smart_capture_id,
        next_payload,
        output_dir=output_dir,
    )
    smart_captures._update_capture(
        db,
        smart_capture_id,
        status="running",
        stage="capturing",
        waiting_reason="",
        control_cause="",
        message="当前搜索条件产出不足，正在切换下一组搜索条件。",
    )
    return True


def _smart_capture_workflow_id(db: Database, smart_capture_id: str) -> str | None:
    with db.connect() as connection:
        row = connection.execute(
            "SELECT workflow_run_id FROM fj_smart_captures WHERE id = ?",
            (smart_capture_id,),
        ).fetchone()
    return str(row["workflow_run_id"] or "") if row is not None else None


def _scope_empty_streak(
    db: Database,
    smart_capture_id: str,
    keyword: str,
    city: str,
) -> int:
    """读取当前词城末尾连续完成的可信空组合数量。"""
    with db.connect() as connection:
        rows = connection.execute(
            """
            SELECT status, jobs_seen
            FROM fj_workflow_search_combinations
            WHERE smart_capture_id = ? AND keyword = ? AND city = ?
            ORDER BY sequence DESC
            """,
            (smart_capture_id, keyword, city),
        ).fetchall()
    streak = 0
    for row in rows:
        if str(row["status"] or "") not in {"completed", "exhausted"}:
            break
        if int(row["jobs_seen"] or 0) != 0:
            break
        streak += 1
    return streak


def _historical_duplicate_distribution(
    db: Database,
    smart_capture_id: str,
    keyword: str,
    city: str,
) -> dict[str, dict[str, int]]:
    """统计当前搜索词和城市下历史重复岗位的平台筛选分布。"""
    with db.connect() as connection:
        rows = connection.execute(
            """
            SELECT j.company_scale, j.company_stage, j.company_industry,
                   j.experience, j.degree, j.salary
            FROM fj_workflow_job_discoveries d
            JOIN fj_boss_jobs j ON j.id = d.job_id
            WHERE d.smart_capture_id = ? AND d.search_keyword = ? AND d.city = ?
              AND d.is_historical_duplicate = 1
            """,
            (smart_capture_id, keyword, city),
        ).fetchall()
    columns = {
        "company_scale": "company_scale",
        "company_stage": "company_stage",
        "company_industry": "company_industry",
        "experience": "experience",
        "degree": "degree",
        "salary": "salary",
    }
    distribution: dict[str, dict[str, int]] = {axis: {} for axis in columns}
    for row in rows:
        for axis, column in columns.items():
            value = str(row[column] or "").strip()
            code = platform_filter_code(axis, value) if value else None
            if code:
                distribution[axis][code] = distribution[axis].get(code, 0) + 1
    return distribution


def _positive_filter_distribution(
    db: Database,
    smart_capture_id: str,
    combination_id: str,
) -> dict[str, dict[str, int]]:
    """统计父组合或当前组合实际岗位对平台筛选值的正向证据。"""
    axes = {
        "company_scale": "company_scale",
        "company_stage": "company_stage",
        "company_industry": "company_industry",
        "experience": "experience",
        "degree": "degree",
        "salary": "salary",
    }
    distribution: dict[str, dict[str, int]] = {axis: {} for axis in axes}
    if not combination_id:
        return distribution
    with db.connect() as connection:
        rows = connection.execute(
            """
            SELECT DISTINCT j.company_scale, j.company_stage, j.company_industry,
                   j.experience, j.degree, j.salary
            FROM fj_workflow_job_discoveries d
            JOIN fj_boss_jobs j ON j.id = d.job_id
            WHERE d.smart_capture_id = ?
              AND json_extract(d.search_combination_json, '$.search_combination_id') = ?
            """,
            (smart_capture_id, combination_id),
        ).fetchall()
    for row in rows:
        for axis, column in axes.items():
            value = str(row[column] or "").strip()
            code = platform_filter_code(axis, value) if value else None
            if code:
                distribution[axis][code] = distribution[axis].get(code, 0) + 1
    return distribution


def _merge_filter_distributions(
    first: dict[str, dict[str, int]],
    second: dict[str, dict[str, int]],
) -> dict[str, dict[str, int]]:
    merged = {axis: dict(values) for axis, values in first.items()}
    for axis, values in second.items():
        target = merged.setdefault(axis, {})
        for value, count in values.items():
            target[value] = target.get(value, 0) + int(count)
    return merged


def _list_combination_filters(
    db: Database,
    smart_capture_id: str,
    keyword: str,
    city: str,
) -> list[dict[str, str]]:
    with db.connect() as connection:
        rows = connection.execute(
            """
            SELECT platform_filters_json
            FROM fj_workflow_search_combinations
            WHERE smart_capture_id = ? AND keyword = ? AND city = ?
            ORDER BY sequence
            """,
            (smart_capture_id, keyword, city),
        ).fetchall()
    return [canonicalize_platform_filters(_load(row["platform_filters_json"])) for row in rows]


def _save_planner_decision(
    db: Database,
    smart_capture_id: str,
    combination_id: str,
    decision: Any,
) -> None:
    if not combination_id:
        return
    with db.connect() as connection:
        connection.execute(
            """
            UPDATE fj_workflow_search_combinations
            SET transition_reason = CASE WHEN ? <> '' THEN ? ELSE transition_reason END,
                selected_axis = CASE WHEN ? <> '' THEN ? ELSE selected_axis END,
                evidence_json = ?
            WHERE id = ? AND smart_capture_id = ?
            """,
            (
                decision.switch_reason,
                decision.switch_reason,
                decision.selected_axis,
                decision.selected_axis,
                _dump(decision.evidence or {}),
                combination_id,
                smart_capture_id,
            ),
        )


def _mark_search_combination_exhausted(
    db: Database,
    combination_id: str,
    stop_reason: str,
) -> None:
    if not combination_id:
        return
    with db.connect() as connection:
        connection.execute(
            """
            UPDATE fj_workflow_search_combinations
            SET status = 'exhausted', completed_at = COALESCE(completed_at, ?), stop_reason = ?
            WHERE id = ? AND status IN ('pending', 'running', 'completed')
            """,
            (utc_now(), stop_reason, combination_id),
        )


def _create_search_combination_task(
    db: Database,
    smart_capture_id: str,
    *,
    workflow_run_id: str | None,
    keyword: str,
    city: str,
    platform_filters: dict[str, str],
    parent_combination_id: str | None,
    transition_action: str,
    transition_reason: str,
    selected_axis: str,
    evidence: dict[str, object],
    is_baseline: bool = False,
) -> dict[str, object] | None:
    filters = canonicalize_platform_filters(platform_filters)
    identity = combination_identity(keyword, city, filters)
    with db.connect() as connection:
        existing = connection.execute(
            "SELECT id FROM fj_workflow_search_combinations WHERE smart_capture_id = ? AND identity_json = ?",
            (smart_capture_id, identity),
        ).fetchone()
        if existing is not None:
            return None
        sequence = int(
            connection.execute(
                "SELECT COALESCE(MAX(sequence), 0) FROM fj_workflow_search_combinations WHERE smart_capture_id = ?",
                (smart_capture_id,),
            ).fetchone()[0]
        ) + 1
        combination_id = new_id()
        now = utc_now()
        connection.execute(
            """
            INSERT INTO fj_workflow_search_combinations (
              id, workflow_run_id, smart_capture_id, keyword, city, platform_filters_json,
              identity_json, status, sequence, parent_combination_id,
              transition_action, transition_reason, selected_axis, evidence_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?)
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
                parent_combination_id,
                transition_action,
                transition_reason,
                selected_axis,
                _dump(evidence),
            ),
        )
        task_id = new_id()
        connection.execute(
            """
            INSERT INTO fj_workflow_tasks (
              id, workflow_run_id, smart_capture_id, task_type, payload_json, result_json,
              created_at, updated_at
            ) VALUES (?, ?, ?, 'deep_job_search', ?, '{}', ?, ?)
            """,
            (
                task_id,
                workflow_run_id,
                smart_capture_id,
                _dump({
                    "keyword": keyword,
                    "city": city,
                    "platform_filters": filters,
                    "search_combination_id": combination_id,
                    "is_baseline": is_baseline,
                    "depth": 0,
                    "low_yield_streak": 0,
                    "low_novelty_streak": 0,
                    "low_qualified_yield_streak": 0,
                }),
                now,
                now,
            ),
        )
    return {
        "combination_id": combination_id,
        "task_id": task_id,
        "keyword": keyword,
        "city": city,
        "platform_filters": filters,
    }


def _create_next_approved_scope(
    db: Database,
    smart_capture_id: str,
    current_keyword: str,
    current_city: str,
    search_config: dict[str, object],
    parent_combination_id: str,
) -> dict[str, object] | None:
    keywords = [str(value) for value in search_config.get("keywords") or []]
    cities = [str(value) for value in search_config.get("cities") or []]
    scopes = [(keyword, city) for keyword in keywords for city in cities]
    try:
        current_index = scopes.index((current_keyword, current_city))
    except ValueError:
        current_index = -1
    if current_index + 1 >= len(scopes):
        return None
    next_keyword, next_city = scopes[current_index + 1]
    reason = "approved_city_next" if next_keyword == current_keyword else "approved_keyword_next"
    return _create_search_combination_task(
        db,
        smart_capture_id,
        workflow_run_id=_smart_capture_workflow_id(db, smart_capture_id),
        keyword=next_keyword,
        city=next_city,
        platform_filters={},
        parent_combination_id=parent_combination_id or None,
        transition_action="SWITCH_COMBINATION",
        transition_reason=reason,
        selected_axis="",
        evidence={"previous_scope": {"keyword": current_keyword, "city": current_city}},
        is_baseline=True,
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
            WHERE d.smart_capture_id = ?
              AND d.is_filter_candidate = 1
              AND j.detail_status = 'not_collected'
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
