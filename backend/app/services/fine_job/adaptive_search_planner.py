from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any, Mapping

from backend.app.services.fine_job.boss_scraper.boss_cdp_raw import (
    DEGREE_MAP,
    EXPERIENCE_MAP,
    INDUSTRY_MAP,
    SALARY_MAP,
    SCALE_MAP,
    STAGE_MAP,
)


PLATFORM_FILTER_KEYS = ("scale", "stage", "salary", "experience", "degree", "industry")

# 只有这些 FineJob 规则维度可以安全映射到 BOSS 平台筛选。
FAILURE_AXIS_MAP = {
    "salary": ("salary", "salary", "salary"),
    "experience": ("experience", "experiences", "experience"),
    "degree": ("degree", "degrees", "degree"),
    "company_scale": ("scale", "company_scales", "company_scale"),
    "company_stage": ("stage", "company_stages", "company_stage"),
    "company_industry": ("industry", "company_industries", "company_industry"),
}

_MAP_BY_AXIS = {
    "company_scale": SCALE_MAP,
    "company_stage": STAGE_MAP,
    "company_industry": INDUSTRY_MAP,
    "experience": EXPERIENCE_MAP,
    "degree": DEGREE_MAP,
    "salary": SALARY_MAP,
}

_SALARY_BUCKET_RANGES: dict[str, tuple[int, int | None]] = {
    "3K以下": (0, 3),
    "3-5K": (3, 5),
    "5-10K": (5, 10),
    "10-20K": (10, 20),
    "20-50K": (20, 50),
    "50K以上": (50, None),
}


@dataclass(frozen=True)
class SearchWindowMetrics:
    """一个 capture window 的可比较指标。"""

    jobs_seen: int = 0
    run_fresh_jobs: int = 0
    historical_duplicates: int = 0
    cooldown_excluded: int = 0
    strategy_pass: int = 0
    strategy_review: int = 0
    strategy_reject: int = 0
    qualified_fresh_jobs: int = 0
    candidate_jobs: int = 0
    novelty_yield: float = 0.0
    qualified_novelty_yield: float = 0.0
    duplicate_rate: float = 0.0
    low_novelty_streak: int = 0
    low_qualified_yield_streak: int = 0
    failure_code_counts: dict[str, int] | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "jobs_seen": self.jobs_seen,
            "run_fresh_jobs": self.run_fresh_jobs,
            "historical_duplicates": self.historical_duplicates,
            "cooldown_excluded": self.cooldown_excluded,
            "strategy_pass": self.strategy_pass,
            "strategy_review": self.strategy_review,
            "strategy_reject": self.strategy_reject,
            "qualified_fresh_jobs": self.qualified_fresh_jobs,
            "candidate_jobs": self.candidate_jobs,
            "novelty_yield": self.novelty_yield,
            "qualified_novelty_yield": self.qualified_novelty_yield,
            "duplicate_rate": self.duplicate_rate,
            "low_novelty_streak": self.low_novelty_streak,
            "low_qualified_yield_streak": self.low_qualified_yield_streak,
            "failure_code_counts": dict(self.failure_code_counts or {}),
        }


@dataclass(frozen=True)
class PlannerDecision:
    """规则 Planner 的结构化业务决策。"""

    action: str
    platform_filters: dict[str, str]
    switch_reason: str = ""
    selected_axis: str = ""
    evidence: dict[str, object] | None = None
    should_switch_scope: bool = False

    def as_dict(self) -> dict[str, object]:
        return {
            "action": self.action,
            "platform_filters": dict(self.platform_filters),
            "switch_reason": self.switch_reason,
            "selected_axis": self.selected_axis,
            "evidence": dict(self.evidence or {}),
            "should_switch_scope": self.should_switch_scope,
        }


def canonicalize_platform_filters(filters: Mapping[str, object] | None) -> dict[str, str]:
    """按稳定键和值顺序规范化 BOSS 平台筛选，供持久化和去重使用。"""
    normalized: dict[str, str] = {}
    for key in PLATFORM_FILTER_KEYS:
        raw = (filters or {}).get(key)
        if raw is None:
            continue
        values = raw if isinstance(raw, (list, tuple, set)) else str(raw).split(",")
        cleaned = sorted({str(value).strip() for value in values if str(value).strip()})
        if cleaned:
            normalized[key] = ",".join(cleaned)
    return normalized


def combination_identity(
    keyword: str,
    city: str,
    platform_filters: Mapping[str, object] | None,
) -> str:
    """生成同一 Run 内可比较的 Search Combination identity。"""
    return json.dumps(
        {
            "keyword": str(keyword).strip(),
            "city": str(city).strip(),
            "platform_filters": canonicalize_platform_filters(platform_filters),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def map_monthly_salary_to_boss_buckets(strategy: Mapping[str, object]) -> list[str]:
    """把 FineJob 月薪条件转换为可能相交的 BOSS 粗粒度 bucket。"""
    daily_min = strategy.get("daily_salary_min")
    monthly_min = strategy.get("monthly_salary_min")
    monthly_max = strategy.get("monthly_salary_max_at_least")
    if daily_min is not None and monthly_min is None and monthly_max is None:
        return []
    if monthly_min is None and monthly_max is None:
        return [label for label in _SALARY_BUCKET_RANGES if label in SALARY_MAP]

    minimum = int(monthly_min or 0)
    required_high = int(monthly_max or 0)
    labels: list[str] = []
    for label, (_, bucket_high) in _SALARY_BUCKET_RANGES.items():
        if bucket_high is not None and bucket_high < minimum:
            continue
        # 月薪上限至少值表示岗位的最高月薪需要达到该值。
        if bucket_high is not None and bucket_high < required_high:
            continue
        labels.append(label)
    return labels


def strategy_platform_options(strategy: Mapping[str, object]) -> dict[str, list[str]]:
    """提取当前 FineJob Strategy 允许探索的各平台筛选值。"""
    options: dict[str, list[str]] = {}
    for axis, (platform_key, strategy_key, _job_key) in FAILURE_AXIS_MAP.items():
        if axis == "salary":
            labels = map_monthly_salary_to_boss_buckets(strategy)
        else:
            configured = [str(value).strip() for value in strategy.get(strategy_key, []) or []]
            source = configured or list(_MAP_BY_AXIS[axis])
            labels = [label for label in source if label in _MAP_BY_AXIS[axis] and label != "不限"]
        codes = [str(_MAP_BY_AXIS[axis][label]) for label in labels]
        if codes:
            options[axis] = codes
    return options


def build_metrics_for_window(
    jobs: list[dict[str, object]],
    results: list[dict[str, object]],
) -> SearchWindowMetrics:
    """从岗位与评估结果直接计算窗口指标，保持三类排除原因相互独立。"""
    result_by_id = {str(item.get("job_id") or ""): item for item in results}
    jobs_seen = len(jobs)
    historical = 0
    cooldown = 0
    strategy_pass = 0
    strategy_review = 0
    strategy_reject = 0
    qualified = 0
    failure_counts: Counter[str] = Counter()
    for job in jobs:
        job_id = str(job.get("job_id") or "")
        result = result_by_id.get(job_id, {})
        is_historical = bool(
            job.get("is_previously_collected")
            or job.get("processing_state") == "duplicate"
        )
        if is_historical:
            historical += 1
        strategy_status = str(
            result.get("strategy_filter_status") or result.get("status") or "review"
        )
        if strategy_status == "pass":
            strategy_pass += 1
        elif strategy_status == "review":
            strategy_review += 1
        elif strategy_status == "reject":
            strategy_reject += 1
            failure_counts.update(str(code) for code in result.get("failure_codes") or [])
        is_cooldown = bool(result.get("cooldown_excluded") or job.get("cooldown_excluded"))
        if is_cooldown:
            cooldown += 1
        final_status = str(result.get("final_filter_status") or result.get("status") or "")
        if (
            not is_historical
            and not is_cooldown
            and final_status in {"pass", "review"}
        ):
            qualified += 1
    fresh = max(0, jobs_seen - historical)
    novelty_yield = fresh / jobs_seen if jobs_seen else 0.0
    qualified_yield = qualified / fresh if fresh else 0.0
    duplicate_rate = historical / jobs_seen if jobs_seen else 0.0
    return SearchWindowMetrics(
        jobs_seen=jobs_seen,
        run_fresh_jobs=fresh,
        historical_duplicates=historical,
        cooldown_excluded=cooldown,
        strategy_pass=strategy_pass,
        strategy_review=strategy_review,
        strategy_reject=strategy_reject,
        qualified_fresh_jobs=qualified,
        candidate_jobs=qualified,
        novelty_yield=round(novelty_yield, 4),
        qualified_novelty_yield=round(qualified_yield, 4),
        duplicate_rate=round(duplicate_rate, 4),
        failure_code_counts=dict(failure_counts),
    )


def plan_next_combination(
    *,
    current_filters: Mapping[str, object] | None,
    strategy: Mapping[str, object],
    metrics: SearchWindowMetrics,
    attempted_filters: list[Mapping[str, object]] | None = None,
    duplicate_distribution: Mapping[str, Mapping[str, int]] | None = None,
    force_transition: bool = False,
    low_yield_streak_limit: int = 3,
    low_novelty_threshold: float = 0.25,
    low_qualified_yield_threshold: float = 0.15,
    duplicate_skew_threshold: float = 0.6,
    combination_safety_limit: int = 24,
) -> PlannerDecision:
    """根据窗口质量和重复池分布，按需选择下一 Search Combination。"""
    current = canonicalize_platform_filters(current_filters)
    attempted = {
        json.dumps(canonicalize_platform_filters(item), sort_keys=True, separators=(",", ":"))
        for item in attempted_filters or []
    }
    if metrics.qualified_fresh_jobs > 0 and not force_transition:
        return PlannerDecision("CONTINUE", current, "qualified_fresh_available", evidence=metrics.as_dict())
    if len(attempted) >= combination_safety_limit:
        return PlannerDecision(
            "SCOPE_EXHAUSTED",
            current,
            "combination_safety_limit_reached",
            evidence={"metrics": metrics.as_dict(), "attempted_count": len(attempted)},
            should_switch_scope=True,
        )

    options = strategy_platform_options(strategy)

    # Fresh 产出偏低或策略拒绝占比高时，优先收窄造成拒绝最多的可映射维度。
    failure_codes = metrics.failure_code_counts or {}
    filter_quality_triggered = bool(
        metrics.run_fresh_jobs > 0
        and failure_codes
        and (
            metrics.qualified_fresh_jobs == 0
            or metrics.qualified_novelty_yield < low_qualified_yield_threshold
        )
    )
    if filter_quality_triggered:
        for axis in sorted(options, key=lambda item: (-failure_codes.get(item, 0), item)):
            if failure_codes.get(axis, 0) <= 0:
                continue
            candidate = _select_axis_value(axis, current, options, duplicate_distribution, prefer_unexplored=False)
            decision = _make_axis_decision(
                current,
                axis,
                candidate,
                attempted,
                reason="filter_quality_exhaustion",
                evidence={"failure_code_counts": failure_codes, "metrics": metrics.as_dict()},
            )
            if decision is not None:
                return decision

    low_novelty = bool(
        metrics.jobs_seen == 0
        or metrics.novelty_yield < low_novelty_threshold
        or metrics.low_novelty_streak >= low_yield_streak_limit
    )
    low_qualified_yield = bool(
        metrics.run_fresh_jobs > 0
        and metrics.qualified_novelty_yield < low_qualified_yield_threshold
    )
    duplicate_triggered = bool(
        metrics.low_novelty_streak >= low_yield_streak_limit
        or metrics.duplicate_rate >= duplicate_skew_threshold
        or metrics.run_fresh_jobs == 0
        or low_novelty
        or low_qualified_yield
        or metrics.low_qualified_yield_streak >= low_yield_streak_limit
    )
    if duplicate_triggered:
        distribution = duplicate_distribution or {}
        axes = sorted(
            options,
            key=lambda axis: (
                -_distribution_share(distribution.get(axis, {})),
                axis,
            ),
        )
        for axis in axes:
            share = _distribution_share(distribution.get(axis, {}))
            if share < duplicate_skew_threshold and metrics.run_fresh_jobs > 0:
                continue
            candidate = _select_axis_value(axis, current, options, distribution, prefer_unexplored=True)
            decision = _make_axis_decision(
                current,
                axis,
                candidate,
                attempted,
                reason="duplicate_pool_skew",
                evidence={
                    "duplicate_rate": metrics.duplicate_rate,
                    "distribution_share": round(share, 4),
                    "metrics": metrics.as_dict(),
                },
            )
            if decision is not None:
                return decision

    # 结果极少且当前已有平台条件时先移除一个条件，给组合恢复探索空间。
    if force_transition and not metrics.jobs_seen and current:
        for axis in reversed(PLATFORM_FILTER_KEYS):
            if axis not in current:
                continue
            next_filters = dict(current)
            next_filters.pop(axis)
            if _filter_key(next_filters) not in attempted:
                return PlannerDecision(
                    "REMOVE_FILTER",
                    next_filters,
                    "combination_too_narrow",
                    axis,
                    {"metrics": metrics.as_dict()},
                )

    # 在没有明显偏斜证据时，按可探索值逐步扩展当前 Scope；每次只创建一个组合。
    if len(attempted) < combination_safety_limit:
        for axis in options:
            candidate = _select_axis_value(axis, current, options, duplicate_distribution, prefer_unexplored=True)
            decision = _make_axis_decision(
                current,
                axis,
                candidate,
                attempted,
                reason=(
                    "low_novelty_exhausted"
                    if low_novelty
                    else "low_qualified_yield"
                    if low_qualified_yield
                    else "filter_quality_exhaustion"
                ),
                evidence={"metrics": metrics.as_dict()},
            )
            if decision is not None:
                return decision

    return PlannerDecision(
        "SCOPE_EXHAUSTED",
        current,
        "approved_platform_search_space_exhausted",
        evidence={"metrics": metrics.as_dict(), "attempted_count": len(attempted)},
        should_switch_scope=True,
    )


def describe_platform_filters(filters: Mapping[str, object] | None) -> list[str]:
    """为驾驶舱提供稳定的人类可读平台筛选名称。"""
    labels: list[str] = []
    for axis, (platform_key, _strategy_key, _job_key) in FAILURE_AXIS_MAP.items():
        code_map = _MAP_BY_AXIS[axis]
        reverse = {str(code): label for label, code in code_map.items()}
        raw = canonicalize_platform_filters(filters).get(platform_key, "")
        if raw:
            values = [reverse.get(value, value) for value in raw.split(",")]
            labels.append("、".join(values))
    return labels


def platform_filter_code(axis: str, label: str) -> str | None:
    """按 Planner 轴把 FineJob/BOSS 展示值转换为现有平台 code。"""
    code_map = _MAP_BY_AXIS.get(axis)
    if code_map is None:
        return None
    value = code_map.get(str(label).strip())
    if value is None and axis == "salary":
        match = re.search(r"(\d+)\s*-\s*(\d+)\s*[Kk]", str(label))
        if match:
            low = int(match.group(1))
            if low < 3:
                value = SALARY_MAP["3K以下"]
            elif low < 5:
                value = SALARY_MAP["3-5K"]
            elif low < 10:
                value = SALARY_MAP["5-10K"]
            elif low < 20:
                value = SALARY_MAP["10-20K"]
            elif low < 50:
                value = SALARY_MAP["20-50K"]
            else:
                value = SALARY_MAP["50K以上"]
    return str(value) if value else None


def _make_axis_decision(
    current: dict[str, str],
    axis: str,
    candidate: str | None,
    attempted: set[str],
    *,
    reason: str,
    evidence: dict[str, object],
) -> PlannerDecision | None:
    if not candidate:
        return None
    platform_key = FAILURE_AXIS_MAP[axis][0]
    next_filters = dict(current)
    action = "ADD_FILTER"
    if platform_key in current:
        action = "REPLACE_FILTER"
    next_filters[platform_key] = candidate
    if _filter_key(next_filters) in attempted:
        return None
    return PlannerDecision(action, next_filters, reason, axis, evidence)


def _select_axis_value(
    axis: str,
    current: Mapping[str, str],
    options: Mapping[str, list[str]],
    distribution: Mapping[str, Mapping[str, int]] | None,
    *,
    prefer_unexplored: bool,
) -> str | None:
    platform_key = FAILURE_AXIS_MAP[axis][0]
    current_values = set(str(current.get(platform_key, "")).split(",")) if current.get(platform_key) else set()
    candidates = [value for value in options.get(axis, []) if value not in current_values]
    if not candidates:
        return None
    counts = distribution.get(axis, {}) if distribution else {}
    # 低覆盖或未探索值优先，用于改变结果池的分布。
    if prefer_unexplored:
        return min(candidates, key=lambda value: (counts.get(value, 0), value))
    return candidates[0]


def _distribution_share(values: Mapping[str, int]) -> float:
    total = sum(int(value) for value in values.values())
    return max((int(value) for value in values.values()), default=0) / total if total else 0.0


def _filter_key(filters: Mapping[str, object]) -> str:
    return json.dumps(canonicalize_platform_filters(filters), sort_keys=True, separators=(",", ":"))
