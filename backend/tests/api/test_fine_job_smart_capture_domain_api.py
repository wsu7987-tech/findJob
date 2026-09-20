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
