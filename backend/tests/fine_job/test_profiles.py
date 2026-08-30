from __future__ import annotations

import time

import pytest

from backend.app.errors import AppError
from backend.app.services.fine_job import profile_analysis


def _default_profile(client):
    response = client.get("/api/fine-job/profiles")
    assert response.status_code == 200
    return response.json()["profiles"][0]


def _profile_with_resume(client, sqlite_connection):
    profile = _default_profile(client)
    source = client.post(
        f"/api/fine-job/profiles/{profile['id']}/sources/text",
        json={
            "source_type": "text",
            "title": "V3 基础简历",
            "content": "Python 与 FastAPI 开发经验。",
            "enabled": True,
        },
    ).json()["source"]
    family_id = "profile-v3-family"
    now = "2026-08-30T00:00:00Z"
    sqlite_connection.execute(
        """
        INSERT INTO fj_resume_families (
          id, profile_id, name, root_source_id, target_role_family,
          content_version, analysis_version, status, created_at, updated_at
        ) VALUES (?, ?, 'V3 简历组', ?, '后端开发', 1, 0, 'active', ?, ?)
        """,
        (family_id, profile["id"], source["id"], now, now),
    )
    sqlite_connection.execute(
        "UPDATE fj_profile_sources SET resume_family_id = ? WHERE id = ?",
        (family_id, source["id"]),
    )
    sqlite_connection.commit()
    base = client.post(
        f"/api/fine-job/profiles/{profile['id']}/resume-versions",
        json={
            "name": "V3 基础简历",
            "resume_family_id": family_id,
            "version_type": "base",
            "current_role": "base",
            "origin_type": "upload_base",
            "role_family": "后端开发",
            "source_id": source["id"],
            "content": "Python 与 FastAPI 开发经验。",
            "based_on_content_version": 1,
        },
    ).json()["resume_version"]
    return profile, source, family_id, base


def test_v3_context_get_is_read_only_and_task_resolution_handles_stale(
    configured_client,
    sqlite_connection,
):
    profile, _source, _family_id, base = _profile_with_resume(
        configured_client, sqlite_connection
    )
    context_url = (
        f"/api/fine-job/profiles/{profile['id']}/resume-versions/{base['id']}"
        "/contexts/evaluation"
    )
    first = configured_client.get(context_url)
    assert first.status_code == 200
    assert first.json()["context"]["current_revision"] is None
    assert sqlite_connection.execute(
        "SELECT COUNT(*) FROM fj_profile_context_heads"
    ).fetchone()[0] == 0

    resolved = configured_client.post(
        f"{context_url}/resolve-task", json={"stale_action": None}
    )
    assert resolved.status_code == 200
    current_id = resolved.json()["context"]["current_revision"]["id"]

    created_fact = configured_client.post(
        f"/api/fine-job/profiles/{profile['id']}/facts",
        json={
            "scope_type": "resume_family",
            "scope_id": base["resume_family_id"],
            "domain": "skill",
            "entity_type": "candidate",
            "entity_id": "candidate",
            "field_key": "python",
            "value": "熟练",
            "source_type": "manual",
            "status": "confirmed",
            "confidence": 1,
            "confirmed_by": "user",
            "resume_version_ids": [base["id"]],
        },
    )
    assert created_fact.status_code == 201
    stale = configured_client.post(
        f"{context_url}/resolve-task", json={"stale_action": None}
    ).json()
    assert stale["status"] == "confirmation_required"
    assert stale["context"]["current_revision"]["id"] == current_id

    regenerated = configured_client.post(
        f"{context_url}/resolve-task", json={"stale_action": "regenerate"}
    ).json()
    assert regenerated["status"] == "ready"
    assert regenerated["context"]["stale"] is False
    assert regenerated["context"]["current_revision"]["id"] != current_id


def test_v3_resume_delete_can_move_exclusive_fact_to_pending(
    configured_client,
    sqlite_connection,
):
    profile, _source, family_id, base = _profile_with_resume(
        configured_client, sqlite_connection
    )
    derived = configured_client.post(
        f"/api/fine-job/profiles/{profile['id']}/resume-versions",
        json={
            "name": "派生简历",
            "resume_family_id": family_id,
            "parent_version_id": base["id"],
            "version_type": "manual_variant",
            "current_role": "derived",
            "origin_type": "manual_copy",
            "derived_from_version_id": base["id"],
            "content": base["content"],
            "based_on_content_version": 1,
        },
    ).json()["resume_version"]
    fact = configured_client.post(
        f"/api/fine-job/profiles/{profile['id']}/facts",
        json={
            "scope_type": "resume_family",
            "scope_id": family_id,
            "domain": "basic",
            "entity_type": "candidate",
            "entity_id": "candidate",
            "field_key": "city",
            "value": "广州",
            "source_type": "manual",
            "status": "confirmed",
            "confidence": 1,
            "confirmed_by": "user",
            "resume_version_ids": [base["id"]],
        },
    ).json()["fact"]

    impact = configured_client.get(
        f"/api/fine-job/profiles/{profile['id']}/resume-versions/{base['id']}/delete-impact"
    ).json()
    assert impact["exclusive_fact_ids"] == [fact["id"]]
    deleted = configured_client.request(
        "DELETE",
        f"/api/fine-job/profiles/{profile['id']}/resume-versions/{base['id']}",
        json={
            "action": "promote_then_delete",
            "promote_resume_version_id": derived["id"],
            "profile_data_action": "move_to_pending",
        },
    )
    assert deleted.status_code == 200
    result = deleted.json()
    assert result["promoted_resume_version_id"] == derived["id"]
    assert result["pending_issue_ids"]
    assert fact["id"] in result["deleted_fact_ids"]
    remaining = configured_client.get(
        f"/api/fine-job/profiles/{profile['id']}/resume-versions"
    ).json()["resume_versions"]
    assert [item["id"] for item in remaining] == [derived["id"]]
    assert remaining[0]["current_role"] == "base"


def test_v3_issue_answer_is_saved_then_applied_as_confirmed_qa(
    configured_client,
    sqlite_connection,
):
    profile, source, _family_id, base = _profile_with_resume(
        configured_client, sqlite_connection
    )
    now = "2026-08-30T00:00:00Z"
    sqlite_connection.execute(
        """
        INSERT INTO fj_profile_issues_v3 (
          id, profile_id, resume_version_id, source_id, issue_type,
          title, description, payload_json, status, created_at, updated_at
        ) VALUES ('issue-v3-qa', ?, ?, ?, 'missing_qa', ?, ?, ?, 'pending', ?, ?)
        """,
        (
            profile["id"],
            base["id"],
            source["id"],
            "目前所在城市？",
            "简历中未明确说明",
            '{"question_key":"current_city"}',
            now,
            now,
        ),
    )
    sqlite_connection.commit()

    answered = configured_client.post(
        f"/api/fine-job/profiles/{profile['id']}/issues/issue-v3-qa/answers",
        json={"answer_text": "目前在广州"},
    )
    assert answered.status_code == 200
    issue = answered.json()["issue"]
    assert issue["status"] == "awaiting_confirmation"
    assert issue["answers"][0]["answer_text"] == "目前在广州"
    assert issue["change_sets"][0]["changes"]["questions"][0]["data"]["final_answer"] == "目前在广州"

    applied = configured_client.post(
        f"/api/fine-job/profiles/{profile['id']}/issues/issue-v3-qa/apply"
    )
    assert applied.status_code == 200
    assert applied.json()["issue"]["status"] == "resolved"
    questions = configured_client.get(
        f"/api/fine-job/profiles/{profile['id']}/questions"
    ).json()["questions"]
    question = next(item for item in questions if item["question_key"] == "current_city")
    assert question["final_answer"] == "目前在广州"
    assert question["resume_version_ids"] == [base["id"]]
    revisions = configured_client.get(
        f"/api/fine-job/profiles/{profile['id']}/questions/{question['id']}/revisions"
    ).json()["revisions"]
    assert revisions[0]["answer"] == "目前在广州"
    assert revisions[0]["status"] == "current"


def test_v3_ai_derived_resume_is_previewed_before_user_saves(
    configured_client,
    sqlite_connection,
):
    profile, _source, _family_id, base = _profile_with_resume(
        configured_client, sqlite_connection
    )
    preview = configured_client.post(
        f"/api/fine-job/profiles/{profile['id']}/resume-versions/ai-derived-preview",
        json={
            "source_resume_version_id": base["id"],
            "target_job_id": "job-1",
            "target_job_snapshot": {"title": "AI Agent 工程师"},
            "jd_text": "负责 Python、FastAPI 与 Agent 应用开发。",
            "instructions": "突出已有的后端开发经验",
        },
    )
    assert preview.status_code == 200
    assert preview.json()["source_resume_version_id"] == base["id"]
    assert "Python" in preview.json()["content"]
    versions = configured_client.get(
        f"/api/fine-job/profiles/{profile['id']}/resume-versions"
    ).json()["resume_versions"]
    assert [item["id"] for item in versions] == [base["id"]]


def test_profile_source_ai_analysis_and_context(configured_client):
    profile = _default_profile(configured_client)
    source_response = configured_client.post(
        f"/api/fine-job/profiles/{profile['id']}/sources/text",
        json={
            "source_type": "markdown",
            "title": "后端开发简历",
            "content": "# 简历\n\n5 年 Python 与 FastAPI 开发经验。",
            "enabled": True,
        },
    )
    assert source_response.status_code == 201
    source = source_response.json()["source"]

    analysis_response = configured_client.post(
        f"/api/fine-job/profiles/{profile['id']}/analysis-runs",
        json={"source_ids": [source["id"]]},
    )
    assert analysis_response.status_code == 200
    analysis_run = analysis_response.json()["analysis_run"]
    assert analysis_run["status"] == "needs_confirmation"
    assert analysis_run["ai_model"] == "stub-summary-model"

    refreshed_source = configured_client.get(
        f"/api/fine-job/profiles/{profile['id']}/sources"
    ).json()["sources"][0]
    assert refreshed_source["status"] == "review_required"
    assert "FastAPI" in refreshed_source["recognized_text"]

    context_response = configured_client.get(
        f"/api/fine-job/profiles/{profile['id']}/context?view=full"
    )
    assert context_response.status_code == 200
    assert context_response.json()["context"]["view"] == "full"


def test_profile_analysis_can_run_as_background_task(configured_client):
    profile = _default_profile(configured_client)
    source = configured_client.post(
        f"/api/fine-job/profiles/{profile['id']}/sources/text",
        json={"source_type": "text", "title": "补充资料", "content": "熟悉 Python", "enabled": True},
    ).json()["source"]
    started = configured_client.post(
        f"/api/fine-job/profiles/{profile['id']}/analysis-runs/async",
        json={"source_ids": [source["id"]]},
    )
    assert started.status_code == 200
    run = started.json()["analysis_run"]
    deadline = time.monotonic() + 3
    while run["status"] in {"pending", "running"} and time.monotonic() < deadline:
        time.sleep(0.02)
        run = configured_client.get(
            f"/api/fine-job/profiles/analysis-runs/{run['id']}"
        ).json()["analysis_run"]
    assert run["status"] == "needs_confirmation"


def test_profile_analysis_persists_codex_error_category_and_reason(
    configured_client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = _default_profile(configured_client)
    source = configured_client.post(
        f"/api/fine-job/profiles/{profile['id']}/sources/text",
        json={"source_type": "text", "title": "简历", "content": "熟悉 Python", "enabled": True},
    ).json()["source"]

    def fail_generation(*_args, **_kwargs):
        raise AppError(
            status_code=502,
            error_category="CODEX_OUTPUT_SCHEMA_INVALID",
            error_message="Codex rejected the output schema: field value must have a JSON type",
        )

    monkeypatch.setattr(profile_analysis, "_generate_ai_output", fail_generation)
    response = configured_client.post(
        f"/api/fine-job/profiles/{profile['id']}/analysis-runs",
        json={"source_ids": [source["id"]]},
    )

    assert response.status_code == 502
    assert response.json() == {
        "error_category": "CODEX_OUTPUT_SCHEMA_INVALID",
        "error_message": "Codex rejected the output schema: field value must have a JSON type",
    }
    with configured_client.app.state.db.connect() as connection:
        row = connection.execute(
            "SELECT error_category, error_message FROM fj_profile_analysis_runs WHERE profile_id = ? ORDER BY created_at DESC LIMIT 1",
            (profile["id"],),
        ).fetchone()
    assert dict(row) == {
        "error_category": "CODEX_OUTPUT_SCHEMA_INVALID",
        "error_message": "Codex rejected the output schema: field value must have a JSON type",
    }


def test_profile_facts_keep_disclosure_separate_from_confirmation(configured_client):
    profile = _default_profile(configured_client)
    base_payload = {
        "domain": "basic",
        "entity_type": "candidate",
        "entity_id": "candidate",
        "source_type": "manual",
        "sort_order": 0,
        "date_precision": "unknown",
        "is_current": True,
        "confidence": 1,
        "status": "confirmed",
        "sensitivity": "normal",
        "disclosure_policy": {},
    }
    private_response = configured_client.post(
        f"/api/fine-job/profiles/{profile['id']}/facts",
        json={
            **base_payload,
            "field_key": "current_salary",
            "value": "20k",
            "external_use": "prohibited",
        },
    )
    assert private_response.status_code == 201
    public_response = configured_client.post(
        f"/api/fine-job/profiles/{profile['id']}/facts",
        json={
            **base_payload,
            "field_key": "current_city",
            "value": "上海",
            "external_use": "allowed",
        },
    )
    assert public_response.status_code == 201

    full_context = configured_client.get(
        f"/api/fine-job/profiles/{profile['id']}/context?view=full"
    ).json()["context"]["markdown"]
    chat_context = configured_client.get(
        f"/api/fine-job/profiles/{profile['id']}/context?view=chat"
    ).json()["context"]["markdown"]
    assert "current_salary" in full_context
    assert "current_salary" not in chat_context
    assert "上海" in chat_context


def test_profile_fact_update_rejects_stale_version(configured_client):
    profile = _default_profile(configured_client)
    created = configured_client.post(
        f"/api/fine-job/profiles/{profile['id']}/facts",
        json={
            "domain": "skill",
            "entity_type": "skill",
            "entity_id": "python",
            "field_key": "name",
            "value": "Python",
            "source_type": "manual",
            "status": "proposed",
            "external_use": "prohibited",
        },
    ).json()["fact"]
    payload = {
        key: value
        for key, value in created.items()
        if key not in {"id", "profile_id", "created_at", "updated_at"}
    }
    payload["expected_facts_version"] = 1
    response = configured_client.put(
        f"/api/fine-job/profiles/facts/{created['id']}",
        json=payload,
    )
    assert response.status_code == 409
    assert response.json()["error_category"] == "CONTEXT_VERSION_CHANGED"


def test_default_qa_is_template_only_and_complete(configured_client):
    profile = _default_profile(configured_client)
    actual_response = configured_client.get(
        f"/api/fine-job/profiles/{profile['id']}/questions"
    )
    assert actual_response.status_code == 200
    assert actual_response.json()["questions"] == []
    response = configured_client.get(
        f"/api/fine-job/profiles/{profile['id']}/qa-templates"
    )
    assert response.status_code == 200
    templates = response.json()["templates"]
    keys = {template["question_key"] for template in templates}
    assert {
        "current_city",
        "acceptable_cities",
        "target_roles",
        "expected_salary",
        "availability",
        "leaving_reason",
        "current_salary",
        "education_confirmation",
    } <= keys

    target = next(template for template in templates if template["question_key"] == "current_salary")
    delete_response = configured_client.delete(
        f"/api/fine-job/profiles/{profile['id']}/qa-templates/{target['id']}"
    )
    assert delete_response.status_code == 204
    refreshed = configured_client.get(
        f"/api/fine-job/profiles/{profile['id']}/qa-templates"
    ).json()["templates"]
    assert "current_salary" not in {template["question_key"] for template in refreshed}


def test_accepted_analysis_fact_is_applied_with_evidence(configured_client, sqlite_connection):
    profile = _default_profile(configured_client)
    source = configured_client.post(
        f"/api/fine-job/profiles/{profile['id']}/sources/text",
        json={"source_type": "text", "title": "简历", "content": "现居上海", "enabled": True},
    ).json()["source"]
    refreshed_profile = configured_client.get(
        f"/api/fine-job/profiles/{profile['id']}"
    ).json()["profile"]
    run_id = "analysis-run-test"
    item_id = "analysis-item-test"
    now = "2026-08-29T00:00:00Z"
    import json

    sqlite_connection.execute(
        """
        INSERT INTO fj_profile_analysis_runs (
          id, profile_id, source_ids_json, input_versions_json, prompt_version,
          status, created_at, updated_at
        ) VALUES (?, ?, ?, ?, 'test', 'needs_confirmation', ?, ?)
        """,
        (run_id, profile["id"], json.dumps([source["id"]]), json.dumps(refreshed_profile["versions"]), now, now),
    )
    sqlite_connection.execute(
        """
        INSERT INTO fj_profile_analysis_items (
          id, analysis_run_id, item_type, source_refs_json, payload_json,
          status, created_at, updated_at
        ) VALUES (?, ?, 'fact', ?, ?, 'pending', ?, ?)
        """,
        (
            item_id,
            run_id,
            json.dumps([{"source_id": source["id"], "source_excerpt": "现居上海", "confidence": 0.98}]),
            json.dumps(
                {
                    "domain": "basic",
                    "entity_type": "candidate",
                    "entity_id": "candidate",
                    "field_key": "current_city",
                    "value": "上海",
                    "sort_order": 0,
                    "valid_from": None,
                    "valid_to": None,
                    "date_precision": "unknown",
                    "is_current": True,
                    "confidence": 0.98,
                    "sensitivity": "normal",
                    "external_use": "allowed",
                    "evidence": [{"source_id": source["id"], "source_excerpt": "现居上海", "confidence": 0.98}],
                },
                ensure_ascii=False,
            ),
            now,
            now,
        ),
    )
    sqlite_connection.commit()

    accepted = configured_client.post(
        f"/api/fine-job/profiles/analysis-items/{item_id}/accepted",
        json={"expected_status": "pending", "decision_note": None},
    )
    assert accepted.status_code == 200
    applied = configured_client.post(
        f"/api/fine-job/profiles/analysis-runs/{run_id}/apply",
        json={"item_ids": [item_id], "expected_versions": refreshed_profile["versions"]},
    )
    assert applied.status_code == 200
    assert applied.json()["items"][0]["status"] == "applied"
    facts = configured_client.get(
        f"/api/fine-job/profiles/{profile['id']}/facts"
    ).json()["facts"]
    fact = next(item for item in facts if item["field_key"] == "current_city")
    evidence = configured_client.get(
        f"/api/fine-job/profiles/facts/{fact['id']}/evidence"
    ).json()["evidence"]
    assert evidence[0]["source_excerpt"] == "现居上海"
