from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


UnknownValuePolicy = Literal["keep", "review", "exclude"]
JobType = Literal["full_time", "internship", "part_time"]
EvaluationMethod = Literal["rules", "llm", "hybrid"]
InsufficientInfoAction = Literal["review", "reject"]


class FineJobFilterStrategyPayload(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    enabled: bool = True
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
    unknown_value_policy: UnknownValuePolicy = "review"
    notes: str = ""


class FineJobFilterStrategyResponse(FineJobFilterStrategyPayload):
    id: str
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
    created_at: str
    updated_at: str


class FineJobRecommendationStrategyListEnvelope(BaseModel):
    strategies: list[FineJobRecommendationStrategyResponse]


class FineJobRecommendationStrategyEnvelope(BaseModel):
    strategy: FineJobRecommendationStrategyResponse


class FineJobStrategyDeleteResponse(BaseModel):
    deleted: bool
    id: str
