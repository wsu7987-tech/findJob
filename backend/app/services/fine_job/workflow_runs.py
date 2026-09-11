from __future__ import annotations

import json
from typing import Any

from backend.app.config import AppConfig
from backend.app.db import Database
from backend.app.errors import AppError
from backend.app.services.fine_job.boss_capture_tasks import boss_capture_task_manager
from backend.app.services.fine_job.boss_capture_history import (
    get_capture_history_job,
    update_capture_job_delivery_evaluation,
)
from backend.app.services.fine_job.boss_scraper.service import BossCaptureRequest, boss_scraper_service
from backend.app.services.fine_job.filter_exclusions import apply_filter_exclusions
from backend.app.services.fine_job.job_evaluation import evaluate_filter_strategy
from backend.app.services.fine_job.profile_store import ensure_default_profile
from backend.app.services.fine_job.profile_context import get_profile_context
from backend.app.services.fine_job.strategies import get_filter_strategy, list_search_keywords
from backend.app.utils import new_id, utc_now


HARD_CONTEXT_BUDGET = 1_000_000


def create_deep_job_search_run(
    db: Database, config: AppConfig, payload: dict[str, Any], *, created_from: str
) -> dict[str, object]:
    """创建搜索 Run；采集器与筛选器仍由既有服务负责。"""
    filter_strategy_id = str(payload["filter_strategy_id"])
    strategy = get_filter_strategy(db, filter_strategy_id)
    if not strategy.get("enabled"):
        raise AppError(409, "FILTER_STRATEGY_DISABLED", "岗位筛选策略当前未启用。")
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

    target_count = int(payload["target_count"])
    candidate_target = int(payload.get("candidate_target_count") or target_count * 3)
    if candidate_target < target_count:
        raise AppError(422, "VALIDATION_FAILED", "候选池目标不能小于本轮推荐岗位目标。")
    min_depth = int(payload.get("min_depth") or 5)
    max_depth = int(payload.get("max_depth") or 20)
    if max_depth < min_depth:
        raise AppError(422, "VALIDATION_FAILED", "最大搜索深度不能小于最低探索深度。")
    contract = {
        "task_type": "deep_job_search",
        "target_count": target_count,
        "counting_rule": "saved_unique_recommend",
        "source_policy": "fresh_only",
        "selected_strategy_ids": {"filter_strategy_id": filter_strategy_id},
        "allowed_search_keywords": requested_keywords,
        "allowed_cities": requested_cities,
        "allow_historical_jobs": False,
        "external_action_policy": "analysis_only",
        "applied_feedback_ids": list(payload.get("applied_feedback_ids") or []),
        "applied_preference_ids": list(payload.get("applied_preference_ids") or []),
        "stop_policy": {
            "min_depth": min_depth,
            "scroll_batch_size": int(payload.get("scroll_batch_size") or 3),
            "max_depth": max_depth,
            "low_yield_streak_limit": int(payload.get("low_yield_streak_limit") or 3),
            "jd_batch_size": int(payload.get("jd_batch_size") or 3),
            "candidate_target_count": candidate_target,
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
            (workflow_run_id, _dump(contract), target_count, now, now),
        )
        for keyword in requested_keywords:
            for city in requested_cities:
                task_id = new_id()
                connection.execute(
                    """
                    INSERT INTO fj_workflow_tasks (
                      id, workflow_run_id, task_type, payload_json, result_json, created_at, updated_at
                    ) VALUES (?, ?, 'deep_job_search', ?, '{}', ?, ?)
                    """,
                    (task_id, workflow_run_id, _dump({"keyword": keyword, "city": city, "depth": 0, "low_yield_streak": 0}), now, now),
                )
    snapshot = _create_search_context_snapshot(db, workflow_run_id, strategy, contract, int(payload.get("context_soft_budget_characters") or 12000))
    if snapshot["status"] == "blocked":
        _wait_for_user(db, workflow_run_id, "context_budget_exceeded", str(snapshot["blocker_reason"]))
    return get_workflow_run(db, workflow_run_id)


def advance_deep_job_search(db: Database, config: AppConfig, workflow_run_id: str) -> dict[str, object]:
    """推进一个采集批次；Codex 只在候选池准备完成后参与。"""
    run = _require_run(db, workflow_run_id)
    if bool(run["paused"]):
        return get_workflow_run(db, workflow_run_id)
    if run["status"] in {"completed", "cancelled", "failed", "waiting_codex", "waiting_for_user"}:
        return get_workflow_run(db, workflow_run_id)
    contract = _load(run["completion_contract_json"], {})
    jd_task = _next_jd_task(db, workflow_run_id)
    if jd_task is not None:
        return _advance_jd_collection(db, config, workflow_run_id, jd_task, contract)
    task = _next_task(db, workflow_run_id)
    if task is None:
        _wait_for_user(db, workflow_run_id, "new_jobs_insufficient", "所有已批准搜索组合均已完成，fresh_only 候选池仍不足。")
        return get_workflow_run(db, workflow_run_id)
    payload = _load(task["payload_json"], {})
    capture_task_id = str(task["operation_ref_id"] or "")
    if not capture_task_id:
        if not boss_scraper_service.get_browser_status().running:
            _wait_for_user(db, workflow_run_id, "browser_not_running", "FineJob 专用 Chrome 未启动，暂不能继续 deep_job_search。")
            return get_workflow_run(db, workflow_run_id)
        pages = min(int(contract["stop_policy"]["min_depth"]), 10)
        capture = boss_capture_task_manager.start_capture(
            BossCaptureRequest(
                keyword=str(payload["keyword"]), city=str(payload["city"]), pages=pages,
                filters={}, include_details=False, max_details=None,
                output_dir=config.output_root / "fine-job" / "boss-capture",
                prefer_current_page=True, filter_strategy_id=str(contract["selected_strategy_ids"]["filter_strategy_id"]),
            ), output_dir=config.output_root / "fine-job" / "boss-capture", db=db,
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
        _finish_task(db, task["id"], "failed", payload, {"error": capture.get("error_message")})
        return advance_deep_job_search(db, config, workflow_run_id)
    _record_batch(db, workflow_run_id, task, capture, contract)
    return _decide_next_step(db, config, workflow_run_id, task, capture, contract)


def get_workflow_run(db: Database, workflow_run_id: str) -> dict[str, object]:
    run = _require_run(db, workflow_run_id)
    with db.connect() as connection:
        tasks = connection.execute("SELECT * FROM fj_workflow_tasks WHERE workflow_run_id = ? ORDER BY created_at", (workflow_run_id,)).fetchall()
        snapshots = connection.execute("SELECT * FROM fj_workflow_context_snapshots WHERE workflow_run_id = ? ORDER BY created_at", (workflow_run_id,)).fetchall()
    return {
        **_serialize_run(run),
        "progress": _get_run_progress(db, workflow_run_id),
        "tasks": [_serialize_task(row) for row in tasks],
        "context_snapshots": [_serialize_snapshot(row) for row in snapshots],
    }


def get_latest_active_workflow_run(db: Database) -> dict[str, object] | None:
    """返回最近一个仍可继续查看或推进的 Workflow Run。"""
    with db.connect() as connection:
        row = connection.execute(
            """
            SELECT * FROM fj_workflow_runs
            WHERE status NOT IN ('completed', 'completed_with_errors', 'cancelled', 'failed')
            ORDER BY updated_at DESC, created_at DESC
            LIMIT 1
            """
        ).fetchone()
    return get_workflow_run(db, str(row["id"])) if row is not None else None


def get_context_snapshot(db: Database, workflow_run_id: str, channel: str = "deep_job_search") -> dict[str, object]:
    with db.connect() as connection:
        row = connection.execute("SELECT * FROM fj_workflow_context_snapshots WHERE workflow_run_id = ? AND channel = ?", (workflow_run_id, channel)).fetchone()
    if row is None:
        raise AppError(404, "CONTEXT_SNAPSHOT_NOT_FOUND", "本轮上下文快照不存在。")
    return _serialize_snapshot(row)


def list_workflow_analysis_items(db: Database, workflow_run_id: str) -> dict[str, object]:
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
    return {"workflow_run_id": workflow_run_id, "items": [_serialize_task(row) for row in rows]}


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
    shared = get_context_snapshot(db, workflow_run_id, "candidate_analysis")
    sections = [
        _section("analysis_item", "analysis_item", {"workflow_task_id": workflow_task_id, "job_id": job_id}, "workflow_task", None, True, ""),
        _section("job_material", "analysis_item", _compact_job_for_analysis(job), "boss_job", int(job.get("detail_version") or 0), True, "只包含当前岗位的 JD 与必要岗位事实。"),
        _section("strategy_reference", "analysis_item", {"filter_strategy_id": strategy["id"], "strategy_version": strategy.get("strategy_version")}, "filter_strategy", int(strategy.get("strategy_version") or 1), True, ""),
        _section("shared_base_reference", "analysis_item", {"context_snapshot_id": shared["context_snapshot_id"], "profile_versions": _shared_profile_versions(shared)}, "workflow_context_snapshot", None, True, "候选人事实与偏好请复用已读取的 Shared Base。"),
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
        "expected_output": {"decision": "recommend | review | reject", "confidence": "0 到 1", "summary": "简要结论", "reasons": ["依据"], "risks": ["风险"]},
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
    job_id = str(payload.get("job_id") or "")
    job = get_capture_history_job(db, job_id)
    update_capture_job_delivery_evaluation(db, job=job, evaluation=evaluation)
    _finish_task(
        db,
        workflow_task_id,
        "succeeded",
        payload,
        {"job_id": job_id, "decision": decision, "evaluation_id": evaluation_id},
    )
    _refresh_counts(db, workflow_run_id)
    refreshed = _require_run(db, workflow_run_id)
    contract = _load(refreshed["completion_contract_json"], {})
    if int(refreshed["completed_count"] or 0) >= int(contract["target_count"]):
        _update_run(db, workflow_run_id, status="completed", current_step="completed", next_action="", next_action_reason="已达到本轮正式 recommend 目标。", completed_at=utc_now())
        return get_workflow_run(db, workflow_run_id)
    if _next_analysis_task(db, workflow_run_id) is not None:
        _update_run(db, workflow_run_id, status="waiting_codex", current_step="waiting_codex", next_action="codex_analysis", next_action_reason="当前 JD 批次仍有待保存的岗位分析 Item。")
        return get_workflow_run(db, workflow_run_id)
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


def resume_deep_job_search_run(db: Database, workflow_run_id: str) -> dict[str, object]:
    """仅在用户确认后恢复中断的采集组合，避免后台静默重复采集。"""
    run = _require_run(db, workflow_run_id)
    if bool(run["paused"]):
        _update_run(
            db,
            workflow_run_id,
            paused=0,
            next_action=str(run["paused_from_next_action"] or "continue_workflow"),
            next_action_reason=str(run["paused_from_next_action_reason"] or "已恢复 Workflow 自动推进。"),
            paused_from_next_action="",
            paused_from_next_action_reason="",
        )
        return get_workflow_run(db, workflow_run_id)
    if run["status"] != "waiting_for_user":
        raise AppError(409, "WORKFLOW_NOT_WAITING", "当前 Workflow Run 不在等待用户恢复状态。")
    if str(run["stop_reason"] or "") not in {"capture_interrupted", "browser_not_running"}:
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


def pause_deep_job_search_run(db: Database, workflow_run_id: str) -> dict[str, object]:
    """暂停 Workflow 自动推进，保留已启动采集和所有已获得成果。"""
    run = _require_run(db, workflow_run_id)
    if run["status"] in {"completed", "completed_with_errors", "cancelled", "failed"}:
        raise AppError(409, "WORKFLOW_NOT_PAUSABLE", "当前 Workflow Run 已结束，不能暂停。")
    if bool(run["paused"]):
        return get_workflow_run(db, workflow_run_id)
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


def cancel_deep_job_search_run(db: Database, workflow_run_id: str) -> dict[str, object]:
    """停止 Workflow，并在列表采集仍运行时请求已有采集器安全停止。"""
    run = _require_run(db, workflow_run_id)
    if run["status"] in {"completed", "completed_with_errors", "cancelled", "failed"}:
        return get_workflow_run(db, workflow_run_id)
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
    with db.connect() as connection:
        connection.execute(
            """
            UPDATE fj_workflow_tasks
            SET status = 'skipped', updated_at = ?, completed_at = COALESCE(completed_at, ?)
            WHERE workflow_run_id = ? AND status IN ('pending', 'running', 'waiting_for_user')
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


def _record_batch(db: Database, workflow_run_id: str, task: Any, capture: dict[str, object], contract: dict[str, Any]) -> None:
    strategy = get_filter_strategy(db, str(contract["selected_strategy_ids"]["filter_strategy_id"]))
    results = evaluate_filter_strategy(list(capture.get("jobs") or []), strategy)
    _jobs, results = apply_filter_exclusions(db, strategy, list(capture.get("jobs") or []), results)
    boss_capture_task_manager.apply_filter_results(str(capture["id"]), results)
    result_by_id = {str(item["job_id"]): item for item in results}
    payload = _load(task["payload_json"], {})
    new_candidates = 0
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
                (new_id(), workflow_run_id, task["id"], job_id, payload["keyword"], payload["city"], _dump({"filter_strategy_id": strategy["id"]}), int(capture.get("total_pages_loaded") or 0), now, int(first), int(is_duplicate), int(filter_result.get("status") in {"pass", "review"})),
            )
            if first and not is_duplicate and filter_result.get("status") in {"pass", "review"}:
                new_candidates += 1
    payload["depth"] = int(capture.get("total_pages_loaded") or payload.get("depth") or 0)
    payload["low_yield_streak"] = int(payload.get("low_yield_streak") or 0) + 1 if new_candidates == 0 else 0
    _finish_task(db, task["id"], "succeeded", payload, {"new_jobs": int(capture.get("last_added_jobs") or 0), "duplicates": int(capture.get("duplicate_jobs_count") or 0), "new_candidates": new_candidates, "capture_task_id": capture["id"]})
    _refresh_counts(db, workflow_run_id)


def _decide_next_step(db: Database, config: AppConfig, workflow_run_id: str, task: Any, capture: dict[str, object], contract: dict[str, Any]) -> dict[str, object]:
    run = _require_run(db, workflow_run_id)
    candidate_target = int(contract["stop_policy"]["candidate_target_count"])
    telemetry = _load(run["telemetry_json"], {})
    if int(telemetry.get("fresh_candidates") or 0) >= candidate_target:
        _create_jd_tasks(db, workflow_run_id, candidate_target)
        return advance_deep_job_search(db, config, workflow_run_id)
    payload = _load(task["payload_json"], {})
    can_continue = bool(capture.get("continuation_available") and capture.get("has_more"))
    depth = int(payload.get("depth") or 0)
    stop = contract["stop_policy"]
    if can_continue and depth < int(stop["max_depth"]) and int(payload.get("low_yield_streak") or 0) < int(stop["low_yield_streak_limit"]):
        next_pages = min(int(stop["scroll_batch_size"]), int(stop["max_depth"]) - depth, 10)
        continued = boss_capture_task_manager.continue_capture(str(capture["id"]), pages=max(1, next_pages))
        # 续采属于同一搜索组合；建立新 Task 保存每个批次的状态与来源。
        now = utc_now()
        with db.connect() as connection:
            next_task_id = new_id()
            connection.execute("INSERT INTO fj_workflow_tasks (id, workflow_run_id, task_type, status, payload_json, operation_ref_type, operation_ref_id, created_at, updated_at) VALUES (?, ?, 'deep_job_search', 'running', ?, 'capture_task', ?, ?, ?)", (next_task_id, workflow_run_id, _dump(payload), str(continued["id"]), now, now))
        _update_run(db, workflow_run_id, status="running", current_step="searching", next_action="continue_scroll", next_action_reason="候选池尚未达到目标，当前组合仍有增量搜索预算。")
        return get_workflow_run(db, workflow_run_id)
    return advance_deep_job_search(db, config, workflow_run_id)


def _create_jd_tasks(db: Database, workflow_run_id: str, candidate_target: int) -> int:
    """按稳定发现顺序选择尚未处理的候选，创建一个小批次 JD 任务。"""
    run = _require_run(db, workflow_run_id)
    contract = _load(run["completion_contract_json"], {})
    jd_target = min(int(contract["stop_policy"].get("jd_batch_size") or 3), candidate_target)
    with db.connect() as connection:
        candidates = connection.execute(
            """
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
            ORDER BY d.discovered_at ASC, d.job_id ASC
            LIMIT ?
            """,
            (workflow_run_id, jd_target),
        ).fetchall()
        now = utc_now()
        batch_id = new_id()
        for candidate in candidates:
            job_id = str(candidate["job_id"])
            connection.execute(
                """
                INSERT INTO fj_workflow_tasks (
                  id, workflow_run_id, task_type, payload_json, result_json, created_at, updated_at
                ) VALUES (?, ?, 'deep_job_search_jd', ?, '{}', ?, ?)
                """,
                (new_id(), workflow_run_id, _dump({"job_id": job_id, "jd_batch_id": batch_id}), now, now),
            )
    if not candidates:
        return 0
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
    if _create_jd_tasks(db, workflow_run_id, int(contract["stop_policy"]["candidate_target_count"])):
        advance_deep_job_search(db, config, workflow_run_id)
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
    with db.connect() as connection:
        for jd_row, job_id in zip(jd_rows, job_ids, strict=True):
            if not job_id:
                continue
            connection.execute(
                """
                INSERT INTO fj_workflow_tasks (
                  id, workflow_run_id, task_type, payload_json, result_json, created_at, updated_at
                ) VALUES (?, ?, 'deep_job_search_analysis', ?, '{}', ?, ?)
                """,
                (new_id(), workflow_run_id, _dump({"job_id": job_id, "jd_task_id": jd_row["id"]}), now, now),
            )
    _update_run(
        db,
        workflow_run_id,
        status="waiting_codex",
        current_step="waiting_codex",
        next_action="codex_analysis",
        next_action_reason="本批岗位 JD 已准备完成，等待 Codex 逐岗位保存正式分析结果。",
    )


def _create_candidate_analysis_snapshot(
    db: Database, workflow_run_id: str, contract: dict[str, Any]
) -> dict[str, object]:
    profile = ensure_default_profile(db)
    profile_context = get_profile_context(db, str(profile["id"]), view="search", persist_artifact=False)
    strategy = get_filter_strategy(db, str(contract["selected_strategy_ids"]["filter_strategy_id"]))
    sections = [
        _section("task_goal", "shared_base", {"target_count": contract["target_count"], "counting_rule": "saved_unique_recommend", "external_action_policy": contract["external_action_policy"]}, "workflow_run", 1, True, ""),
        _section("candidate_facts", "shared_base", {"profile_id": profile_context["profile_id"], "versions": profile_context["versions"], "facts_markdown": profile_context["markdown"]}, "candidate_profile", int(profile_context["artifact_version"]), True, "已使用 search 视图，不包含完整简历或 normalized resume。"),
        _section("filter_strategy_summary", "shared_base", _compact_filter_strategy(strategy), "filter_strategy", int(strategy.get("strategy_version") or 1), True, ""),
        _section("applied_feedback", "shared_base", {"applied_feedback_ids": contract.get("applied_feedback_ids") or [], "applied_preference_ids": contract.get("applied_preference_ids") or []}, "workflow_contract", 1, True, "仅保存已应用引用，不注入无关反馈全文。"),
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


def _create_search_context_snapshot(db: Database, workflow_run_id: str, strategy: dict[str, object], contract: dict[str, Any], soft_budget: int) -> dict[str, object]:
    profile = ensure_default_profile(db)
    sections = [
        _section("task_goal", "shared_base", {"target_count": contract["target_count"], "source_policy": contract["source_policy"], "keywords": contract["allowed_search_keywords"], "cities": contract["allowed_cities"]}, "workflow_run", 1, True, ""),
        _section("filter_strategy", "task_channel", strategy, "filter_strategy", int(strategy.get("strategy_version") or 1), True, ""),
        _section("candidate_compact_facts", "shared_base", {"profile_id": profile["id"], "versions": profile["versions"]}, "candidate_profile", int(profile["versions"]["facts_version"]), True, "搜索阶段只注入候选人版本摘要；详细事实在 JD 分析 Item 按需读取。"),
        _section("complete_resume", "excluded", None, "resume", None, False, "搜索阶段不需要完整简历。"),
        _section("historical_payload", "excluded", None, "boss_job", None, False, "fresh_only 搜索不注入历史岗位 payload。"),
        _section("unrelated_chat", "excluded", None, "chat", None, False, "deep_job_search 不读取无关聊天。"),
    ]
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
        return connection.execute("SELECT * FROM fj_workflow_tasks WHERE workflow_run_id = ? AND task_type = 'deep_job_search' AND status IN ('pending', 'running') ORDER BY CASE status WHEN 'running' THEN 0 ELSE 1 END, created_at LIMIT 1", (workflow_run_id,)).fetchone()


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


def _resume_search_or_wait(db: Database, config: AppConfig, workflow_run_id: str) -> None:
    _update_run(db, workflow_run_id, status="pending", current_step="searching", next_action="continue_search", next_action_reason="当前候选池已处理完，继续寻找 fresh_only 候选。", waiting_for_user=0, stop_reason="")
    if _next_task(db, workflow_run_id) is None:
        _wait_for_user(db, workflow_run_id, "new_jobs_insufficient", "已批准搜索组合与候选池均已处理完，仍未达到正式 recommend 目标。")
        return
    advance_deep_job_search(db, config, workflow_run_id)


def _refresh_counts(db: Database, workflow_run_id: str) -> None:
    with db.connect() as connection:
        fresh = int(connection.execute("SELECT COUNT(DISTINCT d.job_id) FROM fj_workflow_job_discoveries d WHERE d.workflow_run_id = ? AND d.is_run_first_discovery = 1 AND d.is_historical_duplicate = 0 AND d.is_filter_candidate = 1", (workflow_run_id,)).fetchone()[0])
        run = _require_run(db, workflow_run_id)
        target = int(_load(run["completion_contract_json"], {}).get("target_count") or 0)
        completed = int(connection.execute("SELECT COUNT(DISTINCT json_extract(result_json, '$.job_id')) FROM fj_workflow_tasks WHERE workflow_run_id = ? AND task_type = 'deep_job_search_analysis' AND status = 'succeeded' AND json_extract(result_json, '$.decision') = 'recommend'", (workflow_run_id,)).fetchone()[0])
        available = int(connection.execute("""SELECT COUNT(DISTINCT d.job_id) FROM fj_workflow_job_discoveries d WHERE d.workflow_run_id = ? AND d.is_run_first_discovery = 1 AND d.is_historical_duplicate = 0 AND d.is_filter_candidate = 1 AND NOT EXISTS (SELECT 1 FROM fj_workflow_tasks t WHERE t.workflow_run_id = d.workflow_run_id AND t.task_type IN ('deep_job_search_jd', 'deep_job_search_analysis') AND json_extract(t.payload_json, '$.job_id') = d.job_id)""", (workflow_run_id,)).fetchone()[0])
        telemetry = _load(run["telemetry_json"], {})
        telemetry["fresh_candidates"] = fresh
        telemetry["available_fresh_candidates"] = available
        telemetry["search_batches"] = int(connection.execute("SELECT COUNT(*) FROM fj_workflow_tasks WHERE workflow_run_id = ? AND task_type = 'deep_job_search'", (workflow_run_id,)).fetchone()[0])
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


def _require_run(db: Database, workflow_run_id: str):
    with db.connect() as connection:
        row = connection.execute("SELECT * FROM fj_workflow_runs WHERE id = ?", (workflow_run_id,)).fetchone()
    if row is None:
        raise AppError(404, "WORKFLOW_RUN_NOT_FOUND", "Workflow Run 不存在。")
    return row


def _serialize_run(row: Any) -> dict[str, object]:
    return {"workflow_run_id": row["id"], "workflow_type": row["workflow_type"], "completion_contract": _load(row["completion_contract_json"], {}), "status": "paused" if bool(row["paused"]) else row["status"], "completed_count": row["completed_count"], "remaining_count": row["remaining_count"], "current_step": row["current_step"], "next_action": row["next_action"], "next_action_reason": row["next_action_reason"], "waiting_for_user": bool(row["waiting_for_user"]), "stop_reason": row["stop_reason"], "codex_session_ref": row["codex_session_ref"], "codex_runtime_id": row["codex_runtime_id"], "telemetry": _load(row["telemetry_json"], {}), "created_at": row["created_at"], "updated_at": row["updated_at"], "completed_at": row["completed_at"]}


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
              COALESCE(SUM(CASE WHEN is_run_first_discovery = 1 AND is_historical_duplicate = 0 AND is_filter_candidate = 1 THEN 1 ELSE 0 END), 0) AS candidates
            FROM fj_workflow_job_discoveries
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
    jobs_seen = int(discovery["jobs_seen"] or 0)
    fresh_jobs = int(discovery["fresh_jobs"] or 0)
    return {
        "current_keyword": str(task_payload.get("keyword") or ""),
        "current_city": str(task_payload.get("city") or ""),
        "search_depth": int(task_payload.get("depth") or 0),
        "search_batch_count": batch_count,
        "jobs_seen": jobs_seen,
        "fresh_jobs": fresh_jobs,
        "duplicate_jobs": max(0, jobs_seen - fresh_jobs),
        "candidates": int(discovery["candidates"] or 0),
        "current_batch_new_jobs": int(task_result.get("new_jobs") or 0),
        "current_batch_duplicates": int(task_result.get("duplicates") or 0),
        "jd_total": int(jd["total"] or 0),
        "jd_completed": int(jd["completed"] or 0),
        "recommend_count": decisions["recommend"],
        "review_count": decisions["review"],
        "reject_count": decisions["reject"],
    }


def _serialize_task(row: Any) -> dict[str, object]:
    return {"workflow_task_id": row["id"], "task_type": row["task_type"], "status": row["status"], "payload": _load(row["payload_json"], {}), "result": _load(row["result_json"], {}), "operation_ref_type": row["operation_ref_type"], "operation_ref_id": row["operation_ref_id"], "retryable": bool(row["retryable"])}


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
