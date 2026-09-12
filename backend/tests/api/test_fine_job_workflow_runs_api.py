from __future__ import annotations

from backend.app.services.fine_job import workflow_runs
from backend.app.services.fine_job.boss_capture_history import (
    create_capture_batch,
    record_capture_jobs,
)
from backend.app.services.fine_job.boss_scraper.service import BossBrowserStatus
from backend.app.services.fine_job.codex_tools import CodexToolService
from backend.app.errors import AppError
from backend.app.utils import new_id, utc_now


def _strategy_payload(**updates):
    payload = {
        "name": "Workflow Run 筛选策略",
        "enabled": True,
        "search_keywords": ["AI Agent"],
        "cities": ["广州"],
        "title_include_any": ["Agent"],
        "unknown_value_policy": "review",
    }
    payload.update(updates)
    return payload


def _create_run(configured_client, **updates):
    strategy = configured_client.post(
        "/api/fine-job/strategies/filters", json=_strategy_payload()
    ).json()["strategy"]
    profile = configured_client.get("/api/fine-job/profiles").json()["profiles"][0]
    resume = configured_client.post(
        f"/api/fine-job/profiles/{profile['id']}/resume-versions",
        json={
            "name": "Workflow 分析简历",
            "version_type": "base",
            "current_role": "base",
            "content": "具备 Python 与 AI Agent 项目经验。",
        },
    ).json()["resume_version"]
    recommendation = configured_client.post(
        "/api/fine-job/strategies/recommendations",
        json={
            "name": "Workflow 建议投递策略",
            "enabled": True,
            "filter_strategy_id": strategy["id"],
            "candidate_profile_id": profile["id"],
            "resume_version_id": resume["id"],
            "evaluation_method": "hybrid",
        },
    ).json()["strategy"]
    deep_job_search = {
        "filter_strategy_id": strategy["id"],
        "recommendation_strategy_id": recommendation["id"],
        "codex_model": "gpt-5.6-luna",
        "codex_reasoning_effort": "medium",
        "target_count": 2,
        "candidate_target_count": 4,
        "allowed_search_keywords": ["AI Agent"],
        "allowed_cities": ["广州"],
        "applied_feedback_ids": ["feedback-1"],
        "applied_preference_ids": ["preference-1"],
    }
    deep_job_search.update(updates)
    if "recommend_target" in updates and "target_count" not in updates:
        deep_job_search.pop("target_count")
    response = configured_client.post(
        "/api/fine-job/workflow-runs",
        json={
            "task_type": "deep_job_search",
            "created_from": "task_cockpit",
            "deep_job_search": deep_job_search,
        },
    )
    assert response.status_code == 201
    return response.json()


def test_create_workflow_run_exposes_real_search_context_snapshot(configured_client) -> None:
    run = _create_run(configured_client)

    assert run["status"] == "pending"
    assert run["completion_contract"]["source_policy"] == "fresh_only"
    assert run["completion_contract"]["recommend_target"] == 2
    assert run["completion_contract"]["review_target"] is None
    assert run["completion_contract"]["target_mode"] == "all"
    assert run["completion_contract"]["counting_rule"] == "saved_unique_recommend_and_optional_review"
    assert run["completion_contract"]["analysis_policy"] == {
        "analyze_all_candidates": False,
        "stop_after_current_batch": False,
        "analysis_batch_size": 5,
    }
    assert run["completion_contract"]["execution_policy"] == {
        "after_analysis_batch": "auto_continue",
        "codex_handoff": "auto",
    }
    assert run["completion_progress"] == {
        "recommend": {"current": 0, "target": 2, "remaining": 2, "reached": False},
        "review": {"current": 0, "target": None, "remaining": None, "reached": None},
        "target_mode": "all",
        "target_reached": False,
    }
    assert run["completion_contract"]["allow_historical_jobs"] is False
    assert run["completion_contract"]["applied_feedback_ids"] == ["feedback-1"]
    assert run["completion_contract"]["selected_strategy_ids"]["recommendation_strategy_id"]
    assert run["completion_contract"]["selected_strategy_versions"]["recommendation_strategy_version"] >= 1
    assert run["completion_contract"]["codex_execution_config"] == {
        "model": "gpt-5.6-luna", "reasoning_effort": "medium"
    }
    assert run["completion_contract"]["external_action_policy"] == "analysis_only"
    snapshot_response = configured_client.get(
        f"/api/fine-job/workflow-runs/{run['workflow_run_id']}/context-snapshot"
    )

    assert snapshot_response.status_code == 200
    snapshot = snapshot_response.json()
    assert snapshot["status"] == "ready"
    assert snapshot["context_characters"] > 0
    assert snapshot["soft_budget_characters"] == 12000
    assert any(
        item["section_id"] == "complete_resume" and not item["included"]
        for item in snapshot["sections"]
    )


def test_workflow_run_can_require_manual_codex_handoff(configured_client) -> None:
    run = _create_run(configured_client, execution_policy_codex_handoff="manual")

    assert run["completion_contract"]["execution_policy"] == {
        "after_analysis_batch": "auto_continue",
        "codex_handoff": "manual",
    }


def test_workflow_rejects_recommendation_strategy_from_another_filter(configured_client) -> None:
    run = _create_run(configured_client)
    other = configured_client.post(
        "/api/fine-job/strategies/filters", json=_strategy_payload(name="其他筛选策略")
    ).json()["strategy"]
    response = configured_client.post(
        "/api/fine-job/workflow-runs",
        json={
            "task_type": "deep_job_search",
            "deep_job_search": {
                "filter_strategy_id": other["id"],
                "recommendation_strategy_id": run["completion_contract"]["selected_strategy_ids"]["recommendation_strategy_id"],
                "codex_model": "gpt-5.6-luna",
                "codex_reasoning_effort": "medium",
                "target_count": 1,
                "allowed_search_keywords": ["AI Agent"],
                "allowed_cities": ["广州"],
            },
        },
    )
    assert response.status_code == 422
    assert response.json()["error_category"] == "RECOMMENDATION_FILTER_MISMATCH"


def test_latest_workflow_run_restores_the_recent_unfinished_run(configured_client) -> None:
    run = _create_run(configured_client)

    response = configured_client.get("/api/fine-job/workflow-runs/latest")

    assert response.status_code == 200
    assert response.json()["workflow_run"]["workflow_run_id"] == run["workflow_run_id"]


def test_pause_preserves_running_capture_and_resume_allows_auto_advance(
    configured_client, monkeypatch
) -> None:
    run = _create_run(configured_client)
    monkeypatch.setattr(
        workflow_runs.boss_scraper_service,
        "get_browser_status",
        lambda: BossBrowserStatus(running=True, cdp_port=9222),
    )
    monkeypatch.setattr(
        workflow_runs.boss_capture_task_manager,
        "start_capture",
        lambda request, **kwargs: {"id": "capture-running"},
    )

    configured_client.post(f"/api/fine-job/workflow-runs/{run['workflow_run_id']}/advance")
    paused = configured_client.post(f"/api/fine-job/workflow-runs/{run['workflow_run_id']}/pause")
    resumed = configured_client.post(f"/api/fine-job/workflow-runs/{run['workflow_run_id']}/resume")

    assert paused.status_code == 200
    assert paused.json()["status"] == "paused"
    assert paused.json()["tasks"][0]["status"] == "running"
    assert resumed.status_code == 200
    assert resumed.json()["status"] == "running"


def test_cancel_stops_active_list_capture_and_blocks_future_tasks(
    configured_client, monkeypatch
) -> None:
    run = _create_run(configured_client)
    monkeypatch.setattr(
        workflow_runs.boss_scraper_service,
        "get_browser_status",
        lambda: BossBrowserStatus(running=True, cdp_port=9222),
    )
    monkeypatch.setattr(
        workflow_runs.boss_capture_task_manager,
        "start_capture",
        lambda request, **kwargs: {"id": "capture-running"},
    )
    stopped: list[str] = []
    monkeypatch.setattr(
        workflow_runs.boss_capture_task_manager,
        "stop_capture",
        lambda task_id: (stopped.append(task_id) or {"id": task_id}),
    )

    configured_client.post(f"/api/fine-job/workflow-runs/{run['workflow_run_id']}/advance")
    cancelled = configured_client.post(f"/api/fine-job/workflow-runs/{run['workflow_run_id']}/cancel")
    advanced = configured_client.post(f"/api/fine-job/workflow-runs/{run['workflow_run_id']}/advance")

    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert stopped == ["capture-running"]
    assert all(task["status"] == "skipped" for task in cancelled.json()["tasks"])
    assert advanced.json()["status"] == "cancelled"


def test_context_soft_budget_blocks_run_before_search_starts(configured_client) -> None:
    strategy = configured_client.post(
        "/api/fine-job/strategies/filters",
        json=_strategy_payload(notes="上下文预算测试" * 500),
    ).json()["strategy"]
    profile = configured_client.get("/api/fine-job/profiles").json()["profiles"][0]
    resume = configured_client.post(
        f"/api/fine-job/profiles/{profile['id']}/resume-versions",
        json={"name": "预算测试简历", "version_type": "base", "current_role": "base", "content": "Python"},
    ).json()["resume_version"]
    recommendation = configured_client.post(
        "/api/fine-job/strategies/recommendations",
        json={
            "name": "预算测试建议策略", "filter_strategy_id": strategy["id"],
            "candidate_profile_id": profile["id"], "resume_version_id": resume["id"],
        },
    ).json()["strategy"]
    response = configured_client.post(
        "/api/fine-job/workflow-runs",
        json={
            "task_type": "deep_job_search",
            "deep_job_search": {
                "filter_strategy_id": strategy["id"],
                "recommendation_strategy_id": recommendation["id"],
                "codex_model": "gpt-5.6-luna",
                "codex_reasoning_effort": "medium",
                "target_count": 1,
                "allowed_search_keywords": ["AI Agent"],
                "allowed_cities": ["广州"],
                "context_soft_budget_characters": 1000,
            },
        },
    )

    assert response.status_code == 201
    run = response.json()
    assert run["status"] == "waiting_for_user"
    assert run["stop_reason"] == "context_budget_exceeded"
    assert run["context_snapshots"][0]["status"] == "blocked"


def test_fresh_only_count_excludes_historical_discoveries_and_records_source(
    configured_client, test_db, monkeypatch
) -> None:
    run = _create_run(configured_client)
    search_task = run["tasks"][0]
    with test_db.connect() as connection:
        search_task_row = connection.execute(
            "SELECT * FROM fj_workflow_tasks WHERE id = ?",
            (search_task["workflow_task_id"],),
        ).fetchone()
    capture_id = new_id()
    now = utc_now()
    create_capture_batch(
        test_db,
        capture_id=capture_id,
        keyword="AI Agent",
        city="广州",
        pages=5,
        auto_details=False,
        created_at=now,
    )
    jobs = record_capture_jobs(
        test_db,
        capture_id=capture_id,
        search_keyword="AI Agent",
        jobs=[
            {
                "job_id": "source-job-1",
                "encrypt_job_id": "encrypt-job-1",
                "title": "AI Agent 开发工程师",
                "company_name": "示例公司",
                "location": "广州",
            }
        ],
        collected_at=now,
    )
    historical_jobs = record_capture_jobs(
        test_db,
        capture_id=capture_id,
        search_keyword="AI Agent",
        jobs=[
            {
                "job_id": "source-job-history",
                "encrypt_job_id": "encrypt-job-history",
                "title": "AI Agent 历史岗位",
                "company_name": "历史公司",
                "location": "广州",
            }
        ],
        collected_at=now,
    )
    monkeypatch.setattr(
        workflow_runs.boss_capture_task_manager,
        "apply_filter_results",
        lambda task_id, results: {"id": task_id, "results": results},
    )
    workflow_runs._record_batch(
        test_db,
        run["workflow_run_id"],
        search_task_row,
        {
            "id": capture_id,
            "jobs": jobs,
            "total_pages_loaded": 5,
            "last_added_jobs": 1,
            "duplicate_jobs_count": 0,
        },
        run["completion_contract"],
    )
    with test_db.connect() as connection:
        discovery = connection.execute(
            "SELECT * FROM fj_workflow_job_discoveries WHERE workflow_run_id = ?",
            (run["workflow_run_id"],),
        ).fetchone()
        connection.execute(
            """
            INSERT INTO fj_workflow_job_discoveries (
              id, workflow_run_id, task_id, job_id, search_keyword, city,
              search_combination_json, scroll_depth, discovered_at,
              is_run_first_discovery, is_historical_duplicate, is_filter_candidate
            ) VALUES (?, ?, ?, ?, ?, ?, '{}', 5, ?, 1, 1, 1)
            """,
            (
                new_id(),
                run["workflow_run_id"],
                search_task["workflow_task_id"],
                historical_jobs[0]["history_record_id"],
                "AI Agent",
                "广州",
                utc_now(),
            ),
        )
    workflow_runs._refresh_counts(test_db, run["workflow_run_id"])
    refreshed = configured_client.get(
        f"/api/fine-job/workflow-runs/{run['workflow_run_id']}"
    ).json()

    assert discovery["search_keyword"] == "AI Agent"
    assert discovery["city"] == "广州"
    assert discovery["scroll_depth"] == 5
    assert discovery["is_filter_candidate"] == 1
    assert refreshed["telemetry"]["fresh_candidates"] == 1
    assert refreshed["progress"]["current_keyword"] == "AI Agent"
    assert refreshed["progress"]["current_city"] == "广州"
    assert refreshed["progress"]["jobs_seen"] == 2
    assert refreshed["progress"]["fresh_jobs"] == 1
    assert refreshed["progress"]["duplicate_jobs"] == 1
    assert refreshed["progress"]["candidates"] == 1
    assert refreshed["completed_count"] == 0
    assert refreshed["remaining_count"] == 2


def test_exhausted_search_tasks_waits_for_new_jobs_not_history(configured_client, test_db) -> None:
    run = _create_run(configured_client)
    with test_db.connect() as connection:
        connection.execute(
            "UPDATE fj_workflow_tasks SET status = 'succeeded' WHERE workflow_run_id = ?",
            (run["workflow_run_id"],),
        )

    response = configured_client.post(
        f"/api/fine-job/workflow-runs/{run['workflow_run_id']}/advance"
    )

    assert response.status_code == 200
    advanced = response.json()
    assert advanced["status"] == "waiting_for_user"
    assert advanced["stop_reason"] == "new_jobs_insufficient"
    assert advanced["completed_count"] == 0


def test_search_advance_is_idempotent_while_capture_is_running(
    configured_client, monkeypatch
) -> None:
    run = _create_run(configured_client)
    started: list[object] = []
    monkeypatch.setattr(
        workflow_runs.boss_scraper_service,
        "get_browser_status",
        lambda: BossBrowserStatus(running=True, cdp_port=9222),
    )
    monkeypatch.setattr(
        workflow_runs.boss_capture_task_manager,
        "start_capture",
        lambda request, **kwargs: (started.append(request) or {"id": "capture-running"}),
    )
    monkeypatch.setattr(
        workflow_runs.boss_capture_task_manager,
        "get_task",
        lambda task_id: {"id": task_id, "status": "running"},
    )

    first = configured_client.post(
        f"/api/fine-job/workflow-runs/{run['workflow_run_id']}/advance"
    )
    second = configured_client.post(
        f"/api/fine-job/workflow-runs/{run['workflow_run_id']}/advance"
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert len(started) == 1
    assert second.json()["tasks"][0]["operation_ref_id"] == "capture-running"


def test_search_continues_current_combination_before_pending_combinations(
    configured_client, test_db, monkeypatch
) -> None:
    run = _create_run(configured_client)
    first_task_id = run["tasks"][0]["workflow_task_id"]
    with test_db.connect() as connection:
        task = connection.execute(
            "SELECT * FROM fj_workflow_tasks WHERE id = ?", (first_task_id,)
        ).fetchone()
    monkeypatch.setattr(
        workflow_runs.boss_capture_task_manager,
        "continue_capture",
        lambda task_id, pages: {"id": "capture-continuing"},
    )

    advanced = workflow_runs._decide_next_step(
        test_db,
        configured_client.app.state.config,
        run["workflow_run_id"],
        task,
        {
            "id": "capture-initial",
            "continuation_available": True,
            "has_more": True,
        },
        run["completion_contract"],
    )

    assert advanced["next_action"] == "continue_scroll"
    assert next(item for item in advanced["tasks"] if item["operation_ref_id"] == "capture-continuing")["status"] == "running"


def test_codex_reads_backend_context_snapshot_via_registered_tool(
    configured_client, test_db
) -> None:
    run = _create_run(configured_client)
    tool_service = CodexToolService(test_db, configured_client.app.state.config)

    result = tool_service.call(
        "finejob.get_workflow_context_snapshot",
        {"workflow_run_id": run["workflow_run_id"]},
    )

    assert result["status"] == "ready"
    assert result["data"]["context_snapshot_id"] == run["context_snapshots"][0]["context_snapshot_id"]


def test_interrupted_capture_waits_for_user_and_requires_explicit_resume(
    configured_client, monkeypatch
) -> None:
    run = _create_run(configured_client)
    monkeypatch.setattr(
        workflow_runs.boss_scraper_service,
        "get_browser_status",
        lambda: BossBrowserStatus(running=True, cdp_port=9222),
    )
    monkeypatch.setattr(
        workflow_runs.boss_capture_task_manager,
        "start_capture",
        lambda request, **kwargs: {"id": "capture-interrupted"},
    )
    first = configured_client.post(
        f"/api/fine-job/workflow-runs/{run['workflow_run_id']}/advance"
    )
    assert first.status_code == 200
    monkeypatch.setattr(
        workflow_runs.boss_capture_task_manager,
        "get_task",
        lambda task_id: (_ for _ in ()).throw(
            AppError(404, "CAPTURE_TASK_NOT_FOUND", "采集任务已不存在。")
        ),
    )

    interrupted = configured_client.post(
        f"/api/fine-job/workflow-runs/{run['workflow_run_id']}/advance"
    )
    resumed = configured_client.post(
        f"/api/fine-job/workflow-runs/{run['workflow_run_id']}/resume"
    )

    assert interrupted.status_code == 200
    assert interrupted.json()["status"] == "waiting_for_user"
    assert interrupted.json()["stop_reason"] == "capture_interrupted"
    assert resumed.status_code == 200
    assert resumed.json()["status"] == "pending"
    assert resumed.json()["tasks"][0]["operation_ref_id"] is None


def _prepare_analysis_batch(configured_client, test_db, *, candidate_count: int, target_count: int = 2, **updates):
    run_updates = {
        "target_count": target_count,
        "candidate_target_count": candidate_count,
        "analysis_batch_size": min(2, candidate_count),
        **updates,
    }
    if "recommend_target" in updates:
        run_updates.pop("target_count")
    run = _create_run(
        configured_client,
        **run_updates,
    )
    capture_id = new_id()
    now = utc_now()
    create_capture_batch(
        test_db,
        capture_id=capture_id,
        keyword="AI Agent",
        city="广州",
        pages=5,
        auto_details=False,
        created_at=now,
    )
    jobs = record_capture_jobs(
        test_db,
        capture_id=capture_id,
        search_keyword="AI Agent",
        jobs=[
            {
                "job_id": f"analysis-source-{index}",
                "encrypt_job_id": f"analysis-encrypt-{index}",
                "title": f"AI Agent 工程师 {index}",
                "company_name": f"候选公司 {index}",
                "location": "广州",
                "detail_status": "completed",
                "detail": {"job_description": f"当前岗位 JD {index}"},
            }
            for index in range(candidate_count)
        ],
        collected_at=now,
    )
    search_task_id = run["tasks"][0]["workflow_task_id"]
    with test_db.connect() as connection:
        connection.execute(
            "UPDATE fj_workflow_tasks SET status = 'succeeded' WHERE workflow_run_id = ? AND task_type = 'deep_job_search'",
            (run["workflow_run_id"],),
        )
        for job in jobs:
            connection.execute(
                """
                INSERT INTO fj_workflow_job_discoveries (
                  id, workflow_run_id, task_id, job_id, search_keyword, city,
                  search_combination_json, scroll_depth, discovered_at,
                  is_run_first_discovery, is_historical_duplicate, is_filter_candidate
                ) VALUES (?, ?, ?, ?, 'AI Agent', '广州', '{}', 5, ?, 1, 0, 1)
                """,
                (new_id(), run["workflow_run_id"], search_task_id, job["history_record_id"], utc_now()),
            )
    workflow_runs._refresh_counts(test_db, run["workflow_run_id"])
    assert workflow_runs._create_jd_tasks(test_db, run["workflow_run_id"], candidate_count) > 0
    advanced = workflow_runs.advance_deep_job_search(
        test_db, configured_client.app.state.config, run["workflow_run_id"]
    )
    return advanced, jobs


def test_candidate_pool_to_jd_to_waiting_codex_uses_compact_shared_and_item_context(
    configured_client, test_db
) -> None:
    run, jobs = _prepare_analysis_batch(configured_client, test_db, candidate_count=3)

    assert run["status"] == "waiting_codex"
    analysis_items = [item for item in run["tasks"] if item["task_type"] == "deep_job_search_analysis"]
    assert len(analysis_items) == 2
    shared = workflow_runs.get_context_snapshot(test_db, run["workflow_run_id"], "candidate_analysis")
    assert not any(item["section_id"] == "candidate_jds" for item in shared["sections"])
    assert any(item["section_id"] == "complete_resume" and not item["included"] for item in shared["sections"])
    recommendation = next(item for item in shared["sections"] if item["section_id"] == "recommendation_strategy_summary")
    assert recommendation["content"]["id"] == run["completion_contract"]["selected_strategy_ids"]["recommendation_strategy_id"]

    item_context = workflow_runs.get_workflow_analysis_item_context(
        test_db, run["workflow_run_id"], analysis_items[0]["workflow_task_id"]
    )
    material = next(
        item["content"]
        for item in item_context["item_context_snapshot"]["sections"]
        if item["section_id"] == "job_material"
    )
    selected = next(job for job in jobs if job["history_record_id"] == material["job_id"])
    assert material["jd"]["job_description"] == f"当前岗位 JD {selected['job_id'].rsplit('-', 1)[1]}"
    assert sum(job["history_record_id"] == material["job_id"] for job in jobs) == 1


def test_analysis_batch_handoff_attempt_ack_is_idempotent_and_rejects_stale_attempts(
    configured_client, test_db
) -> None:
    run, _jobs = _prepare_analysis_batch(configured_client, test_db, candidate_count=2)
    run_id = run["workflow_run_id"]
    batch_id = run["analysis_handoff"]["analysis_batch_id"]
    assert run["analysis_handoff"]["needs_initial_codex_handoff"] is True

    claimed = configured_client.post(
        f"/api/fine-job/workflow-runs/{run_id}/analysis-handoff/claim",
        json={
            "codex_session_ref": "runtime:session-a",
            "codex_runtime_id": "runtime-a",
            "handoff_kind": "initial",
        },
    )
    assert claimed.status_code == 200
    assert claimed.json()["analysis_handoff"]["handoff_status"] == "claimed"
    first_attempt_id = claimed.json()["analysis_handoff"]["handoff_attempt_id"]
    assert claimed.json()["analysis_handoff"]["pending_item_count"] == 2

    duplicate = configured_client.post(
        f"/api/fine-job/workflow-runs/{run_id}/analysis-handoff/claim",
        json={
            "codex_session_ref": "runtime:session-a",
            "codex_runtime_id": "runtime-a",
            "handoff_kind": "initial",
        },
    )
    assert duplicate.status_code == 409

    released = configured_client.post(
        f"/api/fine-job/workflow-runs/{run_id}/analysis-handoff/release",
        json={
            "analysis_batch_id": batch_id,
            "handoff_attempt_id": first_attempt_id,
            "codex_session_ref": "runtime:session-a",
        },
    )
    assert released.status_code == 200
    assert released.json()["analysis_handoff"]["needs_initial_codex_handoff"] is True

    reclaimed = configured_client.post(
        f"/api/fine-job/workflow-runs/{run_id}/analysis-handoff/claim",
        json={
            "codex_session_ref": "runtime:session-b",
            "codex_runtime_id": "runtime-b",
            "handoff_kind": "initial",
        },
    )
    assert reclaimed.status_code == 200
    second_attempt_id = reclaimed.json()["analysis_handoff"]["handoff_attempt_id"]
    prompt_written = configured_client.post(
        f"/api/fine-job/workflow-runs/{run_id}/analysis-handoff/prompt-written",
        json={
            "analysis_batch_id": batch_id,
            "handoff_attempt_id": second_attempt_id,
            "codex_session_ref": "runtime:session-b",
        },
    )
    assert prompt_written.status_code == 200
    assert prompt_written.json()["analysis_handoff"]["attempt_status"] == "prompt_written"
    assert prompt_written.json()["analysis_handoff"]["codex_processing"] is False
    pending_item = workflow_runs.list_workflow_analysis_items(test_db, run_id, batch_id)["items"][0]
    blocked_save = configured_client.post(
        f"/api/fine-job/workflow-runs/{run_id}/analysis-items/{pending_item['workflow_task_id']}/save",
        json={"decision": "reject", "summary": "尚未确认开始"},
    )
    assert blocked_save.status_code == 409
    assert blocked_save.json()["error_category"] == "WORKFLOW_ANALYSIS_NOT_STARTED"

    stale_ack = configured_client.post(
        f"/api/fine-job/workflow-runs/{run_id}/analysis-handoff/ack-started",
        json={"analysis_batch_id": batch_id, "handoff_attempt_id": first_attempt_id},
    )
    assert stale_ack.status_code == 409

    started = configured_client.post(
        f"/api/fine-job/workflow-runs/{run_id}/analysis-handoff/ack-started",
        json={"analysis_batch_id": batch_id, "handoff_attempt_id": second_attempt_id},
    )
    assert started.status_code == 200
    assert started.json()["analysis_handoff"]["attempt_status"] == "started"
    assert started.json()["analysis_handoff"]["codex_processing"] is True

    repeated = configured_client.post(
        f"/api/fine-job/workflow-runs/{run_id}/analysis-handoff/ack-started",
        json={"analysis_batch_id": batch_id, "handoff_attempt_id": second_attempt_id},
    )
    assert repeated.status_code == 200
    assert repeated.json()["analysis_handoff"]["started_at"] == started.json()["analysis_handoff"]["started_at"]


def test_next_analysis_batch_only_handoffs_new_pending_items(configured_client, test_db) -> None:
    run, _jobs = _prepare_analysis_batch(configured_client, test_db, candidate_count=3, target_count=2)
    run_id = run["workflow_run_id"]
    first_batch_id = run["analysis_handoff"]["analysis_batch_id"]
    claimed = configured_client.post(
        f"/api/fine-job/workflow-runs/{run_id}/analysis-handoff/claim",
        json={"codex_session_ref": "runtime:session-a", "codex_runtime_id": "runtime-a", "handoff_kind": "initial"},
    )
    configured_client.post(
        f"/api/fine-job/workflow-runs/{run_id}/analysis-handoff/prompt-written",
        json={
            "analysis_batch_id": first_batch_id,
            "handoff_attempt_id": claimed.json()["analysis_handoff"]["handoff_attempt_id"],
            "codex_session_ref": "runtime:session-a",
        },
    )
    configured_client.post(
        f"/api/fine-job/workflow-runs/{run_id}/analysis-handoff/ack-started",
        json={
            "analysis_batch_id": first_batch_id,
            "handoff_attempt_id": claimed.json()["analysis_handoff"]["handoff_attempt_id"],
        },
    )
    service = CodexToolService(test_db, configured_client.app.state.config)
    first_items = workflow_runs.list_workflow_analysis_items(test_db, run_id, first_batch_id)["items"]
    service.call("finejob.save_workflow_analysis_item", {
        "workflow_run_id": run_id, "workflow_task_id": first_items[0]["workflow_task_id"],
        "decision": "recommend", "confidence": 0.9, "summary": "第一批推荐",
    })
    service.call("finejob.save_workflow_analysis_item", {
        "workflow_run_id": run_id, "workflow_task_id": first_items[1]["workflow_task_id"],
        "decision": "review", "confidence": 0.5, "summary": "第一批复核",
    })

    refreshed = workflow_runs.get_workflow_run(test_db, run_id)
    next_summary = refreshed["analysis_handoff"]
    assert next_summary["needs_next_batch_handoff"] is True, next_summary
    assert next_summary["analysis_batch_id"] != first_batch_id
    assert all(item["status"] == "succeeded" for item in workflow_runs.list_workflow_analysis_items(test_db, run_id, first_batch_id)["items"])
    next_items = workflow_runs.list_workflow_analysis_items(
        test_db, run_id, str(next_summary["analysis_batch_id"])
    )["items"]
    assert len(next_items) == 1
    assert next_items[0]["status"] == "pending"

    claimed = configured_client.post(
        f"/api/fine-job/workflow-runs/{run_id}/analysis-handoff/claim",
        json={"codex_session_ref": "runtime:session-a", "codex_runtime_id": "runtime-a", "handoff_kind": "next"},
    )
    assert claimed.status_code == 200
    assert claimed.json()["analysis_handoff"]["handoff_status"] == "claimed"


def test_start_ack_timeout_full_retry_releases_old_attempt_before_replacing_it(
    configured_client, test_db
) -> None:
    run, _jobs = _prepare_analysis_batch(configured_client, test_db, candidate_count=1, target_count=1)
    run_id = run["workflow_run_id"]
    batch_id = run["analysis_handoff"]["analysis_batch_id"]
    claimed = configured_client.post(
        f"/api/fine-job/workflow-runs/{run_id}/analysis-handoff/claim",
        json={"codex_session_ref": "runtime:session-a", "codex_runtime_id": "runtime-a", "handoff_kind": "initial"},
    )
    first_attempt_id = claimed.json()["analysis_handoff"]["handoff_attempt_id"]
    configured_client.post(
        f"/api/fine-job/workflow-runs/{run_id}/analysis-handoff/prompt-written",
        json={
            "analysis_batch_id": batch_id,
            "handoff_attempt_id": first_attempt_id,
            "codex_session_ref": "runtime:session-a",
        },
    )
    with test_db.connect() as connection:
        connection.execute(
            "UPDATE fj_workflow_analysis_handoffs SET prompt_written_at = '2000-01-01T00:00:00Z' "
            "WHERE workflow_run_id = ? AND analysis_batch_id = ?",
            (run_id, batch_id),
        )
    waiting = configured_client.get(f"/api/fine-job/workflow-runs/{run_id}").json()["analysis_handoff"]
    assert waiting["attempt_status"] == "prompt_written"
    assert waiting["retry_available"] is True

    released = configured_client.post(
        f"/api/fine-job/workflow-runs/{run_id}/analysis-handoff/release",
        json={
            "analysis_batch_id": batch_id,
            "handoff_attempt_id": first_attempt_id,
            "codex_session_ref": "runtime:session-a",
            "release_reason": "full_retry",
        },
    )
    assert released.status_code == 200
    assert released.json()["analysis_handoff"]["attempt_status"] == "released"

    retried = configured_client.post(
        f"/api/fine-job/workflow-runs/{run_id}/analysis-handoff/claim",
        json={
            "codex_session_ref": "runtime:session-b",
            "codex_runtime_id": "runtime-b",
            "handoff_kind": "initial",
        },
    )
    assert retried.status_code == 200
    second_attempt_id = retried.json()["analysis_handoff"]["handoff_attempt_id"]
    assert second_attempt_id != first_attempt_id
    assert retried.json()["analysis_handoff"]["attempt_status"] == "claimed"

    stale = configured_client.post(
        f"/api/fine-job/workflow-runs/{run_id}/analysis-handoff/ack-started",
        json={"analysis_batch_id": batch_id, "handoff_attempt_id": first_attempt_id},
    )
    assert stale.status_code == 409


def test_workflow_recommend_routes_to_pending_review_without_external_action(configured_client, test_db) -> None:
    run, _jobs = _prepare_analysis_batch(configured_client, test_db, candidate_count=1, target_count=1)
    item = workflow_runs.list_workflow_analysis_items(test_db, run["workflow_run_id"])["items"][0]
    service = CodexToolService(test_db, configured_client.app.state.config)

    result = service.call(
        "finejob.save_workflow_analysis_item",
        {
            "workflow_run_id": run["workflow_run_id"],
            "workflow_task_id": item["workflow_task_id"],
            "decision": "recommend",
            "confidence": 0.9,
            "hard_requirements": ["通过"],
            "strengths": ["Python 匹配"],
            "gaps": ["领域待确认"],
            "risks": ["团队规模未知"],
            "missing_information": ["面试流程"],
            "jd_evidence": ["JD 要求 Python"],
            "candidate_evidence": ["候选人有 Python 经验证据"],
            "reasons": ["策略要求满足"],
        },
    )["data"]

    assert result["status"] == "completed"
    reviews = configured_client.get("/api/fine-job/review-items?status=pending").json()["items"]
    assert len(reviews) == 1
    assert reviews[0]["ai_decision"] == "recommend"
    with test_db.connect() as connection:
        action_count = connection.execute("SELECT COUNT(*) FROM fj_automation_actions").fetchone()[0]
    assert action_count == 0
    item_after = workflow_runs.list_workflow_analysis_items(test_db, run["workflow_run_id"])["items"][0]
    assert item_after["analysis_result"]["jd_evidence"] == ["JD 要求 Python"]
    assert item_after["analysis_result"]["candidate_evidence"] == ["候选人有 Python 经验证据"]


def test_workflow_analysis_feedback_is_persisted_without_changing_strategy(configured_client, test_db) -> None:
    run, _jobs = _prepare_analysis_batch(configured_client, test_db, candidate_count=1, target_count=1)
    item = workflow_runs.list_workflow_analysis_items(test_db, run["workflow_run_id"])["items"][0]
    with test_db.connect() as connection:
        connection.execute(
            "UPDATE fj_workflow_tasks SET status = 'succeeded', result_json = ? WHERE id = ?",
            ('{"decision":"review"}', item["workflow_task_id"]),
        )
    saved = configured_client.post(
        f"/api/fine-job/workflow-runs/{run['workflow_run_id']}/analysis-items/{item['workflow_task_id']}/feedback",
        json={"sentiment": "unexpected", "reason": "jd_understanding", "note": "JD 解析遗漏"},
    )
    assert saved.status_code == 200
    refreshed = workflow_runs.list_workflow_analysis_items(test_db, run["workflow_run_id"])["items"][0]
    assert refreshed["feedback"][0]["reason"] == "jd_understanding"
    assert refreshed["feedback"][0]["note"] == "JD 解析遗漏"


def test_mixed_analysis_results_refill_candidate_pool_and_complete_on_unique_recommends(
    configured_client, test_db
) -> None:
    run, _jobs = _prepare_analysis_batch(configured_client, test_db, candidate_count=3, target_count=2)
    tool_service = CodexToolService(test_db, configured_client.app.state.config)
    first_batch = tool_service.call(
        "finejob.list_workflow_analysis_items", {"workflow_run_id": run["workflow_run_id"]}
    )["data"]["items"]

    first = tool_service.call(
        "finejob.save_workflow_analysis_item",
        {
            "workflow_run_id": run["workflow_run_id"],
            "workflow_task_id": first_batch[0]["workflow_task_id"],
            "decision": "recommend",
            "confidence": 0.9,
            "summary": "适合",
            "reasons": ["技能匹配"],
        },
    )["data"]
    second = tool_service.call(
        "finejob.save_workflow_analysis_item",
        {
            "workflow_run_id": run["workflow_run_id"],
            "workflow_task_id": first_batch[1]["workflow_task_id"],
            "decision": "review",
            "confidence": 0.5,
            "summary": "需要确认",
        },
    )["data"]

    assert first["completed_count"] == 1
    assert first["remaining_count"] == 1
    assert second["status"] == "waiting_codex"
    next_batch = tool_service.call(
        "finejob.list_workflow_analysis_items", {"workflow_run_id": run["workflow_run_id"]}
    )["data"]["items"]
    pending = [item for item in next_batch if item["status"] == "pending"]
    assert len(pending) == 1
    final = tool_service.call(
        "finejob.save_workflow_analysis_item",
        {
            "workflow_run_id": run["workflow_run_id"],
            "workflow_task_id": pending[0]["workflow_task_id"],
            "decision": "recommend",
            "confidence": 0.8,
            "summary": "补足目标",
        },
    )["data"]

    assert final["status"] == "completed"
    assert final["completed_count"] == 2
    assert final["remaining_count"] == 0


def test_candidate_pool_exhausted_after_reject_waits_for_new_jobs(configured_client, test_db) -> None:
    run, _jobs = _prepare_analysis_batch(configured_client, test_db, candidate_count=1, target_count=1)
    item = workflow_runs.list_workflow_analysis_items(test_db, run["workflow_run_id"])["items"][0]
    result = CodexToolService(test_db, configured_client.app.state.config).call(
        "finejob.save_workflow_analysis_item",
        {
            "workflow_run_id": run["workflow_run_id"],
            "workflow_task_id": item["workflow_task_id"],
            "decision": "reject",
            "summary": "不匹配",
        },
    )["data"]

    assert result["status"] == "waiting_for_user"
    assert result["stop_reason"] == "new_jobs_insufficient"


def test_review_target_participates_only_when_configured_and_respects_all_mode(
    configured_client, test_db
) -> None:
    run, _jobs = _prepare_analysis_batch(
        configured_client,
        test_db,
        candidate_count=2,
        recommend_target=1,
        review_target=1,
        target_mode="all",
    )
    items = workflow_runs.list_workflow_analysis_items(test_db, run["workflow_run_id"])["items"]
    service = CodexToolService(test_db, configured_client.app.state.config)

    after_recommend = service.call(
        "finejob.save_workflow_analysis_item",
        {
            "workflow_run_id": run["workflow_run_id"],
            "workflow_task_id": items[0]["workflow_task_id"],
            "decision": "recommend",
            "summary": "达到 recommend 目标",
        },
    )["data"]
    after_review = service.call(
        "finejob.save_workflow_analysis_item",
        {
            "workflow_run_id": run["workflow_run_id"],
            "workflow_task_id": items[1]["workflow_task_id"],
            "decision": "review",
            "summary": "达到 review 目标",
        },
    )["data"]

    assert after_recommend["status"] == "waiting_codex"
    assert after_recommend["completion_progress"] == {
        "recommend": {"current": 1, "target": 1, "remaining": 0, "reached": True},
        "review": {"current": 0, "target": 1, "remaining": 1, "reached": False},
        "target_mode": "all",
        "target_reached": False,
    }
    assert after_review["status"] == "completed"
    assert after_review["stop_reason"] == "completion_target_reached"
    assert after_review["completion_progress"]["target_reached"] is True
    saved_items = workflow_runs.list_workflow_analysis_items(test_db, run["workflow_run_id"])["items"]
    assert sum(item["analysis_result"].get("review_status") == "pending" for item in saved_items) == 2


def test_stop_after_current_batch_waits_for_user_then_prepares_next_batch(
    configured_client, test_db
) -> None:
    run, _jobs = _prepare_analysis_batch(
        configured_client,
        test_db,
        candidate_count=3,
        target_count=2,
        stop_after_current_batch=True,
    )
    items = workflow_runs.list_workflow_analysis_items(test_db, run["workflow_run_id"])["items"]
    service = CodexToolService(test_db, configured_client.app.state.config)
    for item in items:
        result = service.call(
            "finejob.save_workflow_analysis_item",
            {
                "workflow_run_id": run["workflow_run_id"],
                "workflow_task_id": item["workflow_task_id"],
                "decision": "reject",
                "summary": "当前批不匹配",
            },
        )["data"]

    resumed = configured_client.post(f"/api/fine-job/workflow-runs/{run['workflow_run_id']}/resume")

    assert result["status"] == "waiting_for_user"
    assert result["stop_reason"] == "analysis_batch_completed_waiting_user"
    assert resumed.status_code == 200
    assert resumed.json()["status"] == "running"
    assert resumed.json()["current_step"] == "collecting_jd"


def test_any_target_mode_can_complete_on_configured_review_target(configured_client, test_db) -> None:
    run, _jobs = _prepare_analysis_batch(
        configured_client,
        test_db,
        candidate_count=2,
        recommend_target=2,
        review_target=1,
        target_mode="any",
    )
    item = workflow_runs.list_workflow_analysis_items(test_db, run["workflow_run_id"])["items"][0]

    result = CodexToolService(test_db, configured_client.app.state.config).call(
        "finejob.save_workflow_analysis_item",
        {
            "workflow_run_id": run["workflow_run_id"],
            "workflow_task_id": item["workflow_task_id"],
            "decision": "review",
            "summary": "review 目标已达到",
        },
    )["data"]

    assert result["status"] == "completed"
    assert result["completed_count"] == 0
    assert result["remaining_count"] == 2
    assert result["stop_reason"] == "completion_target_reached"
    assert result["completion_progress"] == {
        "recommend": {"current": 0, "target": 2, "remaining": 2, "reached": False},
        "review": {"current": 1, "target": 1, "remaining": 0, "reached": True},
        "target_mode": "any",
        "target_reached": True,
    }


def test_analyze_all_candidates_freezes_pool_and_never_resumes_search(
    configured_client, test_db
) -> None:
    run, _jobs = _prepare_analysis_batch(
        configured_client,
        test_db,
        candidate_count=3,
        target_count=1,
        analyze_all_candidates=True,
    )
    service = CodexToolService(test_db, configured_client.app.state.config)
    first_batch = workflow_runs.list_workflow_analysis_items(test_db, run["workflow_run_id"])["items"]
    first = service.call(
        "finejob.save_workflow_analysis_item",
        {
            "workflow_run_id": run["workflow_run_id"],
            "workflow_task_id": first_batch[0]["workflow_task_id"],
            "decision": "recommend",
            "summary": "达到目标",
        },
    )["data"]
    second = service.call(
        "finejob.save_workflow_analysis_item",
        {
            "workflow_run_id": run["workflow_run_id"],
            "workflow_task_id": first_batch[1]["workflow_task_id"],
            "decision": "reject",
            "summary": "继续完成冻结候选池",
        },
    )["data"]
    pending = [
        item for item in workflow_runs.list_workflow_analysis_items(test_db, run["workflow_run_id"])["items"]
        if item["status"] == "pending"
    ]
    final = service.call(
        "finejob.save_workflow_analysis_item",
        {
            "workflow_run_id": run["workflow_run_id"],
            "workflow_task_id": pending[0]["workflow_task_id"],
            "decision": "reject",
            "summary": "冻结池最后一项",
        },
    )["data"]

    assert len(first["completion_contract"]["frozen_candidate_pool"]["job_ids"]) == 3
    assert second["status"] == "waiting_codex"
    assert len([task for task in second["tasks"] if task["task_type"] == "deep_job_search"]) == 1
    assert final["status"] == "completed"
    assert final["stop_reason"] == "frozen_candidate_pool_analyzed"


def test_wait_for_user_execution_policy_waits_after_each_analysis_batch(
    configured_client, test_db
) -> None:
    run, _jobs = _prepare_analysis_batch(
        configured_client,
        test_db,
        candidate_count=3,
        target_count=2,
        execution_policy_after_analysis_batch="wait_for_user",
    )
    items = workflow_runs.list_workflow_analysis_items(test_db, run["workflow_run_id"])["items"]
    service = CodexToolService(test_db, configured_client.app.state.config)
    for item in items:
        result = service.call(
            "finejob.save_workflow_analysis_item",
            {
                "workflow_run_id": run["workflow_run_id"],
                "workflow_task_id": item["workflow_task_id"],
                "decision": "reject",
                "summary": "等待用户继续",
            },
        )["data"]

    resumed = configured_client.post(f"/api/fine-job/workflow-runs/{run['workflow_run_id']}/resume")

    assert result["status"] == "waiting_for_user"
    assert result["stop_reason"] == "analysis_batch_waiting_user"
    assert resumed.status_code == 200
    assert resumed.json()["status"] == "running"


def test_non_resumable_stop_reason_is_not_resumed(configured_client, test_db) -> None:
    run = _create_run(configured_client)
    workflow_runs._wait_for_user(
        test_db, run["workflow_run_id"], "new_jobs_insufficient", "需要扩大搜索范围。"
    )

    response = configured_client.post(
        f"/api/fine-job/workflow-runs/{run['workflow_run_id']}/resume"
    )

    assert response.status_code == 409
    assert response.json()["error_category"] == "WORKFLOW_NOT_RESUMABLE"
