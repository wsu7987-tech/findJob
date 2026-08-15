from __future__ import annotations


def test_save_and_read_job_intent(configured_client) -> None:
    empty_response = configured_client.get("/api/fine-job/job-intent")

    assert empty_response.status_code == 200
    assert empty_response.json()["intent"] is None

    save_response = configured_client.put(
        "/api/fine-job/job-intent",
        json={
            "target_title": "大模型应用开发",
            "cities": ["上海", "远程"],
            "keywords": ["大模型应用", "AI Agent"],
            "expanded_keywords": ["Agent 开发"],
            "excluded_keywords": ["销售", "客服"],
            "salary_min": 25,
            "salary_max": 35,
            "work_mode": "hybrid",
            "notes": "不考虑纯外包",
        },
    )

    assert save_response.status_code == 200
    saved = save_response.json()["intent"]
    assert saved["ready"] is True
    assert saved["target_title"] == "大模型应用开发"
    assert saved["cities"] == ["上海", "远程"]

    read_response = configured_client.get("/api/fine-job/job-intent")

    assert read_response.status_code == 200
    assert read_response.json()["intent"]["keywords"] == ["大模型应用", "AI Agent"]


def test_job_intent_ready_requires_title_city_and_keyword(configured_client) -> None:
    response = configured_client.put(
        "/api/fine-job/job-intent",
        json={
            "target_title": "大模型应用开发",
            "cities": ["上海"],
            "keywords": [],
            "expanded_keywords": [],
            "excluded_keywords": [],
            "salary_min": None,
            "salary_max": None,
            "work_mode": "any",
            "notes": "",
        },
    )

    assert response.status_code == 200
    assert response.json()["intent"]["ready"] is False
