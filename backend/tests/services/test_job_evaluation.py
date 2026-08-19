from __future__ import annotations

from backend.app.services.fine_job.job_evaluation import (
    evaluate_delivery_jobs,
    evaluate_filter_strategy,
)


def _strategy(**updates):
    value = {
        "id": "filter-1",
        "title_include_any": ["Agent"],
        "title_include_all": [],
        "title_exclude": ["销售"],
        "company_include": [],
        "company_exclude": [],
        "company_scales": ["100-499人"],
        "company_industries": ["人工智能"],
        "company_stages": [],
        "degrees": ["本科"],
        "experiences": ["1-3年"],
        "cities": ["广州"],
        "job_types": ["full_time"],
        "monthly_salary_min": 15,
        "monthly_salary_max_at_least": 30,
        "daily_salary_min": None,
        "skill_include_any": ["Python"],
        "skill_include_all": [],
        "skill_exclude": [],
        "boss_active_statuses": ["刚刚活跃"],
        "unknown_value_policy": "review",
    }
    value.update(updates)
    return value


def test_filter_strategy_returns_review_until_detail_supplies_active_status() -> None:
    job = {
        "job_id": "job-1",
        "title": "AI Agent 开发工程师",
        "boss_name": "示例科技",
        "company_scale": "100-499人",
        "company_industry": "人工智能",
        "salary": "25-45K",
        "location": "广州·黄埔区",
        "experience": "1-3年",
        "degree": "本科",
        "skills": "Python | LangGraph",
        "job_labels": "1-3年 | 本科",
    }

    before = evaluate_filter_strategy([job], _strategy())[0]
    after = evaluate_filter_strategy(
        [{**job, "detail": {"jd": "负责 Agent 应用", "boss_active_status": "刚刚活跃"}}],
        _strategy(),
    )[0]

    assert before["status"] == "review"
    assert before["missing_fields"] == ["招聘者活跃状态"]
    assert after["status"] == "pass"


def test_filter_strategy_distinguishes_daily_salary_from_monthly_salary() -> None:
    result = evaluate_filter_strategy(
        [
            {
                "job_id": "intern-1",
                "title": "AI Agent 实习生",
                "salary": "200-250元/天",
                "job_labels": "4天/周 | 4个月 | 本科",
            }
        ],
        _strategy(
            title_include_any=[],
            company_scales=[],
            company_industries=[],
            degrees=[],
            experiences=[],
            cities=[],
            job_types=["internship"],
            monthly_salary_min=None,
            monthly_salary_max_at_least=None,
            daily_salary_min=180,
            skill_include_any=[],
            boss_active_statuses=[],
        ),
    )[0]

    assert result["status"] == "pass"
    assert "日薪范围符合" in result["reasons"]


def test_rules_delivery_evaluation_works_without_llm() -> None:
    job = {
        "job_id": "job-1",
        "title": "AI Agent 开发工程师",
        "company_industry": "人工智能",
        "skills": "Python | LangGraph",
        "detail": {"jd": "负责 Python、LangGraph 和 Agent 应用落地"},
    }
    recommendation = {
        "evaluation_method": "rules",
        "required_skills": ["Python"],
        "preferred_skills": ["LangGraph"],
        "excluded_terms": ["销售"],
        "preferred_industries": ["人工智能"],
        "minimum_confidence": 0.7,
        "insufficient_info_action": "review",
    }

    result = evaluate_delivery_jobs(
        [job],
        filter_strategy=None,
        recommendation_strategy=recommendation,
        resume_facts=[],
        extra_requirement="",
        config=None,  # type: ignore[arg-type] -- 纯规则路径不会读取配置
    )[0]

    assert result["decision"] == "recommend"
    assert result["source"] == "rules"
