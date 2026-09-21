from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json

import pytest

from backend.app.services.fine_job import workflow_runs
from backend.app.services.fine_job import smart_captures
from backend.app.services.fine_job.boss_capture_history import (
    create_capture_batch,
    record_capture_jobs,
)
from backend.app.services.fine_job.boss_scraper.service import BossBrowserStatus
from backend.app.services.fine_job.codex_tools import CodexToolService
from backend.app.errors import AppError
from backend.app.utils import new_id, utc_now
from backend.app.services.fine_job import workflow_children
from backend.app.services.fine_job import child_adapter
from backend.app.services.fine_job import cutover_guard
from backend.app.services.fine_job import smart_capture_engine


@pytest.fixture(autouse=True)
def reset_runtime_cutover_phase():
    cutover_guard.configure_runtime_cutover_phase(cutover_guard.CutoverPhase.PRE_CUTOVER)
    yield
    cutover_guard.reset_runtime_cutover_guard()


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


def _create_run(configured_client, strategy_updates=None, idempotency_key=None, **updates):
    strategy_payload = _strategy_payload()
    strategy_payload.update(strategy_updates or {})
    strategy = configured_client.post(
        "/api/fine-job/strategies/filters", json=strategy_payload
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
    request_payload = {
        "task_type": "deep_job_search",
        "created_from": "task_cockpit",
        "deep_job_search": deep_job_search,
    }
    if idempotency_key is not None:
        request_payload["idempotency_key"] = idempotency_key
    response = configured_client.post(
        "/api/fine-job/workflow-runs",
        json=request_payload,
    )
    assert response.status_code == 201
    return response.json()


def test_workflow_create_retry_returns_one_parent_child_current_identity(
    configured_client, test_db
) -> None:
    first = _create_run(configured_client, idempotency_key="cockpit-create-retry-1")
    second = _create_run(configured_client, idempotency_key="cockpit-create-retry-1")

    assert second["workflow_run_id"] == first["workflow_run_id"]
    assert second["idempotency_key"] == "cockpit-create-retry-1"
    assert second["children"][0]["child_relation_id"] == first["children"][0]["child_relation_id"]
    with test_db.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) AS count FROM fj_workflow_runs WHERE idempotency_key = ?",
            ("cockpit-create-retry-1",),
        ).fetchone()["count"] == 1
        assert connection.execute(
            "SELECT COUNT(*) AS count FROM fj_smart_captures WHERE workflow_run_id = ?",
            (first["workflow_run_id"],),
        ).fetchone()["count"] == 1
        assert connection.execute(
            "SELECT smart_capture_id FROM fj_smart_capture_current WHERE slot = 1"
        ).fetchone()["smart_capture_id"] == first["children"][0]["smart_capture_id"]


def test_independent_active_capture_rejects_cockpit_without_orphan_parent(
    configured_client, test_db, monkeypatch
) -> None:
    smart_captures.create_smart_capture(
        test_db,
        source="boss_capture",
        workflow_run_id=None,
        search_config={
            "allowed_search_keywords": ["Python"],
            "allowed_cities": ["上海"],
        },
        target_count=15,
    )
    monkeypatch.setattr(
        workflow_runs,
        "get_filter_strategy",
        lambda db, strategy_id: {
            "enabled": True,
            "cities": ["上海"],
            "strategy_version": 1,
        },
    )
    monkeypatch.setattr(
        workflow_runs,
        "list_search_keywords",
        lambda db, strategy_id: [{"keyword": "Python", "enabled": True}],
    )

    with pytest.raises(AppError) as error:
        workflow_runs.create_deep_job_search_run(
            test_db,
            configured_client.app.state.config,
            {
                "filter_strategy_id": "strategy-1",
                "delivery_target_enabled": False,
                "allowed_search_keywords": ["Python"],
                "allowed_cities": ["上海"],
                "candidate_target_count": 15,
                "min_depth": 1,
                "max_depth": 1,
            },
            created_from="task_cockpit",
        )

    assert error.value.error_category == "COLLECTION_TASK_ACTIVE"
    with test_db.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) AS count FROM fj_workflow_runs"
        ).fetchone()["count"] == 0
        assert connection.execute(
            "SELECT COUNT(*) AS count FROM fj_smart_captures WHERE workflow_run_id IS NOT NULL"
        ).fetchone()["count"] == 0


def test_create_workflow_run_exposes_real_search_context_snapshot(configured_client) -> None:
    run = _create_run(configured_client)

    assert run["status"] == "pending"
    assert len(run["children"]) == 1
    child = run["children"][0]
    assert child["child_type"] == "smart_capture"
    assert child["child_ref"] == child["smart_capture_id"]
    assert child["status"] == "pending"
    assert child["capabilities"]["start"] is True
    smart_capture = configured_client.get(
        f"/api/fine-job/smart-captures/{child['smart_capture_id']}"
    ).json()
    assert smart_capture["workflow_run_id"] == run["workflow_run_id"]
    assert smart_capture["execution_config"]["search"]["filter_strategy_id"]
    assert smart_capture["execution_config"]["analysis"]["codex_model"] == "gpt-5.6-luna"
    assert configured_client.get(
        f"/api/fine-job/workflow-runs/{run['workflow_run_id']}/children"
    ).json()["children"][0]["child_relation_id"] == child["child_relation_id"]
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


def test_linked_child_projection_tracks_smart_capture_lifecycle(configured_client, test_db) -> None:
    run = _create_run(configured_client)
    child = run["children"][0]

    smart_captures._update_capture(
        test_db,
        child["smart_capture_id"],
        status="running",
        stage="capturing",
        message="正在采集岗位。",
    )

    refreshed = configured_client.get(
        f"/api/fine-job/workflow-runs/{run['workflow_run_id']}"
    ).json()
    projected = refreshed["children"][0]
    assert projected["status"] == "running"
    assert projected["child_state_version"] > child["child_state_version"]
    assert projected["state_version"] > child["state_version"]


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
    search_combination = json.loads(discovery["search_combination_json"])
    assert search_combination["search_combination_id"] == search_task["payload"]["search_combination_id"]
    assert search_combination["platform_filters"] == {}
    linked_capture = smart_captures.get_smart_capture(
        test_db, run["children"][0]["smart_capture_id"]
    )
    assert [job["source_job_id"] for job in linked_capture["candidate_pool"]] == [
        "source-job-1"
    ]
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
    assert advanced["stop_reason"] == "approved_search_space_exhausted"
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
    assert started[0].filters == {}
    assert started[0].force_search_navigation is False
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


def test_search_planner_passes_dynamic_platform_filters_to_capture(
    configured_client, test_db, monkeypatch
) -> None:
    run = _create_run(
        configured_client,
        strategy_updates={"experiences": ["1-3年", "3-5年"]},
    )
    first_task_id = run["tasks"][0]["workflow_task_id"]
    with test_db.connect() as connection:
        task = connection.execute(
            "SELECT * FROM fj_workflow_tasks WHERE id = ?", (first_task_id,)
        ).fetchone()
        connection.execute(
            "UPDATE fj_workflow_tasks SET status = 'succeeded' WHERE id = ?",
            (first_task_id,),
        )
    started: list[object] = []
    monkeypatch.setattr(
        workflow_runs.boss_scraper_service,
        "get_browser_status",
        lambda: BossBrowserStatus(running=True, cdp_port=9222),
    )
    monkeypatch.setattr(
        workflow_runs.boss_capture_task_manager,
        "start_capture",
        lambda request, **kwargs: (started.append(request) or {"id": "capture-next"}),
    )

    advanced = workflow_runs._decide_next_step(
        test_db,
        configured_client.app.state.config,
        run["workflow_run_id"],
        task,
        {"id": "capture-initial", "continuation_available": False, "has_more": False},
        run["completion_contract"],
        {
            "jobs_seen": 30,
            "run_fresh_jobs": 30,
            "strategy_reject": 15,
            "qualified_fresh_jobs": 0,
            "candidate_jobs": 0,
            "failure_code_counts": {"experience": 15, "degree": 6},
        },
    )

    assert len(started) == 1
    assert started[0].keyword == "AI Agent"
    assert started[0].city == "广州"
    assert started[0].filters == {"experience": "104"}
    assert started[0].force_search_navigation is True
    assert advanced["telemetry"]["search_planner"]["current_combination"]["platform_filters"] == {
        "experience": "104"
    }


def test_search_planner_switches_city_before_keyword(configured_client, test_db) -> None:
    run = _create_run(
        configured_client,
        strategy_updates={
            "search_keywords": ["AI Agent", "产品经理"],
            "cities": ["广州", "上海"],
        },
        allowed_search_keywords=["AI Agent", "产品经理"],
        allowed_cities=["广州", "上海"],
    )
    first_payload = run["tasks"][0]["payload"]
    first_combination_id = first_payload["search_combination_id"]

    assert workflow_runs._create_next_approved_scope(
        test_db, run["workflow_run_id"], first_payload, first_combination_id
    )
    with test_db.connect() as connection:
        tasks = connection.execute(
            "SELECT * FROM fj_workflow_tasks WHERE workflow_run_id = ? ORDER BY created_at",
            (run["workflow_run_id"],),
        ).fetchall()
    payloads = [json.loads(item["payload_json"]) for item in tasks]
    second_payload = next(payload for payload in payloads if payload["city"] == "上海")
    assert (second_payload["keyword"], second_payload["city"]) == ("AI Agent", "上海")
    assert second_payload["platform_filters"] == {}

    assert workflow_runs._create_next_approved_scope(
        test_db,
        run["workflow_run_id"],
        second_payload,
        second_payload["search_combination_id"],
    )
    with test_db.connect() as connection:
        tasks = connection.execute(
            "SELECT * FROM fj_workflow_tasks WHERE workflow_run_id = ? ORDER BY created_at",
            (run["workflow_run_id"],),
        ).fetchall()
    payloads = [json.loads(item["payload_json"]) for item in tasks]
    third_payload = next(payload for payload in payloads if payload["keyword"] == "产品经理")
    assert (third_payload["keyword"], third_payload["city"]) == ("产品经理", "广州")
    assert third_payload["platform_filters"] == {}


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


def _ack_started_batch(configured_client, run: dict[str, object]) -> dict[str, object]:
    run_id = str(run["workflow_run_id"])
    batch_id = str(run["analysis_handoff"]["analysis_batch_id"])
    claimed = configured_client.post(
        f"/api/fine-job/workflow-runs/{run_id}/analysis-handoff/claim",
        json={
            "codex_session_ref": "runtime:prefetch-test",
            "codex_runtime_id": "prefetch-test",
            "handoff_kind": "initial",
        },
    )
    assert claimed.status_code == 200
    attempt_id = claimed.json()["analysis_handoff"]["handoff_attempt_id"]
    prompt_written = configured_client.post(
        f"/api/fine-job/workflow-runs/{run_id}/analysis-handoff/prompt-written",
        json={
            "analysis_batch_id": batch_id,
            "handoff_attempt_id": attempt_id,
            "codex_session_ref": "runtime:prefetch-test",
        },
    )
    assert prompt_written.status_code == 200
    started = configured_client.post(
        f"/api/fine-job/workflow-runs/{run_id}/analysis-handoff/ack-started",
        json={"analysis_batch_id": batch_id, "handoff_attempt_id": attempt_id},
    )
    assert started.status_code == 200
    return started.json()


def _save_analysis_results(
    configured_client, test_db, run: dict[str, object], decisions: list[str]
) -> dict[str, object]:
    current = run
    for decision in decisions:
        item = next(
            task for task in current["tasks"]
            if task["task_type"] == "deep_job_search_analysis"
            and task["status"] == "pending"
        )
        current = workflow_runs.record_workflow_analysis_result(
            test_db,
            configured_client.app.state.config,
            str(current["workflow_run_id"]),
            str(item["workflow_task_id"]),
            decision=decision,
            evaluation_id=f"evaluation-{item['workflow_task_id']}",
            evaluation={"summary": "targeted test"},
        )
    return current


def test_prefetch_stays_outside_current_run_state_and_reuses_completed_jd(
    configured_client, test_db, monkeypatch
) -> None:
    run, _jobs = _prepare_analysis_batch(configured_client, test_db, candidate_count=4)
    monkeypatch.setattr(
        workflow_runs.boss_capture_task_manager,
        "start_history_detail",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("不应重复抓取已有 JD")),
    )

    started = _ack_started_batch(configured_client, run)

    assert started["status"] == "waiting_codex"
    assert started["current_step"] == "waiting_codex"
    assert started["analysis_handoff"]["attempt_status"] == "started"
    assert started["prefetch"]["status"] == "ready"
    assert started["prefetch"]["ready_count"] == 2
    assert started["prefetch"]["target_count"] == 2
    assert len([
        task for task in started["tasks"]
        if task["task_type"] == "deep_job_search_analysis"
    ]) == 2


def test_completion_contract_abandons_ready_prefetch_before_new_analysis_batch(
    configured_client, test_db
) -> None:
    run, _jobs = _prepare_analysis_batch(configured_client, test_db, candidate_count=4)
    _ack_started_batch(configured_client, run)

    completed = _save_analysis_results(
        configured_client, test_db, run, ["recommend", "recommend"]
    )

    assert completed["status"] == "completed"
    assert completed["prefetch"]["status"] == "abandoned"
    assert not any(
        task["task_type"] == "deep_job_search_analysis"
        and task["payload"].get("prefetch_item_id")
        for task in completed["tasks"]
    )
    with test_db.connect() as connection:
        reservation = connection.execute(
            """
            SELECT COUNT(*) FROM fj_workflow_candidate_reservations
            WHERE workflow_run_id = ? AND owner_type = 'prefetch' AND status = 'reserved'
            """,
            (completed["workflow_run_id"],),
        ).fetchone()[0]
    assert reservation == 0


def test_wait_for_user_keeps_ready_buffer_until_user_continue(
    configured_client, test_db
) -> None:
    run, _jobs = _prepare_analysis_batch(
        configured_client,
        test_db,
        candidate_count=4,
        target_count=3,
        execution_policy_after_analysis_batch="wait_for_user",
    )
    _ack_started_batch(configured_client, run)

    waiting = _save_analysis_results(
        configured_client, test_db, run, ["recommend", "recommend"]
    )
    assert waiting["status"] == "waiting_for_user"
    assert waiting["stop_reason"] == "analysis_batch_waiting_user"
    assert waiting["prefetch"]["status"] == "ready"
    assert waiting["prefetch"]["ready_count"] == 2
    assert len([
        task for task in waiting["tasks"]
        if task["task_type"] == "deep_job_search_analysis"
    ]) == 2

    resumed = configured_client.post(
        f"/api/fine-job/workflow-runs/{waiting['workflow_run_id']}/resume"
    )
    assert resumed.status_code == 200
    continued = resumed.json()
    assert continued["status"] == "waiting_codex"
    assert continued["prefetch"]["status"] == "promoted"
    assert len([
        task for task in continued["tasks"]
        if task["task_type"] == "deep_job_search_analysis"
    ]) == 4
    with test_db.connect() as connection:
        active_analysis_reservations = connection.execute(
            """
            SELECT COUNT(*) FROM fj_workflow_candidate_reservations
            WHERE workflow_run_id = ? AND owner_type = 'formal_analysis'
              AND status = 'reserved'
            """,
            (continued["workflow_run_id"],),
        ).fetchone()[0]
    assert active_analysis_reservations == 2


def test_partial_ready_failed_prefetch_promotes_smaller_batch_without_stalling(
    configured_client, test_db
) -> None:
    run, _jobs = _prepare_analysis_batch(
        configured_client,
        test_db,
        candidate_count=10,
        target_count=10,
        analysis_batch_size=5,
    )
    started = _ack_started_batch(configured_client, run)
    batch_id = started["prefetch"]["prefetch_batch_id"]
    with test_db.connect() as connection:
        items = connection.execute(
            """
            SELECT id, job_id FROM fj_workflow_prefetch_items
            WHERE prefetch_batch_id = ? AND status = 'ready'
            ORDER BY created_at, id
            """,
            (batch_id,),
        ).fetchall()
        for item in items[:2]:
            connection.execute(
                """
                UPDATE fj_workflow_prefetch_items
                SET status = 'failed', lifecycle_status = 'abandoned',
                    detail_status = 'failed', error_message = 'targeted failure'
                WHERE id = ?
                """,
                (item["id"],),
            )
            connection.execute(
                """
                UPDATE fj_workflow_candidate_reservations
                SET status = 'failed', released_at = ?, terminal_at = ?
                WHERE owner_type = 'prefetch' AND owner_id = ? AND status = 'reserved'
                """,
                (utc_now(), utc_now(), item["id"]),
            )

    completed = _save_analysis_results(
        configured_client, test_db, run, ["recommend", "reject", "reject", "reject", "reject"]
    )

    assert completed["status"] == "waiting_codex"
    assert completed["prefetch"]["status"] == "promoted"
    assert len([
        task for task in completed["tasks"]
        if task["task_type"] == "deep_job_search_analysis"
    ]) == 8


def test_prefetch_reservation_is_atomic_and_competing_batch_cannot_select_candidate(
    configured_client, test_db
) -> None:
    run, _jobs = _prepare_analysis_batch(configured_client, test_db, candidate_count=3)
    config = configured_client.app.state.config

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(
            lambda source: workflow_runs._ensure_prefetch_batch(
                test_db, config, str(run["workflow_run_id"]), source
            ),
            ["prefetch-source-a", "prefetch-source-b"],
        ))

    assert all(results)
    with test_db.connect() as connection:
        active = connection.execute(
            """
            SELECT COUNT(*) FROM fj_workflow_candidate_reservations
            WHERE job_id = (
              SELECT job_id FROM fj_workflow_prefetch_items
              ORDER BY created_at DESC LIMIT 1
            ) AND status = 'reserved'
            """
        ).fetchone()[0]
        batch_statuses = {
            row[0] for row in connection.execute(
                """
                SELECT status FROM fj_workflow_prefetch_batches
                WHERE workflow_run_id = ?
                """,
                (run["workflow_run_id"],),
            ).fetchall()
        }
    assert active == 1
    assert "failed" in batch_statuses


def test_cancelled_prefetch_releases_reservation_for_later_selection(
    configured_client, test_db
) -> None:
    run, _jobs = _prepare_analysis_batch(configured_client, test_db, candidate_count=3)
    config = configured_client.app.state.config
    first_batch = workflow_runs._ensure_prefetch_batch(
        test_db, config, str(run["workflow_run_id"]), "cancel-source-a"
    )
    assert first_batch

    workflow_runs._cancel_prefetch_batches(test_db, str(run["workflow_run_id"]))
    second_batch = workflow_runs._ensure_prefetch_batch(
        test_db, config, str(run["workflow_run_id"]), "cancel-source-b"
    )

    assert second_batch
    with test_db.connect() as connection:
        statuses = [
            row[0] for row in connection.execute(
                """
                SELECT status FROM fj_workflow_candidate_reservations
                WHERE workflow_run_id = ? AND owner_type = 'prefetch'
                ORDER BY created_at
                """,
                (run["workflow_run_id"],),
            ).fetchall()
        ]
    assert "cancelled" in statuses
    assert statuses.count("reserved") == 1


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
    assert result["stop_reason"] == "approved_search_space_exhausted"


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


def test_parent_pause_resume_is_atomic_and_does_not_append_advance(
    configured_client, test_db
) -> None:
    run = _create_run(configured_client)
    child = run["children"][0]

    paused = configured_client.post(
        f"/api/fine-job/workflow-runs/{run['workflow_run_id']}/pause",
        json={"transition_id": "parent-pause-1"},
    ).json()
    assert paused["control_state"] == "paused"
    assert paused["control_cause"] == "parent_pause"
    assert paused["transition_id"] == "parent-pause-1"
    assert paused["children"][0]["status"] == "pending"
    repeated_pause = configured_client.post(
        f"/api/fine-job/workflow-runs/{run['workflow_run_id']}/pause",
        json={"transition_id": "parent-pause-2"},
    ).json()
    assert repeated_pause["state_version"] == paused["state_version"]

    resumed = configured_client.post(
        f"/api/fine-job/workflow-runs/{run['workflow_run_id']}/resume",
        json={"transition_id": "parent-resume-1"},
    ).json()
    assert resumed["control_state"] == "active"
    assert resumed["status"] == "running"
    assert resumed["children"][0]["status"] == "pending"
    with test_db.connect() as connection:
        task = connection.execute(
            "SELECT status, operation_ref_id FROM fj_workflow_tasks WHERE workflow_run_id = ?",
            (run["workflow_run_id"],),
        ).fetchone()
    assert task["status"] == "pending"
    assert task["operation_ref_id"] is None
    assert child["child_ref"] == resumed["children"][0]["child_ref"]


def test_child_pause_resume_uses_child_waiting_state(configured_client, test_db, monkeypatch) -> None:
    run = _create_run(configured_client)
    child_id = run["children"][0]["smart_capture_id"]
    smart_captures._update_capture(
        test_db,
        child_id,
        status="running",
        stage="capturing",
        waiting_reason="",
        control_cause="",
        transition_id="child-start-1",
        message="正在采集",
    )
    paused = configured_client.post(
        f"/api/fine-job/smart-captures/{child_id}/pause",
        json={"transition_id": "child-pause-1"},
    ).json()
    assert paused["status"] == "paused"
    assert paused["control_cause"] == "child_self_pause"
    repeated_pause = configured_client.post(
        f"/api/fine-job/smart-captures/{child_id}/pause",
        json={"transition_id": "child-pause-2"},
    ).json()
    assert repeated_pause["state_version"] == paused["state_version"]

    waiting = configured_client.get(
        f"/api/fine-job/workflow-runs/{run['workflow_run_id']}"
    ).json()
    assert waiting["control_state"] == "waiting_child_paused"
    assert waiting["control_cause"] == "child_self_pause"

    monkeypatch.setattr(
        smart_captures.boss_scraper_service,
        "get_browser_status",
        lambda: BossBrowserStatus(running=True, cdp_port=9222),
    )
    resumed = configured_client.post(
        f"/api/fine-job/smart-captures/{child_id}/resume",
        json={"transition_id": "child-resume-1"},
    ).json()
    assert resumed["status"] == "running"
    parent = configured_client.get(
        f"/api/fine-job/workflow-runs/{run['workflow_run_id']}"
    ).json()
    assert parent["control_state"] == "active"
    assert parent["status"] == "running"


def test_child_stop_waits_for_parent_decision_and_skip_or_end_preserves_child(
    configured_client,
) -> None:
    run = _create_run(configured_client)
    child = run["children"][0]
    stopped = configured_client.post(
        f"/api/fine-job/smart-captures/{child['smart_capture_id']}/stop",
        json={"transition_id": "child-stop-skip-1"},
    ).json()
    assert stopped["status"] == "stopped"
    waiting = configured_client.get(
        f"/api/fine-job/workflow-runs/{run['workflow_run_id']}"
    ).json()
    assert waiting["control_state"] == "child_cancelled_waiting_decision"
    skipped = configured_client.post(
        f"/api/fine-job/workflow-runs/{run['workflow_run_id']}/children/{child['child_relation_id']}/skip",
        json={"transition_id": "child-skip-1"},
    ).json()
    assert skipped["status"] == "completed"
    assert skipped["children"][0]["status"] == "stopped"

    second = _create_run(configured_client)
    second_child = second["children"][0]
    configured_client.post(
        f"/api/fine-job/smart-captures/{second_child['smart_capture_id']}/stop",
        json={"transition_id": "child-stop-end-1"},
    )
    ended = configured_client.post(
        f"/api/fine-job/workflow-runs/{second['workflow_run_id']}/children/{second_child['child_relation_id']}/end",
        json={"transition_id": "child-end-1"},
    ).json()
    assert ended["status"] == "cancelled"
    assert ended["children"][0]["status"] == "stopped"


def test_hard_failed_child_waits_without_retry_and_supports_skip_or_end(
    configured_client, test_db
) -> None:
    run = _create_run(configured_client)
    child = run["children"][0]
    smart_captures._update_capture(
        test_db,
        child["smart_capture_id"],
        status="failed",
        stage="failed",
        waiting_reason="child_failed",
        control_cause="child_failure",
        transition_id="child-failed-1",
        message="采集失败",
        completed=True,
    )
    waiting = configured_client.get(
        f"/api/fine-job/workflow-runs/{run['workflow_run_id']}"
    ).json()
    assert waiting["control_state"] == "child_failed_waiting_decision"
    assert waiting["children"][0]["status"] == "failed"
    assert waiting["children"][0]["capabilities"]["retry"] is False
    retry = configured_client.post(
        f"/api/fine-job/smart-captures/{child['smart_capture_id']}/retry"
    )
    assert retry.status_code == 409
    skipped = configured_client.post(
        f"/api/fine-job/workflow-runs/{run['workflow_run_id']}/children/{child['child_relation_id']}/skip",
        json={"transition_id": "failed-skip-1"},
    ).json()
    assert skipped["status"] == "completed"
    assert skipped["children"][0]["status"] == "failed"

    second = _create_run(configured_client)
    second_child = second["children"][0]
    smart_captures._update_capture(
        test_db,
        second_child["smart_capture_id"],
        status="failed",
        stage="failed",
        waiting_reason="child_failed",
        control_cause="child_failure",
        transition_id="child-failed-end-1",
        message="采集失败",
        completed=True,
    )
    ended = configured_client.post(
        f"/api/fine-job/workflow-runs/{second['workflow_run_id']}/children/{second_child['child_relation_id']}/end",
        json={"transition_id": "failed-end-1"},
    ).json()
    assert ended["status"] == "cancelled"
    assert ended["children"][0]["status"] == "failed"


def test_parent_controls_preserve_failed_child_and_pause_callback_priority(
    configured_client, test_db
) -> None:
    run = _create_run(configured_client)
    child = run["children"][0]
    smart_captures._update_capture(
        test_db,
        child["smart_capture_id"],
        status="running",
        stage="capturing",
        waiting_reason="",
        control_cause="",
        transition_id="child-running-1",
        message="正在采集",
    )
    paused = configured_client.post(
        f"/api/fine-job/workflow-runs/{run['workflow_run_id']}/pause",
        json={"transition_id": "parent-pause-race-1"},
    ).json()
    assert paused["control_state"] == "paused"
    smart_captures._update_capture(
        test_db,
        child["smart_capture_id"],
        status="failed",
        stage="failed",
        waiting_reason="child_failed",
        control_cause="child_failure",
        transition_id="late-child-failure-1",
        message="迟到失败回调",
        completed=True,
    )
    still_paused = configured_client.get(
        f"/api/fine-job/workflow-runs/{run['workflow_run_id']}"
    ).json()
    assert still_paused["control_state"] == "paused"
    resumed = configured_client.post(
        f"/api/fine-job/workflow-runs/{run['workflow_run_id']}/resume",
        json={"transition_id": "parent-resume-race-1"},
    ).json()
    assert resumed["control_state"] == "child_failed_waiting_decision"
    assert resumed["children"][0]["status"] == "failed"
    blocked_pause = configured_client.post(
        f"/api/fine-job/workflow-runs/{run['workflow_run_id']}/pause"
    )
    assert blocked_pause.status_code == 409
    cancelled = configured_client.post(
        f"/api/fine-job/workflow-runs/{run['workflow_run_id']}/cancel"
    ).json()
    assert cancelled["status"] == "cancelled"
    assert cancelled["children"][0]["status"] == "failed"


def test_parent_cancel_ignores_late_child_event(configured_client, test_db) -> None:
    run = _create_run(configured_client)
    child = run["children"][0]
    cancelled = configured_client.post(
        f"/api/fine-job/workflow-runs/{run['workflow_run_id']}/cancel",
        json={"transition_id": "parent-cancel-race-1"},
    ).json()
    assert cancelled["status"] == "cancelled"
    with test_db.connect() as connection:
        relation = connection.execute(
            "SELECT child_state_version FROM fj_workflow_children WHERE id = ?",
            (child["child_relation_id"],),
        ).fetchone()
        workflow_children.record_child_event_in_connection(
            connection,
            child_relation_id=child["child_relation_id"],
            child_type="smart_capture",
            child_ref=child["smart_capture_id"],
            child_status="interrupted",
            transition_id="late-after-cancel-1",
            state_version=int(relation["child_state_version"]) + 1,
            waiting_reason="capture_interrupted",
            control_cause="recovery",
            event_id="late-after-cancel-event",
        )
    final = configured_client.get(
        f"/api/fine-job/workflow-runs/{run['workflow_run_id']}"
    ).json()
    assert final["status"] == "cancelled"
    assert final["children"][0]["status"] == "stopped"


def test_interrupted_child_requires_explicit_resume_and_restores_parent(
    configured_client, test_db, monkeypatch
) -> None:
    run = _create_run(configured_client)
    child = run["children"][0]
    smart_captures._update_capture(
        test_db,
        child["smart_capture_id"],
        status="interrupted",
        stage="interrupted",
        waiting_reason="capture_interrupted",
        control_cause="recovery",
        transition_id="child-interrupted-1",
        message="执行器已中断",
    )
    waiting = configured_client.get(
        f"/api/fine-job/workflow-runs/{run['workflow_run_id']}"
    ).json()
    assert waiting["control_state"] == "waiting_child_interrupted"
    assert waiting["waiting_reason"] == "capture_interrupted"
    monkeypatch.setattr(
        smart_captures.boss_scraper_service,
        "get_browser_status",
        lambda: BossBrowserStatus(running=True, cdp_port=9222),
    )
    resumed = configured_client.post(
        f"/api/fine-job/smart-captures/{child['smart_capture_id']}/resume",
        json={"transition_id": "child-interrupted-resume-1"},
    ).json()
    assert resumed["status"] == "running"
    restored = configured_client.get(
        f"/api/fine-job/workflow-runs/{run['workflow_run_id']}"
    ).json()
    assert restored["control_state"] == "active"


def test_duplicate_child_event_and_late_cancel_event_do_not_rewrite_parent(
    configured_client, test_db
) -> None:
    run = _create_run(configured_client)
    child = run["children"][0]
    smart_captures._update_capture(
        test_db,
        child["smart_capture_id"],
        status="stopped",
        stage="stopped",
        control_cause="child_user_stop",
        transition_id="stop-event-1",
        message="已停止",
        completed=True,
    )
    stopped = configured_client.get(
        f"/api/fine-job/workflow-runs/{run['workflow_run_id']}"
    ).json()
    assert stopped["control_state"] == "child_cancelled_waiting_decision"
    with test_db.connect() as connection:
        relation = connection.execute(
            "SELECT child_state_version FROM fj_workflow_children WHERE id = ?",
            (child["child_relation_id"],),
        ).fetchone()
        event_version = int(relation["child_state_version"])
        result = workflow_children.record_child_event_in_connection(
            connection,
            child_relation_id=child["child_relation_id"],
            child_type="smart_capture",
            child_ref=child["smart_capture_id"],
            child_status="stopped",
            transition_id="late-stop-1",
            state_version=event_version,
            control_cause="child_user_stop",
            event_id="duplicate-stop-event",
        )
        assert result == "duplicate-stop-event"
    configured_client.post(
        f"/api/fine-job/workflow-runs/{run['workflow_run_id']}/children/{child['child_relation_id']}/end",
        json={"transition_id": "end-after-stop-1"},
    )
    with test_db.connect() as connection:
        workflow_children.consume_child_event_in_connection(connection, "duplicate-stop-event")
    final = configured_client.get(
        f"/api/fine-job/workflow-runs/{run['workflow_run_id']}"
    ).json()
    assert final["status"] == "cancelled"
    assert final["control_state"] == "active"


def test_cutover_starts_linked_child_once_and_consumes_completion_outcome(
    configured_client, test_db, monkeypatch
) -> None:
    cutover_guard.configure_runtime_cutover_phase(cutover_guard.CutoverPhase.POST_CUTOVER)
    run = _create_run(configured_client, delivery_target_enabled=False)
    child = run["children"][0]
    calls: list[tuple[str, str]] = []

    def start_child(_db, _config, child_type: str, child_ref: str):
        calls.append((child_type, child_ref))
        return {"smart_capture_id": child_ref, "status": "running"}

    monkeypatch.setattr(child_adapter, "start", start_child)
    started = configured_client.post(
        f"/api/fine-job/workflow-runs/{run['workflow_run_id']}/children/{child['child_relation_id']}/start"
    )
    assert started.status_code == 200
    assert calls == [("smart_capture", child["smart_capture_id"])]

    with pytest.raises(AppError) as advance_error:
        workflow_runs.advance_deep_job_search(
            test_db, configured_client.app.state.config, run["workflow_run_id"]
        )
    assert advance_error.value.error_category == "WORKFLOW_LIVE_EXECUTION_DISABLED"

    smart_captures._update_capture(
        test_db,
        child["smart_capture_id"],
        status="completed",
        stage="completed",
        waiting_reason="",
        control_cause="",
        transition_id="cutover-completed-1",
        message="岗位采集已完成",
        result_summary={"candidate_count": 4},
        completed=True,
    )
    completed = configured_client.get(
        f"/api/fine-job/workflow-runs/{run['workflow_run_id']}"
    ).json()
    assert completed["status"] == "completed"
    assert completed["children"][0]["result_summary"] == {"candidate_count": 4}


def test_cutover_linked_child_uses_smart_capture_engine_for_first_analysis_batch(
    configured_client, test_db
) -> None:
    run = _create_run(
        configured_client,
        delivery_target_enabled=True,
        recommend_target=2,
        candidate_target_count=2,
        analysis_batch_size=1,
    )
    smart_capture_id = str(run["children"][0]["smart_capture_id"])
    search_task_id = str(run["tasks"][0]["workflow_task_id"])
    batch_id = "cutover-linked-engine-batch"
    now = utc_now()
    create_capture_batch(
        test_db,
        capture_id=batch_id,
        smart_capture_id=smart_capture_id,
        keyword="AI Agent",
        city="广州",
        pages=1,
        auto_details=False,
        created_at=now,
        capture_source="smart",
    )
    jobs = record_capture_jobs(
        test_db,
        capture_id=batch_id,
        search_keyword="AI Agent",
        jobs=[
            {
                "job_id": f"cutover-linked-job-{index}",
                "title": f"AI Agent 工程师 {index}",
                "company_name": f"候选公司 {index}",
                "location": "广州",
                "detail_status": "completed",
                "detail": {"job_description": f"当前岗位 JD {index}"},
            }
            for index in range(2)
        ],
        collected_at=now,
    )
    with test_db.connect() as connection:
        for job in jobs:
            connection.execute(
                """
                INSERT INTO fj_workflow_job_discoveries (
                  id, workflow_run_id, smart_capture_id, task_id, job_id,
                  search_keyword, city, search_combination_json, scroll_depth,
                  discovered_at, is_run_first_discovery, is_historical_duplicate,
                  is_filter_candidate
                ) VALUES (?, ?, ?, ?, ?, 'AI Agent', '广州', '{}', 1, ?, 1, 0, 1)
                """,
                (
                    new_id(),
                    run["workflow_run_id"],
                    smart_capture_id,
                    search_task_id,
                    job["history_record_id"],
                    utc_now(),
                ),
            )
        connection.execute(
            "UPDATE fj_smart_captures SET status = 'running', stage = 'capturing' WHERE id = ?",
            (smart_capture_id,),
        )

    smart_capture_engine.advance_completed_batch(
        test_db,
        smart_capture_id,
        {
            "id": batch_id,
            "has_more": False,
            "_output_dir": configured_client.app.state.config.output_root
            / "fine-job"
            / "boss-capture",
        },
    )

    snapshot = smart_captures.get_smart_capture(test_db, smart_capture_id)
    assert snapshot["status"] == "running"
    assert snapshot["stage"] == "waiting_codex"
    with test_db.connect() as connection:
        analysis = connection.execute(
            "SELECT smart_capture_id, workflow_run_id, status FROM fj_workflow_tasks WHERE smart_capture_id = ? AND task_type = 'deep_job_search_analysis'",
            (smart_capture_id,),
        ).fetchall()
    assert len(analysis) == 1
    assert str(analysis[0]["workflow_run_id"]) == str(run["workflow_run_id"])
    assert str(analysis[0]["status"]) == "pending"
