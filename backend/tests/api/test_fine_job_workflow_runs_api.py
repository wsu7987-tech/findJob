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
    deep_job_search = {
        "filter_strategy_id": strategy["id"],
        "target_count": 2,
        "candidate_target_count": 4,
        "allowed_search_keywords": ["AI Agent"],
        "allowed_cities": ["广州"],
        "applied_feedback_ids": ["feedback-1"],
        "applied_preference_ids": ["preference-1"],
    }
    deep_job_search.update(updates)
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
    assert run["completion_contract"]["allow_historical_jobs"] is False
    assert run["completion_contract"]["applied_feedback_ids"] == ["feedback-1"]
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


def test_context_soft_budget_blocks_run_before_search_starts(configured_client) -> None:
    strategy = configured_client.post(
        "/api/fine-job/strategies/filters",
        json=_strategy_payload(notes="上下文预算测试" * 500),
    ).json()["strategy"]
    response = configured_client.post(
        "/api/fine-job/workflow-runs",
        json={
            "task_type": "deep_job_search",
            "deep_job_search": {
                "filter_strategy_id": strategy["id"],
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


def _prepare_analysis_batch(configured_client, test_db, *, candidate_count: int, target_count: int = 2):
    run = _create_run(
        configured_client,
        target_count=target_count,
        candidate_target_count=candidate_count,
        jd_batch_size=min(2, candidate_count),
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
