from __future__ import annotations

import asyncio
from types import SimpleNamespace

from fastapi.testclient import TestClient


def _runtime_client() -> tuple[TestClient, dict[str, str]]:
    from backend.app.main import create_app

    client = TestClient(create_app())
    created = client.post("/api/internal/codex/v1/runtime")
    assert created.status_code == 200
    headers = {
        "Authorization": f"Bearer {created.json()['token']}",
        "X-FineJob-MCP-Contract-Version": "v1",
        "X-FineJob-Internal-API-Version": "v1",
    }
    return client, headers


def test_internal_runtime_handshake_and_capabilities() -> None:
    client, headers = _runtime_client()
    with client:
        handshake = client.post("/api/internal/codex/v1/handshake", headers=headers)
        assert handshake.status_code == 200
        assert handshake.json()["mcp_contract_version"] == "v1"

        capabilities = client.post(
            "/api/internal/codex/v1/tools/get_capabilities",
            headers=headers,
            json={"arguments": {}},
        )
        assert capabilities.status_code == 200
        registered_tools = capabilities.json()["data"]["registered_tools"]
        from backend.app.services.fine_job.codex_tools import CORE_TOOLS

        assert len(registered_tools) == len(CORE_TOOLS)
        assert "finejob.get_job_hunt_refresh_run" in registered_tools
        assert "finejob.list_job_hunt_refresh_items" in registered_tools
        assert "finejob.refresh_job_hunt_chat_list" not in registered_tools
        assert "finejob.refresh_job_hunt_chat_messages" in registered_tools
        assert "finejob.refresh_job_hunt_related_job" in registered_tools
        assert "finejob.complete_job_hunt_refresh_run" in registered_tools
        assert "finejob.set_company_type" in registered_tools
        assert "finejob.record_job_application" in registered_tools
        assert "finejob.list_job_strategies" in registered_tools
        assert "finejob.get_job_evaluation_context" in registered_tools
        assert "finejob.start_job_capture" in registered_tools
        assert "finejob.continue_job_capture" in registered_tools
        assert "finejob.stop_job_capture" in registered_tools
        assert "finejob.apply_job_filter" in registered_tools
        assert "finejob.collect_job_details" in registered_tools
        assert "finejob.get_job_jd" in registered_tools
        assert "finejob.get_profile_analysis_input" in registered_tools
        assert "finejob.get_profile_context" in registered_tools
        assert "finejob.get_resume_analysis_plan" in registered_tools
        assert "finejob.save_resume_operation_result" in registered_tools
        runtime = capabilities.json()["data"]["runtime"]
        assert runtime["boss_executor_required_for_job_capture"] is False
        assert runtime["job_capture_ready"] is runtime["boss_browser_running"]
        assert "审批后的打招呼" in runtime["boss_executor_scope"]

        run_id = handshake.json()["run_id"]
        completed = client.post(
            f"/api/internal/codex/v1/runtime/{run_id}/complete",
            headers=headers,
            json={"status": "exited", "reason": "测试正常退出"},
        )
        assert completed.status_code == 200
        with client.app.state.db.connect() as connection:
            row = connection.execute(
                "SELECT status, exit_reason FROM fj_codex_sessions WHERE id = ?",
                (run_id,),
            ).fetchone()
        assert dict(row) == {"status": "exited", "exit_reason": "测试正常退出"}


def test_internal_api_rejects_missing_token_and_version() -> None:
    client, headers = _runtime_client()
    with client:
        assert client.post("/api/internal/codex/v1/handshake").status_code == 409
        wrong = {**headers, "Authorization": "Bearer invalid-token"}
        assert client.post("/api/internal/codex/v1/handshake", headers=wrong).status_code == 401
        assert client.patch("/api/internal/codex/v1/permissions", headers=headers).status_code == 404


def test_new_runtime_does_not_invalidate_existing_runtime() -> None:
    client, first_headers = _runtime_client()
    with client:
        second = client.post("/api/internal/codex/v1/runtime")
        assert second.status_code == 200
        second_headers = {
            "Authorization": f"Bearer {second.json()['token']}",
            "X-FineJob-MCP-Contract-Version": "v1",
            "X-FineJob-Internal-API-Version": "v1",
        }

        first_handshake = client.post(
            "/api/internal/codex/v1/handshake", headers=first_headers
        )
        second_handshake = client.post(
            "/api/internal/codex/v1/handshake", headers=second_headers
        )
        assert first_handshake.status_code == 200
        assert second_handshake.status_code == 200
        assert first_handshake.json()["run_id"] != second_handshake.json()["run_id"]

        first_run_id = first_handshake.json()["run_id"]
        completed = client.post(
            f"/api/internal/codex/v1/runtime/{first_run_id}/complete",
            headers=first_headers,
            json={"status": "exited", "reason": "第一个运行结束"},
        )
        assert completed.status_code == 200
        assert client.post(
            "/api/internal/codex/v1/handshake", headers=first_headers
        ).status_code == 401
        assert client.post(
            "/api/internal/codex/v1/handshake", headers=second_headers
        ).status_code == 200


def test_page_permissions_are_persisted_by_registered_key(client) -> None:
    current = client.get("/api/fine-job/codex/permissions")
    assert current.status_code == 200
    permissions = {key: True for key in current.json()["permissions"]}
    updated = client.patch(
        "/api/fine-job/codex/permissions",
        json={"enabled": True, "permissions": permissions},
    )
    assert updated.status_code == 200
    assert updated.json()["enabled"] is True
    assert all(updated.json()["permissions"].values())


def test_mcp_server_registers_exact_core_tool_set() -> None:
    from backend.app.mcp.fine_job_server import server
    from backend.app.services.fine_job.codex_tools import CORE_TOOLS

    tools = asyncio.run(server.list_tools())
    assert tuple(tool.name for tool in tools) == CORE_TOOLS


def test_codex_can_record_outsourcing_company_and_blacklist() -> None:
    from backend.app.services.fine_job.boss_capture_history import (
        create_capture_batch,
        get_capture_history_job,
        record_capture_jobs,
    )

    client, headers = _runtime_client()
    with client:
        create_capture_batch(
            client.app.state.db,
            capture_id="capture-mcp-outsourcing",
            keyword="Python",
            city="广州",
            pages=1,
            auto_details=False,
            created_at="2026-08-30T10:00:00Z",
        )
        recorded = record_capture_jobs(
            client.app.state.db,
            capture_id="capture-mcp-outsourcing",
            search_keyword="Python",
            jobs=[{
                "job_id": "mcp-outsourcing-job",
                "title": "Python 开发工程师",
                "boss_name": "示例外包广州交付中心",
                "location": "广州",
            }],
            collected_at="2026-08-30T10:01:00Z",
        )[0]

        classified = client.post(
            "/api/internal/codex/v1/tools/set_company_type",
            headers=headers,
            json={
                "arguments": {
                    "company_name": "示例人力",
                    "company_type": "outsourcing",
                    "notes": "用户确认",
                    "aliases": ["示例外包"],
                }
            },
        )
        assert classified.status_code == 200
        company = classified.json()["data"]["company"]
        assert company["company_type"] == "outsourcing"
        assert company["classification_source"] == "mcp"
        history = get_capture_history_job(
            client.app.state.db,
            recorded["history_record_id"],
        )
        assert history["company_id"] == company["id"]
        assert history["is_outsourcing_company"] is True

        blacklisted = client.post(
            "/api/internal/codex/v1/tools/set_company_blacklist",
            headers=headers,
            json={
                "arguments": {
                    "company_id": company["id"],
                    "blacklisted": True,
                    "reason": "不再展示",
                }
            },
        )
        assert blacklisted.status_code == 200
        assert blacklisted.json()["data"]["company"]["is_blacklisted"] is True


def test_codex_job_context_jd_and_evaluation_reuse_existing_route(client) -> None:
    from backend.app.services.fine_job.boss_capture_history import (
        create_capture_batch,
        record_capture_jobs,
    )

    profile = client.get("/api/fine-job/profiles").json()["profiles"][0]
    resume = client.post(
        f"/api/fine-job/profiles/{profile['id']}/resume-versions",
        json={
            "name": "Codex 岗位评估简历",
            "version_type": "base",
            "current_role": "base",
            "origin_type": "manual_copy",
            "content": "具备 Python 服务开发经验。",
            "based_on_content_version": 1,
        },
    ).json()["resume_version"]
    filter_strategy = client.post(
        "/api/fine-job/strategies/filters",
        json={
            "name": "Codex 筛选策略",
            "candidate_profile_id": profile["id"],
            "resume_version_id": resume["id"],
            "search_keywords": ["Python 后端", "服务端工程师"],
            "cities": ["上海", "杭州"],
            "title_include_any": ["Python"],
        },
    ).json()["strategy"]
    recommendation_strategy = client.post(
        "/api/fine-job/strategies/recommendations",
        json={
            "name": "Codex 建议投递策略",
            "filter_strategy_id": filter_strategy["id"],
            "resume_version_id": resume["id"],
            "evaluation_method": "llm",
            "required_skills": ["Python"],
        },
    ).json()["strategy"]
    assert recommendation_strategy["candidate_profile_id"] == profile["id"]

    # 模拟升级前已经关联具体简历、但冗余档案字段仍为空的现有策略。
    with client.app.state.db.connect() as connection:
        connection.execute(
            "UPDATE fj_job_recommendation_strategies SET candidate_profile_id = NULL WHERE id = ?",
            (recommendation_strategy["id"],),
        )

    create_capture_batch(
        client.app.state.db,
        capture_id="capture-codex-workflow",
        keyword="Python 后端",
        city="上海",
        pages=1,
        auto_details=False,
        created_at="2026-08-30T10:00:00Z",
    )
    recorded = record_capture_jobs(
        client.app.state.db,
        capture_id="capture-codex-workflow",
        search_keyword="Python 后端",
        jobs=[{
            "job_id": "codex-workflow-job",
            "title": "Python 后端开发",
            "boss_name": "示例科技",
            "location": "上海",
            "detail_status": "completed",
            "detail": {"jd": "负责 Python 服务设计与开发。"},
        }],
        collected_at="2026-08-30T10:01:00Z",
    )
    history_job_id = recorded[0]["history_record_id"]

    runtime = client.post("/api/internal/codex/v1/runtime").json()
    headers = {
        "Authorization": f"Bearer {runtime['token']}",
        "X-FineJob-MCP-Contract-Version": "v1",
        "X-FineJob-Internal-API-Version": "v1",
    }

    def invoke(name: str, arguments: dict) -> dict:
        response = client.post(
            f"/api/internal/codex/v1/tools/{name}",
            headers=headers,
            json={"arguments": arguments},
        )
        assert response.status_code == 200
        return response.json()

    strategies_result = invoke("list_job_strategies", {"enabled_only": True})
    listed_recommendation = next(
        item
        for item in strategies_result["data"]["recommendation_strategies"]
        if item["id"] == recommendation_strategy["id"]
    )
    assert listed_recommendation["candidate_profile_id"] == profile["id"]
    client.app.state.db.initialize()
    with client.app.state.db.connect() as connection:
        stored_profile_id = connection.execute(
            "SELECT candidate_profile_id FROM fj_job_recommendation_strategies WHERE id = ?",
            (recommendation_strategy["id"],),
        ).fetchone()["candidate_profile_id"]
    assert stored_profile_id == profile["id"]

    context_result = invoke(
        "get_job_evaluation_context",
        {"recommendation_strategy_id": recommendation_strategy["id"]},
    )
    assert context_result["status"] == "succeeded"
    assert context_result["data"]["default_search_keyword"] == "Python 后端"
    assert context_result["data"]["default_city"] == "上海"
    assert context_result["data"]["resume_version_id"] == resume["id"]
    context_revision_id = context_result["data"]["context"]["id"]

    jd_result = invoke("get_job_jd", {"job_id": history_job_id})
    assert jd_result["data"]["detail"]["jd"] == "负责 Python 服务设计与开发。"
    assert jd_result["data"]["search_keyword"] == "Python 后端"
    assert "context" not in jd_result["data"]

    saved = invoke(
        "save_job_evaluation",
        {
            "job_id": history_job_id,
            "job_detail_version": jd_result["data"]["detail_version"],
            "recommendation_strategy_id": recommendation_strategy["id"],
            "filter_strategy_id": filter_strategy["id"],
            "context_revision_id": context_revision_id,
            "conclusion": "recommend",
            "confidence": 0.88,
            "summary": "岗位与候选人经验匹配，建议进入人工确认。",
            "reasons": ["Python 服务开发经验匹配"],
            "risks": [],
            "missing_fields": [],
            "missing_information": [],
            "hard_requirements": [],
            "match_dimensions": {"skills": 0.9},
            "strengths": ["服务开发经验"],
            "gaps": [],
            "resume_suggestions": [],
            "greeting_draft": {
                "status": "ready",
                "text": "您好，我有 Python 服务开发经验，希望进一步沟通。",
                "facts_used": ["Python 服务开发经验"],
            },
        },
    )
    assert saved["data"]["route"]["review_status"] == "pending"

    history_items = client.get(
        "/api/fine-job/boss-capture/history",
        params={"page": 1, "page_size": 20},
    ).json()["items"]
    history = next(item for item in history_items if item["id"] == history_job_id)
    assert history["delivery_evaluation"]["decision"] == "recommend"
    pending = client.get(
        "/api/fine-job/review-items", params={"status": "pending"}
    ).json()["items"]
    assert pending[0]["job_id"] == history_job_id

    client.post(
        f"/api/fine-job/profiles/{profile['id']}/facts",
        json={
            "domain": "skill",
            "entity_type": "candidate",
            "entity_id": "candidate",
            "field_key": "python",
            "value": "熟练",
            "source_type": "manual",
            "status": "confirmed",
            "confidence": 1,
            "confirmed_by": "user",
            "resume_version_ids": [resume["id"]],
        },
    )
    stale = invoke(
        "get_job_evaluation_context",
        {"recommendation_strategy_id": recommendation_strategy["id"]},
    )
    assert stale["status"] == "awaiting_confirmation"
    continued = invoke(
        "get_job_evaluation_context",
        {
            "recommendation_strategy_id": recommendation_strategy["id"],
            "context_stale_action": "use_current",
        },
    )
    assert continued["status"] == "succeeded"
    assert continued["data"]["context"]["id"] == context_revision_id


def test_codex_capture_filter_and_detail_tools_reuse_existing_task_manager(
    client,
    monkeypatch,
) -> None:
    from backend.app.services.fine_job import codex_tools

    filter_strategy = client.post(
        "/api/fine-job/strategies/filters",
        json={
            "name": "Codex 采集筛选策略",
            "search_keywords": ["Python 后端", "服务端工程师"],
            "cities": ["上海", "杭州"],
            "title_include_any": ["Python"],
        },
    ).json()["strategy"]
    captured: dict[str, object] = {}
    task = {
        "id": "capture-tool-1",
        "status": "completed",
        "keyword": "Python 后端",
        "city": "上海",
        "continuation_available": True,
        "has_more": True,
        "jobs": [{
            "job_id": "job-tool-1",
            "history_record_id": "history-tool-1",
            "title": "Python 后端开发",
            "boss_name": "示例科技",
            "location": "上海",
        }],
    }

    def fake_start_capture(request, *, output_dir, db):
        captured["request"] = request
        captured["db"] = db
        return task

    def fake_apply_filter_results(task_id, results):
        captured["filter_task_id"] = task_id
        captured["filter_results"] = results
        task["jobs"][0]["filter_status"] = results[0]["status"]
        return task

    def fake_continue_capture(task_id, *, pages):
        captured["continue_call"] = (task_id, pages)
        return {**task, "status": "queued", "stage": "list_continue_queued", "pages": pages}

    def fake_stop_capture(task_id):
        captured["stop_call"] = task_id
        return {**task, "status": "running", "stage": "list_continuing", "stop_requested": True}

    def fake_start_details(task_id, job_ids, *, force=False):
        captured["detail_call"] = (task_id, job_ids, force)
        return {**task, "status": "queued", "stage": "details_queued"}

    monkeypatch.setattr(
        codex_tools.boss_scraper_service,
        "get_browser_status",
        lambda: SimpleNamespace(running=True),
    )
    monkeypatch.setattr(
        codex_tools.boss_capture_task_manager,
        "start_capture",
        fake_start_capture,
    )
    monkeypatch.setattr(
        codex_tools.boss_capture_task_manager,
        "get_task",
        lambda task_id: task,
    )
    monkeypatch.setattr(
        codex_tools.boss_capture_task_manager,
        "continue_capture",
        fake_continue_capture,
    )
    monkeypatch.setattr(
        codex_tools.boss_capture_task_manager,
        "stop_capture",
        fake_stop_capture,
    )
    monkeypatch.setattr(
        codex_tools.boss_capture_task_manager,
        "apply_filter_results",
        fake_apply_filter_results,
    )
    monkeypatch.setattr(
        codex_tools.boss_capture_task_manager,
        "start_details",
        fake_start_details,
    )

    runtime = client.post("/api/internal/codex/v1/runtime").json()
    headers = {
        "Authorization": f"Bearer {runtime['token']}",
        "X-FineJob-MCP-Contract-Version": "v1",
        "X-FineJob-Internal-API-Version": "v1",
    }

    def invoke(name: str, arguments: dict) -> dict:
        response = client.post(
            f"/api/internal/codex/v1/tools/{name}",
            headers=headers,
            json={"arguments": arguments},
        )
        assert response.status_code == 200
        return response.json()

    started = invoke(
        "start_job_capture",
        {"filter_strategy_id": filter_strategy["id"], "pages": 2},
    )
    request = captured["request"]
    assert request.keyword == "Python 后端"
    assert request.city == "上海"
    assert request.pages == 2
    assert started["resource"]["id"] == "capture-tool-1"

    continued_capture = invoke(
        "continue_job_capture",
        {"capture_task_id": "capture-tool-1", "pages": 3},
    )
    assert continued_capture["status"] == "queued"
    assert captured["continue_call"] == ("capture-tool-1", 3)

    stopped_capture = invoke(
        "stop_job_capture",
        {"capture_task_id": "capture-tool-1"},
    )
    assert stopped_capture["status"] == "running"
    assert captured["stop_call"] == "capture-tool-1"

    filtered = invoke(
        "apply_job_filter",
        {
            "capture_task_id": "capture-tool-1",
            "filter_strategy_id": filter_strategy["id"],
        },
    )
    assert filtered["data"]["selected_jobs"] == [{
        "job_id": "job-tool-1",
        "history_job_id": "history-tool-1",
        "status": "pass",
    }]

    details = invoke(
        "collect_job_details",
        {"capture_task_id": "capture-tool-1", "job_ids": ["job-tool-1"]},
    )
    assert details["status"] == "queued"
    assert captured["detail_call"] == ("capture-tool-1", ["job-tool-1"], False)

    task["jobs"][0]["title"] = "销售顾问"
    excluded_page = invoke(
        "apply_job_filter",
        {
            "capture_task_id": "capture-tool-1",
            "filter_strategy_id": filter_strategy["id"],
        },
    )
    assert excluded_page["data"]["selected_jobs"] == []
    assert excluded_page["data"]["continuation"] == {
        "available": True,
        "has_more": True,
        "capture_task_id": "capture-tool-1",
        "next_tool": "finejob.continue_job_capture",
    }
    assert "请调用 finejob.continue_job_capture" in excluded_page["message"]


def test_sensitive_content_classification_and_authorization(app_paths) -> None:
    from backend.app.config import load_config
    from backend.app.services.fine_job.codex_authorization import (
        classify_outbound_content,
        resolve_codex_authorization,
    )

    classification = classify_outbound_content(
        "可以线上面试，薪资可以再沟通，请加微信。",
        base_operation="send_chat_reply",
    )
    assert classification.categories == [
        "send_chat_reply",
        "send_contact_info",
        "send_commitment_reply",
        "send_interview_decision",
    ]

    config = load_config()
    config.codex_sensitive_auto_authorization_enabled = True
    config.codex_sensitive_operation_permissions = {
        category: True for category in classification.categories
    }
    authorization = resolve_codex_authorization(config, classification=classification)
    assert authorization["authorization_mode"] == "pre_authorized"
    assert authorization["requires_confirmation"] is False

    greeting = classify_outbound_content(
        "您好，可以加微信进一步沟通吗？",
        base_operation="send_greeting",
    )
    assert greeting.categories == ["send_greeting", "send_contact_info"]
