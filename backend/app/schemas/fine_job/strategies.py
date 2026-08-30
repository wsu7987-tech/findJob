from __future__ import annotations

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
    exclude_outsourcing_companies: bool = True
    applied_company: CooldownRule = Field(
        default_factory=lambda: CooldownRule(period="permanent", exclude_outsourcing=True)
    )
    detailed_company: CooldownRule = Field(
        default_factory=lambda: CooldownRule(period="days_3", exclude_outsourcing=True)
    )
    evaluated_company: CooldownRule = Field(
        default_factory=lambda: CooldownRule(period="days_3", exclude_outsourcing=True)
    )
    applied_job: CooldownRule = Field(default_factory=lambda: CooldownRule(period="permanent"))
    detailed_job: CooldownRule = Field(default_factory=lambda: CooldownRule(period="days_3"))
    evaluated_job: CooldownRule = Field(default_factory=lambda: CooldownRule(period="days_7"))


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
