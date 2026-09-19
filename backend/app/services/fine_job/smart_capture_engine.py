from __future__ import annotations

import json
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
    update_capture_job_filter_result,
)
from backend.app.services.fine_job.boss_capture_tasks import boss_capture_task_manager
from backend.app.services.fine_job.filter_exclusions import apply_filter_exclusions
from backend.app.services.fine_job.job_evaluation import evaluate_filter_strategy
from backend.app.services.fine_job.strategies import get_filter_strategy
from backend.app.utils import new_id, utc_now


SEARCH_TASK_TYPE = "deep_job_search"


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
