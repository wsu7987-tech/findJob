from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, Field


UnknownValuePolicy = Literal["keep", "review", "exclude"]
JobType = Literal["full_time", "internship", "part_time"]
EvaluationMethod = Literal["rules", "llm", "hybrid"]
InsufficientInfoAction = Literal["review", "reject"]
CooldownPeriod = Literal["disabled", "days_3", "days_7", "days_30", "permanent"]


class CooldownRule(BaseModel):
    period: CooldownPeriod
    exclude_outsourcing: bool = False


class FineJobCooldownRules(BaseModel):
    applied_company: CooldownRule = Field(
        default_factory=lambda: CooldownRule(period="permanent", exclude_outsourcing=True)
    )
    detailed_and_evaluated_company: CooldownRule = Field(
        default_factory=lambda: CooldownRule(period="days_3", exclude_outsourcing=True)
    )
    applied_job: CooldownRule = Field(default_factory=lambda: CooldownRule(period="permanent"))
    detailed_and_evaluated_job: CooldownRule = Field(default_factory=lambda: CooldownRule(period="days_7"))


def normalize_cooldown_rules_payload(value: object) -> dict[str, object]:
    """将旧的详情/建议分离配置转换为新的组合冷却配置。"""
    if isinstance(value, str):
        try:
            value = json.loads(value or "{}")
        except json.JSONDecodeError:
            value = {}
    payload = dict(value) if isinstance(value, dict) else {}

    if "detailed_and_evaluated_company" not in payload:
        # 组合冷却在第二个业务事实完成时开始，沿用旧投递建议公司的期限。
        payload["detailed_and_evaluated_company"] = payload.get(
            "evaluated_company",
            payload.get("detailed_company", {"period": "days_3", "exclude_outsourcing": True}),
        )
    if "detailed_and_evaluated_job" not in payload:
        # 岗位组合冷却沿用旧投递建议岗位的 7 天期限。
        payload["detailed_and_evaluated_job"] = payload.get(
            "evaluated_job",
            payload.get("detailed_job", {"period": "days_7"}),
        )

    for legacy_key in (
        "exclude_outsourcing_companies",
        "detailed_company",
        "evaluated_company",
        "detailed_job",
        "evaluated_job",
    ):
        payload.pop(legacy_key, None)
    return FineJobCooldownRules(**payload).model_dump()


class FineJobFilterStrategyPayload(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    enabled: bool = True
    candidate_profile_id: str | None = None
    resume_version_id: str | None = None
    source_type: Literal["user", "ai", "migration"] = "user"
    based_on_analysis_run_id: str | None = None
    based_on_resume_content_version: int | None = Field(default=None, ge=1)
    based_on_facts_version: int | None = Field(default=None, ge=1)
    based_on_qa_version: int | None = Field(default=None, ge=1)
    search_keywords: list[str] = Field(default_factory=list)
    cities: list[str] = Field(default_factory=list)
    title_include_any: list[str] = Field(default_factory=list)
    title_include_all: list[str] = Field(default_factory=list)
    title_exclude: list[str] = Field(default_factory=list)
    company_include: list[str] = Field(default_factory=list)
    company_exclude: list[str] = Field(default_factory=list)
    company_scales: list[str] = Field(default_factory=list)
    company_industries: list[str] = Field(default_factory=list)
    company_stages: list[str] = Field(default_factory=list)
    degrees: list[str] = Field(default_factory=list)
    experiences: list[str] = Field(default_factory=list)
    job_types: list[JobType] = Field(default_factory=list)
    monthly_salary_min: int | None = Field(default=None, ge=0)
    monthly_salary_max_at_least: int | None = Field(default=None, ge=0)
    daily_salary_min: int | None = Field(default=None, ge=0)
    skill_include_any: list[str] = Field(default_factory=list)
    skill_include_all: list[str] = Field(default_factory=list)
    skill_exclude: list[str] = Field(default_factory=list)
    boss_active_statuses: list[str] = Field(default_factory=list)
    cooldown_rules: FineJobCooldownRules = Field(default_factory=FineJobCooldownRules)
    unknown_value_policy: UnknownValuePolicy = "review"
    notes: str = ""


class FineJobFilterStrategyResponse(FineJobFilterStrategyPayload):
    id: str
    strategy_version: int
    created_at: str
    updated_at: str


class FineJobFilterStrategyListEnvelope(BaseModel):
    strategies: list[FineJobFilterStrategyResponse]


class FineJobFilterStrategyEnvelope(BaseModel):
    strategy: FineJobFilterStrategyResponse


class FineJobRecommendationStrategyPayload(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    enabled: bool = True
    filter_strategy_id: str | None = None
    resume_id: str | None = None
    candidate_profile_id: str | None = None
    resume_version_id: str | None = None
    source_type: Literal["user", "ai", "migration"] = "user"
    based_on_analysis_run_id: str | None = None
    based_on_resume_content_version: int | None = Field(default=None, ge=1)
    based_on_facts_version: int | None = Field(default=None, ge=1)
    based_on_qa_version: int | None = Field(default=None, ge=1)
    evaluation_method: EvaluationMethod = "hybrid"
    desired_responsibilities: list[str] = Field(default_factory=list)
    required_skills: list[str] = Field(default_factory=list)
    preferred_skills: list[str] = Field(default_factory=list)
    excluded_terms: list[str] = Field(default_factory=list)
    preferred_industries: list[str] = Field(default_factory=list)
    work_preferences: str = ""
    risk_notes: str = ""
    minimum_confidence: float = Field(default=0.7, ge=0, le=1)
    insufficient_info_action: InsufficientInfoAction = "review"
    notes: str = ""


class FineJobRecommendationStrategyResponse(FineJobRecommendationStrategyPayload):
    id: str
    strategy_version: int
    created_at: str
    updated_at: str


class FineJobRecommendationStrategyListEnvelope(BaseModel):
    strategies: list[FineJobRecommendationStrategyResponse]


class FineJobRecommendationStrategyEnvelope(BaseModel):
    strategy: FineJobRecommendationStrategyResponse


class FineJobStrategyDeleteResponse(BaseModel):
    deleted: bool
    id: str
