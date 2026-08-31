from __future__ import annotations

from backend.app.services.fine_job.boss_capture_history import (
    create_capture_batch,
    get_capture_history_job,
    record_capture_jobs,
    update_capture_job_detail,
)
from backend.app.services.fine_job.filter_exclusions import (
    apply_filter_exclusions,
    assert_job_action_allowed,
    record_job_event,
)
from backend.app.services.fine_job.strategies import get_filter_strategy
from backend.app.utils import new_id, utc_now


def _record_job(test_db, *, job_id: str = "source-job-1", company: str = "示例科技"):
    capture_id = f"batch-company-{job_id}"
    create_capture_batch(
        test_db,
        capture_id=capture_id,
        keyword="Python",
        city="广州",
        pages=1,
        auto_details=False,
        created_at=utc_now(),
    )
    return record_capture_jobs(
        test_db,
        capture_id=capture_id,
        search_keyword="Python",
        jobs=[
            {
                "job_id": job_id,
                "title": "Python 开发工程师",
                "boss_name": company,
                "location": "广州",
                "salary": "20-30K",
            }
        ],
    )[0]


def test_outsourcing_name_and_alias_use_contains_matching(
    configured_client,
    test_db,
) -> None:
    existing_job = _record_job(
        test_db,
        job_id="outsourcing-existing-job",
        company="北斗人力（广州）有限公司",
    )
    original_company_id = existing_job["company_id"]
    applied = configured_client.put(
        f"/api/fine-job/companies/jobs/{existing_job['history_record_id']}/application",
        json={"applied": True, "note": "识别前已投递"},
    )
    assert applied.status_code == 200

    created = configured_client.post(
        "/api/fine-job/companies",
        json={"name": "北斗人力", "company_type": "outsourcing"},
    )
    assert created.status_code == 201
    outsourcing = created.json()["company"]
    assert outsourcing["id"] != original_company_id

    history = get_capture_history_job(test_db, existing_job["history_record_id"])
    assert history["company_id"] == outsourcing["id"]
    assert history["is_outsourcing_company"] is True
    with test_db.connect() as connection:
        application_company_id = connection.execute(
            "SELECT company_id FROM fj_job_applications WHERE job_id = ?",
            (existing_job["history_record_id"],),
        ).fetchone()["company_id"]
    assert application_company_id == outsourcing["id"]

    aliased = configured_client.post(
        f"/api/fine-job/companies/{outsourcing['id']}/aliases",
        json={"alias_name": "星桥劳务"},
    )
    assert aliased.status_code == 200
    alias_job = _record_job(
        test_db,
        job_id="outsourcing-alias-job",
        company="星桥劳务上海交付中心",
    )
    assert alias_job["company_id"] == outsourcing["id"]
    assert alias_job["is_outsourcing_company"] is True

    shorter = configured_client.post(
        "/api/fine-job/companies",
        json={"name": "北斗", "company_type": "outsourcing"},
    )
    assert shorter.status_code == 201
    refreshed = get_capture_history_job(test_db, existing_job["history_record_id"])
    assert refreshed["company_id"] == outsourcing["id"]

    # 完整名称被人工标记为直招后，优先采用该明确分类。
    direct = configured_client.post(
        "/api/fine-job/companies",
        json={"name": "北斗人力（广州）有限公司", "company_type": "direct"},
    )
    assert direct.status_code == 201
    direct_company = direct.json()["company"]
    direct_history = get_capture_history_job(test_db, existing_job["history_record_id"])
    assert direct_history["company_id"] == direct_company["id"]
    assert direct_history["is_outsourcing_company"] is False

    new_job = _record_job(
        test_db,
        job_id="outsourcing-new-job",
        company="北斗人力（深圳）项目组",
    )
    assert new_job["company_id"] == outsourcing["id"]
    assert new_job["is_outsourcing_company"] is True


def test_exact_blacklisted_company_overrides_outsourcing_contains_match(
    configured_client,
    test_db,
) -> None:
    job = _record_job(
        test_db,
        job_id="blacklist-override-job",
        company="远航人力（广州）有限公司",
    )
    exact_company_id = job["company_id"]
    outsourcing = configured_client.post(
        "/api/fine-job/companies",
        json={"name": "远航人力", "company_type": "outsourcing"},
    ).json()["company"]
    assert get_capture_history_job(test_db, job["history_record_id"])["company_id"] == outsourcing["id"]

    blacklisted = configured_client.put(
        f"/api/fine-job/companies/{exact_company_id}/blacklist",
        json={"blacklisted": True, "reason": "明确排除该完整公司"},
    )
    assert blacklisted.status_code == 200
    blocked_history = get_capture_history_job(test_db, job["history_record_id"])
    assert blocked_history["company_id"] == exact_company_id
    assert blocked_history["is_blacklisted"] is True

    restored = configured_client.put(
        f"/api/fine-job/companies/{exact_company_id}/blacklist",
        json={"blacklisted": False},
    )
    assert restored.status_code == 200
    restored_history = get_capture_history_job(test_db, job["history_record_id"])
    assert restored_history["company_id"] == outsourcing["id"]
    assert restored_history["is_outsourcing_company"] is True


def test_company_management_supports_type_alias_and_blacklist(
    configured_client,
    test_db,
) -> None:
    job = _record_job(test_db)
    listed = configured_client.get("/api/fine-job/companies").json()

    assert listed["total"] == 1
    company = listed["items"][0]
    assert company["canonical_name"] == "示例科技"
    assert job["company_id"] == company["id"]

    updated = configured_client.patch(
        f"/api/fine-job/companies/{company['id']}",
        json={"company_type": "outsourcing", "notes": "人工确认"},
    ).json()["company"]
    assert updated["company_type"] == "outsourcing"

    aliased = configured_client.post(
        f"/api/fine-job/companies/{company['id']}/aliases",
        json={"alias_name": "示例人力"},
    ).json()["company"]
    assert aliased["aliases"][0]["alias_name"] == "示例人力"

    blacklisted = configured_client.put(
        f"/api/fine-job/companies/{company['id']}/blacklist",
        json={"blacklisted": True, "reason": "岗位质量低"},
    ).json()["company"]
    assert blacklisted["is_blacklisted"] is True
    assert blacklisted["blacklist_reason"] == "岗位质量低"


def test_cooldown_rules_require_detail_and_evaluation_before_exclusion(
    configured_client,
    test_db,
) -> None:
    job = _record_job(test_db)
    strategy_payload = {
        "name": "冷却策略",
        "cooldown_rules": {
            "applied_company": {"period": "permanent", "exclude_outsourcing": True},
            "detailed_and_evaluated_company": {"period": "days_3", "exclude_outsourcing": True},
            "applied_job": {"period": "permanent", "exclude_outsourcing": False},
            "detailed_and_evaluated_job": {"period": "days_7", "exclude_outsourcing": False},
        },
    }
    strategy_response = configured_client.post(
        "/api/fine-job/strategies/filters", json=strategy_payload
    )
    assert strategy_response.status_code == 201
    strategy = get_filter_strategy(test_db, strategy_response.json()["strategy"]["id"])

    update_capture_job_detail(
        test_db,
        job=job,
        detail={"jd": "负责 Python 服务开发"},
        status="completed",
    )
    jobs, results = apply_filter_exclusions(
        test_db,
        strategy,
        [job],
        [{"job_id": job["job_id"], "status": "pass", "reasons": [], "missing_fields": [], "strategy_id": strategy["id"]}],
    )
    assert jobs[0]["cooldown_excluded"] is False
    assert results[0]["status"] == "pass"

    with test_db.connect() as connection:
        connection.execute(
            "INSERT INTO fj_job_evaluations (id, job_id, source, decision, confidence, evaluation_json, created_at) VALUES (?, ?, 'rules', 'recommend', 1, '{}', ?)",
            (new_id(), job["history_record_id"], utc_now()),
        )
    record_job_event(test_db, "evaluation", job["history_record_id"])

    jobs, results = apply_filter_exclusions(
        test_db,
        strategy,
        [job],
        [{"job_id": job["job_id"], "status": "review", "reasons": [], "missing_fields": [], "strategy_id": strategy["id"]}],
    )
    assert jobs[0]["cooldown_excluded"] is True
    assert results[0]["status"] == "exclude"
    assert "已获取详情和投递建议岗位冷却" in results[0]["cooldown_reasons"]

    # 自动流程继续遵循冷却规则，用户明确操作时允许查看详情和生成建议。
    assert_job_action_allowed(
        test_db,
        job["history_record_id"],
        strategy=strategy,
        action="detail",
        allow_manual_override=True,
    )
    assert_job_action_allowed(
        test_db,
        job["history_record_id"],
        strategy=strategy,
        action="evaluation",
        allow_manual_override=True,
    )

    application = configured_client.put(
        f"/api/fine-job/companies/jobs/{job['history_record_id']}/application",
        json={"applied": True, "note": "人工投递"},
    )
    assert application.status_code == 200
    assert application.json()["status"] == "applied"

    refreshed = configured_client.get(
        f"/api/fine-job/strategies/filters/{strategy['id']}/exclusions"
    ).json()
    assert refreshed["job_count"] == 1


def test_filter_strategy_cooldown_defaults_are_returned(configured_client) -> None:
    response = configured_client.post(
        "/api/fine-job/strategies/filters", json={"name": "默认冷却"}
    )

    assert response.status_code == 201
    rules = response.json()["strategy"]["cooldown_rules"]
    assert "exclude_outsourcing_companies" not in rules
    assert rules["detailed_and_evaluated_company"]["period"] == "days_3"
    assert rules["detailed_and_evaluated_company"]["exclude_outsourcing"] is True
    assert rules["detailed_and_evaluated_job"]["period"] == "days_7"


def test_combined_company_cooldown_skips_outsourcing_company(
    configured_client,
    test_db,
) -> None:
    job = _record_job(test_db, company="外包服务公司")
    configured_client.patch(
        f"/api/fine-job/companies/{job['company_id']}",
        json={"company_type": "outsourcing"},
    )
    strategy = get_filter_strategy(
        test_db,
        configured_client.post(
            "/api/fine-job/strategies/filters", json={"name": "外包冷却"}
        ).json()["strategy"]["id"],
    )
    update_capture_job_detail(
        test_db,
        job=job,
        detail={"jd": "负责 Python 服务开发"},
        status="completed",
    )
    with test_db.connect() as connection:
        connection.execute(
            "INSERT INTO fj_job_evaluations (id, job_id, source, decision, confidence, evaluation_json, created_at) VALUES (?, ?, 'rules', 'review', 1, '{}', ?)",
            (new_id(), job["history_record_id"], utc_now()),
        )
    record_job_event(test_db, "evaluation", job["history_record_id"])

    state = configured_client.get(
        f"/api/fine-job/strategies/filters/{strategy['id']}/exclusions"
    ).json()
    assert state["company_count"] == 0
    assert state["job_count"] == 1
