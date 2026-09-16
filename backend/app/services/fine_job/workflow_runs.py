from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from threading import RLock, Thread
from typing import Any

from backend.app.config import AppConfig
from backend.app.db import Database
from backend.app.errors import AppError
from backend.app.services.fine_job.boss_capture_tasks import boss_capture_task_manager
from backend.app.services.fine_job.workflow_run_events import workflow_run_event_broker
from backend.app.services.fine_job.boss_capture_history import get_capture_history_job
from backend.app.services.fine_job.boss_scraper.service import BossCaptureRequest, boss_scraper_service
from backend.app.services.fine_job.adaptive_search_planner import (
    SearchWindowMetrics,
    build_metrics_for_window,
    canonicalize_platform_filters,
    combination_identity,
    describe_platform_filters,
    plan_next_combination,
)
from backend.app.services.fine_job.filter_exclusions import apply_filter_exclusions
from backend.app.services.fine_job.job_evaluation import evaluate_filter_strategy
from backend.app.services.fine_job import profile_store, profile_v3, smart_captures
from backend.app.services.fine_job.profile_context import get_profile_context
from backend.app.services.fine_job.strategies import (
    get_filter_strategy,
    get_recommendation_strategy,
    list_search_keywords,
)
from backend.app.utils import new_id, utc_now


HARD_CONTEXT_BUDGET = 1_000_000
START_ACK_TIMEOUT_SECONDS = 45
_realtime_runtime: tuple[Database, AppConfig] | None = None
_realtime_runtime_lock = RLock()


def configure_realtime_runtime(db: Database, config: AppConfig) -> None:
    """保存当前应用运行时，用于后台采集完成后继续推进 Workflow。"""
    global _realtime_runtime
    with _realtime_runtime_lock:
        _realtime_runtime = (db, config)


def _on_capture_task_updated(capture: dict[str, object]) -> None:
    """将采集器进度转换为 Workflow 快照事件，并在结束时继续编排。"""
    with _realtime_runtime_lock:
        runtime = _realtime_runtime
    if runtime is None:
        return
    db, config = runtime
    smart_captures.sync_capture_snapshot(db, capture)
    capture_id = str(capture.get("id") or "")
    if not capture_id:
        return
    with db.connect() as connection:
        rows = connection.execute(
            """
            SELECT DISTINCT workflow_run_id
            FROM fj_workflow_tasks
            WHERE operation_ref_type = 'capture_task' AND operation_ref_id = ?
            """,
            (capture_id,),
        ).fetchall()
    for row in rows:
        workflow_run_id = str(row["workflow_run_id"])
        try:
            workflow_run_event_broker.publish(workflow_run_id, get_workflow_run(db, workflow_run_id))
        except Exception:
            continue
        stage = str(capture.get("stage") or "")
        if (
            str(capture.get("status") or "") in {"completed", "failed"}
            and not stage.endswith("paused")
            and not stage.endswith("stopped")
        ):
            Thread(
                target=_advance_after_capture_finished,
                args=(db, config, workflow_run_id),
                daemon=True,
            ).start()


def _advance_after_capture_finished(db: Database, config: AppConfig, workflow_run_id: str) -> None:
    """采集结束后由后端继续推进，页面只订阅状态。"""
    try:
        snapshot = advance_deep_job_search(db, config, workflow_run_id)
        workflow_run_event_broker.publish(workflow_run_id, snapshot)
    except Exception:
        # 后续恢复入口仍可读取已持久化的 Run 状态并由用户继续处理。
        return


boss_capture_task_manager.add_listener(_on_capture_task_updated)


def create_deep_job_search_run(
    db: Database, config: AppConfig, payload: dict[str, Any], *, created_from: str
) -> dict[str, object]:
    """创建搜索 Run；采集器与筛选器仍由既有服务负责。"""
    assert_collection_start_allowed(db, requested_kind="smart")
    filter_strategy_id = str(payload["filter_strategy_id"])
    strategy = get_filter_strategy(db, filter_strategy_id)
    if not strategy.get("enabled"):
        raise AppError(409, "FILTER_STRATEGY_DISABLED", "岗位筛选策略当前未启用。")
    delivery_target_enabled = bool(payload.get("delivery_target_enabled", True))
    recommendation_strategy = None
    if delivery_target_enabled:
        recommendation_strategy = _require_workflow_recommendation_strategy(
            db,
            recommendation_strategy_id=str(payload["recommendation_strategy_id"]),
            filter_strategy_id=filter_strategy_id,
        )
    allowed_keywords = {str(item["keyword"]) for item in list_search_keywords(db, filter_strategy_id) if item.get("enabled")}
    requested_keywords = [str(value).strip() for value in payload["allowed_search_keywords"] if str(value).strip()]
    invalid_keywords = [value for value in requested_keywords if value not in allowed_keywords]
    if invalid_keywords:
        raise AppError(422, "SEARCH_KEYWORD_INVALID", f"搜索词未在当前策略中启用：{'、'.join(invalid_keywords)}")
    allowed_cities = {str(value) for value in strategy.get("cities") or []}
    requested_cities = [str(value).strip() for value in payload["allowed_cities"] if str(value).strip()]
    invalid_cities = [value for value in requested_cities if value not in allowed_cities]
    if invalid_cities:
        raise AppError(422, "SEARCH_CITY_INVALID", f"城市未在当前策略中配置：{'、'.join(invalid_cities)}")

    recommend_target = int(payload.get("recommend_target") or 0)
    review_target = payload.get("review_target")
    candidate_target = int(payload.get("candidate_target_count") or (recommend_target * 3 if recommend_target else 15))
    if delivery_target_enabled and candidate_target < recommend_target:
        raise AppError(422, "VALIDATION_FAILED", "候选池目标不能小于本轮推荐岗位目标。")
    min_depth = int(payload.get("min_depth") or 5)
    max_depth = int(payload.get("max_depth") or 20)
    if max_depth < min_depth:
        raise AppError(422, "VALIDATION_FAILED", "最大搜索深度不能小于最低探索深度。")
    contract = {
        "task_type": "deep_job_search",
        "delivery_target_enabled": delivery_target_enabled,
        # target_count 只用于兼容历史读取；运行时只从 recommend_target 解析推荐目标。
        "recommend_target": recommend_target,
        "review_target": int(review_target) if review_target is not None else None,
        "target_mode": str(payload.get("target_mode") or "all"),
        "target_count": recommend_target,
        "counting_rule": "saved_unique_recommend_and_optional_review",
        "source_policy": "fresh_only",
        "selected_strategy_ids": {
            "filter_strategy_id": filter_strategy_id,
            "recommendation_strategy_id": str(recommendation_strategy["id"]) if recommendation_strategy else "",
        },
        "selected_strategy_versions": {
            "filter_strategy_version": int(strategy.get("strategy_version") or 1),
            "recommendation_strategy_version": int(recommendation_strategy.get("strategy_version") or 1) if recommendation_strategy else None,
        },
        "allowed_search_keywords": requested_keywords,
        "allowed_cities": requested_cities,
        "allow_historical_jobs": False,
        "external_action_policy": "analysis_only",
        "codex_execution_config": {
            "model": str(payload.get("codex_model") or ""),
            "reasoning_effort": str(payload.get("codex_reasoning_effort") or ""),
        },
        "analysis_guidance": {
            "text": str(payload.get("analysis_guidance") or "").strip(),
            "version": 1,
        },
        "applied_feedback_ids": list(payload.get("applied_feedback_ids") or []),
        "applied_preference_ids": list(payload.get("applied_preference_ids") or []),
        "stop_policy": {
            "min_depth": min_depth,
            "scroll_batch_size": int(payload.get("scroll_batch_size") or 3),
            "max_depth": max_depth,
            "low_yield_streak_limit": int(payload.get("low_yield_streak_limit") or 3),
            "candidate_target_count": candidate_target,
        },
        "planner_policy": {
            "low_novelty_threshold": float(payload.get("low_novelty_threshold") or 0.25),
            "low_qualified_yield_threshold": float(payload.get("low_qualified_yield_threshold") or 0.15),
            "duplicate_skew_threshold": 0.6,
            "combination_safety_limit": int(payload.get("search_combination_safety_limit") or 24),
        },
        "analysis_policy": {
            "enabled": delivery_target_enabled,
            "analyze_all_candidates": bool(payload.get("analyze_all_candidates")),
            "stop_after_current_batch": bool(payload.get("stop_after_current_batch")),
            "analysis_batch_size": int(payload.get("analysis_batch_size") or payload.get("jd_batch_size") or 5),
        },
        "execution_policy": {
            "after_analysis_batch": str(payload.get("execution_policy_after_analysis_batch") or "auto_continue"),
            "codex_handoff": str(payload.get("execution_policy_codex_handoff") or "auto"),
        },
        "created_from": created_from,
        "version": 1,
    }
    workflow_run_id = new_id()
    now = utc_now()
    with db.connect() as connection:
        connection.execute(
            """
            INSERT INTO fj_workflow_runs (
              id, workflow_type, completion_contract_json, status, completed_count,
              remaining_count, current_step, next_action, next_action_reason,
              waiting_for_user, paused, telemetry_json, created_at, updated_at
            ) VALUES (?, 'deep_job_search', ?, 'pending', 0, ?, 'created',
                      'start_search', '等待开始第一个已批准搜索组合。', 0, 0, '{}', ?, ?)
            """,
            (workflow_run_id, _dump(contract), recommend_target, now, now),
        )
        # 先只建立第一个 Scope 的 baseline 组合，后续组合由 Planner 按结果按需创建。
        keyword = requested_keywords[0]
        city = requested_cities[0]
        combination_id = new_id()
        filters: dict[str, str] = {}
        connection.execute(
            """
            INSERT INTO fj_workflow_search_combinations (
              id, workflow_run_id, keyword, city, platform_filters_json,
              identity_json, status, sequence, transition_action, transition_reason
            ) VALUES (?, ?, ?, ?, ?, ?, 'pending', 1, 'SWITCH_COMBINATION', 'baseline')
            """,
            (
                combination_id,
                workflow_run_id,
                keyword,
                city,
                _dump(filters),
                combination_identity(keyword, city, filters),
            ),
        )
        task_id = new_id()
        connection.execute(
            """
            INSERT INTO fj_workflow_tasks (
              id, workflow_run_id, task_type, payload_json, result_json, created_at, updated_at
            ) VALUES (?, ?, 'deep_job_search', ?, '{}', ?, ?)
            """,
            (
                task_id,
                workflow_run_id,
                _dump({
                    "keyword": keyword,
                    "city": city,
                    "platform_filters": filters,
                    "search_combination_id": combination_id,
                    "is_baseline": True,
                    "depth": 0,
                    "low_yield_streak": 0,
                    "low_novelty_streak": 0,
                    "low_qualified_yield_streak": 0,
                }),
                now,
                now,
            ),
        )
    smart_captures.create_smart_capture(
        db,
        source="task_cockpit",
        workflow_run_id=workflow_run_id,
        search_config=payload,
        target_count=candidate_target,
    )
    snapshot = _create_search_context_snapshot(
        db, workflow_run_id, strategy, recommendation_strategy, contract,
        int(payload.get("context_soft_budget_characters") or 12000),
    )
    if snapshot["status"] == "blocked":
        _wait_for_user(db, workflow_run_id, "context_budget_exceeded", str(snapshot["blocker_reason"]))
    return get_workflow_run(db, workflow_run_id)


def advance_deep_job_search(db: Database, config: AppConfig, workflow_run_id: str) -> dict[str, object]:
    """推进一个采集批次；Codex 只在候选池准备完成后参与。"""
    run = _require_run(db, workflow_run_id)
    if bool(run["paused"]):
        _advance_prefetch(db, config, workflow_run_id, allow_start=False)
        return get_workflow_run(db, workflow_run_id)
    if run["status"] in {"waiting_codex", "waiting_for_user"}:
        # Prefetch 是旁路调度；这里只轮询/启动已获准的下一批 JD，不改变主 Run 状态机。
        _advance_prefetch(db, config, workflow_run_id)
        refreshed = _require_run(db, workflow_run_id)
        if (
            refreshed["status"] == "waiting_codex"
            and str(refreshed["next_action"] or "") == "wait_prefetch"
        ):
            _continue_after_prefetch_wait(db, config, workflow_run_id)
        return get_workflow_run(db, workflow_run_id)
    if run["status"] in {"completed", "cancelled", "failed", "waiting_codex", "waiting_for_user"}:
        _abandon_prefetch_batches(db, workflow_run_id)
        return get_workflow_run(db, workflow_run_id)
    contract = _load(run["completion_contract_json"], {})
    jd_task = _next_jd_task(db, workflow_run_id)
    if jd_task is not None:
        return _advance_jd_collection(db, config, workflow_run_id, jd_task, contract)
    task = _next_task(db, workflow_run_id)
    if task is None:
        _wait_for_user(
            db,
            workflow_run_id,
            "approved_search_space_exhausted",
            "已耗尽本轮批准的搜索词、城市与合理平台组合，等待你调整搜索范围。",
        )
        return get_workflow_run(db, workflow_run_id)
    payload = _load(task["payload_json"], {})
    capture_task_id = str(task["operation_ref_id"] or "")
    if not capture_task_id:
        if not boss_scraper_service.get_browser_status().running:
            _wait_for_user(db, workflow_run_id, "browser_not_running", "FineJob 专用 Chrome 未启动，暂不能继续 deep_job_search。")
            return get_workflow_run(db, workflow_run_id)
        pages = min(int(contract["stop_policy"]["min_depth"]), 10)
        platform_filters = canonicalize_platform_filters(payload.get("platform_filters"))
        _mark_search_combination_started(
            db,
            workflow_run_id,
            str(payload.get("search_combination_id") or ""),
        )
        linked_capture = smart_captures.get_by_workflow_run(db, workflow_run_id)
        if linked_capture is None:
            linked_capture = smart_captures.create_smart_capture(
                db,
                source="task_cockpit",
                workflow_run_id=workflow_run_id,
                search_config=contract,
                target_count=int(contract["stop_policy"]["candidate_target_count"]),
            )
        active_capture = smart_captures.get_active_smart_capture(db)
        if (
            active_capture is not None
            and str(active_capture["smart_capture_id"]) != str(linked_capture["smart_capture_id"])
        ):
            _mark_task_waiting_for_user(db, task["id"], payload)
            _wait_for_user(
                db,
                workflow_run_id,
                "collection_task_active",
                "当前存在独立岗位采集任务，停止该任务后可继续驾驶舱采集。",
            )
            return get_workflow_run(db, workflow_run_id)
        capture = boss_capture_task_manager.start_capture(
            BossCaptureRequest(
                keyword=str(payload["keyword"]), city=str(payload["city"]), pages=pages,
                filters=platform_filters, include_details=False, max_details=None,
                output_dir=config.output_root / "fine-job" / "boss-capture",
                prefer_current_page=True,
                force_search_navigation=not bool(payload.get("is_baseline")),
                filter_strategy_id=str(contract["selected_strategy_ids"]["filter_strategy_id"]),
                capture_source="smart",
                workflow_run_id=workflow_run_id,
                smart_capture_id=str(linked_capture["smart_capture_id"]),
            ), output_dir=config.output_root / "fine-job" / "boss-capture", db=db,
        )
        smart_captures.bind_batch(
            db,
            str(linked_capture["smart_capture_id"]),
            str(capture["id"]),
        )
        _update_task_operation(db, task["id"], str(capture["id"]), "running", payload)
        _update_run(db, workflow_run_id, status="running", current_step="searching", next_action="wait_capture", next_action_reason="正在执行后端岗位采集。")
        return get_workflow_run(db, workflow_run_id)
    try:
        capture = boss_capture_task_manager.get_task(capture_task_id)
    except AppError:
        _mark_task_waiting_for_user(db, task["id"], payload)
        _wait_for_user(db, workflow_run_id, "capture_interrupted", "采集进程已中断；确认恢复后会从当前搜索组合重新开始。")
        return get_workflow_run(db, workflow_run_id)
    if capture.get("status") in {"queued", "running"}:
        return get_workflow_run(db, workflow_run_id)
    if capture.get("status") == "failed":
        _mark_task_waiting_for_user(db, task["id"], payload)
        _wait_for_user(
            db,
            workflow_run_id,
            "capture_interrupted",
            str(capture.get("error_message") or "岗位采集失败，请继续任务后重试当前搜索组合。"),
        )
        return get_workflow_run(db, workflow_run_id)
    metrics = _record_batch(db, workflow_run_id, task, capture, contract)
    return _decide_next_step(db, config, workflow_run_id, task, capture, contract, metrics)


def get_workflow_run(db: Database, workflow_run_id: str) -> dict[str, object]:
    run = _require_run(db, workflow_run_id)
    with db.connect() as connection:
        tasks = connection.execute("SELECT * FROM fj_workflow_tasks WHERE workflow_run_id = ? ORDER BY created_at", (workflow_run_id,)).fetchall()
        snapshots = connection.execute("SELECT * FROM fj_workflow_context_snapshots WHERE workflow_run_id = ? ORDER BY created_at", (workflow_run_id,)).fetchall()
    return {
        **_serialize_run(db, run),
        "progress": _get_run_progress(db, workflow_run_id),
        "analysis_handoff": _get_analysis_handoff_summary(db, workflow_run_id),
        "prefetch": _get_prefetch_summary(db, workflow_run_id),
        "tasks": [_serialize_task(row) for row in tasks],
        "capture_jobs": list_workflow_capture_jobs(db, workflow_run_id)["items"],
        "context_snapshots": [_serialize_snapshot(row) for row in snapshots],
    }


def get_latest_workflow_run(
    db: Database, *, include_completed: bool = False
) -> dict[str, object] | None:
    """返回最近的 Workflow Run；岗位采集页面可读取已完成 Run 的岗位列表。"""
    status_filter = "" if include_completed else "WHERE status NOT IN ('completed', 'completed_with_errors', 'cancelled', 'failed')"
    with db.connect() as connection:
        row = connection.execute(
            f"SELECT * FROM fj_workflow_runs {status_filter} ORDER BY updated_at DESC, created_at DESC LIMIT 1"
        ).fetchone()
    return get_workflow_run(db, str(row["id"])) if row is not None else None


def get_latest_active_workflow_run(db: Database) -> dict[str, object] | None:
    """返回最近一个仍可继续查看或推进的 Workflow Run。"""
    return get_latest_workflow_run(db)


def get_active_collection_task(db: Database) -> dict[str, object] | None:
    """返回唯一允许存在的未结束岗位采集任务。"""
    smart_capture = smart_captures.get_active_smart_capture(db)
    if smart_capture is not None:
        return {
            "kind": "smart",
            "id": str(smart_capture["smart_capture_id"]),
            "status": str(smart_capture["status"]),
            "message": str(smart_capture.get("message") or "智能采集尚未结束。"),
        }

    task = boss_capture_task_manager.get_active_task(capture_source="custom")
    if task is None:
        return None
    return {
        "kind": "custom",
        "id": str(task["id"]),
        "status": str(task["status"]),
        "message": str(task.get("message") or "自定义采集尚未结束。"),
    }


def assert_collection_start_allowed(db: Database, *, requested_kind: str) -> None:
    """确保智能采集与自定义采集在任意入口都严格互斥。"""
    active = get_active_collection_task(db)
    if active is None:
        return
    active_label = "智能采集" if active["kind"] == "smart" else "自定义采集"
    requested_label = "智能采集" if requested_kind == "smart" else "自定义采集"
    raise AppError(
        409,
        "COLLECTION_TASK_ACTIVE",
        f"当前{active_label}尚未结束，请先停止{active_label}后再开始{requested_label}。",
    )


def list_workflow_capture_jobs(db: Database, workflow_run_id: str) -> dict[str, object]:
    """返回整个智能采集 Run 已保存的岗位，供岗位采集列表汇总展示。"""
    _require_run(db, workflow_run_id)
    with db.connect() as connection:
        rows = connection.execute(
            """
            SELECT DISTINCT job_id
            FROM fj_workflow_job_discoveries
            WHERE workflow_run_id = ?
            ORDER BY discovered_at ASC
            """,
            (workflow_run_id,),
        ).fetchall()

    items_by_id: dict[str, dict[str, object]] = {}
    for row in rows:
        try:
            item = get_capture_history_job(db, str(row["job_id"]))
            items_by_id[str(item.get("id") or item.get("job_id") or "")] = item
        except AppError:
            # 岗位可能已被用户删除；保留其余正式保存记录继续展示。
            continue

    # 当前批次尚未写入 Workflow Discovery 时，直接补入采集任务内存快照。
    with db.connect() as connection:
        task_rows = connection.execute(
            """
            SELECT operation_ref_id
            FROM fj_workflow_tasks
            WHERE workflow_run_id = ? AND task_type = 'deep_job_search'
              AND operation_ref_type = 'capture_task' AND operation_ref_id IS NOT NULL
            ORDER BY created_at ASC
            """,
            (workflow_run_id,),
        ).fetchall()
    for task_row in task_rows:
        try:
            task = boss_capture_task_manager.get_task(str(task_row["operation_ref_id"]))
        except AppError:
            continue
        for job in task.get("jobs") or []:
            if not isinstance(job, dict):
                continue
            key = str(job.get("history_record_id") or job.get("job_id") or "")
            if not key:
                continue
            # 当前采集任务包含最新详情和筛选状态，覆盖历史汇总中的旧快照。
            items_by_id[key] = dict(job)

    items = list(items_by_id.values())
    return {"items": items, "total": len(items)}


def get_context_snapshot(db: Database, workflow_run_id: str, channel: str = "deep_job_search") -> dict[str, object]:
    with db.connect() as connection:
        row = connection.execute("SELECT * FROM fj_workflow_context_snapshots WHERE workflow_run_id = ? AND channel = ?", (workflow_run_id, channel)).fetchone()
    if row is None:
        raise AppError(404, "CONTEXT_SNAPSHOT_NOT_FOUND", "本轮上下文快照不存在。")
    return _serialize_snapshot(row)


def list_workflow_analysis_items(
    db: Database, workflow_run_id: str, analysis_batch_id: str | None = None
) -> dict[str, object]:
    _require_run(db, workflow_run_id)
    with db.connect() as connection:
        rows = connection.execute(
            """
            SELECT * FROM fj_workflow_tasks
            WHERE workflow_run_id = ? AND task_type = 'deep_job_search_analysis'
            ORDER BY created_at
            """,
            (workflow_run_id,),
        ).fetchall()
        discoveries = connection.execute(
            """
            SELECT d.*, j.title, j.company_name, j.salary, j.location, j.detail_status,
                   j.payload_json
            FROM fj_workflow_job_discoveries d
            JOIN fj_boss_jobs j ON j.id = d.job_id
            WHERE d.workflow_run_id = ?
            ORDER BY d.discovered_at, d.job_id
            """,
            (workflow_run_id,),
        ).fetchall()
        feedback_rows = connection.execute(
            """
            SELECT * FROM fj_workflow_evaluation_feedback
            WHERE workflow_run_id = ? ORDER BY created_at DESC
            """,
            (workflow_run_id,),
        ).fetchall()
    discoveries_by_job = {str(row["job_id"]): row for row in discoveries}
    feedback_by_task: dict[str, list[dict[str, object]]] = {}
    for row in feedback_rows:
        feedback_by_task.setdefault(str(row["workflow_task_id"]), []).append(_serialize_feedback(row))
    if analysis_batch_id:
        rows = [row for row in rows if _analysis_batch_id(row, workflow_run_id) == analysis_batch_id]
    return {
        "workflow_run_id": workflow_run_id,
        "analysis_batch_id": analysis_batch_id or str(_get_analysis_handoff_summary(db, workflow_run_id).get("analysis_batch_id") or ""),
        "analysis_handoff": _get_analysis_handoff_summary(db, workflow_run_id),
        "items": [
            _serialize_analysis_item(
                row,
                discoveries_by_job.get(str(_load(row["payload_json"], {}).get("job_id") or "")),
                feedback_by_task.get(str(row["id"]), []),
            )
            for row in rows
        ],
    }


def save_workflow_analysis_feedback(
    db: Database,
    workflow_run_id: str,
    workflow_task_id: str,
    payload: dict[str, object],
) -> dict[str, object]:
    """保存用户对本条评估的反馈，不自动改变任何正式策略。"""
    _require_run(db, workflow_run_id)
    with db.connect() as connection:
        item = connection.execute(
            """
            SELECT result_json FROM fj_workflow_tasks
            WHERE id = ? AND workflow_run_id = ? AND task_type = 'deep_job_search_analysis'
            """,
            (workflow_task_id, workflow_run_id),
        ).fetchone()
    if item is None:
        raise AppError(404, "WORKFLOW_ANALYSIS_ITEM_NOT_FOUND", "Workflow 分析 Item 不存在。")
    result = _load(item["result_json"], {})
    feedback_id = new_id()
    with db.connect() as connection:
        connection.execute(
            """
            INSERT INTO fj_workflow_evaluation_feedback (
              id, workflow_run_id, workflow_task_id, evaluation_id, sentiment, reason, note, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                feedback_id,
                workflow_run_id,
                workflow_task_id,
                str(result.get("evaluation_id") or "") or None,
                str(payload["sentiment"]),
                str(payload.get("reason") or "") or None,
                str(payload.get("note") or "").strip(),
                utc_now(),
            ),
        )
    return {"feedback_id": feedback_id, "workflow_run_id": workflow_run_id, "workflow_task_id": workflow_task_id}


def update_workflow_analysis_guidance(
    db: Database, workflow_run_id: str, guidance: str
) -> dict[str, object]:
    """记录仅作用于当前 Run 的临时分析指导，后续批次会从契约读取。"""
    run = _require_run(db, workflow_run_id)
    contract = _load(run["completion_contract_json"], {})
    current = contract.get("analysis_guidance") if isinstance(contract.get("analysis_guidance"), dict) else {}
    contract["analysis_guidance"] = {
        "text": guidance.strip(),
        "version": int(current.get("version") or 0) + 1,
        "updated_at": utc_now(),
    }
    _update_run(db, workflow_run_id, completion_contract_json=_dump(contract))
    return get_workflow_run(db, workflow_run_id)


def get_workflow_analysis_item_context(
    db: Database, workflow_run_id: str, workflow_task_id: str
) -> dict[str, object]:
    run = _require_run(db, workflow_run_id)
    with db.connect() as connection:
        item = connection.execute(
            """
            SELECT * FROM fj_workflow_tasks
            WHERE id = ? AND workflow_run_id = ? AND task_type = 'deep_job_search_analysis'
            """,
            (workflow_task_id, workflow_run_id),
        ).fetchone()
    if item is None:
        raise AppError(404, "WORKFLOW_ANALYSIS_ITEM_NOT_FOUND", "Workflow 分析 Item 不存在。")
    payload = _load(item["payload_json"], {})
    job_id = str(payload.get("job_id") or "")
    job = get_capture_history_job(db, job_id)
    if str(job.get("detail_status") or "") != "completed":
        raise AppError(409, "CAPTURE_NOT_READY", "当前 Item 缺少完整 JD，不能进入 Codex 分析。")
    contract = _load(run["completion_contract_json"], {})
    strategy = get_filter_strategy(db, str(contract["selected_strategy_ids"]["filter_strategy_id"]))
    recommendation_strategy = get_recommendation_strategy(
        db, str(contract["selected_strategy_ids"]["recommendation_strategy_id"])
    )
    shared = get_context_snapshot(db, workflow_run_id, "candidate_analysis")
    sections = [
        _section("analysis_item", "analysis_item", {"workflow_task_id": workflow_task_id, "job_id": job_id}, "workflow_task", None, True, ""),
        _section("job_material", "analysis_item", _compact_job_for_analysis(job), "boss_job", int(job.get("detail_version") or 0), True, "只包含当前岗位的 JD 与必要岗位事实。"),
        _section("filter_strategy_reference", "analysis_item", {"filter_strategy_id": strategy["id"], "strategy_version": strategy.get("strategy_version")}, "filter_strategy", int(strategy.get("strategy_version") or 1), True, "筛选策略仅用于前置候选过滤。"),
        _section("recommendation_strategy_reference", "analysis_item", {"recommendation_strategy_id": recommendation_strategy["id"], "strategy_version": recommendation_strategy.get("strategy_version")}, "recommendation_strategy", int(recommendation_strategy.get("strategy_version") or 1), True, "建议投递策略是最终 recommend、review、reject 的主要业务规则。"),
        _section("shared_base_reference", "analysis_item", {"context_snapshot_id": shared["context_snapshot_id"], "profile_versions": _shared_profile_versions(shared), "analysis_guidance_version": ((contract.get("analysis_guidance") or {}).get("version"))}, "workflow_context_snapshot", None, True, "候选人紧凑事实、策略与已应用反馈请复用 Shared Base。"),
        _section("applied_feedback", "analysis_item", {"applied_feedback_ids": contract.get("applied_feedback_ids") or [], "applied_preference_ids": contract.get("applied_preference_ids") or []}, "workflow_contract", 1, True, ""),
        _section("complete_resume", "excluded", None, "resume", None, False, "单 Item 默认不注入完整简历。"),
        _section("historical_payload", "excluded", None, "boss_job", None, False, "单 Item 默认不注入历史岗位 payload。"),
        _section("unrelated_qa", "excluded", None, "profile_qa", None, False, "单 Item 默认不注入无关 QA。"),
        _section("unrelated_chat", "excluded", None, "chat", None, False, "单 Item 默认不注入无关聊天。"),
    ]
    snapshot = _save_context_snapshot(
        db,
        workflow_run_id,
        f"analysis_item:{workflow_task_id}",
        sections,
        int(shared["soft_budget_characters"]),
    )
    if snapshot["status"] == "blocked":
        raise AppError(409, "CONTEXT_SNAPSHOT_BLOCKED", str(snapshot["blocker_reason"]))
    return {
        "workflow_run_id": workflow_run_id,
        "workflow_task_id": workflow_task_id,
        "job_id": job_id,
        "shared_context_snapshot_id": shared["context_snapshot_id"],
        "item_context_snapshot": snapshot,
        "expected_output": {
            "decision": "recommend | review | reject", "confidence": "0 到 1", "summary": "简要结论",
            "hard_requirements": ["硬条件判断"], "match_dimensions": {"维度": "判断"},
            "strengths": ["匹配点"], "gaps": ["差距"], "risks": ["风险"],
            "missing_information": ["缺失信息"], "reasons": ["依据"],
            "jd_evidence": ["JD 证据"], "candidate_evidence": ["候选人证据"],
        },
    }


def record_workflow_analysis_result(
    db: Database,
    config: AppConfig,
    workflow_run_id: str,
    workflow_task_id: str,
    *,
    decision: str,
    evaluation_id: str,
    evaluation: dict[str, object],
) -> dict[str, object]:
    if decision not in {"recommend", "review", "reject"}:
        raise AppError(422, "VALIDATION_FAILED", "Workflow 分析结论无效。")
    run = _require_run(db, workflow_run_id)
    with db.connect() as connection:
        item = connection.execute(
            "SELECT * FROM fj_workflow_tasks WHERE id = ? AND workflow_run_id = ? AND task_type = 'deep_job_search_analysis'",
            (workflow_task_id, workflow_run_id),
        ).fetchone()
    if item is None:
        raise AppError(404, "WORKFLOW_ANALYSIS_ITEM_NOT_FOUND", "Workflow 分析 Item 不存在。")
    if item["status"] == "succeeded":
        return get_workflow_run(db, workflow_run_id)
    payload = _load(item["payload_json"], {})
    _require_analysis_batch_started(
        db, workflow_run_id, _analysis_batch_id_from_payload(payload, workflow_run_id)
    )
    job_id = str(payload.get("job_id") or "")
    _finish_task(
        db,
        workflow_task_id,
        "succeeded",
        payload,
        {"job_id": job_id, "decision": decision, "evaluation_id": evaluation_id, **evaluation},
    )
    _release_candidate_reservation(
        db, workflow_run_id, "formal_analysis", workflow_task_id, job_id, "released"
    )
    _complete_analysis_handoff_if_finished(
        db, workflow_run_id, _analysis_batch_id_from_payload(payload, workflow_run_id)
    )
    _advance_prefetch(db, config, workflow_run_id)
    _refresh_counts(db, workflow_run_id)
    refreshed = _require_run(db, workflow_run_id)
    contract = _load(refreshed["completion_contract_json"], {})
    target_reached = _business_target_reached(db, workflow_run_id, contract)
    analysis_policy = _analysis_policy(contract)
    batch_id = _analysis_batch_id_from_payload(payload, workflow_run_id)
    batch_complete = _is_analysis_batch_complete(db, workflow_run_id, batch_id)

    if batch_complete and analysis_policy.get("manual_batch_only"):
        _wait_for_user(
            db,
            workflow_run_id,
            "manual_analysis_batch_completed",
            "当前批量建议已完成；可从岗位列表继续选择下一批岗位交给 Codex。",
        )
        return get_workflow_run(db, workflow_run_id)

    if target_reached and not analysis_policy["analyze_all_candidates"]:
        _skip_pending_analysis_items(db, workflow_run_id)
        _complete_analysis_handoff_if_finished(db, workflow_run_id, batch_id)
        _abandon_prefetch_batches(db, workflow_run_id)
        _complete_run(db, workflow_run_id, "completion_target_reached", "已达到本轮完成目标，已保存成果并结束 Run。")
        return get_workflow_run(db, workflow_run_id)

    if target_reached and analysis_policy["analyze_all_candidates"]:
        contract = _freeze_candidate_pool(db, workflow_run_id, contract)

    # stop_after_current_batch 只消费一次；用户继续后会按既有候选优先策略继续。
    if batch_complete and _stop_after_current_batch_active(contract):
        _consume_stop_after_current_batch(db, workflow_run_id, contract)
        _wait_for_user(
            db,
            workflow_run_id,
            "analysis_batch_completed_waiting_user",
            "当前 Analysis Batch 已完成；已保留成果，等待你点击继续后再处理下一批或恢复搜索。",
        )
        return get_workflow_run(db, workflow_run_id)

    if batch_complete and _execution_policy_after_analysis_batch(contract) == "wait_for_user":
        _wait_for_user(
            db,
            workflow_run_id,
            "analysis_batch_waiting_user",
            "当前 Analysis Batch 已完成；等待你点击继续后再处理下一批或恢复搜索。",
        )
        return get_workflow_run(db, workflow_run_id)

    if _next_analysis_task(db, workflow_run_id) is not None:
        _update_run(db, workflow_run_id, status="waiting_codex", current_step="waiting_codex", next_action="codex_analysis", next_action_reason="当前 JD 批次仍有待保存的岗位分析 Item。")
        return get_workflow_run(db, workflow_run_id)
    promotion = _promote_ready_prefetch(db, workflow_run_id, contract)
    if promotion == "promoted":
        return get_workflow_run(db, workflow_run_id)
    if promotion == "waiting":
        _wait_for_prefetch(db, workflow_run_id)
        return get_workflow_run(db, workflow_run_id)
    if target_reached and analysis_policy["analyze_all_candidates"]:
        return _continue_frozen_candidate_pool(db, config, workflow_run_id, contract)
    if _create_jd_tasks(db, workflow_run_id, int(contract["stop_policy"]["candidate_target_count"])):
        return advance_deep_job_search(db, config, workflow_run_id)
    _resume_search_or_wait(db, config, workflow_run_id)
    return get_workflow_run(db, workflow_run_id)


def attach_codex_session(db: Database, workflow_run_id: str, codex_session_ref: str, codex_runtime_id: str | None) -> dict[str, object]:
    run = _require_run(db, workflow_run_id)
    if run["status"] != "waiting_codex":
        raise AppError(409, "WORKFLOW_NOT_READY_FOR_CODEX", "候选池与 JD 尚未准备完成，不能提交 Codex 分析。")
    snapshot = get_context_snapshot(db, workflow_run_id, "candidate_analysis")
    if snapshot["status"] != "ready":
        raise AppError(409, "CONTEXT_SNAPSHOT_BLOCKED", "分析上下文尚未通过预算检查。")
    _update_run(db, workflow_run_id, codex_session_ref=codex_session_ref, codex_runtime_id=codex_runtime_id or "", current_step="waiting_codex", next_action="codex_analysis", next_action_reason="候选池与 JD 准备完成后可提交 Codex 分析。")
    return get_workflow_run(db, workflow_run_id)


def claim_workflow_analysis_handoff(
    db: Database,
    workflow_run_id: str,
    *,
    codex_session_ref: str,
    codex_runtime_id: str | None,
    handoff_kind: str,
    retry_handoff_attempt_id: str | None = None,
) -> dict[str, object]:
    """原子占用当前分析批次；占用本身不改变岗位分析 Item 的业务状态。"""
    run = _require_run(db, workflow_run_id)
    if run["status"] != "waiting_codex":
        raise AppError(409, "WORKFLOW_NOT_READY_FOR_CODEX", "当前 Workflow 不在等待 Codex 分析状态。")
    snapshot = get_context_snapshot(db, workflow_run_id, "candidate_analysis")
    if snapshot["status"] != "ready":
        raise AppError(409, "CONTEXT_SNAPSHOT_BLOCKED", "分析上下文尚未通过预算检查。")
    summary = _get_analysis_handoff_summary(db, workflow_run_id)
    batch_id = str(summary.get("analysis_batch_id") or "")
    if not batch_id or int(summary.get("pending_item_count") or 0) <= 0:
        raise AppError(409, "WORKFLOW_ANALYSIS_BATCH_NOT_READY", "当前没有可交接的 pending 分析批次。")
    is_retry = bool(retry_handoff_attempt_id)
    if handoff_kind == "initial" and not bool(summary.get("needs_initial_codex_handoff")) and not is_retry:
        raise AppError(409, "WORKFLOW_ANALYSIS_HANDOFF_NOT_INITIAL", "当前批次不是首次 Codex 交接。")
    if handoff_kind == "next" and not bool(summary.get("needs_next_batch_handoff")) and not is_retry:
        raise AppError(409, "WORKFLOW_ANALYSIS_HANDOFF_NOT_NEXT", "当前没有可继续的后续分析批次。")

    now = utc_now()
    attempt_id = new_id()
    with db.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        rows = connection.execute(
            "SELECT * FROM fj_workflow_tasks WHERE workflow_run_id = ? AND task_type = 'deep_job_search_analysis' ORDER BY created_at",
            (workflow_run_id,),
        ).fetchall()
        pending = [
            row for row in rows
            if row["status"] == "pending" and _analysis_batch_id(row, workflow_run_id) == batch_id
        ]
        if not pending:
            raise AppError(409, "WORKFLOW_ANALYSIS_BATCH_NOT_READY", "当前分析批次已被处理或交接。")
        existing = connection.execute(
            "SELECT * FROM fj_workflow_analysis_handoffs WHERE workflow_run_id = ? AND analysis_batch_id = ?",
            (workflow_run_id, batch_id),
        ).fetchone()
        if existing is not None:
            existing_attempt_status = str(existing["attempt_status"] or "claimed")
            can_retry = (
                existing_attempt_status == "prompt_written"
                and str(existing["handoff_attempt_id"] or "") == str(retry_handoff_attempt_id or "")
                and _start_ack_timed_out(existing)
            )
            if not can_retry and existing_attempt_status in {"claimed", "prompt_written", "started"}:
                raise AppError(409, "WORKFLOW_ANALYSIS_BATCH_IN_PROGRESS", "当前分析批次已有有效 Codex 交接。")
        if existing is None:
            connection.execute(
                """
                INSERT INTO fj_workflow_analysis_handoffs (
                  workflow_run_id, analysis_batch_id, status, handoff_attempt_id, attempt_status,
                  codex_session_ref, codex_runtime_id, claimed_at
                ) VALUES (?, ?, 'claimed', ?, 'claimed', ?, ?, ?)
                """,
                (workflow_run_id, batch_id, attempt_id, codex_session_ref, codex_runtime_id or "", now),
            )
        else:
            connection.execute(
                """
                UPDATE fj_workflow_analysis_handoffs
                SET status = 'claimed', handoff_attempt_id = ?, attempt_status = 'claimed',
                    codex_session_ref = ?, codex_runtime_id = ?, claimed_at = ?, submitted_at = NULL,
                    prompt_written_at = NULL, started_at = NULL, released_at = NULL, completed_at = NULL,
                    recovered_at = ?, recovery_reason = ?
                WHERE workflow_run_id = ? AND analysis_batch_id = ?
                """,
                (
                    attempt_id,
                    codex_session_ref,
                    codex_runtime_id or "",
                    now,
                    now if is_retry else None,
                    "start_ack_timeout_retry" if is_retry else "",
                    workflow_run_id,
                    batch_id,
                ),
            )
    _update_run(
        db,
        workflow_run_id,
        codex_session_ref=codex_session_ref,
        codex_runtime_id=codex_runtime_id or "",
        current_step="waiting_codex",
        next_action="codex_analysis",
        next_action_reason="Codex 已占用当前岗位分析批次，等待写入 Prompt。",
    )
    return get_workflow_run(db, workflow_run_id)


def mark_workflow_analysis_handoff_prompt_written(
    db: Database,
    workflow_run_id: str,
    analysis_batch_id: str,
    handoff_attempt_id: str,
    codex_session_ref: str,
) -> dict[str, object]:
    """记录 Prompt 已写入终端；业务开始仍由 Codex ACK 决定。"""
    with db.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        handoff = connection.execute(
            "SELECT * FROM fj_workflow_analysis_handoffs WHERE workflow_run_id = ? AND analysis_batch_id = ?",
            (workflow_run_id, analysis_batch_id),
        ).fetchone()
        if (
            handoff is None
            or str(handoff["codex_session_ref"] or "") != codex_session_ref
            or str(handoff["handoff_attempt_id"] or "") != handoff_attempt_id
        ):
            raise AppError(409, "WORKFLOW_ANALYSIS_HANDOFF_STALE", "当前 Codex 交接已失效，不能确认 Prompt 写入。")
        if handoff["attempt_status"] == "claimed":
            connection.execute(
                """
                UPDATE fj_workflow_analysis_handoffs
                SET status = 'submitted', attempt_status = 'prompt_written', submitted_at = ?, prompt_written_at = ?
                WHERE workflow_run_id = ? AND analysis_batch_id = ?
                """,
                (utc_now(), utc_now(), workflow_run_id, analysis_batch_id),
            )
        elif handoff["attempt_status"] not in {"prompt_written", "started"}:
            raise AppError(409, "WORKFLOW_ANALYSIS_HANDOFF_STALE", "当前 Codex 交接已失效，不能确认 Prompt 写入。")
    _update_run(
        db,
        workflow_run_id,
        next_action_reason="Prompt 已写入 Codex 终端，等待当前交接尝试确认开始。",
    )
    return get_workflow_run(db, workflow_run_id)


def release_workflow_analysis_handoff(
    db: Database,
    workflow_run_id: str,
    analysis_batch_id: str,
    handoff_attempt_id: str,
    codex_session_ref: str,
    release_reason: str | None = None,
) -> dict[str, object]:
    """按 transport 失败或用户完整重试释放当前交接尝试。"""
    with db.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        handoff = connection.execute(
            "SELECT * FROM fj_workflow_analysis_handoffs WHERE workflow_run_id = ? AND analysis_batch_id = ?",
            (workflow_run_id, analysis_batch_id),
        ).fetchone()
        if (
            handoff is None
            or str(handoff["codex_session_ref"] or "") != codex_session_ref
            or str(handoff["handoff_attempt_id"] or "") != handoff_attempt_id
        ):
            raise AppError(409, "WORKFLOW_ANALYSIS_HANDOFF_STALE", "当前批次没有可释放的有效 Codex 交接。")
        can_full_retry = handoff["attempt_status"] == "prompt_written" and release_reason == "full_retry"
        if handoff["attempt_status"] != "claimed" and not can_full_retry:
            raise AppError(409, "WORKFLOW_ANALYSIS_HANDOFF_ALREADY_SUBMITTED", "当前批次已提交给 Codex，不能按失败释放。")
        connection.execute(
            """
            UPDATE fj_workflow_analysis_handoffs
            SET status = 'released', attempt_status = 'released', released_at = ?, recovery_reason = ?
            WHERE workflow_run_id = ? AND analysis_batch_id = ?
            """,
            (utc_now(), release_reason or "transport_failure", workflow_run_id, analysis_batch_id),
        )
    return get_workflow_run(db, workflow_run_id)


def ack_workflow_analysis_batch_started(
    db: Database,
    workflow_run_id: str,
    analysis_batch_id: str,
    handoff_attempt_id: str,
    config: AppConfig | None = None,
) -> dict[str, object]:
    """仅由当前有效交接尝试确认 Codex 已开始处理分析批次。"""
    with db.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        handoff = connection.execute(
            "SELECT * FROM fj_workflow_analysis_handoffs WHERE workflow_run_id = ? AND analysis_batch_id = ?",
            (workflow_run_id, analysis_batch_id),
        ).fetchone()
        if handoff is None or str(handoff["handoff_attempt_id"] or "") != handoff_attempt_id:
            raise AppError(409, "WORKFLOW_ANALYSIS_HANDOFF_STALE", "当前 Codex 交接尝试已失效，不能确认开始。")
        attempt_status = str(handoff["attempt_status"] or "")
        if attempt_status == "started":
            return get_workflow_run(db, workflow_run_id)
        if attempt_status != "prompt_written":
            raise AppError(409, "WORKFLOW_ANALYSIS_HANDOFF_NOT_READY", "Prompt 尚未写入或当前交接已结束，不能确认开始。")
        rows = connection.execute(
            "SELECT status, payload_json FROM fj_workflow_tasks WHERE workflow_run_id = ? AND task_type = 'deep_job_search_analysis'",
            (workflow_run_id,),
        ).fetchall()
        has_active_item = any(
            _analysis_batch_id_from_payload(_load(row["payload_json"], {}), workflow_run_id) == analysis_batch_id
            and row["status"] in {"pending", "running"}
            for row in rows
        )
        if not has_active_item:
            raise AppError(409, "WORKFLOW_ANALYSIS_BATCH_FINISHED", "当前分析批次已完成，不能重新启动。")
        connection.execute(
            """
            UPDATE fj_workflow_analysis_handoffs
            SET status = 'submitted', attempt_status = 'started', started_at = ?
            WHERE workflow_run_id = ? AND analysis_batch_id = ? AND handoff_attempt_id = ?
            """,
            (utc_now(), workflow_run_id, analysis_batch_id, handoff_attempt_id),
        )
    _update_run(
        db,
        workflow_run_id,
        current_step="waiting_codex",
        next_action="codex_analysis",
        next_action_reason="Codex 已确认开始当前岗位分析批次。",
    )
    if config is not None:
        _ensure_prefetch_batch(db, config, workflow_run_id, analysis_batch_id)
        _advance_prefetch(db, config, workflow_run_id)
    return get_workflow_run(db, workflow_run_id)


def resume_deep_job_search_run(
    db: Database,
    config: AppConfig,
    workflow_run_id: str,
    *,
    sync_capture: bool = True,
) -> dict[str, object]:
    """仅在用户确认后恢复中断的采集组合，避免后台静默重复采集。"""
    run = _require_run(db, workflow_run_id)
    if sync_capture:
        linked_capture = smart_captures.get_by_workflow_run(db, workflow_run_id)
        if linked_capture is not None and str(linked_capture["status"]) in {
            "paused", "pausing", "waiting_next_batch", "interrupted"
        }:
            smart_captures.resume_smart_capture(
                db,
                config,
                str(linked_capture["smart_capture_id"]),
                sync_workflow=False,
            )
    if bool(run["paused"]):
        refreshed_capture = smart_captures.get_by_workflow_run(db, workflow_run_id)
        if refreshed_capture is not None and str(refreshed_capture["status"]) == "interrupted":
            with db.connect() as connection:
                connection.execute(
                    """
                    UPDATE fj_workflow_tasks
                    SET status = 'pending', operation_ref_type = NULL, operation_ref_id = NULL,
                        updated_at = ?
                    WHERE workflow_run_id = ? AND task_type = 'deep_job_search'
                      AND status IN ('running', 'waiting_for_user')
                    """,
                    (utc_now(), workflow_run_id),
                )
            _update_run(
                db,
                workflow_run_id,
                status="pending",
                current_step="resume_requested",
                next_action="restart_current_combination",
                next_action_reason="采集执行已中断，将重新开始当前搜索组合。",
                waiting_for_user=0,
                stop_reason="",
                paused=0,
                paused_from_next_action="",
                paused_from_next_action_reason="",
            )
            return get_workflow_run(db, workflow_run_id)
        _update_run(
            db,
            workflow_run_id,
            paused=0,
            next_action=str(run["paused_from_next_action"] or "continue_workflow"),
            next_action_reason=str(run["paused_from_next_action_reason"] or "已恢复 Workflow 自动推进。"),
            paused_from_next_action="",
            paused_from_next_action_reason="",
        )
        _advance_prefetch(db, config, workflow_run_id)
        refreshed = _require_run(db, workflow_run_id)
        if (
            refreshed["status"] == "waiting_codex"
            and str(refreshed["next_action"] or "") == "wait_prefetch"
        ):
            _continue_after_prefetch_wait(db, config, workflow_run_id)
        return get_workflow_run(db, workflow_run_id)
    if run["status"] != "waiting_for_user":
        raise AppError(409, "WORKFLOW_NOT_WAITING", "当前 Workflow Run 不在等待用户恢复状态。")
    stop_reason = str(run["stop_reason"] or "")
    if stop_reason in {"analysis_batch_completed_waiting_user", "analysis_batch_waiting_user"}:
        return _continue_after_analysis_batch(db, config, workflow_run_id)
    if stop_reason not in {"capture_interrupted", "browser_not_running", "collection_task_active"}:
        raise AppError(409, "WORKFLOW_NOT_RESUMABLE", "当前等待原因需要先调整任务范围或上下文，不能直接恢复。")
    with db.connect() as connection:
        connection.execute(
            """
            UPDATE fj_workflow_tasks
            SET status = 'pending', operation_ref_type = NULL, operation_ref_id = NULL,
                updated_at = ?
            WHERE workflow_run_id = ? AND status = 'waiting_for_user'
            """,
            (utc_now(), workflow_run_id),
        )
    _update_run(
        db,
        workflow_run_id,
        status="pending",
        current_step="resume_requested",
        next_action="restart_current_combination",
        next_action_reason="用户已确认恢复，将重新开始中断的搜索组合。",
        waiting_for_user=0,
        stop_reason="",
    )
    return get_workflow_run(db, workflow_run_id)


def pause_deep_job_search_run(
    db: Database,
    workflow_run_id: str,
    *,
    sync_capture: bool = True,
) -> dict[str, object]:
    """暂停 Workflow 自动推进，保留已启动采集和所有已获得成果。"""
    run = _require_run(db, workflow_run_id)
    if run["status"] in {"completed", "completed_with_errors", "cancelled", "failed"}:
        raise AppError(409, "WORKFLOW_NOT_PAUSABLE", "当前 Workflow Run 已结束，不能暂停。")
    if bool(run["paused"]):
        return get_workflow_run(db, workflow_run_id)
    if sync_capture:
        linked_capture = smart_captures.get_by_workflow_run(db, workflow_run_id)
        if linked_capture is not None and str(linked_capture["status"]) in smart_captures.ACTIVE_STATUSES:
            smart_captures.pause_smart_capture(
                db,
                str(linked_capture["smart_capture_id"]),
                sync_workflow=False,
            )
    _update_run(
        db,
        workflow_run_id,
        paused=1,
        paused_from_next_action=str(run["next_action"] or "continue_workflow"),
        paused_from_next_action_reason=str(run["next_action_reason"] or "已暂停 Workflow 自动推进。"),
        next_action="resume_workflow",
        next_action_reason="Workflow 已暂停；当前采集与已获得成果会保留，恢复后继续自动推进。",
    )
    return get_workflow_run(db, workflow_run_id)


def cancel_deep_job_search_run(
    db: Database,
    workflow_run_id: str,
    *,
    sync_capture: bool = True,
) -> dict[str, object]:
    """停止 Workflow，并在列表采集仍运行时请求已有采集器安全停止。"""
    run = _require_run(db, workflow_run_id)
    if run["status"] in {"completed", "completed_with_errors", "cancelled", "failed"}:
        return get_workflow_run(db, workflow_run_id)
    if sync_capture:
        linked_capture = smart_captures.get_by_workflow_run(db, workflow_run_id)
        if linked_capture is not None and str(linked_capture["status"]) not in smart_captures.TERMINAL_STATUSES:
            smart_captures.stop_smart_capture(
                db,
                str(linked_capture["smart_capture_id"]),
                sync_workflow=False,
            )
    with db.connect() as connection:
        active_tasks = connection.execute(
            """
            SELECT * FROM fj_workflow_tasks
            WHERE workflow_run_id = ? AND status IN ('pending', 'running', 'waiting_for_user')
            """,
            (workflow_run_id,),
        ).fetchall()
    for task in active_tasks:
        operation_id = str(task["operation_ref_id"] or "")
        if task["operation_ref_type"] == "capture_task" and operation_id:
            try:
                boss_capture_task_manager.stop_capture(operation_id)
            except AppError:
                # 详情采集与已结束任务继续保留其当前执行结果，Run 不再创建后续任务。
                pass
    _cancel_prefetch_batches(db, workflow_run_id)
    with db.connect() as connection:
        connection.execute(
            """
            UPDATE fj_workflow_tasks
            SET status = 'skipped', updated_at = ?, completed_at = COALESCE(completed_at, ?)
            WHERE workflow_run_id = ? AND status IN ('pending', 'running', 'waiting_for_user')
            """,
            (utc_now(), utc_now(), workflow_run_id),
        )
        connection.execute(
            """
            UPDATE fj_workflow_candidate_reservations
            SET status = 'cancelled', released_at = ?, terminal_at = ?
            WHERE workflow_run_id = ? AND status = 'reserved'
            """,
            (utc_now(), utc_now(), workflow_run_id),
        )
    _update_run(
        db,
        workflow_run_id,
        status="cancelled",
        current_step="cancelled",
        next_action="",
        next_action_reason="用户已停止 Workflow；系统不会再创建后续任务。",
        waiting_for_user=0,
        paused=0,
        paused_from_next_action="",
        paused_from_next_action_reason="",
        stop_reason="cancelled_by_user",
        completed_at=utc_now(),
    )
    return get_workflow_run(db, workflow_run_id)


def _record_batch(
    db: Database,
    workflow_run_id: str,
    task: Any,
    capture: dict[str, object],
    contract: dict[str, Any],
) -> dict[str, object]:
    strategy = get_filter_strategy(db, str(contract["selected_strategy_ids"]["filter_strategy_id"]))
    results = evaluate_filter_strategy(list(capture.get("jobs") or []), strategy)
    _jobs, results = apply_filter_exclusions(db, strategy, list(capture.get("jobs") or []), results)
    boss_capture_task_manager.apply_filter_results(str(capture["id"]), results)
    result_by_id = {str(item["job_id"]): item for item in results}
    payload = _load(task["payload_json"], {})
    combination_id = str(payload.get("search_combination_id") or "")
    if not combination_id:
        combination_id = _ensure_search_combination_for_task(
            db,
            workflow_run_id,
            payload,
        )
        payload["search_combination_id"] = combination_id
    platform_filters = canonicalize_platform_filters(payload.get("platform_filters"))
    metrics = build_metrics_for_window(list(capture.get("jobs") or []), results)
    planner_policy = contract.get("planner_policy") if isinstance(contract.get("planner_policy"), dict) else {}
    now = utc_now()
    with db.connect() as connection:
        for job in capture.get("jobs") or []:
            job_id = str(job.get("history_record_id") or "")
            source_job_id = str(job.get("job_id") or "")
            if not job_id or not source_job_id:
                continue
            is_duplicate = bool(job.get("is_previously_collected") or job.get("processing_state") == "duplicate")
            filter_result = result_by_id.get(source_job_id, {})
            first = connection.execute("SELECT 1 FROM fj_workflow_job_discoveries WHERE workflow_run_id = ? AND job_id = ? LIMIT 1", (workflow_run_id, job_id)).fetchone() is None
            connection.execute(
                """INSERT OR IGNORE INTO fj_workflow_job_discoveries (
                    id, workflow_run_id, task_id, job_id, search_keyword, city, search_combination_json,
                    scroll_depth, discovered_at, is_run_first_discovery, is_historical_duplicate,
                    is_filter_candidate
                  ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    new_id(),
                    workflow_run_id,
                    task["id"],
                    job_id,
                    payload["keyword"],
                    payload["city"],
                    _dump({
                        "search_combination_id": combination_id,
                        "platform_filters": platform_filters,
                        "filter_strategy_id": strategy["id"],
                    }),
                    int(capture.get("total_pages_loaded") or 0),
                    now,
                    int(first),
                    int(is_duplicate),
                    int(filter_result.get("status") in {"pass", "review"}),
                ),
            )
    payload["depth"] = int(capture.get("total_pages_loaded") or payload.get("depth") or 0)
    low_novelty_streak, low_qualified_yield_streak = _update_search_combination_metrics(
        db,
        combination_id,
        metrics,
        pages_seen=max(
            0,
            int(capture.get("total_pages_loaded") or 0)
            - int(_load(task["payload_json"], {}).get("depth") or 0),
        ),
        low_novelty_threshold=float(planner_policy.get("low_novelty_threshold") or 0.25),
        low_qualified_yield_threshold=float(planner_policy.get("low_qualified_yield_threshold") or 0.15),
    )
    metrics_dict = metrics.as_dict()
    metrics_dict["low_novelty_streak"] = low_novelty_streak
    metrics_dict["low_qualified_yield_streak"] = low_qualified_yield_streak
    payload["low_yield_streak"] = low_novelty_streak
    payload["low_novelty_streak"] = low_novelty_streak
    payload["low_qualified_yield_streak"] = low_qualified_yield_streak
    _finish_task(
        db,
        task["id"],
        "succeeded",
        payload,
        {
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
            "capture_task_id": capture["id"],
        },
    )
    _refresh_counts(db, workflow_run_id)
    return metrics_dict


def _decide_next_step(
    db: Database,
    config: AppConfig,
    workflow_run_id: str,
    task: Any,
    capture: dict[str, object],
    contract: dict[str, Any],
    metrics: dict[str, object] | None = None,
) -> dict[str, object]:
    run = _require_run(db, workflow_run_id)
    candidate_target = int(contract["stop_policy"]["candidate_target_count"])
    telemetry = _load(run["telemetry_json"], {})
    if int(telemetry.get("fresh_candidates") or 0) >= candidate_target:
        if not bool(contract.get("delivery_target_enabled", True)):
            # 关闭投递目标时冻结候选池并停在岗位列表，等待用户选择分析岗位。
            _freeze_candidate_pool(db, workflow_run_id, contract)
            smart_captures.mark_workflow_capture_completed(db, workflow_run_id)
            _wait_for_user(
                db,
                workflow_run_id,
                "candidate_target_reached_waiting_analysis",
                "候选岗位目标已达到，等待从岗位列表选择岗位交给 Codex。",
            )
            return get_workflow_run(db, workflow_run_id)
        _create_jd_tasks(db, workflow_run_id, candidate_target)
        return advance_deep_job_search(db, config, workflow_run_id)
    with db.connect() as connection:
        refreshed_task = connection.execute(
            "SELECT * FROM fj_workflow_tasks WHERE id = ?",
            (str(task["id"]),),
        ).fetchone()
    if refreshed_task is not None:
        task = refreshed_task
    payload = _load(task["payload_json"], {})
    task_result = _load(task["result_json"], {})
    window = metrics or task_result.get("metrics") or {}
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
    can_continue = bool(capture.get("continuation_available") and capture.get("has_more"))
    depth = int(payload.get("depth") or 0)
    stop = contract["stop_policy"]
    planner_policy = contract.get("planner_policy") if isinstance(contract.get("planner_policy"), dict) else {}
    combination_id = str(payload.get("search_combination_id") or "")
    combination = _get_search_combination(db, combination_id)
    current_filters = canonicalize_platform_filters(
        payload.get("platform_filters")
        or (_load(combination["platform_filters_json"], {}) if combination is not None else {})
    )
    if (
        can_continue
        and depth < int(stop["max_depth"])
        and (
            window_metrics.qualified_fresh_jobs > 0
            or (
                window_metrics.run_fresh_jobs == 0
                and window_metrics.low_novelty_streak < int(stop["low_yield_streak_limit"])
                and window_metrics.duplicate_rate
                < float(planner_policy.get("duplicate_skew_threshold") or 0.6)
            )
        )
    ):
        next_pages = min(int(stop["scroll_batch_size"]), int(stop["max_depth"]) - depth, 10)
        continued = boss_capture_task_manager.continue_capture(str(capture["id"]), pages=max(1, next_pages))
        # 续采属于同一搜索组合；建立新 Task 保存每个批次的状态与来源。
        now = utc_now()
        with db.connect() as connection:
            next_task_id = new_id()
            connection.execute(
                "INSERT INTO fj_workflow_tasks (id, workflow_run_id, task_type, status, payload_json, operation_ref_type, operation_ref_id, created_at, updated_at) VALUES (?, ?, 'deep_job_search', 'running', ?, 'capture_task', ?, ?, ?)",
                (next_task_id, workflow_run_id, _dump(payload), str(continued["id"]), now, now),
            )
        _update_run(db, workflow_run_id, status="running", current_step="searching", next_action="continue_scroll", next_action_reason="候选池尚未达到目标，当前组合仍有增量搜索预算。")
        return get_workflow_run(db, workflow_run_id)

    duplicate_distribution = _historical_duplicate_distribution(
        db,
        workflow_run_id,
        str(payload.get("keyword") or ""),
        str(payload.get("city") or ""),
    )
    attempted_filters = _list_combination_filters(
        db,
        workflow_run_id,
        str(payload.get("keyword") or ""),
        str(payload.get("city") or ""),
    )
    decision = plan_next_combination(
        current_filters=current_filters,
        strategy=get_filter_strategy(db, str(contract["selected_strategy_ids"]["filter_strategy_id"])),
        metrics=window_metrics,
        attempted_filters=attempted_filters,
        duplicate_distribution=duplicate_distribution,
        force_transition=not can_continue or depth >= int(stop["max_depth"]),
        low_yield_streak_limit=int(stop["low_yield_streak_limit"]),
        low_novelty_threshold=float(planner_policy.get("low_novelty_threshold") or 0.25),
        low_qualified_yield_threshold=float(planner_policy.get("low_qualified_yield_threshold") or 0.15),
        duplicate_skew_threshold=float(planner_policy.get("duplicate_skew_threshold") or 0.6),
        combination_safety_limit=int(planner_policy.get("combination_safety_limit") or 24),
    )
    _save_planner_decision(db, workflow_run_id, combination_id, decision)
    if decision.action != "SCOPE_EXHAUSTED":
        next_task = _create_search_combination_task(
            db,
            workflow_run_id,
            keyword=str(payload.get("keyword") or ""),
            city=str(payload.get("city") or ""),
            platform_filters=decision.platform_filters,
            parent_combination_id=combination_id or None,
            transition_action=decision.action,
            transition_reason=decision.switch_reason,
            selected_axis=decision.selected_axis,
            evidence=decision.evidence or {},
        )
        if next_task is not None:
            return advance_deep_job_search(db, config, workflow_run_id)
    _mark_search_combination_exhausted(
        db,
        combination_id,
        decision.switch_reason or "approved_platform_search_space_exhausted",
    )
    if _create_next_approved_scope(db, workflow_run_id, payload, combination_id):
        return advance_deep_job_search(db, config, workflow_run_id)
    _wait_for_user(
        db,
        workflow_run_id,
        "approved_search_space_exhausted",
        "已耗尽本轮批准的搜索词、城市与合理平台组合，等待你调整搜索范围。",
    )
    return get_workflow_run(db, workflow_run_id)


def _get_search_combination(db: Database, combination_id: str):
    if not combination_id:
        return None
    with db.connect() as connection:
        return connection.execute(
            "SELECT * FROM fj_workflow_search_combinations WHERE id = ?",
            (combination_id,),
        ).fetchone()


def _ensure_search_combination_for_task(
    db: Database,
    workflow_run_id: str,
    payload: dict[str, object],
) -> str:
    """为旧版任务补建组合记录，保证升级后的 discovery 也能追踪来源。"""
    keyword = str(payload.get("keyword") or "")
    city = str(payload.get("city") or "")
    filters = canonicalize_platform_filters(payload.get("platform_filters"))
    identity = combination_identity(keyword, city, filters)
    with db.connect() as connection:
        existing = connection.execute(
            "SELECT id FROM fj_workflow_search_combinations WHERE workflow_run_id = ? AND identity_json = ?",
            (workflow_run_id, identity),
        ).fetchone()
        if existing is not None:
            return str(existing["id"])
        sequence = int(
            connection.execute(
                "SELECT COALESCE(MAX(sequence), 0) FROM fj_workflow_search_combinations WHERE workflow_run_id = ?",
                (workflow_run_id,),
            ).fetchone()[0]
        )
        combination_id = new_id()
        connection.execute(
            """
            INSERT INTO fj_workflow_search_combinations (
              id, workflow_run_id, keyword, city, platform_filters_json,
              identity_json, status, sequence, transition_action, transition_reason
            ) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, 'SWITCH_COMBINATION', 'legacy_task_backfill')
            """,
            (
                combination_id,
                workflow_run_id,
                keyword,
                city,
                _dump(filters),
                identity,
                sequence + 1,
            ),
        )
    return combination_id


def _mark_search_combination_started(
    db: Database,
    workflow_run_id: str,
    combination_id: str,
) -> None:
    if not combination_id:
        return
    with db.connect() as connection:
        connection.execute(
            """
            UPDATE fj_workflow_search_combinations
            SET status = 'running', started_at = COALESCE(started_at, ?)
            WHERE id = ? AND workflow_run_id = ? AND status IN ('pending', 'running')
            """,
            (utc_now(), combination_id, workflow_run_id),
        )


def _update_search_combination_metrics(
    db: Database,
    combination_id: str,
    metrics: SearchWindowMetrics,
    *,
    pages_seen: int,
    low_novelty_threshold: float = 0.25,
    low_qualified_yield_threshold: float = 0.15,
) -> tuple[int, int]:
    if not combination_id:
        return metrics.low_novelty_streak, metrics.low_qualified_yield_streak
    with db.connect() as connection:
        row = connection.execute(
            "SELECT * FROM fj_workflow_search_combinations WHERE id = ?",
            (combination_id,),
        ).fetchone()
        if row is None:
            return metrics.low_novelty_streak, metrics.low_qualified_yield_streak
        low_novelty_window = (
            metrics.jobs_seen == 0
            or metrics.novelty_yield < low_novelty_threshold
        )
        low_qualified_window = (
            metrics.run_fresh_jobs > 0
            and metrics.qualified_novelty_yield < low_qualified_yield_threshold
        )
        low_novelty = int(row["low_novelty_streak"] or 0) + 1 if low_novelty_window else 0
        low_qualified = int(row["low_qualified_yield_streak"] or 0) + 1 if low_qualified_window else 0
        jobs_seen = int(row["jobs_seen"] or 0) + metrics.jobs_seen
        fresh = int(row["run_fresh_jobs"] or 0) + metrics.run_fresh_jobs
        qualified = int(row["qualified_fresh_jobs"] or 0) + metrics.qualified_fresh_jobs
        duplicates = int(row["historical_duplicates"] or 0) + metrics.historical_duplicates
        connection.execute(
            """
            UPDATE fj_workflow_search_combinations
            SET batch_count = batch_count + 1,
                pages_seen = pages_seen + ?,
                jobs_seen = ?,
                run_fresh_jobs = ?,
                historical_duplicates = ?,
                cooldown_excluded = cooldown_excluded + ?,
                strategy_pass = strategy_pass + ?,
                strategy_review = strategy_review + ?,
                strategy_reject = strategy_reject + ?,
                qualified_fresh_jobs = ?,
                candidate_jobs = candidate_jobs + ?,
                novelty_yield = ?,
                qualified_novelty_yield = ?,
                duplicate_rate = ?,
                low_novelty_streak = ?,
                low_qualified_yield_streak = ?
            WHERE id = ?
            """,
            (
                max(0, int(pages_seen)),
                jobs_seen,
                fresh,
                duplicates,
                metrics.cooldown_excluded,
                metrics.strategy_pass,
                metrics.strategy_review,
                metrics.strategy_reject,
                qualified,
                metrics.candidate_jobs,
                round(fresh / jobs_seen, 4) if jobs_seen else 0,
                round(qualified / fresh, 4) if fresh else 0,
                round(duplicates / jobs_seen, 4) if jobs_seen else 0,
                low_novelty,
                low_qualified,
                combination_id,
            ),
        )
    return low_novelty, low_qualified


def _historical_duplicate_distribution(
    db: Database,
    workflow_run_id: str,
    keyword: str,
    city: str,
) -> dict[str, dict[str, int]]:
    """统计当前 Scope 历史重复岗位在可映射维度上的分布。"""
    with db.connect() as connection:
        rows = connection.execute(
            """
            SELECT j.company_scale, j.company_stage, j.company_industry,
                   j.experience, j.degree, j.salary
            FROM fj_workflow_job_discoveries d
            JOIN fj_boss_jobs j ON j.id = d.job_id
            WHERE d.workflow_run_id = ?
              AND d.search_keyword = ?
              AND d.city = ?
              AND d.is_historical_duplicate = 1
            """,
            (workflow_run_id, keyword, city),
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
            if not value:
                continue
            # 经验、学历等字段已经是 BOSS 映射的展示值；薪资取粗 bucket 文本。
            code = _planner_value_code(axis, value)
            if code:
                distribution[axis][code] = distribution[axis].get(code, 0) + 1
    return distribution


def _planner_value_code(axis: str, value: str) -> str | None:
    from backend.app.services.fine_job.adaptive_search_planner import platform_filter_code

    return platform_filter_code(axis, value)


def _list_combination_filters(
    db: Database,
    workflow_run_id: str,
    keyword: str,
    city: str,
) -> list[dict[str, str]]:
    with db.connect() as connection:
        rows = connection.execute(
            """
            SELECT platform_filters_json
            FROM fj_workflow_search_combinations
            WHERE workflow_run_id = ? AND keyword = ? AND city = ?
            ORDER BY sequence
            """,
            (workflow_run_id, keyword, city),
        ).fetchall()
    return [
        canonicalize_platform_filters(_load(row["platform_filters_json"], {}))
        for row in rows
    ]


def _save_planner_decision(
    db: Database,
    workflow_run_id: str,
    combination_id: str,
    decision,
) -> None:
    if not combination_id:
        return
    evidence = decision.evidence or {}
    with db.connect() as connection:
        connection.execute(
            """
            UPDATE fj_workflow_search_combinations
            SET transition_reason = CASE WHEN ? <> '' THEN ? ELSE transition_reason END,
                selected_axis = CASE WHEN ? <> '' THEN ? ELSE selected_axis END,
                evidence_json = ?
            WHERE id = ? AND workflow_run_id = ?
            """,
            (
                decision.switch_reason,
                decision.switch_reason,
                decision.selected_axis,
                decision.selected_axis,
                _dump(evidence),
                combination_id,
                workflow_run_id,
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
            WHERE id = ? AND status IN ('pending', 'running')
            """,
            (utc_now(), stop_reason, combination_id),
        )


def _create_search_combination_task(
    db: Database,
    workflow_run_id: str,
    *,
    keyword: str,
    city: str,
    platform_filters: dict[str, str],
    parent_combination_id: str | None,
    transition_action: str,
    transition_reason: str,
    selected_axis: str,
    evidence: dict[str, object],
    is_baseline: bool = False,
) -> dict[str, str] | None:
    filters = canonicalize_platform_filters(platform_filters)
    identity = combination_identity(keyword, city, filters)
    with db.connect() as connection:
        existing = connection.execute(
            "SELECT id, status FROM fj_workflow_search_combinations WHERE workflow_run_id = ? AND identity_json = ?",
            (workflow_run_id, identity),
        ).fetchone()
        if existing is not None:
            return None
        sequence = int(
            connection.execute(
                "SELECT COALESCE(MAX(sequence), 0) FROM fj_workflow_search_combinations WHERE workflow_run_id = ?",
                (workflow_run_id,),
            ).fetchone()[0]
        )
        combination_id = new_id()
        now = utc_now()
        connection.execute(
            """
            INSERT INTO fj_workflow_search_combinations (
              id, workflow_run_id, keyword, city, platform_filters_json,
              identity_json, status, sequence, parent_combination_id,
              transition_action, transition_reason, selected_axis, evidence_json
            ) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?)
            """,
            (
                combination_id,
                workflow_run_id,
                keyword,
                city,
                _dump(filters),
                identity,
                sequence + 1,
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
              id, workflow_run_id, task_type, payload_json, result_json, created_at, updated_at
            ) VALUES (?, ?, 'deep_job_search', ?, '{}', ?, ?)
            """,
            (
                task_id,
                workflow_run_id,
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
    return {"combination_id": combination_id, "task_id": task_id}


def _create_next_approved_scope(
    db: Database,
    workflow_run_id: str,
    current_payload: dict[str, object],
    parent_combination_id: str,
) -> bool:
    run = _require_run(db, workflow_run_id)
    contract = _load(run["completion_contract_json"], {})
    keywords = [str(value) for value in contract.get("allowed_search_keywords") or []]
    cities = [str(value) for value in contract.get("allowed_cities") or []]
    current_keyword = str(current_payload.get("keyword") or "")
    current_city = str(current_payload.get("city") or "")
    scopes = [(keyword, city) for keyword in keywords for city in cities]
    try:
        current_index = scopes.index((current_keyword, current_city))
    except ValueError:
        current_index = -1
    if current_index + 1 >= len(scopes):
        return False
    next_keyword, next_city = scopes[current_index + 1]
    reason = "approved_city_next" if next_keyword == current_keyword else "approved_keyword_next"
    created = _create_search_combination_task(
        db,
        workflow_run_id,
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
    return created is not None


def _create_jd_tasks(
    db: Database,
    workflow_run_id: str,
    candidate_target: int,
    candidate_job_ids: list[str] | None = None,
) -> int:
    """按稳定发现顺序选择尚未处理的候选，创建一个小批次 JD 任务。"""
    run = _require_run(db, workflow_run_id)
    contract = _load(run["completion_contract_json"], {})
    jd_target = min(int(_analysis_policy(contract)["analysis_batch_size"]), candidate_target)
    scope_clause = ""
    scope_values: list[object] = [workflow_run_id]
    if candidate_job_ids is not None:
        if not candidate_job_ids:
            return 0
        scope_clause = f" AND d.job_id IN ({','.join('?' for _ in candidate_job_ids)})"
        scope_values.extend(candidate_job_ids)
    with db.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        candidates = connection.execute(
            f"""
            SELECT d.job_id
            FROM fj_workflow_job_discoveries d
            WHERE d.workflow_run_id = ?
              AND d.is_run_first_discovery = 1
              AND d.is_historical_duplicate = 0
              AND d.is_filter_candidate = 1
              AND NOT EXISTS (
                SELECT 1 FROM fj_workflow_tasks t
                WHERE t.workflow_run_id = d.workflow_run_id
                  AND t.task_type IN ('deep_job_search_jd', 'deep_job_search_analysis')
                  AND json_extract(t.payload_json, '$.job_id') = d.job_id
              )
              AND NOT EXISTS (
                SELECT 1 FROM fj_workflow_candidate_reservations r
                WHERE r.job_id = d.job_id
                  AND r.status = 'reserved'
              )
              AND NOT EXISTS (
                SELECT 1
                FROM fj_workflow_prefetch_items pi
                JOIN fj_workflow_prefetch_batches pb ON pb.id = pi.prefetch_batch_id
                WHERE pi.workflow_run_id = d.workflow_run_id
                  AND pi.job_id = d.job_id
                  AND pb.status IN ('preparing', 'ready', 'failed')
                  AND pi.status IN ('pending', 'collecting', 'ready', 'failed')
              )
              {scope_clause}
            ORDER BY d.discovered_at ASC, d.job_id ASC
            LIMIT ?
            """,
            (*scope_values, jd_target),
        ).fetchall()
        now = utc_now()
        batch_id = new_id()
        for candidate in candidates:
            job_id = str(candidate["job_id"])
            task_id = new_id()
            connection.execute(
                """
                INSERT INTO fj_workflow_candidate_reservations (
                  id, workflow_run_id, job_id, owner_type, owner_id, status, created_at
                ) VALUES (?, ?, ?, 'formal_jd', ?, 'reserved', ?)
                """,
                (new_id(), workflow_run_id, job_id, task_id, now),
            )
            connection.execute(
                """
                INSERT INTO fj_workflow_tasks (
                  id, workflow_run_id, task_type, payload_json, result_json, created_at, updated_at
                ) VALUES (?, ?, 'deep_job_search_jd', ?, '{}', ?, ?)
                """,
                (task_id, workflow_run_id, _dump({"job_id": job_id, "jd_batch_id": batch_id}), now, now),
            )
    if not candidates:
        return 0
    # 进入 JD 阶段后不再占用列表采集能力；后续若需补搜，绑定新批次时会重新进入运行态。
    smart_captures.mark_workflow_capture_completed(db, workflow_run_id)
    _update_run(
        db,
        workflow_run_id,
        status="running",
        current_step="collecting_jd",
        next_action="collect_jd",
        next_action_reason="新岗位候选池已达到目标，正在按发现顺序补齐一小批岗位 JD。",
    )
    return len(candidates)


def _advance_jd_collection(
    db: Database,
    config: AppConfig,
    workflow_run_id: str,
    task: Any,
    contract: dict[str, Any],
) -> dict[str, object]:
    payload = _load(task["payload_json"], {})
    job_id = str(payload.get("job_id") or "")
    if not job_id:
        _finish_task(db, task["id"], "failed", payload, {"error": "JD 任务缺少岗位标识。"})
        return advance_deep_job_search(db, config, workflow_run_id)
    detail_task_id = str(task["operation_ref_id"] or "")
    if not detail_task_id:
        job = get_capture_history_job(db, job_id)
        if str(job.get("detail_status") or "") == "completed":
            _finish_task(db, task["id"], "succeeded", payload, {"job_id": job_id, "reused_detail": True})
            _release_candidate_reservation(
                db, workflow_run_id, "formal_jd", str(task["id"]), job_id, "released"
            )
            if _next_jd_task(db, workflow_run_id) is not None:
                return advance_deep_job_search(db, config, workflow_run_id)
            _finish_jd_collection(db, config, workflow_run_id, contract)
            return get_workflow_run(db, workflow_run_id)
        if not boss_scraper_service.get_browser_status().running:
            _wait_for_user(db, workflow_run_id, "browser_not_running", "候选池已形成，但浏览器未启动，暂不能采集岗位 JD。")
            return get_workflow_run(db, workflow_run_id)
        detail_task = boss_capture_task_manager.start_history_detail(
            job,
            output_dir=config.output_root / "fine-job" / "boss-capture",
            db=db,
        )
        _update_task_operation(db, task["id"], str(detail_task["id"]), "running", payload)
        _update_run(db, workflow_run_id, status="running", current_step="collecting_jd", next_action="wait_jd", next_action_reason="正在采集候选岗位 JD。")
        return get_workflow_run(db, workflow_run_id)
    try:
        detail_task = boss_capture_task_manager.get_task(detail_task_id)
    except AppError:
        _mark_task_waiting_for_user(db, task["id"], payload)
        _wait_for_user(db, workflow_run_id, "capture_interrupted", "JD 采集进程已中断；确认恢复后会重新采集当前候选岗位详情。")
        return get_workflow_run(db, workflow_run_id)
    if detail_task.get("status") in {"queued", "running"}:
        return get_workflow_run(db, workflow_run_id)
    if detail_task.get("status") == "failed":
        _finish_task(db, task["id"], "failed", payload, {"job_id": job_id, "error": detail_task.get("error_message")})
    else:
        job = get_capture_history_job(db, job_id)
        if str(job.get("detail_status") or "") == "completed":
            _finish_task(db, task["id"], "succeeded", payload, {"job_id": job_id, "detail_version": job.get("detail_version")})
        else:
            _finish_task(db, task["id"], "failed", payload, {"job_id": job_id, "error": "JD 采集没有返回完整详情。"})
    _release_candidate_reservation(
        db, workflow_run_id, "formal_jd", str(task["id"]), job_id,
        "released" if detail_task.get("status") != "failed" else "failed",
    )
    if _next_jd_task(db, workflow_run_id) is not None:
        return advance_deep_job_search(db, config, workflow_run_id)
    _finish_jd_collection(db, config, workflow_run_id, contract)
    return get_workflow_run(db, workflow_run_id)


def _finish_jd_collection(
    db: Database, config: AppConfig, workflow_run_id: str, contract: dict[str, Any]
) -> None:
    with db.connect() as connection:
        rows = connection.execute(
            "SELECT * FROM fj_workflow_tasks WHERE workflow_run_id = ? AND task_type = 'deep_job_search_jd' ORDER BY created_at",
            (workflow_run_id,),
        ).fetchall()
    succeeded = [row for row in rows if row["status"] == "succeeded" and not _analysis_exists_for_job(db, workflow_run_id, str(_load(row["payload_json"], {}).get("job_id") or ""))]
    if succeeded:
        _create_analysis_tasks(db, workflow_run_id, succeeded, contract)
        return
    frozen_scope = _frozen_candidate_job_ids(contract)
    if _create_jd_tasks(
        db,
        workflow_run_id,
        int(contract["stop_policy"]["candidate_target_count"]),
        frozen_scope,
    ):
        advance_deep_job_search(db, config, workflow_run_id)
        return
    if frozen_scope is not None:
        _complete_run(
            db,
            workflow_run_id,
            "frozen_candidate_pool_analyzed",
            "目标达成时冻结的候选池已全部完成分析。",
        )
        return
    _resume_search_or_wait(db, config, workflow_run_id)


def _create_analysis_tasks(
    db: Database,
    workflow_run_id: str,
    jd_rows: list[Any],
    contract: dict[str, Any],
) -> None:
    job_ids = [str(_load(row["payload_json"], {}).get("job_id") or "") for row in jd_rows]
    snapshot = _create_candidate_analysis_snapshot(db, workflow_run_id, contract)
    if snapshot["status"] == "blocked":
        _wait_for_user(db, workflow_run_id, "context_budget_exceeded", str(snapshot["blocker_reason"]))
        return
    now = utc_now()
    analysis_batch_id = new_id()
    with db.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        for jd_row, job_id in zip(jd_rows, job_ids, strict=True):
            if not job_id:
                continue
            analysis_task_id = new_id()
            connection.execute(
                """
                UPDATE fj_workflow_candidate_reservations
                SET status = 'released', released_at = ?, terminal_at = ?
                WHERE workflow_run_id = ? AND owner_type = 'formal_jd'
                  AND owner_id = ? AND job_id = ? AND status = 'reserved'
                """,
                (now, now, workflow_run_id, str(jd_row["id"]), job_id),
            )
            connection.execute(
                """
                INSERT INTO fj_workflow_candidate_reservations (
                  id, workflow_run_id, job_id, owner_type, owner_id, status, created_at
                ) VALUES (?, ?, ?, 'formal_analysis', ?, 'reserved', ?)
                """,
                (new_id(), workflow_run_id, job_id, analysis_task_id, now),
            )
            connection.execute(
                """
                INSERT INTO fj_workflow_tasks (
                  id, workflow_run_id, task_type, payload_json, result_json, created_at, updated_at
                ) VALUES (?, ?, 'deep_job_search_analysis', ?, '{}', ?, ?)
                """,
                (
                    analysis_task_id,
                    workflow_run_id,
                    _dump({
                        "job_id": job_id,
                        "jd_task_id": jd_row["id"],
                        "analysis_batch_id": analysis_batch_id,
                    }),
                    now,
                    now,
                ),
            )
    _update_run(
        db,
        workflow_run_id,
        status="waiting_codex",
        current_step="waiting_codex",
        next_action="codex_analysis",
        next_action_reason="本批岗位 JD 已准备完成，等待 Codex 逐岗位保存正式分析结果。",
    )


def create_manual_analysis_batch(
    db: Database,
    config: AppConfig,
    workflow_run_id: str,
    *,
    recommendation_strategy_id: str,
    job_ids: list[str],
    analysis_batch_size: int,
    codex_model: str | None = None,
    codex_reasoning_effort: str | None = None,
) -> dict[str, object]:
    """将候选列表中选中的岗位接入已有 Codex 分析批次。"""
    run = _require_run(db, workflow_run_id)
    contract = _load(run["completion_contract_json"], {})
    filter_strategy_id = str((contract.get("selected_strategy_ids") or {}).get("filter_strategy_id") or "")
    recommendation_strategy = _require_workflow_recommendation_strategy(
        db,
        recommendation_strategy_id=recommendation_strategy_id,
        filter_strategy_id=filter_strategy_id,
    )
    frozen_ids = set(_frozen_candidate_job_ids(contract) or [])
    if not frozen_ids:
        raise AppError(409, "CANDIDATE_POOL_NOT_READY", "候选岗位目标尚未完成，暂不能创建分析批次。")

    normalized_job_ids = list(dict.fromkeys(str(job_id).strip() for job_id in job_ids if str(job_id).strip()))
    invalid_ids = [job_id for job_id in normalized_job_ids if job_id not in frozen_ids]
    if invalid_ids:
        raise AppError(422, "CANDIDATE_NOT_IN_POOL", "只能选择本轮候选池中的岗位。")

    ready_job_ids: list[str] = []
    for job_id in normalized_job_ids:
        job = get_capture_history_job(db, job_id)
        if str(job.get("detail_status") or "") != "completed":
            raise AppError(409, "CAPTURE_NOT_READY", f"岗位 {job_id} 的详情尚未完成，不能进入 Codex 分析。")
        if not _analysis_exists_for_job(db, workflow_run_id, job_id):
            ready_job_ids.append(job_id)
    if not ready_job_ids:
        raise AppError(409, "ANALYSIS_ALREADY_CREATED", "所选岗位都已经进入分析任务。")
    ready_job_ids = ready_job_ids[: max(1, int(analysis_batch_size))]

    strategy_versions = contract.get("selected_strategy_versions")
    if not isinstance(strategy_versions, dict):
        strategy_versions = {}
    strategy_versions["recommendation_strategy_version"] = int(recommendation_strategy.get("strategy_version") or 1)
    selected_strategy_ids = contract.get("selected_strategy_ids")
    if not isinstance(selected_strategy_ids, dict):
        selected_strategy_ids = {}
    selected_strategy_ids["recommendation_strategy_id"] = recommendation_strategy_id
    contract["selected_strategy_ids"] = selected_strategy_ids
    contract["selected_strategy_versions"] = strategy_versions
    contract["delivery_target_enabled"] = True
    contract["codex_execution_config"] = {
        "model": str(codex_model or config.codex_model or ""),
        "reasoning_effort": str(codex_reasoning_effort or config.codex_reasoning_effort or "medium"),
    }
    analysis_policy = _analysis_policy(contract)
    analysis_policy["enabled"] = True
    analysis_policy["manual_batch_only"] = True
    analysis_policy["analysis_batch_size"] = min(int(analysis_batch_size), len(ready_job_ids))
    contract["analysis_policy"] = analysis_policy
    contract["recommend_target"] = max(1, len(ready_job_ids))
    contract["target_count"] = contract["recommend_target"]
    _update_run(db, workflow_run_id, completion_contract_json=_dump(contract))

    snapshot = _create_candidate_analysis_snapshot(db, workflow_run_id, contract)
    if snapshot["status"] == "blocked":
        _wait_for_user(db, workflow_run_id, "context_budget_exceeded", str(snapshot["blocker_reason"]))
        return get_workflow_run(db, workflow_run_id)

    now = utc_now()
    analysis_batch_id = new_id()
    with db.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        for job_id in ready_job_ids:
            task_id = new_id()
            connection.execute(
                """
                INSERT INTO fj_workflow_candidate_reservations (
                  id, workflow_run_id, job_id, owner_type, owner_id, status, created_at
                ) VALUES (?, ?, ?, 'formal_analysis', ?, 'reserved', ?)
                """,
                (new_id(), workflow_run_id, job_id, task_id, now),
            )
            connection.execute(
                """
                INSERT INTO fj_workflow_tasks (
                  id, workflow_run_id, task_type, status, payload_json, result_json, created_at, updated_at
                ) VALUES (?, ?, 'deep_job_search_analysis', 'pending', ?, '{}', ?, ?)
                """,
                (
                    task_id,
                    workflow_run_id,
                    _dump({"job_id": job_id, "analysis_batch_id": analysis_batch_id, "manual_batch": True}),
                    now,
                    now,
                ),
            )
    _update_run(
        db,
        workflow_run_id,
        status="waiting_codex",
        current_step="waiting_codex",
        next_action="codex_analysis",
        next_action_reason="已根据岗位列表选择创建 Codex 分析批次，等待 Codex 生成建议。",
        waiting_for_user=0,
        stop_reason="",
    )
    return get_workflow_run(db, workflow_run_id)


def _get_prefetch_summary(db: Database, workflow_run_id: str) -> dict[str, object]:
    with db.connect() as connection:
        batch = connection.execute(
            """
            SELECT * FROM fj_workflow_prefetch_batches
            WHERE workflow_run_id = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (workflow_run_id,),
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
                "created_at": None,
                "started_at": None,
                "completed_at": None,
            }
        counts = connection.execute(
            """
            SELECT
              COALESCE(SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END), 0) AS pending_count,
              COALESCE(SUM(CASE WHEN status = 'collecting' THEN 1 ELSE 0 END), 0) AS collecting_count,
              COALESCE(SUM(CASE WHEN status = 'ready' THEN 1 ELSE 0 END), 0) AS ready_count,
              COALESCE(SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END), 0) AS failed_count
            FROM fj_workflow_prefetch_items
            WHERE prefetch_batch_id = ?
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
        "created_at": batch["created_at"],
        "started_at": batch["started_at"],
        "completed_at": batch["completed_at"],
    }


def _prefetch_source_batch_id(db: Database, workflow_run_id: str) -> str:
    with db.connect() as connection:
        handoff = connection.execute(
            """
            SELECT analysis_batch_id
            FROM fj_workflow_analysis_handoffs
            WHERE workflow_run_id = ? AND attempt_status = 'started'
            ORDER BY started_at DESC
            LIMIT 1
            """,
            (workflow_run_id,),
        ).fetchone()
        if handoff is not None:
            return str(handoff["analysis_batch_id"])
        batch = connection.execute(
            """
            SELECT source_analysis_batch_id
            FROM fj_workflow_prefetch_batches
            WHERE workflow_run_id = ? AND status IN ('preparing', 'ready')
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (workflow_run_id,),
        ).fetchone()
    return str(batch["source_analysis_batch_id"]) if batch is not None else ""


def _ensure_prefetch_batch(
    db: Database,
    config: AppConfig,
    workflow_run_id: str,
    source_analysis_batch_id: str,
) -> str | None:
    run = _require_run(db, workflow_run_id)
    if bool(run["paused"]) or run["status"] in {
        "completed", "completed_with_errors", "cancelled", "failed"
    }:
        return None
    contract = _load(run["completion_contract_json"], {})
    target_count = min(
        int(_analysis_policy(contract)["analysis_batch_size"]),
        int(contract["stop_policy"]["candidate_target_count"]),
    )
    if target_count <= 0:
        return None
    with db.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        existing = connection.execute(
            """
            SELECT id FROM fj_workflow_prefetch_batches
            WHERE workflow_run_id = ? AND source_analysis_batch_id = ?
            """,
            (workflow_run_id, source_analysis_batch_id),
        ).fetchone()
        if existing is not None:
            return str(existing["id"])
        batch_id = new_id()
        now = utc_now()
        candidates = connection.execute(
            """
            SELECT d.job_id, j.detail_status
            FROM fj_workflow_job_discoveries d
            JOIN fj_boss_jobs j ON j.id = d.job_id
            WHERE d.workflow_run_id = ?
              AND d.is_run_first_discovery = 1
              AND d.is_historical_duplicate = 0
              AND d.is_filter_candidate = 1
              AND NOT EXISTS (
                SELECT 1 FROM fj_workflow_tasks t
                WHERE t.workflow_run_id = d.workflow_run_id
                  AND t.task_type IN ('deep_job_search_jd', 'deep_job_search_analysis')
                  AND json_extract(t.payload_json, '$.job_id') = d.job_id
              )
              AND NOT EXISTS (
                SELECT 1 FROM fj_workflow_candidate_reservations r
                WHERE r.job_id = d.job_id
                  AND r.status = 'reserved'
              )
            ORDER BY d.discovered_at ASC, d.job_id ASC
            LIMIT ?
            """,
            (workflow_run_id, target_count),
        ).fetchall()
        status = "preparing" if candidates else "failed"
        connection.execute(
            """
            INSERT INTO fj_workflow_prefetch_batches (
              id, workflow_run_id, source_analysis_batch_id, target_count, status,
              failure_reason, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                batch_id,
                workflow_run_id,
                source_analysis_batch_id,
                target_count,
                status,
                "" if candidates else "no_available_candidates",
                now,
                now,
            ),
        )
        for candidate in candidates:
            job_id = str(candidate["job_id"])
            item_id = new_id()
            detail_status = str(candidate["detail_status"] or "not_collected")
            connection.execute(
                """
                INSERT INTO fj_workflow_prefetch_items (
                  id, workflow_run_id, prefetch_batch_id, job_id, status,
                  lifecycle_status, detail_status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'preparing', ?, ?, ?)
                """,
                (
                    item_id,
                    workflow_run_id,
                    batch_id,
                    job_id,
                    "ready" if detail_status == "completed" else "pending",
                    detail_status,
                    now,
                    now,
                ),
            )
            connection.execute(
                """
                INSERT INTO fj_workflow_candidate_reservations (
                  id, workflow_run_id, job_id, owner_type, owner_id, status, created_at
                ) VALUES (?, ?, ?, 'prefetch', ?, 'reserved', ?)
                """,
                (new_id(), workflow_run_id, job_id, item_id, now),
            )
            if detail_status == "completed":
                connection.execute(
                    """
                    UPDATE fj_workflow_prefetch_items
                    SET lifecycle_status = 'ready', completed_at = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (now, now, item_id),
                )
        if candidates and all(str(item["detail_status"] or "") == "completed" for item in candidates):
            connection.execute(
                """
                UPDATE fj_workflow_prefetch_batches
                SET status = 'ready', started_at = ?, completed_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (now, now, now, batch_id),
            )
    return batch_id


def _advance_prefetch(
    db: Database,
    config: AppConfig,
    workflow_run_id: str,
    *,
    allow_start: bool = True,
) -> None:
    run = _require_run(db, workflow_run_id)
    source_batch_id = _prefetch_source_batch_id(db, workflow_run_id)
    if not source_batch_id:
        return
    batch_id = _ensure_prefetch_batch(
        db, config, workflow_run_id, source_batch_id
    )
    if not batch_id:
        return
    with db.connect() as connection:
        batch = connection.execute(
            "SELECT * FROM fj_workflow_prefetch_batches WHERE id = ?",
            (batch_id,),
        ).fetchone()
        item = connection.execute(
            """
            SELECT * FROM fj_workflow_prefetch_items
            WHERE prefetch_batch_id = ? AND status = 'collecting'
            ORDER BY started_at, created_at
            LIMIT 1
            """,
            (batch_id,),
        ).fetchone()
    if batch is None or str(batch["status"]) in {
        "promoted", "abandoned", "cancelled", "failed"
    }:
        return
    if item is not None:
        operation_id = str(item["operation_ref_id"] or "")
        if operation_id:
            try:
                detail_task = boss_capture_task_manager.get_task(operation_id)
            except AppError:
                _finish_prefetch_item(
                    db, item, status="failed", detail_status="failed",
                    error_message="BOSS 详情任务已中断。",
                )
                detail_task = None
            if detail_task is not None and detail_task.get("status") in {"queued", "running"}:
                return
            if detail_task is not None and detail_task.get("status") == "failed":
                _finish_prefetch_item(
                    db, item, status="failed", detail_status="failed",
                    error_message=str(detail_task.get("error_message") or "JD 采集失败。"),
                )
            elif detail_task is not None:
                job = get_capture_history_job(db, str(item["job_id"]))
                if str(job.get("detail_status") or "") == "completed":
                    _finish_prefetch_item(
                        db, item, status="ready", detail_status="completed"
                    )
                else:
                    _finish_prefetch_item(
                        db, item, status="failed", detail_status=str(job.get("detail_status") or "failed"),
                        error_message="JD 采集未返回完整正式详情。",
                    )
    with db.connect() as connection:
        counts = connection.execute(
            """
            SELECT
              COALESCE(SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END), 0) AS pending_count,
              COALESCE(SUM(CASE WHEN status = 'collecting' THEN 1 ELSE 0 END), 0) AS collecting_count,
              COALESCE(SUM(CASE WHEN status = 'ready' THEN 1 ELSE 0 END), 0) AS ready_count
            FROM fj_workflow_prefetch_items
            WHERE prefetch_batch_id = ?
            """,
            (batch_id,),
        ).fetchone()
        pending_count = int(counts["pending_count"] or 0)
        collecting_count = int(counts["collecting_count"] or 0)
        ready_count = int(counts["ready_count"] or 0)
        if collecting_count == 0 and pending_count == 0:
            now = utc_now()
            connection.execute(
                """
                UPDATE fj_workflow_prefetch_batches
                SET status = ?, started_at = COALESCE(started_at, ?),
                    completed_at = ?, updated_at = ?
                WHERE id = ? AND status = 'preparing'
                """,
                (
                    "ready" if ready_count else "failed",
                    now,
                    now,
                    now,
                    batch_id,
                ),
            )
            return
    if not allow_start or bool(run["paused"]):
        return
    _start_prefetch_item(db, config, workflow_run_id, batch_id)


def _start_prefetch_item(
    db: Database, config: AppConfig, workflow_run_id: str, batch_id: str
) -> None:
    now = utc_now()
    with db.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        item = connection.execute(
            """
            SELECT * FROM fj_workflow_prefetch_items
            WHERE prefetch_batch_id = ? AND status = 'pending'
            ORDER BY created_at, id
            LIMIT 1
            """,
            (batch_id,),
        ).fetchone()
        if item is None:
            return
        changed = connection.execute(
            """
            UPDATE fj_workflow_prefetch_items
            SET status = 'collecting', lifecycle_status = 'preparing',
                detail_status = CASE WHEN detail_status = 'not_collected' THEN 'queued' ELSE detail_status END,
                started_at = ?, updated_at = ?
            WHERE id = ? AND status = 'pending'
            """,
            (now, now, item["id"]),
        )
        if changed.rowcount != 1:
            return
        connection.execute(
            """
            UPDATE fj_workflow_prefetch_batches
            SET started_at = COALESCE(started_at, ?), updated_at = ?
            WHERE id = ? AND status = 'preparing'
            """,
            (now, now, batch_id),
        )
    try:
        job = get_capture_history_job(db, str(item["job_id"]))
        if str(job.get("detail_status") or "") == "completed":
            _finish_prefetch_item(db, item, status="ready", detail_status="completed")
            return
        if not boss_scraper_service.get_browser_status().running:
            raise AppError(423, "BROWSER_NOT_RUNNING", "BOSS 浏览器未启动。")
        detail_task = boss_capture_task_manager.start_history_detail(
            job,
            output_dir=config.output_root / "fine-job" / "boss-capture",
            db=db,
        )
        with db.connect() as connection:
            connection.execute(
                """
                UPDATE fj_workflow_prefetch_items
                SET operation_ref_type = 'capture_task', operation_ref_id = ?,
                    detail_status = 'collecting', updated_at = ?
                WHERE id = ? AND status = 'collecting'
                """,
                (str(detail_task["id"]), utc_now(), item["id"]),
            )
    except Exception as exc:  # noqa: BLE001 - Prefetch 单 Item 失败后继续旁路批次
        _finish_prefetch_item(
            db, item, status="failed", detail_status="failed",
            error_message=str(exc),
        )


def _finish_prefetch_item(
    db: Database,
    item: Any,
    *,
    status: str,
    detail_status: str,
    error_message: str = "",
) -> None:
    now = utc_now()
    lifecycle = "ready" if status == "ready" else "abandoned"
    with db.connect() as connection:
        connection.execute(
            """
            UPDATE fj_workflow_prefetch_items
            SET status = ?, lifecycle_status = ?, detail_status = ?,
                error_message = ?, completed_at = ?, updated_at = ?
            WHERE id = ? AND lifecycle_status = 'preparing'
            """,
            (
                status,
                lifecycle,
                detail_status,
                error_message,
                now,
                now,
                item["id"],
            ),
        )
        if status == "failed":
            connection.execute(
                """
                UPDATE fj_workflow_candidate_reservations
                SET status = 'failed', released_at = ?, terminal_at = ?
                WHERE workflow_run_id = ? AND owner_type = 'prefetch'
                  AND owner_id = ? AND status = 'reserved'
                """,
                (now, now, item["workflow_run_id"], item["id"]),
            )


def _prefetch_batch_state(db: Database, workflow_run_id: str) -> tuple[str, Any | None]:
    with db.connect() as connection:
        batch = connection.execute(
            """
            SELECT * FROM fj_workflow_prefetch_batches
            WHERE workflow_run_id = ? AND status IN ('preparing', 'ready', 'failed')
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (workflow_run_id,),
        ).fetchone()
        if batch is None:
            return "none", None
        counts = connection.execute(
            """
            SELECT
              COALESCE(SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END), 0) AS pending_count,
              COALESCE(SUM(CASE WHEN status = 'collecting' THEN 1 ELSE 0 END), 0) AS collecting_count,
              COALESCE(SUM(CASE WHEN status = 'ready' THEN 1 ELSE 0 END), 0) AS ready_count
            FROM fj_workflow_prefetch_items
            WHERE prefetch_batch_id = ?
            """,
            (batch["id"],),
        ).fetchone()
    if int(counts["ready_count"] or 0) > 0:
        return "ready", batch
    if int(counts["pending_count"] or 0) or int(counts["collecting_count"] or 0):
        return "waiting", batch
    return "none", batch


def _completion_target_reached_in_connection(
    connection: Any, workflow_run_id: str, contract: dict[str, Any]
) -> bool:
    rows = connection.execute(
        """
        SELECT json_extract(payload_json, '$.job_id') AS job_id,
               json_extract(result_json, '$.decision') AS decision
        FROM fj_workflow_tasks
        WHERE workflow_run_id = ? AND task_type = 'deep_job_search_analysis'
          AND status = 'succeeded'
        """,
        (workflow_run_id,),
    ).fetchall()
    decisions: dict[str, set[str]] = {"recommend": set(), "review": set()}
    for row in rows:
        decision = str(row["decision"] or "")
        job_id = str(row["job_id"] or "")
        if decision in decisions and job_id:
            decisions[decision].add(job_id)
    recommend_reached = len(decisions["recommend"]) >= int(
        contract.get("recommend_target") or contract.get("target_count") or 0
    )
    review_target = contract.get("review_target")
    configured = [recommend_reached]
    if review_target is not None:
        configured.append(len(decisions["review"]) >= int(review_target))
    return (
        any(configured) if contract.get("target_mode") == "any" else all(configured)
    )


def _promote_ready_prefetch(
    db: Database, workflow_run_id: str, contract: dict[str, Any]
) -> str:
    state, batch = _prefetch_batch_state(db, workflow_run_id)
    if state == "waiting":
        return "waiting"
    if state != "ready" or batch is None:
        return "none"
    # 先完成业务目标判断，只有确定需要提升时才绑定最新分析上下文。
    if _business_target_reached(db, workflow_run_id, contract) and not _analysis_policy(
        contract
    )["analyze_all_candidates"]:
        _abandon_prefetch_batches(db, workflow_run_id)
        return "abandoned"
    snapshot = _create_candidate_analysis_snapshot(db, workflow_run_id, contract)
    if snapshot["status"] == "blocked":
        _wait_for_user(db, workflow_run_id, "context_budget_exceeded", str(snapshot["blocker_reason"]))
        return "waiting"
    now = utc_now()
    analysis_batch_id = new_id()
    with db.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        locked_batch = connection.execute(
            "SELECT * FROM fj_workflow_prefetch_batches WHERE id = ?",
            (batch["id"],),
        ).fetchone()
        if locked_batch is None or str(locked_batch["status"]) != "ready":
            return "none"
        if _completion_target_reached_in_connection(
            connection, workflow_run_id, contract
        ) and not _analysis_policy(contract)["analyze_all_candidates"]:
            abandoned_at = utc_now()
            connection.execute(
                """
                UPDATE fj_workflow_prefetch_items
                SET lifecycle_status = 'abandoned', abandoned_at = ?, updated_at = ?
                WHERE prefetch_batch_id = ? AND lifecycle_status <> 'promoted'
                """,
                (abandoned_at, abandoned_at, batch["id"]),
            )
            connection.execute(
                """
                UPDATE fj_workflow_candidate_reservations
                SET status = 'abandoned', released_at = ?, terminal_at = ?
                WHERE workflow_run_id = ? AND owner_type = 'prefetch'
                  AND owner_id IN (
                    SELECT id FROM fj_workflow_prefetch_items WHERE prefetch_batch_id = ?
                  ) AND status = 'reserved'
                """,
                (abandoned_at, abandoned_at, workflow_run_id, batch["id"]),
            )
            connection.execute(
                """
                UPDATE fj_workflow_prefetch_batches
                SET status = 'abandoned', abandoned_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (abandoned_at, abandoned_at, batch["id"]),
            )
            return "abandoned"
        pending = connection.execute(
            """
            SELECT 1 FROM fj_workflow_prefetch_items
            WHERE prefetch_batch_id = ? AND status IN ('pending', 'collecting')
            LIMIT 1
            """,
            (batch["id"],),
        ).fetchone()
        if pending is not None:
            return "waiting"
        items = connection.execute(
            """
            SELECT * FROM fj_workflow_prefetch_items
            WHERE prefetch_batch_id = ? AND status = 'ready'
            ORDER BY created_at, id
            """,
            (batch["id"],),
        ).fetchall()
        if not items:
            return "none"
        for item in items:
            reservation = connection.execute(
                """
                SELECT id FROM fj_workflow_candidate_reservations
                WHERE workflow_run_id = ? AND owner_type = 'prefetch'
                  AND owner_id = ? AND job_id = ? AND status = 'reserved'
                """,
                (workflow_run_id, item["id"], item["job_id"]),
            ).fetchone()
            if reservation is None:
                return "none"
        for item in items:
            task_id = new_id()
            connection.execute(
                """
                INSERT INTO fj_workflow_tasks (
                  id, workflow_run_id, task_type, payload_json, result_json, created_at, updated_at
                ) VALUES (?, ?, 'deep_job_search_analysis', ?, '{}', ?, ?)
                """,
                (
                    task_id,
                    workflow_run_id,
                    _dump({
                        "job_id": str(item["job_id"]),
                        "jd_task_id": str(item["id"]),
                        "prefetch_item_id": str(item["id"]),
                        "analysis_batch_id": analysis_batch_id,
                    }),
                    now,
                    now,
                ),
            )
            connection.execute(
                """
                UPDATE fj_workflow_prefetch_items
                SET lifecycle_status = 'promoted', promoted_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (now, now, item["id"]),
            )
            connection.execute(
                """
                UPDATE fj_workflow_candidate_reservations
                SET status = 'promoted', released_at = ?, terminal_at = ?
                WHERE workflow_run_id = ? AND owner_type = 'prefetch'
                  AND owner_id = ? AND status = 'reserved'
                """,
                (now, now, workflow_run_id, item["id"]),
            )
            connection.execute(
                """
                INSERT INTO fj_workflow_candidate_reservations (
                  id, workflow_run_id, job_id, owner_type, owner_id, status, created_at
                ) VALUES (?, ?, ?, 'formal_analysis', ?, 'reserved', ?)
                """,
                (new_id(), workflow_run_id, item["job_id"], task_id, now),
            )
        connection.execute(
            """
            UPDATE fj_workflow_prefetch_batches
            SET status = 'promoted', promoted_at = ?, completed_at = COALESCE(completed_at, ?),
                updated_at = ?
            WHERE id = ?
            """,
            (now, now, now, batch["id"]),
        )
        connection.execute(
            """
            UPDATE fj_workflow_runs
            SET status = 'waiting_codex', current_step = 'waiting_codex',
                next_action = 'codex_analysis',
                next_action_reason = ?,
                waiting_for_user = 0, stop_reason = '', updated_at = ?
            WHERE id = ?
            """,
            ("旁路 Prefetch 已提升为新的正式 Analysis Batch，等待 Codex 接管。", now, workflow_run_id),
        )
    return "promoted"


def _wait_for_prefetch(db: Database, workflow_run_id: str) -> None:
    _update_run(
        db,
        workflow_run_id,
        status="waiting_codex",
        current_step="waiting_codex",
        next_action="wait_prefetch",
        next_action_reason="下一批 JD 正在旁路准备，当前 Run 保持正式批次边界。",
        waiting_for_user=0,
        stop_reason="",
    )


def _continue_after_prefetch_wait(
    db: Database, config: AppConfig, workflow_run_id: str
) -> None:
    run = _require_run(db, workflow_run_id)
    contract = _load(run["completion_contract_json"], {})
    if _business_target_reached(db, workflow_run_id, contract) and not _analysis_policy(
        contract
    )["analyze_all_candidates"]:
        _complete_run(
            db,
            workflow_run_id,
            "completion_target_reached",
            "已达到本轮完成目标，已保存成果并结束 Run。",
        )
        return
    promotion = _promote_ready_prefetch(db, workflow_run_id, contract)
    if promotion in {"promoted", "waiting"}:
        return
    if _analysis_policy(contract)["analyze_all_candidates"] and _business_target_reached(
        db, workflow_run_id, contract
    ):
        _continue_frozen_candidate_pool(db, config, workflow_run_id, contract)
        return
    if _create_jd_tasks(
        db, workflow_run_id, int(contract["stop_policy"]["candidate_target_count"])
    ):
        advance_deep_job_search(db, config, workflow_run_id)
        return
    _resume_search_or_wait(db, config, workflow_run_id)


def _release_candidate_reservation(
    db: Database,
    workflow_run_id: str,
    owner_type: str,
    owner_id: str,
    job_id: str,
    status: str,
) -> None:
    if not job_id:
        return
    now = utc_now()
    with db.connect() as connection:
        connection.execute(
            """
            UPDATE fj_workflow_candidate_reservations
            SET status = ?, released_at = ?, terminal_at = ?
            WHERE workflow_run_id = ? AND owner_type = ? AND owner_id = ?
              AND job_id = ? AND status = 'reserved'
            """,
            (status, now, now, workflow_run_id, owner_type, owner_id, job_id),
        )


def _abandon_prefetch_batches(db: Database, workflow_run_id: str) -> None:
    now = utc_now()
    with db.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        batches = connection.execute(
            """
            SELECT id FROM fj_workflow_prefetch_batches
            WHERE workflow_run_id = ? AND status <> 'promoted'
            """,
            (workflow_run_id,),
        ).fetchall()
        for batch in batches:
            connection.execute(
                """
                UPDATE fj_workflow_prefetch_items
                SET lifecycle_status = 'abandoned', abandoned_at = ?, updated_at = ?
                WHERE prefetch_batch_id = ? AND lifecycle_status <> 'promoted'
                """,
                (now, now, batch["id"]),
            )
            connection.execute(
                """
                UPDATE fj_workflow_candidate_reservations
                SET status = 'abandoned', released_at = ?, terminal_at = ?
                WHERE workflow_run_id = ? AND owner_type = 'prefetch'
                  AND owner_id IN (
                    SELECT id FROM fj_workflow_prefetch_items WHERE prefetch_batch_id = ?
                  ) AND status = 'reserved'
                """,
                (now, now, workflow_run_id, batch["id"]),
            )
            connection.execute(
                """
                UPDATE fj_workflow_prefetch_batches
                SET status = 'abandoned', abandoned_at = ?, updated_at = ?
                WHERE id = ? AND status <> 'promoted'
                """,
                (now, now, batch["id"]),
            )


def _cancel_prefetch_batches(db: Database, workflow_run_id: str) -> None:
    now = utc_now()
    with db.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        batches = connection.execute(
            """
            SELECT id FROM fj_workflow_prefetch_batches
            WHERE workflow_run_id = ? AND status <> 'promoted'
            """,
            (workflow_run_id,),
        ).fetchall()
        for batch in batches:
            connection.execute(
                """
                UPDATE fj_workflow_prefetch_items
                SET lifecycle_status = 'cancelled', abandoned_at = ?, updated_at = ?
                WHERE prefetch_batch_id = ? AND lifecycle_status <> 'promoted'
                """,
                (now, now, batch["id"]),
            )
            connection.execute(
                """
                UPDATE fj_workflow_candidate_reservations
                SET status = 'cancelled', released_at = ?, terminal_at = ?
                WHERE workflow_run_id = ? AND owner_type = 'prefetch'
                  AND owner_id IN (
                    SELECT id FROM fj_workflow_prefetch_items WHERE prefetch_batch_id = ?
                  ) AND status = 'reserved'
                """,
                (now, now, workflow_run_id, batch["id"]),
            )
            connection.execute(
                """
                UPDATE fj_workflow_prefetch_batches
                SET status = 'cancelled', abandoned_at = ?, updated_at = ?
                WHERE id = ? AND status <> 'promoted'
                """,
                (now, now, batch["id"]),
            )


def _create_candidate_analysis_snapshot(
    db: Database, workflow_run_id: str, contract: dict[str, Any]
) -> dict[str, object]:
    recommendation_strategy = _require_workflow_recommendation_strategy(
        db,
        recommendation_strategy_id=str(contract["selected_strategy_ids"]["recommendation_strategy_id"]),
        filter_strategy_id=str(contract["selected_strategy_ids"]["filter_strategy_id"]),
    )
    profile_id = str(recommendation_strategy["candidate_profile_id"])
    resume_version_id = str(recommendation_strategy["resume_version_id"])
    resume_version = profile_store.get_resume_version(db, resume_version_id)
    # 生成正式评估上下文修订供评估落库校验，但 Shared Base 只保留紧凑事实。
    resolution = profile_v3.resolve_task_context(
        db, profile_id, resume_version_id, "evaluation", "regenerate"
    )
    evaluation_context = dict((resolution.get("context") or {}).get("current_revision") or {})
    profile_context = get_profile_context(
        db,
        profile_id,
        view="search",
        resume_family_id=str(resume_version.get("resume_family_id") or "") or None,
        persist_artifact=False,
    )
    strategy = get_filter_strategy(db, str(contract["selected_strategy_ids"]["filter_strategy_id"]))
    sections = [
        _section("task_goal", "shared_base", {"recommend_target": contract["recommend_target"], "review_target": contract.get("review_target"), "target_mode": contract.get("target_mode"), "external_action_policy": contract["external_action_policy"]}, "workflow_run", 1, True, ""),
        _section("candidate_facts", "shared_base", {"profile_id": profile_context["profile_id"], "resume_version_id": resume_version_id, "resume_version": resume_version.get("content_version"), "versions": profile_context["versions"], "facts_markdown": profile_context["markdown"], "evaluation_context_revision_id": evaluation_context.get("id")}, "candidate_profile", int(profile_context["artifact_version"]), True, "已使用紧凑 search 视图；正式评估上下文仅保存修订引用，不注入完整简历。"),
        _section("filter_strategy_summary", "shared_base", _compact_filter_strategy(strategy), "filter_strategy", int(strategy.get("strategy_version") or 1), True, ""),
        _section("recommendation_strategy_summary", "shared_base", _compact_recommendation_strategy(recommendation_strategy), "recommendation_strategy", int(recommendation_strategy.get("strategy_version") or 1), True, "最终投递建议的主要业务规则。"),
        _section("applied_feedback", "shared_base", {"applied_feedback_ids": contract.get("applied_feedback_ids") or [], "applied_preference_ids": contract.get("applied_preference_ids") or []}, "workflow_contract", 1, True, "仅保存已应用引用，不注入无关反馈全文。"),
        _section("analysis_guidance", "shared_base", contract.get("analysis_guidance") or {}, "workflow_contract", int(((contract.get("analysis_guidance") or {}).get("version")) or 1), True, "仅作用于当前 Workflow Run，不修改长期正式策略。"),
        _section("complete_resume", "excluded", None, "resume", None, False, "岗位评估默认不注入完整简历。"),
        _section("normalized_resume", "excluded", None, "resume", None, False, "岗位评估默认不注入 normalized resume 全文。"),
        _section("historical_job_payload", "excluded", None, "boss_job", None, False, "岗位历史 payload 只在单 Item 中按需读取必要 JD 字段。"),
        _section("unrelated_qa", "excluded", None, "profile_qa", None, False, "岗位评估默认不注入无关 QA。"),
        _section("unrelated_chat", "excluded", None, "chat", None, False, "岗位分析不读取无关聊天。"),
    ]
    characters = sum(int(item["character_count"]) for item in sections if item["included"])
    search_snapshot = get_context_snapshot(db, workflow_run_id)
    soft_budget = int(search_snapshot["soft_budget_characters"])
    status = "blocked" if characters > soft_budget else "ready"
    blocker = "分析上下文超过本轮软预算，请在驾驶舱裁剪候选或资料后继续。" if status == "blocked" else ""
    now = utc_now()
    with db.connect() as connection:
        connection.execute(
            """
            INSERT INTO fj_workflow_context_snapshots (
              id, workflow_run_id, channel, snapshot_json, context_characters,
              estimated_tokens, soft_budget_characters, hard_budget_characters,
              status, blocker_reason, created_at
            ) VALUES (?, ?, 'candidate_analysis', ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(workflow_run_id, channel) DO UPDATE SET
              snapshot_json = excluded.snapshot_json,
              context_characters = excluded.context_characters,
              estimated_tokens = excluded.estimated_tokens,
              soft_budget_characters = excluded.soft_budget_characters,
              hard_budget_characters = excluded.hard_budget_characters,
              status = excluded.status,
              blocker_reason = excluded.blocker_reason,
              created_at = excluded.created_at
            """,
            (new_id(), workflow_run_id, _dump({"sections": sections}), characters, _estimate_tokens(characters), soft_budget, HARD_CONTEXT_BUDGET, status, blocker, now),
        )
    return get_context_snapshot(db, workflow_run_id, "candidate_analysis")


def _create_search_context_snapshot(
    db: Database,
    workflow_run_id: str,
    strategy: dict[str, object],
    recommendation_strategy: dict[str, object] | None,
    contract: dict[str, Any],
    soft_budget: int,
) -> dict[str, object]:
    sections = [
        _section("task_goal", "shared_base", {"recommend_target": contract["recommend_target"], "review_target": contract.get("review_target"), "target_mode": contract.get("target_mode"), "source_policy": contract["source_policy"], "keywords": contract["allowed_search_keywords"], "cities": contract["allowed_cities"]}, "workflow_run", 1, True, ""),
        _section("filter_strategy", "task_channel", strategy, "filter_strategy", int(strategy.get("strategy_version") or 1), True, ""),
        _section("complete_resume", "excluded", None, "resume", None, False, "搜索阶段不需要完整简历。"),
        _section("historical_payload", "excluded", None, "boss_job", None, False, "fresh_only 搜索不注入历史岗位 payload。"),
        _section("unrelated_chat", "excluded", None, "chat", None, False, "deep_job_search 不读取无关聊天。"),
    ]
    if recommendation_strategy is not None:
        profile = profile_store.get_profile(db, str(recommendation_strategy["candidate_profile_id"]))
        sections.insert(2, _section("recommendation_strategy", "task_channel", _compact_recommendation_strategy(recommendation_strategy), "recommendation_strategy", int(recommendation_strategy.get("strategy_version") or 1), True, "最终投递建议策略会在候选分析阶段作为主要规则。"))
        sections.insert(3, _section("candidate_compact_facts", "shared_base", {"profile_id": profile["id"], "versions": profile["versions"]}, "candidate_profile", int(profile["versions"]["facts_version"]), True, "搜索阶段只注入候选人版本摘要；详细事实在 JD 分析 Item 按需读取。"))
    characters = sum(int(item["character_count"]) for item in sections if item["included"])
    status = "blocked" if characters > soft_budget else "ready"
    blocker = "上下文超过本轮软预算，请裁剪后重试。" if status == "blocked" else ""
    now = utc_now()
    with db.connect() as connection:
        connection.execute("INSERT INTO fj_workflow_context_snapshots (id, workflow_run_id, channel, snapshot_json, context_characters, estimated_tokens, soft_budget_characters, hard_budget_characters, status, blocker_reason, created_at) VALUES (?, ?, 'deep_job_search', ?, ?, ?, ?, ?, ?, ?, ?)", (new_id(), workflow_run_id, _dump({"sections": sections}), characters, _estimate_tokens(characters), soft_budget, HARD_CONTEXT_BUDGET, status, blocker, now))
    return get_context_snapshot(db, workflow_run_id)


def _section(section_id: str, section_type: str, content: object, source: str, version: int | None, included: bool, exclusion_reason: str) -> dict[str, object]:
    rendered = _dump(content) if content is not None else ""
    return {"section_id": section_id, "section_type": section_type, "source": source, "source_version": version, "included": included, "exclusion_reason": exclusion_reason, "character_count": len(rendered), "estimated_tokens": _estimate_tokens(len(rendered)), "content": content}


def _compact_filter_strategy(strategy: dict[str, object]) -> dict[str, object]:
    fields = (
        "id", "strategy_version", "title_include_any", "title_include_all", "title_exclude",
        "company_include", "company_exclude", "company_scales", "company_industries",
        "company_stages", "degrees", "experiences", "job_types", "monthly_salary_min",
        "monthly_salary_max_at_least", "daily_salary_min", "skill_include_any",
        "skill_include_all", "skill_exclude", "unknown_value_policy",
    )
    return {field: strategy.get(field) for field in fields}


def _compact_recommendation_strategy(strategy: dict[str, object]) -> dict[str, object]:
    fields = (
        "id", "name", "strategy_version", "filter_strategy_id", "candidate_profile_id",
        "resume_version_id", "evaluation_method", "desired_responsibilities", "required_skills",
        "preferred_skills", "excluded_terms", "preferred_industries", "boss_active_statuses",
        "work_preferences",
        "risk_notes", "minimum_confidence", "insufficient_info_action", "notes",
    )
    return {field: strategy.get(field) for field in fields}


def _require_workflow_recommendation_strategy(
    db: Database, *, recommendation_strategy_id: str, filter_strategy_id: str
) -> dict[str, object]:
    strategy = get_recommendation_strategy(db, recommendation_strategy_id)
    if not strategy.get("enabled"):
        raise AppError(409, "RECOMMENDATION_STRATEGY_DISABLED", "建议投递策略当前未启用。")
    if str(strategy.get("filter_strategy_id") or "") != filter_strategy_id:
        raise AppError(422, "RECOMMENDATION_FILTER_MISMATCH", "建议投递策略必须关联本轮选择的岗位筛选策略。")
    resume_version_id = str(strategy.get("resume_version_id") or "")
    profile_id = str(strategy.get("candidate_profile_id") or "")
    if not resume_version_id or not profile_id:
        raise AppError(422, "RECOMMENDATION_PROFILE_REQUIRED", "建议投递策略必须关联候选人档案和具体简历。")
    resume_version = profile_store.get_resume_version(db, resume_version_id)
    if str(resume_version.get("profile_id") or "") != profile_id:
        raise AppError(409, "RECOMMENDATION_PROFILE_MISMATCH", "建议投递策略的候选人档案与具体简历不一致。")
    return strategy


def _compact_job_for_analysis(job: dict[str, object]) -> dict[str, object]:
    detail = job.get("detail") if isinstance(job.get("detail"), dict) else {}
    return {
        "job_id": job.get("id"),
        "source_job_id": job.get("job_id"),
        "title": job.get("title"),
        "company_name": job.get("boss_name") or job.get("company_name"),
        "salary": job.get("salary"),
        "location": job.get("location"),
        "experience": job.get("experience"),
        "degree": job.get("degree"),
        "skills": job.get("skills"),
        "job_detail_version": job.get("detail_version"),
        "jd": detail,
    }


def _shared_profile_versions(snapshot: dict[str, object]) -> dict[str, object]:
    for section in snapshot.get("sections", []):
        if isinstance(section, dict) and section.get("section_id") == "candidate_facts":
            content = section.get("content")
            if isinstance(content, dict) and isinstance(content.get("versions"), dict):
                return content["versions"]
    return {}


def _save_context_snapshot(
    db: Database,
    workflow_run_id: str,
    channel: str,
    sections: list[dict[str, object]],
    soft_budget: int,
) -> dict[str, object]:
    characters = sum(int(item["character_count"]) for item in sections if item["included"])
    status = "blocked" if characters > soft_budget else "ready"
    blocker = "上下文超过本轮软预算，请先缩小本轮范围后继续。" if status == "blocked" else ""
    now = utc_now()
    with db.connect() as connection:
        connection.execute(
            """
            INSERT INTO fj_workflow_context_snapshots (
              id, workflow_run_id, channel, snapshot_json, context_characters,
              estimated_tokens, soft_budget_characters, hard_budget_characters,
              status, blocker_reason, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(workflow_run_id, channel) DO UPDATE SET
              snapshot_json = excluded.snapshot_json,
              context_characters = excluded.context_characters,
              estimated_tokens = excluded.estimated_tokens,
              soft_budget_characters = excluded.soft_budget_characters,
              hard_budget_characters = excluded.hard_budget_characters,
              status = excluded.status,
              blocker_reason = excluded.blocker_reason,
              created_at = excluded.created_at
            """,
            (new_id(), workflow_run_id, channel, _dump({"sections": sections}), characters, _estimate_tokens(characters), soft_budget, HARD_CONTEXT_BUDGET, status, blocker, now),
        )
    return get_context_snapshot(db, workflow_run_id, channel)


def _next_task(db: Database, workflow_run_id: str):
    with db.connect() as connection:
        return connection.execute(
            """
            SELECT t.*
            FROM fj_workflow_tasks t
            LEFT JOIN fj_workflow_search_combinations c
              ON c.id = json_extract(t.payload_json, '$.search_combination_id')
            WHERE t.workflow_run_id = ? AND t.task_type = 'deep_job_search'
              AND t.status IN ('pending', 'running')
            ORDER BY CASE t.status WHEN 'running' THEN 0 ELSE 1 END,
                     COALESCE(c.sequence, 0), t.created_at
            LIMIT 1
            """,
            (workflow_run_id,),
        ).fetchone()


def _next_jd_task(db: Database, workflow_run_id: str):
    with db.connect() as connection:
        return connection.execute("SELECT * FROM fj_workflow_tasks WHERE workflow_run_id = ? AND task_type = 'deep_job_search_jd' AND status IN ('pending', 'running') ORDER BY CASE status WHEN 'running' THEN 0 ELSE 1 END, created_at LIMIT 1", (workflow_run_id,)).fetchone()


def _next_analysis_task(db: Database, workflow_run_id: str):
    with db.connect() as connection:
        return connection.execute("SELECT * FROM fj_workflow_tasks WHERE workflow_run_id = ? AND task_type = 'deep_job_search_analysis' AND status IN ('pending', 'running') ORDER BY created_at LIMIT 1", (workflow_run_id,)).fetchone()


def _analysis_exists_for_job(db: Database, workflow_run_id: str, job_id: str) -> bool:
    if not job_id:
        return True
    with db.connect() as connection:
        return connection.execute("SELECT 1 FROM fj_workflow_tasks WHERE workflow_run_id = ? AND task_type = 'deep_job_search_analysis' AND json_extract(payload_json, '$.job_id') = ? LIMIT 1", (workflow_run_id, job_id)).fetchone() is not None


def _analysis_policy(contract: dict[str, Any]) -> dict[str, Any]:
    policy = contract.get("analysis_policy")
    return {
        "analyze_all_candidates": False,
        "stop_after_current_batch": False,
        "analysis_batch_size": 3,
        **(policy if isinstance(policy, dict) else {}),
    }


def _execution_policy_after_analysis_batch(contract: dict[str, Any]) -> str:
    policy = contract.get("execution_policy")
    value = policy.get("after_analysis_batch") if isinstance(policy, dict) else None
    return "wait_for_user" if value == "wait_for_user" else "auto_continue"


def _completion_counts(db: Database, workflow_run_id: str) -> dict[str, int]:
    with db.connect() as connection:
        rows = connection.execute(
            """
            SELECT payload_json, result_json FROM fj_workflow_tasks
            WHERE workflow_run_id = ? AND task_type = 'deep_job_search_analysis' AND status = 'succeeded'
            """,
            (workflow_run_id,),
        ).fetchall()
    counts = {"recommend": 0, "review": 0}
    seen: dict[str, set[str]] = {"recommend": set(), "review": set()}
    for row in rows:
        decision = str(_load(row["result_json"], {}).get("decision") or "")
        job_id = str(_load(row["payload_json"], {}).get("job_id") or "")
        if decision in counts and job_id and job_id not in seen[decision]:
            seen[decision].add(job_id)
            counts[decision] += 1
    return counts


def _business_target_reached(db: Database, workflow_run_id: str, contract: dict[str, Any]) -> bool:
    return bool(_completion_progress(db, workflow_run_id, contract)["target_reached"])


def _completion_progress(
    db: Database, workflow_run_id: str, contract: dict[str, Any]
) -> dict[str, object]:
    counts = _completion_counts(db, workflow_run_id)
    recommend_target = int(contract.get("recommend_target") or contract.get("target_count") or 0)
    review_target = contract.get("review_target")
    recommend_reached = counts["recommend"] >= recommend_target
    review_reached = counts["review"] >= int(review_target) if review_target is not None else None
    configured_reached = [recommend_reached]
    if review_reached is not None:
        configured_reached.append(review_reached)
    target_mode = "any" if contract.get("target_mode") == "any" else "all"
    return {
        "recommend": {
            "current": counts["recommend"],
            "target": recommend_target,
            "remaining": max(0, recommend_target - counts["recommend"]),
            "reached": recommend_reached,
        },
        "review": {
            "current": counts["review"],
            "target": int(review_target) if review_target is not None else None,
            "remaining": max(0, int(review_target) - counts["review"]) if review_target is not None else None,
            "reached": review_reached,
        },
        "target_mode": target_mode,
        "target_reached": any(configured_reached) if target_mode == "any" else all(configured_reached),
    }


def _is_analysis_batch_complete(db: Database, workflow_run_id: str, analysis_batch_id: str) -> bool:
    with db.connect() as connection:
        rows = connection.execute(
            "SELECT payload_json, status FROM fj_workflow_tasks WHERE workflow_run_id = ? AND task_type = 'deep_job_search_analysis'",
            (workflow_run_id,),
        ).fetchall()
    return not any(
        _analysis_batch_id_from_payload(_load(row["payload_json"], {}), workflow_run_id) == analysis_batch_id
        and row["status"] in {"pending", "running"}
        for row in rows
    )


def _skip_pending_analysis_items(db: Database, workflow_run_id: str) -> None:
    """完成目标后不再要求 Codex 保存当前批剩余 Item。"""
    with db.connect() as connection:
        rows = connection.execute(
            """
            SELECT id, json_extract(payload_json, '$.job_id') AS job_id
            FROM fj_workflow_tasks
            WHERE workflow_run_id = ? AND task_type = 'deep_job_search_analysis'
              AND status = 'pending'
            """,
            (workflow_run_id,),
        ).fetchall()
        connection.execute(
            """
            UPDATE fj_workflow_tasks
            SET status = 'skipped', completed_at = ?, updated_at = ?
            WHERE workflow_run_id = ? AND task_type = 'deep_job_search_analysis' AND status = 'pending'
            """,
            (utc_now(), utc_now(), workflow_run_id),
        )
        now = utc_now()
        for row in rows:
            connection.execute(
                """
                UPDATE fj_workflow_candidate_reservations
                SET status = 'released', released_at = ?, terminal_at = ?
                WHERE workflow_run_id = ? AND owner_type = 'formal_analysis'
                  AND owner_id = ? AND status = 'reserved'
                """,
                (now, now, workflow_run_id, str(row["id"])),
            )


def _freeze_candidate_pool(
    db: Database, workflow_run_id: str, contract: dict[str, Any]
) -> dict[str, Any]:
    existing = contract.get("frozen_candidate_pool")
    if isinstance(existing, dict):
        return contract
    with db.connect() as connection:
        rows = connection.execute(
            """
            SELECT DISTINCT job_id FROM fj_workflow_job_discoveries
            WHERE workflow_run_id = ? AND is_run_first_discovery = 1
              AND is_historical_duplicate = 0 AND is_filter_candidate = 1
            ORDER BY discovered_at ASC, job_id ASC
            """,
            (workflow_run_id,),
        ).fetchall()
    # 冻结命中的候选集合，后续只在该集合内补 JD 与 Analysis Batch。
    contract["frozen_candidate_pool"] = {
        "job_ids": [str(row["job_id"]) for row in rows],
        "frozen_at": utc_now(),
        "reason": "completion_target_reached",
    }
    _update_run(db, workflow_run_id, completion_contract_json=_dump(contract))
    return contract


def _frozen_candidate_job_ids(contract: dict[str, Any]) -> list[str] | None:
    frozen = contract.get("frozen_candidate_pool")
    if not isinstance(frozen, dict):
        return None
    return [str(value) for value in frozen.get("job_ids") or [] if str(value)]


def _stop_after_current_batch_active(contract: dict[str, Any]) -> bool:
    policy = _analysis_policy(contract)
    return bool(policy.get("stop_after_current_batch")) and not bool(policy.get("stop_after_current_batch_consumed"))


def _consume_stop_after_current_batch(
    db: Database, workflow_run_id: str, contract: dict[str, Any]
) -> None:
    policy = _analysis_policy(contract)
    policy["stop_after_current_batch_consumed"] = True
    policy["stop_after_current_batch_consumed_at"] = utc_now()
    contract["analysis_policy"] = policy
    _update_run(db, workflow_run_id, completion_contract_json=_dump(contract))


def _complete_run(db: Database, workflow_run_id: str, stop_reason: str, reason: str) -> None:
    _abandon_prefetch_batches(db, workflow_run_id)
    _update_run(
        db,
        workflow_run_id,
        status="completed",
        current_step="completed",
        next_action="",
        next_action_reason=reason,
        waiting_for_user=0,
        stop_reason=stop_reason,
        completed_at=utc_now(),
    )


def _continue_frozen_candidate_pool(
    db: Database, config: AppConfig, workflow_run_id: str, contract: dict[str, Any]
) -> dict[str, object]:
    candidate_job_ids = _frozen_candidate_job_ids(contract) or []
    if _create_jd_tasks(
        db,
        workflow_run_id,
        int(contract["stop_policy"]["candidate_target_count"]),
        candidate_job_ids,
    ):
        return advance_deep_job_search(db, config, workflow_run_id)
    _complete_run(
        db,
        workflow_run_id,
        "frozen_candidate_pool_analyzed",
        "目标达成时冻结的候选池已全部完成分析。",
    )
    return get_workflow_run(db, workflow_run_id)


def _continue_after_analysis_batch(
    db: Database, config: AppConfig, workflow_run_id: str
) -> dict[str, object]:
    """用户确认后恢复批次衔接，仍由已有 handoff 生成后续 Codex 批次。"""
    run = _require_run(db, workflow_run_id)
    contract = _load(run["completion_contract_json"], {})
    _update_run(
        db,
        workflow_run_id,
        status="pending",
        current_step="continuing_after_analysis_batch",
        next_action="prepare_next_analysis_batch",
        next_action_reason="用户已确认继续，优先处理当前未分析候选。",
        waiting_for_user=0,
        stop_reason="",
    )
    _advance_prefetch(db, config, workflow_run_id)
    promotion = _promote_ready_prefetch(db, workflow_run_id, contract)
    if promotion == "promoted":
        return get_workflow_run(db, workflow_run_id)
    if promotion == "waiting":
        _wait_for_prefetch(db, workflow_run_id)
        return get_workflow_run(db, workflow_run_id)
    if _business_target_reached(db, workflow_run_id, contract) and _analysis_policy(contract)["analyze_all_candidates"]:
        # 这里不需要浏览器配置；有候选时只创建 JD 任务，随后由正常 advance 调度。
        candidate_job_ids = _frozen_candidate_job_ids(contract) or []
        if _create_jd_tasks(db, workflow_run_id, int(contract["stop_policy"]["candidate_target_count"]), candidate_job_ids):
            return get_workflow_run(db, workflow_run_id)
        _complete_run(db, workflow_run_id, "frozen_candidate_pool_analyzed", "目标达成时冻结的候选池已全部完成分析。")
        return get_workflow_run(db, workflow_run_id)
    if _create_jd_tasks(db, workflow_run_id, int(contract["stop_policy"]["candidate_target_count"])):
        return get_workflow_run(db, workflow_run_id)
    return get_workflow_run(db, workflow_run_id)


def _resume_search_or_wait(db: Database, config: AppConfig, workflow_run_id: str) -> None:
    _update_run(db, workflow_run_id, status="pending", current_step="searching", next_action="continue_search", next_action_reason="当前候选池已处理完，继续寻找 fresh_only 候选。", waiting_for_user=0, stop_reason="")
    if _next_task(db, workflow_run_id) is None:
        _wait_for_user(
            db,
            workflow_run_id,
            "approved_search_space_exhausted",
            "已耗尽本轮批准的搜索词、城市与合理平台组合，等待你调整搜索范围。",
        )
        return
    advance_deep_job_search(db, config, workflow_run_id)


def _refresh_counts(db: Database, workflow_run_id: str) -> None:
    with db.connect() as connection:
        fresh = int(connection.execute("SELECT COUNT(DISTINCT d.job_id) FROM fj_workflow_job_discoveries d WHERE d.workflow_run_id = ? AND d.is_run_first_discovery = 1 AND d.is_historical_duplicate = 0 AND d.is_filter_candidate = 1", (workflow_run_id,)).fetchone()[0])
        run = _require_run(db, workflow_run_id)
        contract = _load(run["completion_contract_json"], {})
        target = int(contract.get("recommend_target") or contract.get("target_count") or 0)
        completed = int(connection.execute("SELECT COUNT(DISTINCT json_extract(result_json, '$.job_id')) FROM fj_workflow_tasks WHERE workflow_run_id = ? AND task_type = 'deep_job_search_analysis' AND status = 'succeeded' AND json_extract(result_json, '$.decision') = 'recommend'", (workflow_run_id,)).fetchone()[0])
        available = int(connection.execute("""SELECT COUNT(DISTINCT d.job_id) FROM fj_workflow_job_discoveries d WHERE d.workflow_run_id = ? AND d.is_run_first_discovery = 1 AND d.is_historical_duplicate = 0 AND d.is_filter_candidate = 1 AND NOT EXISTS (SELECT 1 FROM fj_workflow_tasks t WHERE t.workflow_run_id = d.workflow_run_id AND t.task_type IN ('deep_job_search_jd', 'deep_job_search_analysis') AND json_extract(t.payload_json, '$.job_id') = d.job_id)""", (workflow_run_id,)).fetchone()[0])
        telemetry = _load(run["telemetry_json"], {})
        telemetry["fresh_candidates"] = fresh
        telemetry["available_fresh_candidates"] = available
        completion_counts = _completion_counts(db, workflow_run_id)
        telemetry["recommend_count"] = completion_counts["recommend"]
        telemetry["review_count"] = completion_counts["review"]
        telemetry["search_batches"] = int(connection.execute("SELECT COUNT(*) FROM fj_workflow_tasks WHERE workflow_run_id = ? AND task_type = 'deep_job_search'", (workflow_run_id,)).fetchone()[0])
        metrics_row = connection.execute(
            """
            SELECT
              COALESCE(SUM(jobs_seen), 0) AS jobs_seen,
              COALESCE(SUM(run_fresh_jobs), 0) AS run_fresh_jobs,
              COALESCE(SUM(historical_duplicates), 0) AS historical_duplicates,
              COALESCE(SUM(cooldown_excluded), 0) AS cooldown_excluded,
              COALESCE(SUM(strategy_pass), 0) AS strategy_pass,
              COALESCE(SUM(strategy_review), 0) AS strategy_review,
              COALESCE(SUM(strategy_reject), 0) AS strategy_reject,
              COALESCE(SUM(qualified_fresh_jobs), 0) AS qualified_fresh_jobs,
              COALESCE(SUM(candidate_jobs), 0) AS candidate_jobs
            FROM fj_workflow_search_combinations
            WHERE workflow_run_id = ?
            """,
            (workflow_run_id,),
        ).fetchone()
        jobs_seen = int(metrics_row["jobs_seen"] or 0)
        run_fresh_jobs = int(metrics_row["run_fresh_jobs"] or 0)
        telemetry["search_metrics"] = {
            key: int(metrics_row[key] or 0)
            for key in (
                "jobs_seen",
                "run_fresh_jobs",
                "historical_duplicates",
                "cooldown_excluded",
                "strategy_pass",
                "strategy_review",
                "strategy_reject",
                "qualified_fresh_jobs",
                "candidate_jobs",
            )
        }
        telemetry["search_metrics"].update({
            "novelty_yield": round(run_fresh_jobs / jobs_seen, 4) if jobs_seen else 0,
            "qualified_novelty_yield": round(fresh / run_fresh_jobs, 4) if run_fresh_jobs else 0,
            "duplicate_rate": round(int(metrics_row["historical_duplicates"] or 0) / jobs_seen, 4) if jobs_seen else 0,
        })
        telemetry["search_planner"] = _get_search_planner_summary(db, workflow_run_id)
        connection.execute("UPDATE fj_workflow_runs SET completed_count = ?, remaining_count = ?, telemetry_json = ?, updated_at = ? WHERE id = ?", (completed, max(0, target - completed), _dump(telemetry), utc_now(), workflow_run_id))


def _update_task_operation(db: Database, task_id: str, operation_id: str, status: str, payload: dict[str, Any]) -> None:
    with db.connect() as connection:
        connection.execute("UPDATE fj_workflow_tasks SET status = ?, operation_ref_type = 'capture_task', operation_ref_id = ?, payload_json = ?, started_at = COALESCE(started_at, ?), updated_at = ? WHERE id = ?", (status, operation_id, _dump(payload), utc_now(), utc_now(), task_id))


def _mark_task_waiting_for_user(db: Database, task_id: str, payload: dict[str, Any]) -> None:
    with db.connect() as connection:
        connection.execute(
            "UPDATE fj_workflow_tasks SET status = 'waiting_for_user', payload_json = ?, updated_at = ? WHERE id = ?",
            (_dump(payload), utc_now(), task_id),
        )


def _finish_task(db: Database, task_id: str, status: str, payload: dict[str, Any], result: dict[str, Any]) -> None:
    with db.connect() as connection:
        connection.execute("UPDATE fj_workflow_tasks SET status = ?, payload_json = ?, result_json = ?, completed_at = ?, updated_at = ? WHERE id = ?", (status, _dump(payload), _dump(result), utc_now(), utc_now(), task_id))


def _wait_for_user(db: Database, workflow_run_id: str, stop_reason: str, reason: str) -> None:
    _update_run(db, workflow_run_id, status="waiting_for_user", current_step="waiting_for_user", next_action="await_user_choice", next_action_reason=reason, waiting_for_user=1, stop_reason=stop_reason)


def _update_run(db: Database, workflow_run_id: str, **fields: object) -> None:
    if not fields:
        return
    fields["updated_at"] = utc_now()
    keys = list(fields)
    assignments = ", ".join(f"{key} = ?" for key in keys)
    with db.connect() as connection:
        connection.execute(f"UPDATE fj_workflow_runs SET {assignments} WHERE id = ?", [*fields.values(), workflow_run_id])
    try:
        workflow_run_event_broker.publish(workflow_run_id, get_workflow_run(db, workflow_run_id))
    except Exception:
        # 实时通道短暂不可用时，数据库中的最新快照会在重连后补发。
        return


def _require_run(db: Database, workflow_run_id: str):
    with db.connect() as connection:
        row = connection.execute("SELECT * FROM fj_workflow_runs WHERE id = ?", (workflow_run_id,)).fetchone()
    if row is None:
        raise AppError(404, "WORKFLOW_RUN_NOT_FOUND", "Workflow Run 不存在。")
    return row


def _serialize_run(db: Database, row: Any) -> dict[str, object]:
    contract = _load(row["completion_contract_json"], {})
    telemetry = _load(row["telemetry_json"], {})
    if not isinstance(telemetry, dict):
        telemetry = {}
    telemetry["prefetch"] = _get_prefetch_summary(db, str(row["id"]))
    telemetry["search_planner"] = _get_search_planner_summary(db, str(row["id"]))
    return {"workflow_run_id": row["id"], "workflow_type": row["workflow_type"], "completion_contract": contract, "completion_progress": _completion_progress(db, str(row["id"]), contract), "status": "paused" if bool(row["paused"]) else row["status"], "completed_count": row["completed_count"], "remaining_count": row["remaining_count"], "current_step": row["current_step"], "next_action": row["next_action"], "next_action_reason": row["next_action_reason"], "waiting_for_user": bool(row["waiting_for_user"]), "stop_reason": row["stop_reason"], "codex_session_ref": row["codex_session_ref"], "codex_runtime_id": row["codex_runtime_id"], "telemetry": telemetry, "created_at": row["created_at"], "updated_at": row["updated_at"], "completed_at": row["completed_at"]}


def _get_search_planner_summary(db: Database, workflow_run_id: str) -> dict[str, object]:
    with db.connect() as connection:
        row = connection.execute(
            """
            SELECT * FROM fj_workflow_search_combinations
            WHERE workflow_run_id = ?
            ORDER BY sequence DESC
            LIMIT 1
            """,
            (workflow_run_id,),
        ).fetchone()
        pending = connection.execute(
            """
            SELECT COUNT(*) AS amount
            FROM fj_workflow_search_combinations
            WHERE workflow_run_id = ? AND status IN ('pending', 'running')
            """,
            (workflow_run_id,),
        ).fetchone()
    if row is None:
        return {
            "current_scope": {"keyword": "", "city": ""},
            "current_combination": None,
            "last_transition": None,
            "pending_combination_count": 0,
        }
    filters = canonicalize_platform_filters(_load(row["platform_filters_json"], {}))
    metrics = {
        key: int(row[key] or 0)
        for key in (
            "batch_count",
            "pages_seen",
            "jobs_seen",
            "run_fresh_jobs",
            "historical_duplicates",
            "cooldown_excluded",
            "strategy_pass",
            "strategy_review",
            "strategy_reject",
            "qualified_fresh_jobs",
            "candidate_jobs",
            "low_novelty_streak",
            "low_qualified_yield_streak",
        )
    }
    metrics.update({
        "novelty_yield": float(row["novelty_yield"] or 0),
        "qualified_novelty_yield": float(row["qualified_novelty_yield"] or 0),
        "duplicate_rate": float(row["duplicate_rate"] or 0),
    })
    return {
        "current_scope": {"keyword": str(row["keyword"]), "city": str(row["city"])},
        "current_combination": {
            "id": str(row["id"]),
            "sequence": int(row["sequence"] or 0),
            "status": str(row["status"]),
            "platform_filters": filters,
            "platform_filter_labels": describe_platform_filters(filters),
            "metrics": metrics,
        },
        "last_transition": {
            "action": str(row["transition_action"]),
            "reason": str(row["transition_reason"] or ""),
            "selected_axis": str(row["selected_axis"] or ""),
            "evidence": _load(row["evidence_json"], {}),
            "stop_reason": str(row["stop_reason"] or ""),
        },
        "pending_combination_count": int(pending["amount"] or 0),
    }


def _analysis_batch_id_from_payload(payload: dict[str, Any], workflow_run_id: str) -> str:
    """旧 Run 没有 batch 标记时作为一个兼容批次处理。"""
    return str(payload.get("analysis_batch_id") or f"legacy:{workflow_run_id}")


def _analysis_batch_id(row: Any, workflow_run_id: str) -> str:
    return _analysis_batch_id_from_payload(_load(row["payload_json"], {}), workflow_run_id)


def _start_ack_timed_out(handoff: Any) -> bool:
    """Prompt 写入后只展示等待确认，超时由用户显式重试生成新尝试。"""
    prompt_written_at = str(handoff["prompt_written_at"] or "")
    if not prompt_written_at:
        return False
    try:
        written_at = datetime.fromisoformat(prompt_written_at.replace("Z", "+00:00"))
    except ValueError:
        return False
    return datetime.now(timezone.utc) >= written_at + timedelta(seconds=START_ACK_TIMEOUT_SECONDS)


def _require_analysis_batch_started(
    db: Database, workflow_run_id: str, analysis_batch_id: str
) -> None:
    """正式保存分析结果前确认当前批次已收到 Codex 的开始 ACK。"""
    with db.connect() as connection:
        handoff = connection.execute(
            "SELECT attempt_status FROM fj_workflow_analysis_handoffs WHERE workflow_run_id = ? AND analysis_batch_id = ?",
            (workflow_run_id, analysis_batch_id),
        ).fetchone()
    # 旧的直接服务调用没有创建 handoff 记录，保持其既有兼容行为；
    # 一旦存在交接记录，正式保存必须等待当前 attempt 的 ACK。
    if handoff is not None and str(handoff["attempt_status"] or "") != "started":
        raise AppError(409, "WORKFLOW_ANALYSIS_NOT_STARTED", "当前分析批次尚未确认由 Codex 开始处理。")


def _get_analysis_handoff_summary(db: Database, workflow_run_id: str) -> dict[str, object]:
    with db.connect() as connection:
        rows = connection.execute(
            "SELECT * FROM fj_workflow_tasks WHERE workflow_run_id = ? AND task_type = 'deep_job_search_analysis' ORDER BY created_at",
            (workflow_run_id,),
        ).fetchall()
    batches: dict[str, list[Any]] = {}
    for row in rows:
        batches.setdefault(_analysis_batch_id(row, workflow_run_id), []).append(row)
    active_batch_id = next(
        (
            batch_id for batch_id, batch_rows in batches.items()
            if any(row["status"] in {"pending", "running"} for row in batch_rows)
        ),
        next(reversed(batches), ""),
    )
    if not active_batch_id:
        return {
            "analysis_batch_id": "",
            "pending_item_count": 0,
            "running_item_count": 0,
            "succeeded_item_count": 0,
            "handoff_status": "none",
            "handoff_attempt_id": None,
            "attempt_status": "none",
            "needs_initial_codex_handoff": False,
            "needs_next_batch_handoff": False,
            "codex_processing": False,
            "analysis_batch_complete": True,
            "recovery_available": False,
            "awaiting_start_ack": False,
            "start_ack_timed_out": False,
            "retry_available": False,
            "start_ack_timeout_seconds": START_ACK_TIMEOUT_SECONDS,
        }
    batch_rows = batches[active_batch_id]
    counts = {status: sum(row["status"] == status for row in batch_rows) for status in ("pending", "running", "succeeded")}
    with db.connect() as connection:
        handoff = connection.execute(
            "SELECT * FROM fj_workflow_analysis_handoffs WHERE workflow_run_id = ? AND analysis_batch_id = ?",
            (workflow_run_id, active_batch_id),
        ).fetchone()
    handoff_status = str(handoff["status"]) if handoff is not None else "none"
    attempt_status = str(handoff["attempt_status"] or "claimed") if handoff is not None else "none"
    start_ack_timed_out = bool(handoff is not None and attempt_status == "prompt_written" and _start_ack_timed_out(handoff))
    batch_index = list(batches).index(active_batch_id)
    ready_for_handoff = counts["pending"] > 0 and (handoff is None or attempt_status == "released")
    return {
        "analysis_batch_id": active_batch_id,
        "pending_item_count": counts["pending"],
        "running_item_count": counts["running"],
        "succeeded_item_count": counts["succeeded"],
        "handoff_status": handoff_status,
        "handoff_attempt_id": handoff["handoff_attempt_id"] if handoff is not None else None,
        "attempt_status": attempt_status,
        "claimed_at": handoff["claimed_at"] if handoff is not None else None,
        "submitted_at": handoff["submitted_at"] if handoff is not None else None,
        "prompt_written_at": handoff["prompt_written_at"] if handoff is not None else None,
        "started_at": handoff["started_at"] if handoff is not None else None,
        "codex_session_ref": handoff["codex_session_ref"] if handoff is not None else None,
        "needs_initial_codex_handoff": ready_for_handoff and batch_index == 0,
        "needs_next_batch_handoff": ready_for_handoff and batch_index > 0,
        "codex_processing": attempt_status == "started" and (counts["pending"] + counts["running"]) > 0,
        "analysis_batch_complete": counts["pending"] + counts["running"] == 0,
        "recovery_available": False,
        "awaiting_start_ack": attempt_status == "prompt_written",
        "start_ack_timed_out": start_ack_timed_out,
        "retry_available": start_ack_timed_out,
        "start_ack_timeout_seconds": START_ACK_TIMEOUT_SECONDS,
    }


def _complete_analysis_handoff_if_finished(
    db: Database, workflow_run_id: str, analysis_batch_id: str
) -> None:
    with db.connect() as connection:
        rows = connection.execute(
            "SELECT * FROM fj_workflow_tasks WHERE workflow_run_id = ? AND task_type = 'deep_job_search_analysis'",
            (workflow_run_id,),
        ).fetchall()
        unfinished = any(
            _analysis_batch_id(row, workflow_run_id) == analysis_batch_id
            and row["status"] in {"pending", "running"}
            for row in rows
        )
        if not unfinished:
            connection.execute(
                """
                UPDATE fj_workflow_analysis_handoffs
                SET status = 'completed', attempt_status = 'completed', completed_at = ?
                WHERE workflow_run_id = ? AND analysis_batch_id = ? AND attempt_status IN ('claimed', 'prompt_written', 'started')
                """,
                (utc_now(), workflow_run_id, analysis_batch_id),
            )


def _get_run_progress(db: Database, workflow_run_id: str) -> dict[str, object]:
    """从 Run 的持久化任务与 discovery 记录生成业务过程快照。"""
    with db.connect() as connection:
        search_task = connection.execute(
            """
            SELECT * FROM fj_workflow_tasks
            WHERE workflow_run_id = ? AND task_type = 'deep_job_search'
            ORDER BY CASE status WHEN 'running' THEN 0 ELSE 1 END, updated_at DESC, created_at DESC
            LIMIT 1
            """,
            (workflow_run_id,),
        ).fetchone()
        discovery = connection.execute(
            """
            SELECT
              COUNT(*) AS jobs_seen,
              COALESCE(SUM(CASE WHEN is_run_first_discovery = 1 AND is_historical_duplicate = 0 THEN 1 ELSE 0 END), 0) AS fresh_jobs,
              COALESCE(SUM(CASE WHEN is_historical_duplicate = 1 THEN 1 ELSE 0 END), 0) AS historical_duplicates,
              COALESCE(SUM(CASE WHEN is_run_first_discovery = 1 AND is_historical_duplicate = 0 AND is_filter_candidate = 1 THEN 1 ELSE 0 END), 0) AS candidates
            FROM fj_workflow_job_discoveries
            WHERE workflow_run_id = ?
            """,
            (workflow_run_id,),
        ).fetchone()
        combination_metrics = connection.execute(
            """
            SELECT
              COUNT(*) AS combination_count,
              COALESCE(SUM(jobs_seen), 0) AS jobs_seen,
              COALESCE(SUM(run_fresh_jobs), 0) AS run_fresh_jobs,
              COALESCE(SUM(historical_duplicates), 0) AS historical_duplicates,
              COALESCE(SUM(cooldown_excluded), 0) AS cooldown_excluded,
              COALESCE(SUM(strategy_pass), 0) AS strategy_pass,
              COALESCE(SUM(strategy_review), 0) AS strategy_review,
              COALESCE(SUM(strategy_reject), 0) AS strategy_reject,
              COALESCE(SUM(qualified_fresh_jobs), 0) AS qualified_fresh_jobs,
              COALESCE(SUM(candidate_jobs), 0) AS candidate_jobs
            FROM fj_workflow_search_combinations
            WHERE workflow_run_id = ?
            """,
            (workflow_run_id,),
        ).fetchone()
        jd = connection.execute(
            """
            SELECT COUNT(*) AS total, COALESCE(SUM(CASE WHEN status = 'succeeded' THEN 1 ELSE 0 END), 0) AS completed
            FROM fj_workflow_tasks
            WHERE workflow_run_id = ? AND task_type = 'deep_job_search_jd'
            """,
            (workflow_run_id,),
        ).fetchone()
        analysis_rows = connection.execute(
            """
            SELECT result_json FROM fj_workflow_tasks
            WHERE workflow_run_id = ? AND task_type = 'deep_job_search_analysis' AND status = 'succeeded'
            """,
            (workflow_run_id,),
        ).fetchall()
        batch_count = int(connection.execute(
            "SELECT COUNT(*) FROM fj_workflow_tasks WHERE workflow_run_id = ? AND task_type = 'deep_job_search'",
            (workflow_run_id,),
        ).fetchone()[0])
    task_payload = _load(search_task["payload_json"], {}) if search_task is not None else {}
    task_result = _load(search_task["result_json"], {}) if search_task is not None else {}
    decisions = {"recommend": 0, "review": 0, "reject": 0}
    for row in analysis_rows:
        decision = str(_load(row["result_json"], {}).get("decision") or "")
        if decision in decisions:
            decisions[decision] += 1
    use_combination_metrics = int(combination_metrics["combination_count"] or 0) > 0
    # discovery 是候选池的权威行数；组合累计值补充策略与冷却维度统计。
    jobs_seen = int(discovery["jobs_seen"] or 0)
    fresh_jobs = int(discovery["fresh_jobs"] or 0)
    historical_duplicates = int(discovery["historical_duplicates"] or 0)
    return {
        "current_keyword": str(task_payload.get("keyword") or ""),
        "current_city": str(task_payload.get("city") or ""),
        "search_depth": int(task_payload.get("depth") or 0),
        "search_batch_count": batch_count,
        "jobs_seen": jobs_seen,
        "fresh_jobs": fresh_jobs,
        "duplicate_jobs": historical_duplicates,
        "candidates": int(discovery["candidates"] or 0),
        "historical_duplicates": historical_duplicates,
        "cooldown_excluded": int(combination_metrics["cooldown_excluded"] or 0) if use_combination_metrics else 0,
        "strategy_pass": int(combination_metrics["strategy_pass"] or 0) if use_combination_metrics else 0,
        "strategy_review": int(combination_metrics["strategy_review"] or 0) if use_combination_metrics else 0,
        "strategy_reject": int(combination_metrics["strategy_reject"] or 0) if use_combination_metrics else 0,
        "qualified_fresh_jobs": int(discovery["candidates"] or 0),
        "candidate_jobs": int(discovery["candidates"] or 0),
        "novelty_yield": round(fresh_jobs / jobs_seen, 4) if jobs_seen else 0,
        "qualified_novelty_yield": round(int(combination_metrics["qualified_fresh_jobs"] or 0) / fresh_jobs, 4) if use_combination_metrics and fresh_jobs else 0,
        "duplicate_rate": round(historical_duplicates / jobs_seen, 4) if jobs_seen else 0,
        "current_batch_new_jobs": int(task_result.get("new_jobs") or 0),
        "current_batch_duplicates": int(task_result.get("duplicates") or 0),
        "jd_total": int(jd["total"] or 0),
        "jd_completed": int(jd["completed"] or 0),
        "recommend_count": decisions["recommend"],
        "review_count": decisions["review"],
        "reject_count": decisions["reject"],
    }


def _serialize_task(row: Any) -> dict[str, object]:
    capture_status = None
    if row["operation_ref_type"] == "capture_task" and row["operation_ref_id"]:
        try:
            capture_status = boss_capture_task_manager.get_task_status(
                str(row["operation_ref_id"])
            )
        except AppError:
            capture_status = {
                "id": str(row["operation_ref_id"]),
                "status": "unavailable",
                "stage": "unavailable",
                "message": "采集任务状态暂不可用。",
                "progress_current": 0,
                "progress_total": 0,
                "jobs_collected": 0,
                "details_completed": 0,
                "details_failed": 0,
                "current_job": None,
                "error_message": "采集任务可能已随后端进程重启而失去运行上下文。",
                "updated_at": None,
                "finished_at": None,
            }
    return {
        "workflow_task_id": row["id"],
        "task_type": row["task_type"],
        "status": row["status"],
        "payload": _load(row["payload_json"], {}),
        "result": _load(row["result_json"], {}),
        "operation_ref_type": row["operation_ref_type"],
        "operation_ref_id": row["operation_ref_id"],
        "capture": capture_status,
        "retryable": bool(row["retryable"]),
    }


def _serialize_analysis_item(
    row: Any, discovery: Any | None, feedback: list[dict[str, object]]
) -> dict[str, object]:
    item = _serialize_task(row)
    payload = _load(row["payload_json"], {})
    job_payload = _load(discovery["payload_json"], {}) if discovery is not None else {}
    search_combination = _load(discovery["search_combination_json"], {}) if discovery is not None else {}
    item["job"] = {
        "job_id": str(payload.get("job_id") or ""),
        "title": str(discovery["title"] or "") if discovery is not None else "",
        "company": str(discovery["company_name"] or "") if discovery is not None else "",
        "salary": str(discovery["salary"] or "") if discovery is not None else "",
        "city": str(discovery["location"] or "") if discovery is not None else "",
        "discovery_keyword": str(discovery["search_keyword"] or "") if discovery is not None else "",
        "discovery_depth": int(discovery["scroll_depth"] or 0) if discovery is not None else 0,
        "search_combination_id": str(search_combination.get("search_combination_id") or ""),
        "platform_filters": canonicalize_platform_filters(search_combination.get("platform_filters")),
        "filter_result": str(job_payload.get("final_filter_status") or job_payload.get("filter_status") or ""),
        "filter_reasons": list(job_payload.get("filter_reasons") or []),
        "filter_failure_codes": list(job_payload.get("filter_failure_codes") or []),
        "jd_status": str(discovery["detail_status"] or "") if discovery is not None else "",
    }
    item["analysis_result"] = item["result"]
    item["feedback"] = feedback
    return item


def _serialize_feedback(row: Any) -> dict[str, object]:
    return {
        "feedback_id": str(row["id"]),
        "sentiment": str(row["sentiment"]),
        "reason": row["reason"],
        "note": str(row["note"] or ""),
        "created_at": str(row["created_at"]),
    }


def _serialize_snapshot(row: Any) -> dict[str, object]:
    snapshot = _load(row["snapshot_json"], {})
    return {"context_snapshot_id": row["id"], "channel": row["channel"], "sections": snapshot.get("sections", []), "context_characters": row["context_characters"], "estimated_tokens": row["estimated_tokens"], "soft_budget_characters": row["soft_budget_characters"], "hard_budget_characters": row["hard_budget_characters"], "status": row["status"], "blocker_reason": row["blocker_reason"], "generated_at": row["created_at"]}


def _estimate_tokens(characters: int) -> int:
    # 中文与结构化 JSON 以保守比例估算，页面会明确标注为估算值。
    return max(0, (characters + 2) // 3)


def _dump(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _load(value: object, default: Any) -> Any:
    try:
        return json.loads(str(value)) if value not in {None, ""} else default
    except (TypeError, json.JSONDecodeError):
        return default
