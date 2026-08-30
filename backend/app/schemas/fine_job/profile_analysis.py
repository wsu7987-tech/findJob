from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictAnalysisModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


# AI 输出中的事实值限制为常见 JSON 值，确保输出 Schema 能声明明确类型。
AnalysisFactValue = str | int | float | bool | list[str]
AnalysisAnswerValue = str | int | float | bool | list[str] | None


class AnalysisEvidenceOutput(StrictAnalysisModel):
    source_id: str
    source_excerpt: str
    confidence: float = Field(ge=0, le=1)


class AnalysisFactOutput(StrictAnalysisModel):
    domain: str
    entity_type: str
    entity_id: str
    field_key: str
    value: AnalysisFactValue
    sort_order: int = 0
    valid_from: str | None = None
    valid_to: str | None = None
    date_precision: Literal["year", "month", "day", "unknown"] = "unknown"
    is_current: bool = False
    confidence: float = Field(ge=0, le=1)
    sensitivity: Literal["normal", "private", "sensitive"] = "normal"
    external_use: Literal["prohibited", "summary_only", "allowed"] = "prohibited"
    evidence: list[AnalysisEvidenceOutput] = Field(default_factory=list)


class AnalysisQuestionOutput(StrictAnalysisModel):
    question_key: str
    question_text: str
    reason: str
    answer_type: Literal["text", "number", "date", "range", "select", "multi_select", "boolean"] = "text"
    required_stage: Literal["search", "greeting", "application", "chat", "interview"] = "chat"
    priority: Literal["high", "medium", "low"] = "medium"
    proposed_answer: AnalysisAnswerValue = None
    external_use: Literal["prohibited", "summary_only", "allowed"] = "prohibited"
    writes_to_field: str | None = None
    origin: Literal["resume_analysis", "jd_analysis"] = "resume_analysis"
    job_id: str | None = None


class AnalysisAnswerVariantOutput(StrictAnalysisModel):
    question_key: str
    name: str
    scope_type: Literal["general", "role_family", "job"] = "general"
    scope_id: str | None = None
    answer_text: str
    internal_note: str = ""
    usage_condition: str = ""
    external_use: Literal["prohibited", "summary_only", "allowed"] = "prohibited"
    based_on_job_version: int | None = None


class AnalysisStrategyOutput(StrictAnalysisModel):
    strategy_type: Literal["positioning", "search", "screening", "communication"]
    name: str
    content: str


class AnalysisSearchQueryOutput(StrictAnalysisModel):
    name: str
    role_family: str = ""
    platform: str = "boss"
    keyword: str
    cities: list[str] = Field(default_factory=list)
    work_modes: list[str] = Field(default_factory=list)
    positive_terms: list[str] = Field(default_factory=list)
    excluded_terms: list[str] = Field(default_factory=list)
    priority: int = 0
    reason: str = ""


class AnalysisResumeVersionSuggestionOutput(StrictAnalysisModel):
    name: str
    role_family: str = ""
    source_id: str | None = None
    content: str
    fact_entity_ids: list[str] = Field(default_factory=list)
    reason: str = ""


class ProfileAnalysisOutput(StrictAnalysisModel):
    candidate_summary: str
    normalized_markdown: str
    facts: list[AnalysisFactOutput] = Field(default_factory=list)
    questions: list[AnalysisQuestionOutput] = Field(default_factory=list)
    answer_variants: list[AnalysisAnswerVariantOutput] = Field(default_factory=list)
    strategies: list[AnalysisStrategyOutput] = Field(default_factory=list)
    search_queries: list[AnalysisSearchQueryOutput] = Field(default_factory=list)
    resume_version_suggestions: list[AnalysisResumeVersionSuggestionOutput] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ProfileSourceCleanOutput(StrictAnalysisModel):
    normalized_markdown: str = Field(min_length=1)
