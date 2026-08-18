from __future__ import annotations

from backend.app.services.fine_job.boss_detail_suggestions import suggest_by_strategy


def test_strategy_suggestions_use_intent_and_exclusions() -> None:
    jobs = [
        {
            "job_id": "job-1",
            "title": "Python 后端开发",
            "boss_name": "产品公司",
            "salary": "25-35K",
            "boss_active_status": "刚刚活跃",
        },
        {
            "job_id": "job-2",
            "title": "Python 销售顾问",
            "boss_name": "测试公司",
            "salary": "20-30K",
        },
    ]

    suggestions = suggest_by_strategy(
        jobs,
        intent={
            "target_title": "后端开发",
            "keywords": ["Python"],
            "expanded_keywords": [],
            "excluded_keywords": ["销售"],
            "salary_min": 20,
        },
    )

    assert set(suggestions) == {"job-1"}
    assert "Python" in suggestions["job-1"]
    assert "25K" in suggestions["job-1"]
