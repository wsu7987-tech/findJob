from __future__ import annotations


from backend.app.services.fine_job import job_hunt_refresh


def _run_payload(scope_id: str) -> dict[str, object]:
    return {
        "scope_id": scope_id,
        "workflow_options": {
            "refresh_chat_list": False,
            "refresh_chat_messages": True,
            "refresh_related_jobs": False,
            "analyze_conversations": False,
            "generate_missing_suggestions": False,
        },
        "trigger_source": "page",
    }


def _mock_friend_list(monkeypatch) -> None:
    monkeypatch.setattr(
        job_hunt_refresh.boss_scraper_service,
        "capture_chat_friend_list",
        lambda: {
            "account_uid": "candidate",
            "url": "test",
            "response": {"zpData": {"result": []}},
        },
    )


def test_scope_discovery_and_run_endpoints_persist_fixed_scope(client, monkeypatch) -> None:
    _mock_friend_list(monkeypatch)
    context = client.get("/api/fine-job/job-hunt-refresh/context")
    assert context.status_code == 200
    assert context.json()["timezone"] == "Asia/Shanghai"

    discovered = client.post(
        "/api/fine-job/job-hunt-refresh/scopes",
        json={"selected_since_time": "2026-09-04T00:00:00Z"},
    )
    assert discovered.status_code == 201
    scope_id = discovered.json()["id"]
    assert discovered.json()["scope_generated_at"]
    with client.app.state.db.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM fj_job_hunt_refresh_scopes"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM fj_job_hunt_refresh_runs"
        ).fetchone()[0] == 0

    created = client.post("/api/fine-job/job-hunt-refresh/runs", json=_run_payload(scope_id))
    assert created.status_code == 201
    run_id = created.json()["id"]
    assert created.json()["status"] == "pending"
    assert created.json()["scope_id"] == scope_id
    assert created.json()["selected_since_time"] == "2026-09-04T00:00:00Z"
    assert created.json()["current_step"] == "waiting_codex"

    attached = client.patch(
        f"/api/fine-job/job-hunt-refresh/runs/{run_id}/codex-session",
        json={"codex_session_ref": "codex-run-example"},
    )
    assert attached.status_code == 200
    assert attached.json()["codex_session_ref"] == "codex-run-example"
    assert attached.json()["current_step"] == "waiting_codex"

    submitted = client.post(
        f"/api/fine-job/job-hunt-refresh/runs/{run_id}/prompt-submitted"
    )
    assert submitted.status_code == 200
    assert submitted.json()["current_step"] == "waiting_completion"

    cancelled = client.post(
        f"/api/fine-job/job-hunt-refresh/runs/{run_id}/cancel"
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"

    listed = client.get("/api/fine-job/job-hunt-refresh/runs")
    assert listed.status_code == 200
    assert listed.json()["runs"][0]["id"] == run_id


def test_codex_mcp_can_read_items_and_complete_empty_run(client, monkeypatch) -> None:
    _mock_friend_list(monkeypatch)
    scope_id = client.post(
        "/api/fine-job/job-hunt-refresh/scopes",
        json={"selected_since_time": "2026-09-04T00:00:00Z"},
    ).json()["id"]
    created_run = client.post(
        "/api/fine-job/job-hunt-refresh/runs",
        json=_run_payload(scope_id),
    ).json()
    runtime = client.post("/api/internal/codex/v1/runtime").json()
    headers = {
        "Authorization": f"Bearer {runtime['token']}",
        "X-FineJob-MCP-Contract-Version": "v1",
        "X-FineJob-Internal-API-Version": "v1",
    }

    read = client.post(
        "/api/internal/codex/v1/tools/get_job_hunt_refresh_run",
        headers=headers,
        json={"arguments": {"run_id": created_run["id"]}},
    )
    assert read.status_code == 200
    assert read.json()["data"]["id"] == created_run["id"]

    items = client.post(
        "/api/internal/codex/v1/tools/list_job_hunt_refresh_items",
        headers=headers,
        json={
            "arguments": {
                "run_id": created_run["id"],
                "item_type": "chat_session",
            }
        },
    )
    assert items.status_code == 200
    assert items.json()["data"]["items"] == []

    completed = client.post(
        "/api/internal/codex/v1/tools/complete_job_hunt_refresh_run",
        headers=headers,
        json={"arguments": {"run_id": created_run["id"]}},
    )
    assert completed.status_code == 200
    assert completed.json()["status"] == "completed"
