from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.app.schemas.fine_job.profile_analysis import AnalysisFactOutput


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


ResumeAnalysisOperationId = Literal[
    "clean_content",
    "extract_facts",
    "extract_qa",
    "generate_filter_strategy",
    "generate_recommendation_strategy",
    "generate_search_keywords",
]


class ResumeFamilyImportRequest(StrictModel):
    file_path: str = Field(min_length=1)
    name: str | None = Field(default=None, max_length=120)
    target_role_family: str = Field(default="", max_length=120)


class DerivedResumeImportRequest(StrictModel):
    file_path: str = Field(min_length=1)
    name: str | None = Field(default=None, max_length=120)
    derived_reason: str = Field(default="", max_length=1000)


class ResumeFamilyResponse(StrictModel):
    id: str
    profile_id: str
    name: str
    root_source_id: str | None
    target_role_family: str
    base_version_id: str | None
    default_version_id: str | None
    default_delivery_version_id: str | None = None
    content_version: int
    analysis_version: int
    status: Literal["active", "stale", "archived"]
    created_at: str
    updated_at: str


class ResumeFamilyEnvelope(StrictModel):
    resume_family: ResumeFamilyResponse


class ResumeFamilyListEnvelope(StrictModel):
    resume_families: list[ResumeFamilyResponse]


class ResumeEditableContentUpdate(StrictModel):
    content: str = Field(min_length=1)
    expected_source_version: int = Field(ge=1)


class ResumeNormalizedMarkdownUpdate(StrictModel):
    content: str = Field(min_length=1)
    expected_content_version: int = Field(ge=1)


class ResumeAnalysisRunCreate(StrictModel):
    resume_version_id: str | None = None
    source_ids: list[str] = Field(default_factory=list)
    operation_ids: list[ResumeAnalysisOperationId] = Field(min_length=1)
    pipeline_mode: Literal["single", "chained"] = "chained"
    execution_path: Literal["structured", "codex_workspace"] = "structured"


class ResumeAnalysisOperationResponse(StrictModel):
    id: str
    run_id: str
    operation_id: ResumeAnalysisOperationId
    sequence_no: int
    status: Literal["queued", "running", "succeeded", "failed", "blocked", "cancelled", "stale"]
    input_versions: dict[str, Any]
    output_summary: dict[str, Any]
    error_category: str | None
    error_message: str | None
    started_at: str | None
    completed_at: str | None
    created_at: str
    updated_at: str


class ResumeAnalysisRunResponse(StrictModel):
    id: str
    profile_id: str
    resume_family_id: str
    resume_version_id: str | None = None
    source_ids: list[str]
    operation_ids: list[ResumeAnalysisOperationId]
    input_versions: dict[str, Any]
    pipeline_mode: Literal["single", "chained"]
    execution_path: Literal["structured", "codex_workspace"]
    ai_model: str | None
    status: Literal["queued", "running", "completed", "partial_failed", "failed", "cancelled"]
    error_category: str | None
    error_message: str | None
    started_at: str | None
    completed_at: str | None
    created_at: str
    updated_at: str
    operations: list[ResumeAnalysisOperationResponse] = Field(default_factory=list)


class ResumeAnalysisRunEnvelope(StrictModel):
    analysis_run: ResumeAnalysisRunResponse


class ResumeAnalysisIssueOutput(StrictModel):
    issue_type: Literal["uncertain_fact", "conflict", "missing_information", "suggested_question"]
    title: str
    description: str = ""
    source_excerpt: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)


class ResumeFactsOperationOutput(StrictModel):
    facts: list[AnalysisFactOutput] = Field(default_factory=list)
    issues: list[ResumeAnalysisIssueOutput] = Field(default_factory=list)


class ResumeQuestionOutput(StrictModel):
    question_key: str
    question_text: str
    reason: str = ""
    answer_type: Literal["text", "number", "date", "range", "select", "multi_select", "boolean"] = "text"
    required_stage: Literal["search", "greeting", "application", "chat", "interview"] = "chat"
    priority: Literal["high", "medium", "low"] = "medium"
    answer: str | int | float | bool | list[str] | None = None
    confidence: float = Field(default=0, ge=0, le=1)
    source_excerpt: str = ""
    external_use: Literal["prohibited", "summary_only", "allowed"] = "prohibited"
    writes_to_field: str | None = None


class ResumeQuestionsOperationOutput(StrictModel):
    questions: list[ResumeQuestionOutput] = Field(default_factory=list)
    issues: list[ResumeAnalysisIssueOutput] = Field(default_factory=list)


class ResumeFilterStrategyOutput(StrictModel):
    name: str
    target_titles: list[str] = Field(default_factory=list)
    cities: list[str] = Field(default_factory=list)
    work_modes: list[str] = Field(default_factory=list)
    required_skills: list[str] = Field(default_factory=list)
    preferred_skills: list[str] = Field(default_factory=list)
    preferred_industries: list[str] = Field(default_factory=list)
    excluded_terms: list[str] = Field(default_factory=list)
    salary_min: int | None = None
    salary_max: int | None = None
    notes: str = ""
    issues: list[ResumeAnalysisIssueOutput] = Field(default_factory=list)


class ResumeRecommendationStrategyOutput(StrictModel):
    name: str
    recommend_when: list[str] = Field(default_factory=list)
    review_when: list[str] = Field(default_factory=list)
    skip_when: list[str] = Field(default_factory=list)
    minimum_match_score: float = Field(default=0.7, ge=0, le=1)
    resume_selection_rule: str = ""
    insufficient_information_action: Literal["review", "skip"] = "review"
    notes: str = ""
    issues: list[ResumeAnalysisIssueOutput] = Field(default_factory=list)


class ResumeSearchKeywordOutput(StrictModel):
    keyword: str
    reason: str = ""


class ResumeSearchKeywordsOperationOutput(StrictModel):
    keywords: list[ResumeSearchKeywordOutput] = Field(default_factory=list)
    issues: list[ResumeAnalysisIssueOutput] = Field(default_factory=list)


class ResumeAnalysisIssueResponse(StrictModel):
    id: str
    profile_id: str
    resume_family_id: str
    source_id: str | None
    operation_run_id: str | None
    issue_type: Literal["uncertain_fact", "conflict", "missing_information", "suggested_question"]
    title: str
    description: str
    source_excerpt: str
    payload: dict[str, Any]
    status: Literal["pending", "resolved", "dismissed"]
    created_at: str
    updated_at: str
    resolved_at: str | None


class ResumeAnalysisIssueListEnvelope(StrictModel):
    issues: list[ResumeAnalysisIssueResponse]


class ResumeAnalysisIssueStatusUpdate(StrictModel):
    status: Literal["resolved", "dismissed"]


class ResumeStrategyResponse(StrictModel):
    id: str
    profile_id: str
    resume_family_id: str
    strategy_type: Literal["filter", "recommendation"]
    name: str
    content: dict[str, Any]
    version: int
    status: Literal["current", "stale", "archived"]
    generated_by: Literal["ai", "user"]
    operation_run_id: str | None
    created_at: str
    updated_at: str


class ResumeStrategyListEnvelope(StrictModel):
    strategies: list[ResumeStrategyResponse]


class ResumeStrategyUpdate(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    content: dict[str, Any]
    expected_version: int = Field(ge=1)


class ResumeSearchKeywordResponse(StrictModel):
    id: str
    profile_id: str
    resume_family_id: str
    keyword: str
    sort_order: int
    reason: str
    enabled: bool
    version: int
    status: Literal["current", "stale", "archived"]
    operation_run_id: str | None
    created_at: str
    updated_at: str


class ResumeSearchKeywordListEnvelope(StrictModel):
    keywords: list[ResumeSearchKeywordResponse]


class ResumeSearchKeywordUpdate(StrictModel):
    keyword: str = Field(min_length=1, max_length=160)
    reason: str = Field(default="", max_length=1000)
    enabled: bool = True


class ResumeSearchKeywordsReplace(StrictModel):
    keywords: list[ResumeSearchKeywordUpdate]
