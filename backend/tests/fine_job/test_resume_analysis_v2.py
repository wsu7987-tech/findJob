from __future__ import annotations

import time

from backend.app.services.fine_job.codex_tools import CodexToolService
from backend.app.services.fine_job import resume_analysis_v2


def _default_profile(client):
    return client.get("/api/fine-job/profiles").json()["profiles"][0]


def _create_text_resume_family(client, sqlite_connection):
    profile = _default_profile(client)
    source = client.post(
        f"/api/fine-job/profiles/{profile['id']}/sources/text",
        json={
            "source_type": "text",
            "title": "AI 应用开发简历",
            "content": "5 年 Python 开发经验，熟悉 FastAPI 和 RAG。",
            "enabled": True,
        },
    ).json()["source"]
    family_id = "resume-family-v2"
    now = "2026-08-29T00:00:00Z"
    sqlite_connection.execute(
        """
        INSERT INTO fj_resume_families (
          id, profile_id, name, root_source_id, target_role_family,
          content_version, analysis_version, status, created_at, updated_at
        ) VALUES (?, ?, 'AI 应用开发', ?, 'AI 应用开发', 1, 0, 'active', ?, ?)
        """,
        (family_id, profile["id"], source["id"], now, now),
    )
    sqlite_connection.execute(
        "UPDATE fj_profile_sources SET resume_family_id = ? WHERE id = ?",
        (family_id, source["id"]),
    )
    sqlite_connection.commit()
    created_version = client.post(
        f"/api/fine-job/profiles/{profile['id']}/resume-versions",
        json={
            "name": "AI 应用开发简历",
            "resume_family_id": family_id,
            "version_type": "base",
            "current_role": "base",
            "origin_type": "upload_base",
            "role_family": "AI 应用开发",
            "source_id": source["id"],
            "content": "5 年 Python 开发经验，熟悉 FastAPI 和 RAG。",
            "based_on_content_version": 1,
        },
    )
    assert created_version.status_code == 201
    return profile, source, family_id


def _wait_for_run(client, run_id: str):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        run = client.get(
            f"/api/fine-job/profiles/resume-analysis-runs/{run_id}"
        ).json()["analysis_run"]
        if run["status"] not in {"queued", "running"}:
            return run
        time.sleep(0.02)
    raise AssertionError("V2 analysis run did not finish")


def test_resume_analysis_v2_runs_selected_operations_in_dependency_order(
    configured_client,
    sqlite_connection,
):
    profile, source, family_id = _create_text_resume_family(
        configured_client, sqlite_connection
    )
    response = configured_client.post(
        f"/api/fine-job/profiles/{profile['id']}/resume-families/{family_id}/analysis-runs",
        json={
            "source_ids": [source["id"]],
            "operation_ids": [
                "generate_search_keywords",
                "extract_qa",
                "clean_content",
                "generate_filter_strategy",
                "extract_facts",
                "generate_recommendation_strategy",
            ],
            "pipeline_mode": "chained",
            "execution_path": "structured",
        },
    )
    assert response.status_code == 200
    run = _wait_for_run(configured_client, response.json()["analysis_run"]["id"])

    assert run["status"] == "completed"
    assert [item["operation_id"] for item in run["operations"]] == [
        "clean_content",
        "extract_facts",
        "extract_qa",
        "generate_filter_strategy",
        "generate_recommendation_strategy",
        "generate_search_keywords",
    ]
    assert {item["status"] for item in run["operations"]} == {"succeeded"}

    refreshed_source = configured_client.get(
        f"/api/fine-job/profiles/{profile['id']}/sources"
    ).json()["sources"][0]
    assert "FastAPI" in refreshed_source["normalized_markdown"]
    filters = configured_client.get("/api/fine-job/strategies/filters").json()["strategies"]
    recommendations = configured_client.get(
        "/api/fine-job/strategies/recommendations"
    ).json()["strategies"]
    assert len(filters) == 1
    assert len(recommendations) == 1
    assert filters[0]["candidate_profile_id"] == profile["id"]
    assert recommendations[0]["resume_version_id"] == run["resume_version_id"]
    keywords = configured_client.get(
        f"/api/fine-job/strategies/filters/{filters[0]['id']}/search-keywords"
    ).json()["keywords"]
    assert keywords
    for view in ("full", "search", "evaluation", "chat"):
        context = configured_client.get(
            f"/api/fine-job/profiles/{profile['id']}/resume-versions/{run['resume_version_id']}/contexts/{view}"
        ).json()["context"]
        assert context["draft_revision"]
    versions = configured_client.get(
        f"/api/fine-job/profiles/{profile['id']}/resume-versions"
    ).json()["resume_versions"]
    assert versions[0]["resume_family_id"] == family_id
    assert versions[0]["version_type"] == "base"


def test_resume_analysis_v2_allows_single_strategy_operation_without_cleaning(
    configured_client,
    sqlite_connection,
):
    profile, source, family_id = _create_text_resume_family(
        configured_client, sqlite_connection
    )
    response = configured_client.post(
        f"/api/fine-job/profiles/{profile['id']}/resume-families/{family_id}/analysis-runs",
        json={
            "source_ids": [source["id"]],
            "operation_ids": ["generate_filter_strategy"],
            "pipeline_mode": "single",
            "execution_path": "structured",
        },
    )
    run = _wait_for_run(configured_client, response.json()["analysis_run"]["id"])

    assert run["status"] == "completed"
    assert run["pipeline_mode"] == "single"
    assert run["operations"][0]["operation_id"] == "generate_filter_strategy"
    assert run["operations"][0]["status"] == "succeeded"


def test_profile_questions_can_use_same_key_in_general_and_resume_scope(
    configured_client,
    sqlite_connection,
):
    profile, source, family_id = _create_text_resume_family(
        configured_client, sqlite_connection
    )
    response = configured_client.post(
        f"/api/fine-job/profiles/{profile['id']}/questions",
        json={
            "scope_type": "resume_family",
            "scope_id": family_id,
            "question_key": "current_city",
            "question_text": "这份简历对应的当前城市是什么？",
            "origin": "user",
            "status": "pending",
            "external_use": "prohibited",
            "source_id": source["id"],
        },
    )

    assert response.status_code == 201
    question = response.json()["question"]
    assert question["scope_type"] == "resume_family"
    assert question["scope_id"] == family_id


def test_codex_v2_executes_one_operation_and_saves_official_result(
    configured_client,
    sqlite_connection,
):
    profile, source, family_id = _create_text_resume_family(
        configured_client, sqlite_connection
    )
    service = CodexToolService(
        configured_client.app.state.db,
        configured_client.app.state.config,
    )
    plan = service.call(
        "finejob.get_resume_analysis_plan",
        {
            "profile_id": profile["id"],
            "resume_family_id": family_id,
            "source_ids": [source["id"]],
            "operation_ids": ["clean_content"],
        },
    )
    run_id = plan["data"]["analysis_run"]["id"]

    operation_input = service.call(
        "finejob.get_resume_operation_input",
        {"run_id": run_id, "operation_id": "clean_content"},
    )
    assert "normalized_markdown" in operation_input["data"]["output_schema"]["properties"]

    saved = service.call(
        "finejob.save_resume_operation_result",
        {
            "run_id": run_id,
            "operation_id": "clean_content",
            "output": {"normalized_markdown": "# AI 应用开发\n\n5 年 Python 开发经验。"},
        },
    )
    assert saved["data"]["analysis_run"]["status"] == "completed"
    refreshed = configured_client.get(
        f"/api/fine-job/profiles/{profile['id']}/sources"
    ).json()["sources"][0]
    assert refreshed["normalized_markdown"].startswith("# AI 应用开发")


def test_resume_strategy_edit_updates_existing_strategy_version(
    configured_client,
    sqlite_connection,
):
    profile, source, family_id = _create_text_resume_family(
        configured_client, sqlite_connection
    )
    response = configured_client.post(
        f"/api/fine-job/profiles/{profile['id']}/resume-families/{family_id}/analysis-runs",
        json={
            "source_ids": [source["id"]],
            "operation_ids": ["generate_filter_strategy"],
            "pipeline_mode": "single",
            "execution_path": "structured",
        },
    )
    _wait_for_run(configured_client, response.json()["analysis_run"]["id"])
    strategy = configured_client.get(
        "/api/fine-job/strategies/filters"
    ).json()["strategies"][0]

    updated = configured_client.put(
        f"/api/fine-job/strategies/filters/{strategy['id']}",
        json={**strategy, "name": "用户调整后的岗位筛选", "title_include_any": ["AI 应用工程师"]},
    )
    assert updated.status_code == 200
    assert updated.json()["strategy"]["name"] == "用户调整后的岗位筛选"
    assert updated.json()["strategy"]["strategy_version"] == strategy["strategy_version"] + 1


def test_cancelled_codex_run_can_retry_its_unfinished_nodes(
    configured_client,
    sqlite_connection,
):
    profile, source, family_id = _create_text_resume_family(
        configured_client, sqlite_connection
    )
    created = configured_client.post(
        f"/api/fine-job/profiles/{profile['id']}/resume-families/{family_id}/analysis-runs",
        json={
            "source_ids": [source["id"]],
            "operation_ids": ["extract_facts", "extract_qa"],
            "pipeline_mode": "chained",
            "execution_path": "codex_workspace",
        },
    ).json()["analysis_run"]
    configured_client.post(
        f"/api/fine-job/profiles/resume-analysis-runs/{created['id']}/cancel"
    )

    retried = configured_client.post(
        f"/api/fine-job/profiles/resume-analysis-runs/{created['id']}/retry"
    )
    assert retried.status_code == 200
    run = retried.json()["analysis_run"]
    assert run["id"] != created["id"]
    assert run["execution_path"] == "codex_workspace"
    assert run["operation_ids"] == ["extract_facts", "extract_qa"]


def test_uploaded_resume_creates_one_base_and_derived_can_be_promoted(
    configured_client,
    monkeypatch,
    tmp_path,
):
    profile = _default_profile(configured_client)
    pdf_path = tmp_path / "基础简历A.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 test")

    def recognize_source(_db, _config, source):
        return {**source, "recognized_text": "Python 开发经历"}

    monkeypatch.setattr(
        resume_analysis_v2.profile_analysis,
        "_recognize_source",
        recognize_source,
    )
    imported = configured_client.post(
        f"/api/fine-job/profiles/{profile['id']}/resume-families/from-pdf",
        json={"file_path": str(pdf_path), "name": "简历A", "target_role_family": ""},
    )
    assert imported.status_code == 201
    family = imported.json()["resume_family"]
    assert family["base_version_id"]

    versions = configured_client.get(
        f"/api/fine-job/profiles/{profile['id']}/resume-versions"
    ).json()["resume_versions"]
    base = next(item for item in versions if item["id"] == family["base_version_id"])
    assert base["name"] == "简历A"
    assert base["version_type"] == "base"

    derived = configured_client.post(
        f"/api/fine-job/profiles/{profile['id']}/resume-families/{family['id']}/derived-from-pdf",
        json={"file_path": str(pdf_path), "name": "简历A-a", "derived_reason": "针对 JD"},
    )
    assert derived.status_code == 201
    derived_version = derived.json()["resume_version"]
    assert derived_version["version_type"] == "manual_variant"
    assert derived_version["parent_version_id"] == base["id"]

    promoted = configured_client.post(
        f"/api/fine-job/profiles/resume-versions/{derived_version['id']}/set-as-base"
    )
    assert promoted.status_code == 200
    assert promoted.json()["resume_version"]["current_role"] == "base"
    assert promoted.json()["resume_version"]["origin_type"] == "upload_derived"

    refreshed_family = configured_client.get(
        f"/api/fine-job/profiles/resume-families/{family['id']}"
    ).json()["resume_family"]
    assert refreshed_family["base_version_id"] == derived_version["id"]
    refreshed_versions = configured_client.get(
        f"/api/fine-job/profiles/{profile['id']}/resume-versions"
    ).json()["resume_versions"]
    old_base = next(item for item in refreshed_versions if item["id"] == base["id"])
    assert old_base["current_role"] == "derived"
    assert old_base["origin_type"] == "upload_base"
    assert old_base["parent_version_id"] is None

    delete_base = configured_client.delete(
        f"/api/fine-job/profiles/resume-versions/{derived_version['id']}"
    )
    assert delete_base.status_code == 422
