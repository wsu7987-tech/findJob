from __future__ import annotations


def _filter_payload(**updates):
    payload = {
        "name": "AI Agent 正职",
        "enabled": True,
        "search_keywords": ["AI Agent"],
        "cities": ["广州"],
        "title_include_any": ["Agent"],
        "title_include_all": [],
        "title_exclude": ["销售"],
        "company_include": [],
        "company_exclude": ["外包公司"],
        "company_scales": ["20-99人", "100-499人"],
        "company_industries": ["人工智能"],
        "company_stages": [],
        "degrees": ["本科"],
        "experiences": ["1-3年", "3-5年"],
        "job_types": ["full_time"],
        "monthly_salary_min": 15,
        "monthly_salary_max_at_least": 25,
        "daily_salary_min": None,
        "skill_include_any": ["Python"],
        "skill_include_all": [],
        "skill_exclude": ["驻场"],
        "boss_active_statuses": ["刚刚活跃"],
        "unknown_value_policy": "review",
        "notes": "应用研发岗位",
    }
    payload.update(updates)
    return payload


def test_filter_strategy_crud(configured_client) -> None:
    created_response = configured_client.post(
        "/api/fine-job/strategies/filters", json=_filter_payload()
    )

    assert created_response.status_code == 201
    created = created_response.json()["strategy"]
    assert created["company_industries"] == ["人工智能"]

    updated_response = configured_client.put(
        f"/api/fine-job/strategies/filters/{created['id']}",
        json=_filter_payload(name="AI Agent 正职新版", monthly_salary_min=20),
    )
    assert updated_response.status_code == 200
    assert updated_response.json()["strategy"]["monthly_salary_min"] == 20

    listed = configured_client.get("/api/fine-job/strategies/filters").json()["strategies"]
    assert [item["name"] for item in listed] == ["AI Agent 正职新版"]

    deleted_response = configured_client.delete(
        f"/api/fine-job/strategies/filters/{created['id']}"
    )
    assert deleted_response.status_code == 204


def test_legacy_job_intent_migrates_to_filter_strategy(configured_client) -> None:
    configured_client.put(
        "/api/fine-job/job-intent",
        json={
            "target_title": "大模型应用开发",
            "cities": ["广州"],
            "keywords": ["AI Agent"],
            "expanded_keywords": ["LangGraph"],
            "excluded_keywords": ["销售"],
            "salary_min": 15,
            "salary_max": 25,
            "work_mode": "any",
            "notes": "旧求职意向",
        },
    )

    response = configured_client.get("/api/fine-job/strategies/filters")

    assert response.status_code == 200
    strategy = response.json()["strategies"][0]
    assert strategy["id"] == "legacy-intent-default"
    assert strategy["search_keywords"] == ["AI Agent"]
    assert strategy["title_exclude"] == ["销售"]


def test_recommendation_strategy_crud(configured_client) -> None:
    filter_strategy = configured_client.post(
        "/api/fine-job/strategies/filters", json=_filter_payload()
    ).json()["strategy"]
    payload = {
        "name": "应用研发投递建议",
        "enabled": True,
        "filter_strategy_id": filter_strategy["id"],
        "resume_id": None,
        "evaluation_method": "hybrid",
        "desired_responsibilities": ["Agent 应用落地"],
        "required_skills": ["Python"],
        "preferred_skills": ["LangGraph"],
        "excluded_terms": ["销售"],
        "preferred_industries": ["人工智能"],
        "work_preferences": "优先产品研发",
        "risk_notes": "排除驻场",
        "minimum_confidence": 0.7,
        "insufficient_info_action": "review",
        "notes": "",
    }

    created = configured_client.post(
        "/api/fine-job/strategies/recommendations", json=payload
    )

    assert created.status_code == 201
    strategy = created.json()["strategy"]
    assert strategy["evaluation_method"] == "hybrid"
    assert strategy["filter_strategy_id"] == filter_strategy["id"]
    assert configured_client.get(
        "/api/fine-job/strategies/recommendations"
    ).json()["strategies"][0]["required_skills"] == ["Python"]
