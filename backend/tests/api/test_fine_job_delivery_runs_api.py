from __future__ import annotations


def _prepare_ready_delivery_inputs(configured_client) -> None:
    configured_client.put(
        "/api/fine-job/job-intent",
        json={
            "target_title": "大模型应用开发",
            "cities": ["上海"],
            "keywords": ["AI Agent"],
            "expanded_keywords": ["LangGraph"],
            "excluded_keywords": ["销售"],
            "salary_min": 20,
            "salary_max": 40,
            "work_mode": "any",
            "notes": "",
        },
    )
    configured_client.put(
        "/api/fine-job/delivery-strategy",
        json={
            "automation_level": "assist",
            "auto_greeting_enabled": False,
            "daily_greeting_limit": 20,
            "hourly_greeting_limit": 5,
            "min_match_score": 0.72,
            "resume_submit_mode": "manual",
            "contact_share_mode": "manual",
            "interview_accept_mode": "manual",
            "only_online_interview": False,
            "pause_on_risk": True,
            "notes": "",
        },
    )


def test_create_dry_run_creates_run_and_logs(configured_client) -> None:
    _prepare_ready_delivery_inputs(configured_client)

    response = configured_client.post(
        "/api/fine-job/delivery-runs",
        json={"mode": "dry_run", "real_collect": False},
    )

    assert response.status_code == 200
    run = response.json()["run"]
    assert run["mode"] == "dry_run"
    assert run["status"] == "completed"
    assert run["stage"] == "dry_run_candidates_generated"
    assert run["searched_count"] == 2

    candidates_response = configured_client.get(
        f"/api/fine-job/delivery-runs/{run['id']}/candidates"
    )

    assert candidates_response.status_code == 200
    candidates = candidates_response.json()["candidates"]
    assert len(candidates) == 2
    assert {item["keyword"] for item in candidates} == {"AI Agent", "LangGraph"}

    logs_response = configured_client.get(f"/api/fine-job/delivery-runs/{run['id']}/logs")

    assert logs_response.status_code == 200
    logs = logs_response.json()["logs"]
    assert {item["action_type"] for item in logs} == {
        "dry_run_guard",
        "candidates_generated",
        "search_plan",
        "run_created",
    }


def test_create_dry_run_requires_ready_inputs(configured_client) -> None:
    response = configured_client.post("/api/fine-job/delivery-runs", json={"mode": "dry_run"})

    assert response.status_code == 400
    assert "Job intent is required" in response.json()["error_message"]


def test_real_collect_pauses_when_boss_login_is_not_ready(configured_client) -> None:
    _prepare_ready_delivery_inputs(configured_client)

    response = configured_client.post(
        "/api/fine-job/delivery-runs",
        json={"mode": "dry_run", "real_collect": True},
    )

    assert response.status_code == 200
    run = response.json()["run"]
    assert run["status"] == "paused"
    assert run["stage"] == "waiting_for_login"
    assert run["error_count"] == 1
    assert "BOSS" in run["error_message"]


def test_log_filters_cleanup_dashboard_and_legacy_run_delete(configured_client) -> None:
    _prepare_ready_delivery_inputs(configured_client)
    run = configured_client.post(
        "/api/fine-job/delivery-runs",
        json={"mode": "dry_run", "real_collect": False},
    ).json()["run"]

    filtered = configured_client.get(
        "/api/fine-job/delivery-runs/logs/recent",
        params={
            "source": "legacy_run",
            "category": "capture",
            "query": "dry-run",
            "page": 1,
            "page_size": 10,
        },
    )
    assert filtered.status_code == 200
    assert filtered.json()["total"] >= 2
    assert all(item["source"] == "legacy_run" for item in filtered.json()["logs"])
    assert "run_created" in filtered.json()["action_types"]

    issues = configured_client.get(
        "/api/fine-job/delivery-runs/logs/recent",
        params={"level": "issue", "page_size": 10},
    )
    assert issues.status_code == 200
    assert issues.json()["total"] >= 1
    assert all(item["level"] in {"warning", "error"} for item in issues.json()["logs"])

    dashboard = configured_client.get(
        "/api/fine-job/delivery-runs/operations/dashboard"
    )
    assert dashboard.status_code == 200
    assert dashboard.json()["legacy_runs"][0]["id"] == run["id"]
    assert "pending_reviews" in dashboard.json()["metrics"]

    deleted = configured_client.delete(f"/api/fine-job/delivery-runs/{run['id']}")
    assert deleted.status_code == 200
    assert deleted.json()["candidates_deleted"] == 2
    assert deleted.json()["logs_deleted"] == 4

    cleanup = configured_client.post(
        "/api/fine-job/delivery-runs/logs/cleanup",
        json={"before": "2099-01-01T00:00:00Z", "source": "legacy_run"},
    )
    assert cleanup.status_code == 200
    assert cleanup.json()["deleted"] == 0
