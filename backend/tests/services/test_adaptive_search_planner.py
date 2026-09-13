from __future__ import annotations

from backend.app.services.fine_job.adaptive_search_planner import (
    SearchWindowMetrics,
    build_metrics_for_window,
    canonicalize_platform_filters,
    map_monthly_salary_to_boss_buckets,
    plan_next_combination,
    strategy_platform_options,
)


def _strategy(**updates):
    value = {
        "experiences": ["1-3年", "3-5年"],
        "degrees": ["本科", "硕士"],
        "company_scales": [],
        "company_stages": [],
        "company_industries": [],
        "monthly_salary_min": None,
        "monthly_salary_max_at_least": None,
        "daily_salary_min": None,
    }
    value.update(updates)
    return value


def test_window_metrics_keep_historical_cooldown_and_strategy_reject_separate():
    jobs = [
        {"job_id": "fresh-reject", "is_previously_collected": False},
        {"job_id": "historical", "is_previously_collected": True},
        {"job_id": "cooldown", "is_previously_collected": False},
    ]
    results = [
        {
            "job_id": "fresh-reject",
            "status": "reject",
            "strategy_filter_status": "reject",
            "failure_codes": ["experience"],
        },
        {
            "job_id": "historical",
            "status": "pass",
            "strategy_filter_status": "pass",
            "failure_codes": [],
        },
        {
            "job_id": "cooldown",
            "status": "exclude",
            "final_filter_status": "exclude",
            "strategy_filter_status": "pass",
            "cooldown_excluded": True,
            "cooldown_reasons": ["已投递岗位冷却"],
            "failure_codes": [],
        },
    ]

    metrics = build_metrics_for_window(jobs, results)

    assert metrics.jobs_seen == 3
    assert metrics.run_fresh_jobs == 2
    assert metrics.historical_duplicates == 1
    assert metrics.cooldown_excluded == 1
    assert metrics.strategy_pass == 2
    assert metrics.strategy_reject == 1
    assert metrics.qualified_fresh_jobs == 0
    assert metrics.failure_code_counts == {"experience": 1}


def test_duplicate_pool_skew_selects_unexplored_company_scale():
    decision = plan_next_combination(
        current_filters={},
        strategy=_strategy(),
        metrics=SearchWindowMetrics(
            jobs_seen=30,
            run_fresh_jobs=3,
            historical_duplicates=27,
            duplicate_rate=0.9,
            low_novelty_streak=1,
        ),
        attempted_filters=[{}],
        duplicate_distribution={"company_scale": {"303": 68, "301": 32}},
    )

    assert decision.action == "ADD_FILTER"
    assert decision.selected_axis == "company_scale"
    assert decision.switch_reason == "duplicate_pool_skew"
    assert decision.platform_filters["scale"] in {"301", "302", "304", "305", "306"}


def test_filter_quality_replaces_experience_with_another_allowed_value():
    decision = plan_next_combination(
        current_filters={"experience": "104"},
        strategy=_strategy(),
        metrics=SearchWindowMetrics(
            jobs_seen=30,
            run_fresh_jobs=30,
            strategy_reject=15,
            failure_code_counts={"experience": 15, "degree": 6},
        ),
        attempted_filters=[{}, {"experience": "104"}],
    )

    assert decision.action == "REPLACE_FILTER"
    assert decision.selected_axis == "experience"
    assert decision.platform_filters == {"experience": "105"}


def test_high_filter_reject_yield_selects_experience_even_with_some_qualified_fresh():
    decision = plan_next_combination(
        current_filters={},
        strategy=_strategy(),
        metrics=SearchWindowMetrics(
            jobs_seen=30,
            run_fresh_jobs=30,
            strategy_reject=26,
            qualified_fresh_jobs=4,
            qualified_novelty_yield=round(4 / 30, 4),
            failure_code_counts={"experience": 15, "degree": 6, "salary": 3},
        ),
        attempted_filters=[{}],
        force_transition=True,
        low_qualified_yield_threshold=0.15,
    )

    assert decision.action == "ADD_FILTER"
    assert decision.selected_axis == "experience"
    assert decision.platform_filters == {"experience": "104"}


def test_qualified_fresh_keeps_current_combination():
    decision = plan_next_combination(
        current_filters={"degree": "203"},
        strategy=_strategy(),
        metrics=SearchWindowMetrics(jobs_seen=10, run_fresh_jobs=4, qualified_fresh_jobs=2),
        attempted_filters=[{"degree": "203"}],
    )

    assert decision.action == "CONTINUE"
    assert decision.platform_filters == {"degree": "203"}


def test_low_qualified_yield_generates_a_new_combination_when_transition_is_due():
    decision = plan_next_combination(
        current_filters={},
        strategy=_strategy(),
        metrics=SearchWindowMetrics(
            jobs_seen=10,
            run_fresh_jobs=10,
            qualified_fresh_jobs=1,
            novelty_yield=1.0,
            qualified_novelty_yield=0.1,
            low_qualified_yield_streak=3,
        ),
        attempted_filters=[{}],
        force_transition=True,
        low_qualified_yield_threshold=0.15,
    )

    assert decision.action == "ADD_FILTER"
    assert decision.switch_reason == "low_qualified_yield"


def test_salary_bucket_mapping_stays_platform_only_and_skips_daily_salary():
    assert map_monthly_salary_to_boss_buckets({"monthly_salary_min": 30}) == ["20-50K", "50K以上"]
    assert map_monthly_salary_to_boss_buckets({"monthly_salary_max_at_least": 25}) == ["20-50K", "50K以上"]
    assert map_monthly_salary_to_boss_buckets({"daily_salary_min": 200}) == []


def test_strategy_platform_options_uses_only_existing_boss_filter_maps():
    options = strategy_platform_options(
        _strategy(
            company_scales=["100-499人"],
            company_stages=["A轮"],
            company_industries=["互联网"],
            experiences=["1-3年"],
            degrees=["本科"],
            monthly_salary_min=30,
        )
    )

    assert options == {
        "salary": ["406", "407"],
        "experience": ["104"],
        "degree": ["203"],
        "company_scale": ["303"],
        "company_stage": ["803"],
        "company_industry": ["1001"],
    }


def test_combination_identity_filters_are_canonicalized():
    assert canonicalize_platform_filters({"degree": "203, 204,203", "scale": "303"}) == {
        "scale": "303",
        "degree": "203,204",
    }
