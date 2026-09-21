from __future__ import annotations

import json

from backend.app.services.fine_job import smart_capture_engine, smart_captures
from backend.app.services.fine_job.boss_capture_history import create_capture_batch, record_capture_jobs


def _setup_completed_capture(test_db) -> tuple[str, str]:
    capture = smart_captures.create_smart_capture(
        test_db,
        source="boss_capture",
        workflow_run_id=None,
        search_config={
            "filter_strategy_id": "strategy-1",
            "allowed_search_keywords": ["Python"],
            "allowed_cities": ["上海"],
            "candidate_target_count": 1,
        },
        target_count=1,
    )
    smart_capture_id = str(capture["smart_capture_id"])
    search_task = smart_capture_engine.prepare_search_execution(
        test_db,
        smart_capture_id,
        {
            "filter_strategy_id": "strategy-1",
            "allowed_search_keywords": ["Python"],
            "allowed_cities": ["上海"],
        },
    )
    batch_id = "domain-analysis-batch"
    create_capture_batch(
        test_db,
        capture_id=batch_id,
        smart_capture_id=smart_capture_id,
        keyword="Python",
        city="上海",
        pages=1,
        auto_details=True,
        created_at="2026-09-21T00:00:00Z",
        capture_source="smart",
    )
    jobs = record_capture_jobs(
        test_db,
        capture_id=batch_id,
        search_keyword="Python",
        jobs=[
            {
                "job_id": "domain-job-1",
                "title": "Python 工程师",
                "boss_name": "测试公司",
                "detail_status": "completed",
                "detail": {"description": "负责 Python 服务开发"},
            }
        ],
    )
    job_id = str(jobs[0]["history_record_id"])
    with test_db.connect() as connection:
        connection.execute(
            """
            INSERT INTO fj_workflow_job_discoveries (
              id, workflow_run_id, smart_capture_id, task_id, job_id, search_keyword, city,
              search_combination_json, discovered_at, is_run_first_discovery,
              is_historical_duplicate, is_filter_candidate
            ) VALUES (?, NULL, ?, ?, ?, 'Python', '上海', ?, ?, 1, 0, 1)
            """,
            (
                "domain-discovery-1",
                smart_capture_id,
                search_task["task_id"],
                job_id,
                json.dumps({"search_combination_id": search_task["combination_id"]}),
                "2026-09-21T00:00:01Z",
            ),
        )
        connection.execute(
            "UPDATE fj_smart_captures SET status = 'completed', stage = 'completed', completed_at = ? WHERE id = ?",
            ("2026-09-21T00:00:02Z", smart_capture_id),
        )
    return smart_capture_id, job_id


def _setup_running_delivery_capture(test_db) -> tuple[str, list[str]]:
    payload = {
        "filter_strategy_id": "strategy-1",
        "allowed_search_keywords": ["Python"],
        "allowed_cities": ["上海"],
        "candidate_target_count": 3,
        "delivery_target_enabled": True,
        "recommend_target": 2,
        "analysis_batch_size": 1,
    }
    capture = smart_captures.create_smart_capture(
        test_db,
        source="boss_capture",
        workflow_run_id=None,
        search_config=payload,
        target_count=3,
        execution_config=smart_captures._build_execution_config(payload),
    )
    smart_capture_id = str(capture["smart_capture_id"])
    search_task = smart_capture_engine.prepare_search_execution(
        test_db, smart_capture_id, payload
    )
    batch_id = "domain-delivery-batch"
    create_capture_batch(
        test_db,
        capture_id=batch_id,
        smart_capture_id=smart_capture_id,
        keyword="Python",
        city="上海",
        pages=1,
        auto_details=True,
        created_at="2026-09-21T00:00:00Z",
        capture_source="smart",
    )
    jobs = record_capture_jobs(
        test_db,
        capture_id=batch_id,
        search_keyword="Python",
        jobs=[
            {
                "job_id": f"delivery-job-{index}",
                "title": f"Python 工程师 {index}",
                "boss_name": f"测试公司 {index}",
                "detail_status": "completed",
                "detail": {"description": f"负责 Python 服务开发 {index}"},
            }
            for index in range(1, 4)
        ],
    )
    job_ids = [str(job["history_record_id"]) for job in jobs]
    with test_db.connect() as connection:
        for index, job_id in enumerate(job_ids, start=1):
            connection.execute(
                """
                INSERT INTO fj_workflow_job_discoveries (
                  id, workflow_run_id, smart_capture_id, task_id, job_id,
                  search_keyword, city, search_combination_json, discovered_at,
                  is_run_first_discovery, is_historical_duplicate, is_filter_candidate
                ) VALUES (?, NULL, ?, ?, ?, 'Python', '上海', ?, ?, 1, 0, 1)
                """,
                (
                    f"delivery-discovery-{index}",
                    smart_capture_id,
                    search_task["task_id"],
                    job_id,
                    json.dumps({"search_combination_id": search_task["combination_id"]}),
                    f"2026-09-21T00:00:0{index}Z",
                ),
            )
        connection.execute(
            "UPDATE fj_smart_captures SET status = 'running', stage = 'waiting_codex' WHERE id = ?",
            (smart_capture_id,),
        )
    return smart_capture_id, job_ids


def _create_linked_workflow_run(configured_client) -> dict[str, object]:
    strategy = configured_client.post(
        "/api/fine-job/strategies/filters",
        json={
            "name": "Linked Manual Analysis 筛选策略",
            "enabled": True,
            "search_keywords": ["Python"],
            "cities": ["上海"],
            "title_include_any": ["Python"],
            "unknown_value_policy": "review",
        },
    ).json()["strategy"]
    profile = configured_client.get("/api/fine-job/profiles").json()["profiles"][0]
    resume = configured_client.post(
        f"/api/fine-job/profiles/{profile['id']}/resume-versions",
        json={
            "name": "Linked Manual Analysis 简历",
            "version_type": "base",
            "current_role": "base",
            "content": "具备 Python 服务开发经验。",
        },
    ).json()["resume_version"]
    recommendation = configured_client.post(
        "/api/fine-job/strategies/recommendations",
        json={
            "name": "Linked Manual Analysis 建议投递策略",
            "enabled": True,
            "filter_strategy_id": strategy["id"],
            "candidate_profile_id": profile["id"],
            "resume_version_id": resume["id"],
            "evaluation_method": "hybrid",
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
                "candidate_target_count": 1,
                "allowed_search_keywords": ["Python"],
                "allowed_cities": ["上海"],
            },
        },
    )
    assert response.status_code == 201
    return response.json()


def test_independent_smart_capture_analysis_handoff_context_and_save(configured_client) -> None:
    smart_capture_id, job_id = _setup_completed_capture(configured_client.app.state.db)
    before = configured_client.get(f"/api/fine-job/smart-captures/{smart_capture_id}").json()
    current_before = configured_client.get("/api/fine-job/smart-captures/current").json()["smart_capture"]

    batch = configured_client.post(
        f"/api/fine-job/smart-captures/{smart_capture_id}/analysis-batches",
        json={"job_ids": [job_id], "analysis_batch_size": 1},
    )
    assert batch.status_code == 200
    analysis_batch_id = batch.json()["analysis_batch_id"]
    task_id = batch.json()["items"][0]["workflow_task_id"]

    listed = configured_client.get(
        f"/api/fine-job/smart-captures/{smart_capture_id}/analysis-items",
        params={"analysis_batch_id": analysis_batch_id},
    )
    assert listed.status_code == 200
    assert listed.json()["items"][0]["workflow_task_id"] == task_id

    claimed = configured_client.post(
        f"/api/fine-job/smart-captures/{smart_capture_id}/analysis-handoff/claim",
        json={"codex_session_ref": "domain-codex", "handoff_kind": "initial"},
    )
    assert claimed.status_code == 200
    attempt_id = claimed.json()["handoff"]["handoff_attempt_id"]

    prompt_written = configured_client.post(
        f"/api/fine-job/smart-captures/{smart_capture_id}/analysis-handoff/prompt-written",
        json={
            "analysis_batch_id": analysis_batch_id,
            "handoff_attempt_id": attempt_id,
            "codex_session_ref": "domain-codex",
        },
    )
    assert prompt_written.status_code == 200

    ack = configured_client.post(
        f"/api/fine-job/smart-captures/{smart_capture_id}/analysis-handoff/ack-started",
        json={"analysis_batch_id": analysis_batch_id, "handoff_attempt_id": attempt_id},
    )
    assert ack.status_code == 200
    assert ack.json()["handoff"]["attempt_status"] == "started"
    after_ack = configured_client.get(
        f"/api/fine-job/smart-captures/{smart_capture_id}"
    ).json()
    assert after_ack["prefetch"]["status"] == "none"
    with configured_client.app.state.db.connect() as connection:
        started_at = connection.execute(
            "SELECT started_at FROM fj_workflow_analysis_handoffs WHERE smart_capture_id = ? AND analysis_batch_id = ?",
            (smart_capture_id, analysis_batch_id),
        ).fetchone()["started_at"]
    repeated_ack = configured_client.post(
        f"/api/fine-job/smart-captures/{smart_capture_id}/analysis-handoff/ack-started",
        json={"analysis_batch_id": analysis_batch_id, "handoff_attempt_id": attempt_id},
    )
    assert repeated_ack.status_code == 200
    with configured_client.app.state.db.connect() as connection:
        assert connection.execute(
            "SELECT started_at FROM fj_workflow_analysis_handoffs WHERE smart_capture_id = ? AND analysis_batch_id = ?",
            (smart_capture_id, analysis_batch_id),
        ).fetchone()["started_at"] == started_at

    context = configured_client.get(
        f"/api/fine-job/smart-captures/{smart_capture_id}/analysis-items/{task_id}/context"
    )
    assert context.status_code == 200
    assert context.json()["item_context_snapshot"]["context_snapshot_id"]

    saved = configured_client.post(
        f"/api/fine-job/smart-captures/{smart_capture_id}/analysis-items/{task_id}/save",
        json={"decision": "review", "summary": "保留人工复核"},
    )
    assert saved.status_code == 200
    assert saved.json()["items"][0]["status"] == "succeeded"

    final = configured_client.get(f"/api/fine-job/smart-captures/{smart_capture_id}")
    assert final.status_code == 200
    assert final.json()["status"] == "completed"
    assert final.json()["state_version"] == before["state_version"]
    assert configured_client.get("/api/fine-job/smart-captures/current").json()["smart_capture"]["smart_capture_id"] == current_before["smart_capture_id"]


def test_linked_completed_capture_manual_analysis_keeps_parent_terminal(
    configured_client, test_db
) -> None:
    run_data = _create_linked_workflow_run(configured_client)
    child = run_data["children"][0]
    smart_capture_id = str(child["smart_capture_id"])
    capture_batch_id = "linked-manual-analysis-batch"
    create_capture_batch(
        test_db,
        capture_id=capture_batch_id,
        smart_capture_id=smart_capture_id,
        keyword="Python",
        city="上海",
        pages=1,
        auto_details=True,
        created_at="2026-09-22T00:00:00Z",
        capture_source="smart",
    )
    jobs = record_capture_jobs(
        test_db,
        capture_id=capture_batch_id,
        search_keyword="Python",
        jobs=[
            {
                "job_id": "linked-manual-job",
                "title": "Python 工程师",
                "detail_status": "completed",
                "detail": {"description": "负责 Python 服务开发"},
            }
        ],
    )
    job_id = str(jobs[0]["history_record_id"])
    with test_db.connect() as connection:
        connection.execute(
            """
            INSERT INTO fj_workflow_job_discoveries (
              id, workflow_run_id, smart_capture_id, task_id, job_id, search_keyword, city,
              search_combination_json, discovered_at, is_run_first_discovery,
              is_historical_duplicate, is_filter_candidate
            ) VALUES (?, ?, ?, ?, ?, 'Python', '上海', '{}', ?, 1, 0, 1)
            """,
            (
                "linked-manual-discovery",
                run_data["workflow_run_id"],
                smart_capture_id,
                run_data["tasks"][0]["workflow_task_id"],
                job_id,
                "2026-09-22T00:00:01Z",
            ),
        )
    smart_captures._update_capture(
        test_db,
        smart_capture_id,
        status="completed",
        stage="completed",
        waiting_reason="",
        control_cause="",
        transition_id="linked-manual-completed",
        message="候选目标已达到",
        result_summary={
            "candidate_count": 1,
            "completion_reason": "candidate_target_reached",
        },
        completed=True,
    )
    before = configured_client.get(
        f"/api/fine-job/workflow-runs/{run_data['workflow_run_id']}"
    ).json()
    with configured_client.app.state.db.connect() as connection:
        completion_events_before = connection.execute(
            "SELECT COUNT(*) AS count FROM fj_workflow_child_events WHERE child_relation_id = ?",
            (child["child_relation_id"],),
        ).fetchone()["count"]

    batch = configured_client.post(
        f"/api/fine-job/smart-captures/{smart_capture_id}/analysis-batches",
        json={"job_ids": [job_id], "analysis_batch_size": 1},
    )
    assert batch.status_code == 200
    analysis_batch_id = batch.json()["analysis_batch_id"]
    task_id = batch.json()["items"][0]["workflow_task_id"]
    claimed = configured_client.post(
        f"/api/fine-job/smart-captures/{smart_capture_id}/analysis-handoff/claim",
        json={"codex_session_ref": "linked-manual-codex", "handoff_kind": "initial"},
    )
    assert claimed.status_code == 200
    attempt_id = claimed.json()["handoff"]["handoff_attempt_id"]
    assert configured_client.post(
        f"/api/fine-job/smart-captures/{smart_capture_id}/analysis-handoff/prompt-written",
        json={
            "analysis_batch_id": analysis_batch_id,
            "handoff_attempt_id": attempt_id,
            "codex_session_ref": "linked-manual-codex",
        },
    ).status_code == 200
    ack = configured_client.post(
        f"/api/fine-job/smart-captures/{smart_capture_id}/analysis-handoff/ack-started",
        json={"analysis_batch_id": analysis_batch_id, "handoff_attempt_id": attempt_id},
    )
    assert ack.status_code == 200
    assert configured_client.get(
        f"/api/fine-job/smart-captures/{smart_capture_id}"
    ).json()["prefetch"]["status"] == "none"
    saved = configured_client.post(
        f"/api/fine-job/smart-captures/{smart_capture_id}/analysis-items/{task_id}/save",
        json={"decision": "review", "summary": "保留人工复核"},
    )
    assert saved.status_code == 200
    after_capture = configured_client.get(
        f"/api/fine-job/smart-captures/{smart_capture_id}"
    ).json()
    after_parent = configured_client.get(
        f"/api/fine-job/workflow-runs/{run_data['workflow_run_id']}"
    ).json()
    assert after_capture["status"] == "completed"
    assert after_capture["result_summary"] == before["children"][0]["result_summary"]
    assert after_parent["status"] == "completed"
    assert after_parent["state_version"] == before["state_version"]
    with configured_client.app.state.db.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) AS count FROM fj_workflow_child_events WHERE child_relation_id = ?",
            (child["child_relation_id"],),
        ).fetchone()["count"] == completion_events_before


def test_independent_smart_capture_save_requires_started_handoff(configured_client) -> None:
    smart_capture_id, job_id = _setup_completed_capture(configured_client.app.state.db)
    batch = configured_client.post(
        f"/api/fine-job/smart-captures/{smart_capture_id}/analysis-batches",
        json={"job_ids": [job_id], "analysis_batch_size": 1},
    )
    assert batch.status_code == 200
    task_id = batch.json()["items"][0]["workflow_task_id"]

    blocked_save = configured_client.post(
        f"/api/fine-job/smart-captures/{smart_capture_id}/analysis-items/{task_id}/save",
        json={"decision": "review", "summary": "尚未 ACK"},
    )
    assert blocked_save.status_code == 409
    assert blocked_save.json()["error_category"] == "SMART_CAPTURE_ANALYSIS_NOT_STARTED"


def test_analysis_and_prefetch_run_in_parallel_then_promote_ready_batch(configured_client) -> None:
    smart_capture_id, job_ids = _setup_running_delivery_capture(
        configured_client.app.state.db
    )
    first_batch = configured_client.post(
        f"/api/fine-job/smart-captures/{smart_capture_id}/analysis-batches",
        json={"job_ids": [job_ids[0]], "analysis_batch_size": 1},
    ).json()
    first_batch_id = str(first_batch["analysis_batch_id"])
    first_task_id = str(first_batch["items"][0]["workflow_task_id"])
    claimed = configured_client.post(
        f"/api/fine-job/smart-captures/{smart_capture_id}/analysis-handoff/claim",
        json={"codex_session_ref": "parallel-codex", "handoff_kind": "initial"},
    ).json()
    attempt_id = str(claimed["handoff"]["handoff_attempt_id"])
    configured_client.post(
        f"/api/fine-job/smart-captures/{smart_capture_id}/analysis-handoff/prompt-written",
        json={
            "analysis_batch_id": first_batch_id,
            "handoff_attempt_id": attempt_id,
            "codex_session_ref": "parallel-codex",
        },
    )
    acked = configured_client.post(
        f"/api/fine-job/smart-captures/{smart_capture_id}/analysis-handoff/ack-started",
        json={"analysis_batch_id": first_batch_id, "handoff_attempt_id": attempt_id},
    )
    assert acked.status_code == 200

    parallel = configured_client.get(
        f"/api/fine-job/smart-captures/{smart_capture_id}"
    ).json()
    assert parallel["status"] == "running"
    assert parallel["stage"] == "analyzing_prefetch"
    assert parallel["prefetch"]["status"] == "ready"
    assert parallel["prefetch"]["ready_count"] == 1

    saved = configured_client.post(
        f"/api/fine-job/smart-captures/{smart_capture_id}/analysis-items/{first_task_id}/save",
        json={"decision": "recommend", "summary": "第一批通过"},
    )
    assert saved.status_code == 200
    promoted = configured_client.get(
        f"/api/fine-job/smart-captures/{smart_capture_id}"
    ).json()
    assert promoted["status"] == "running"
    assert promoted["stage"] == "waiting_codex"
    assert promoted["prefetch"]["status"] == "promoted"
    pending = [
        item
        for item in configured_client.get(
            f"/api/fine-job/smart-captures/{smart_capture_id}/analysis-items"
        ).json()["items"]
        if item["status"] == "pending"
    ]
    assert len(pending) == 1
    assert pending[0]["job"]["job_id"] == job_ids[1]
    with configured_client.app.state.db.connect() as connection:
        active_jobs = connection.execute(
            "SELECT job_id FROM fj_workflow_candidate_reservations WHERE status = 'reserved' ORDER BY job_id"
        ).fetchall()
    assert [str(row["job_id"]) for row in active_jobs] == [job_ids[1]]


def test_on_recommend_target_completes_only_after_result_contract(test_db) -> None:
    smart_capture_id, job_ids = _setup_running_delivery_capture(test_db)
    now = "2026-09-22T00:00:00Z"
    with test_db.connect() as connection:
        for index, job_id in enumerate(job_ids[:2], start=1):
            connection.execute(
                """
                INSERT INTO fj_workflow_tasks (
                  id, workflow_run_id, smart_capture_id, task_type, status,
                  payload_json, result_json, created_at, updated_at, completed_at
                ) VALUES (?, NULL, ?, 'deep_job_search_analysis', 'succeeded', ?, ?, ?, ?, ?)
                """,
                (
                    f"on-completion-analysis-{index}",
                    smart_capture_id,
                    json.dumps({"job_id": job_id, "analysis_batch_id": "on-completion-batch"}),
                    json.dumps({"job_id": job_id, "decision": "recommend"}),
                    now,
                    now,
                    now,
                ),
            )

    smart_capture_engine.analysis_item_saved(
        test_db, smart_capture_id, "on-completion-batch"
    )

    snapshot = smart_captures.get_smart_capture(test_db, smart_capture_id)
    assert snapshot["status"] == "completed"
    assert snapshot["result_summary"] == {
        "recommend_count": 2,
        "review_count": 0,
        "reject_count": 0,
        "completion_reason": "delivery_target_reached",
    }


def test_restart_converges_running_prefetch_unit_to_recoverable_pending(
    configured_client, monkeypatch
) -> None:
    db = configured_client.app.state.db
    smart_capture_id, job_ids = _setup_running_delivery_capture(db)
    with db.connect() as connection:
        connection.execute(
            "UPDATE fj_boss_jobs SET detail_status = 'not_collected' WHERE id = ?",
            (job_ids[1],),
        )
    batch = configured_client.post(
        f"/api/fine-job/smart-captures/{smart_capture_id}/analysis-batches",
        json={"job_ids": [job_ids[0]], "analysis_batch_size": 1},
    ).json()
    batch_id = str(batch["analysis_batch_id"])
    claimed = configured_client.post(
        f"/api/fine-job/smart-captures/{smart_capture_id}/analysis-handoff/claim",
        json={"codex_session_ref": "restart-codex", "handoff_kind": "initial"},
    ).json()
    attempt_id = str(claimed["handoff"]["handoff_attempt_id"])
    configured_client.post(
        f"/api/fine-job/smart-captures/{smart_capture_id}/analysis-handoff/prompt-written",
        json={
            "analysis_batch_id": batch_id,
            "handoff_attempt_id": attempt_id,
            "codex_session_ref": "restart-codex",
        },
    )
    monkeypatch.setattr(
        smart_capture_engine.boss_capture_task_manager,
        "start_history_detail",
        lambda *args, **kwargs: {"id": "prefetch-detail-running"},
    )
    acked = configured_client.post(
        f"/api/fine-job/smart-captures/{smart_capture_id}/analysis-handoff/ack-started",
        json={"analysis_batch_id": batch_id, "handoff_attempt_id": attempt_id},
    )
    assert acked.status_code == 200
    with db.connect() as connection:
        before = connection.execute(
            "SELECT status, operation_ref_id FROM fj_workflow_prefetch_items WHERE smart_capture_id = ?",
            (smart_capture_id,),
        ).fetchone()
    assert str(before["status"]) == "collecting"
    assert str(before["operation_ref_id"]) == "prefetch-detail-running"

    smart_captures.recover_interrupted_smart_captures(db)

    recovered = smart_captures.get_smart_capture(db, smart_capture_id)
    assert recovered["status"] == "interrupted"
    assert recovered["capabilities"]["retry"] is True
    with db.connect() as connection:
        after = connection.execute(
            "SELECT status, operation_ref_id FROM fj_workflow_prefetch_items WHERE smart_capture_id = ?",
            (smart_capture_id,),
        ).fetchone()
    assert str(after["status"]) == "pending"
    assert after["operation_ref_id"] is None


def test_last_prefetch_detail_promotes_when_analysis_finished_first(
    configured_client, monkeypatch
) -> None:
    db = configured_client.app.state.db
    smart_capture_id, job_ids = _setup_running_delivery_capture(db)
    with db.connect() as connection:
        connection.execute(
            "UPDATE fj_boss_jobs SET detail_status = 'not_collected' WHERE id = ?",
            (job_ids[1],),
        )
    batch = configured_client.post(
        f"/api/fine-job/smart-captures/{smart_capture_id}/analysis-batches",
        json={"job_ids": [job_ids[0]], "analysis_batch_size": 1},
    ).json()
    batch_id = str(batch["analysis_batch_id"])
    first_task_id = str(batch["items"][0]["workflow_task_id"])
    claimed = configured_client.post(
        f"/api/fine-job/smart-captures/{smart_capture_id}/analysis-handoff/claim",
        json={"codex_session_ref": "late-prefetch-codex", "handoff_kind": "initial"},
    ).json()
    attempt_id = str(claimed["handoff"]["handoff_attempt_id"])
    configured_client.post(
        f"/api/fine-job/smart-captures/{smart_capture_id}/analysis-handoff/prompt-written",
        json={
            "analysis_batch_id": batch_id,
            "handoff_attempt_id": attempt_id,
            "codex_session_ref": "late-prefetch-codex",
        },
    )
    monkeypatch.setattr(
        smart_capture_engine.boss_capture_task_manager,
        "start_history_detail",
        lambda *args, **kwargs: {"id": "late-prefetch-detail"},
    )
    configured_client.post(
        f"/api/fine-job/smart-captures/{smart_capture_id}/analysis-handoff/ack-started",
        json={"analysis_batch_id": batch_id, "handoff_attempt_id": attempt_id},
    )
    saved = configured_client.post(
        f"/api/fine-job/smart-captures/{smart_capture_id}/analysis-items/{first_task_id}/save",
        json={"decision": "recommend", "summary": "分析先完成"},
    )
    assert saved.status_code == 200
    waiting = smart_captures.get_smart_capture(db, smart_capture_id)
    assert waiting["status"] == "waiting_next_batch"
    waiting_version = int(waiting["state_version"])
    with db.connect() as connection:
        item = connection.execute(
            "SELECT id FROM fj_workflow_prefetch_items WHERE smart_capture_id = ? AND status = 'collecting'",
            (smart_capture_id,),
        ).fetchone()
        connection.execute(
            "UPDATE fj_boss_jobs SET detail_status = 'completed' WHERE id = ?",
            (job_ids[1],),
        )

    smart_capture_engine.process_detail_task_update(
        db,
        smart_capture_id,
        {
            "status": "completed",
            "pipeline_unit_type": "prefetch",
            "pipeline_unit_id": str(item["id"]),
            "_output_dir": configured_client.app.state.config.output_root
            / "fine-job"
            / "boss-capture",
        },
    )

    promoted = smart_captures.get_smart_capture(db, smart_capture_id)
    assert promoted["status"] == "running"
    assert promoted["stage"] == "waiting_codex"
    assert promoted["prefetch"]["status"] == "promoted"
    assert int(promoted["state_version"]) > waiting_version


def test_independent_smart_capture_context_and_state_are_mcp_identity_based(test_db) -> None:
    smart_capture_id, job_id = _setup_completed_capture(test_db)
    from backend.app.services.fine_job import smart_capture_domain
    from backend.app.services.fine_job.codex_tools import CodexToolService

    service = CodexToolService(test_db, object())  # type: ignore[arg-type]
    batch = smart_capture_domain.create_manual_analysis_batch(
        test_db,
        object(),  # type: ignore[arg-type]
        smart_capture_id,
        job_ids=[job_id],
        analysis_batch_size=1,
    )
    analysis_batch_id = str(batch["analysis_batch_id"])
    task_id = str(batch["items"][0]["workflow_task_id"])
    state = service.call("finejob.get_smart_capture_state", {"smart_capture_id": smart_capture_id})
    context = service.call(
        "finejob.get_smart_capture_context",
        {"smart_capture_id": smart_capture_id, "channel": "candidate_analysis"},
    )
    listed = service.call(
        "finejob.list_smart_capture_analysis_items",
        {"smart_capture_id": smart_capture_id, "analysis_batch_id": analysis_batch_id},
    )
    claimed = service.call(
        "finejob.claim_smart_capture_analysis_handoff",
        {"smart_capture_id": smart_capture_id, "codex_session_ref": "mcp-codex", "handoff_kind": "initial"},
    )
    attempt_id = str(claimed["data"]["handoff"]["handoff_attempt_id"])
    service.call(
        "finejob.mark_smart_capture_analysis_prompt_written",
        {
            "smart_capture_id": smart_capture_id,
            "analysis_batch_id": analysis_batch_id,
            "handoff_attempt_id": attempt_id,
            "codex_session_ref": "mcp-codex",
        },
    )
    acked = service.call(
        "finejob.ack_smart_capture_analysis_batch_started",
        {
            "smart_capture_id": smart_capture_id,
            "analysis_batch_id": analysis_batch_id,
            "handoff_attempt_id": attempt_id,
        },
    )
    item_context = service.call(
        "finejob.get_smart_capture_analysis_item_context",
        {"smart_capture_id": smart_capture_id, "workflow_task_id": task_id},
    )
    saved = service.call(
        "finejob.save_smart_capture_analysis_item",
        {
            "smart_capture_id": smart_capture_id,
            "workflow_task_id": task_id,
            "decision": "review",
            "summary": "MCP full chain",
        },
    )

    assert state["data"]["smart_capture_id"] == smart_capture_id
    assert context["data"]["channel"] == "candidate_analysis"
    assert listed["data"]["items"][0]["workflow_task_id"] == task_id
    assert acked["data"]["handoff"]["attempt_status"] == "started"
    assert item_context["data"]["job_id"] == job_id
    assert saved["data"]["items"][0]["status"] == "succeeded"
