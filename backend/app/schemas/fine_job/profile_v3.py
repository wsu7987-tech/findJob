from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ResumeLinksUpdate(StrictModel):
    resume_version_ids: list[str] = Field(default_factory=list)
    applies_to_all_resumes: bool = False


class ResumeDeleteImpactResponse(StrictModel):
    resume_version_id: str
    resume_family_id: str | None
    is_base: bool
    source_id: str | None
    derived_versions: list[dict[str, Any]] = Field(default_factory=list)
    exclusive_fact_ids: list[str] = Field(default_factory=list)
    exclusive_question_ids: list[str] = Field(default_factory=list)
    shared_fact_ids: list[str] = Field(default_factory=list)
    shared_question_ids: list[str] = Field(default_factory=list)


class ResumeDeleteRequest(StrictModel):
    action: Literal["delete_version", "promote_then_delete", "delete_family"]
    promote_resume_version_id: str | None = None
    profile_data_action: Literal["delete", "move_to_pending"]


class ResumeDeleteResult(StrictModel):
    deleted_resume_version_ids: list[str]
    deleted_source_ids: list[str]
    deleted_fact_ids: list[str]
    deleted_question_ids: list[str]
    pending_issue_ids: list[str]
    promoted_resume_version_id: str | None = None


class AIDerivedResumePreviewRequest(StrictModel):
    source_resume_version_id: str
    target_job_id: str | None = None
    target_job_snapshot: dict[str, Any] = Field(default_factory=dict)
    jd_text: str = Field(min_length=1, max_length=50000)
    instructions: str = Field(default="", max_length=10000)


class AIDerivedResumePreviewResponse(StrictModel):
    source_resume_version_id: str
    suggested_name: str
    content: str
    derived_reason: str
    target_job_id: str | None = None
    target_job_snapshot: dict[str, Any] = Field(default_factory=dict)


class QATemplatePayload(StrictModel):
    question_key: str = Field(min_length=1, max_length=120)
    question_text: str = Field(min_length=1, max_length=500)
    reason: str = Field(default="", max_length=1000)
    answer_type: Literal["text", "number", "date", "range", "select", "multi_select", "boolean"] = "text"
    required_stage: Literal["search", "greeting", "application", "chat", "interview"] = "chat"
    priority: Literal["high", "medium", "low"] = "medium"
    writes_to_field: str | None = None
    enabled: bool = True
    sort_order: int = 0


class QATemplateResponse(QATemplatePayload):
    id: str
    profile_id: str
    source_type: Literal["system", "user"]
    created_at: str
    updated_at: str


class QATemplateEnvelope(StrictModel):
    template: QATemplateResponse


class QATemplateListEnvelope(StrictModel):
    templates: list[QATemplateResponse]


class QARevisionResponse(StrictModel):
    id: str
    question_id: str
    revision: int
    answer: Any
    source_type: Literal["user", "ai_extraction", "restored", "migration"]
    status: Literal["current", "history"]
    created_at: str


class QARevisionListEnvelope(StrictModel):
    revisions: list[QARevisionResponse]


class QAAnswerPreviewRequest(StrictModel):
    resume_version_id: str
    instructions: str = Field(default="", max_length=5000)


class QAAnswerPreviewResponse(StrictModel):
    question_id: str
    resume_version_id: str
    answer: str


IssueType = Literal[
    "uncertain_fact",
    "fact_conflict",
    "missing_information",
    "missing_qa",
    "qa_conflict",
    "orphaned_profile_data",
    "analysis_choice",
]
IssueStatus = Literal["pending", "organizing", "awaiting_confirmation", "resolved", "dismissed"]


class IssueAnswerResponse(StrictModel):
    id: str
    issue_id: str
    answer_text: str
    created_at: str


class IssueChangeSetResponse(StrictModel):
    id: str
    issue_id: str
    answer_id: str
    changes: dict[str, Any]
    status: Literal["draft", "applied", "discarded"]
    created_at: str
    updated_at: str
    applied_at: str | None


class ProfileIssueResponse(StrictModel):
    id: str
    profile_id: str
    resume_version_id: str | None
    source_id: str | None
    operation_run_id: str | None
    issue_type: IssueType
    title: str
    description: str
    source_excerpt: str
    payload: dict[str, Any]
    status: IssueStatus
    answers: list[IssueAnswerResponse] = Field(default_factory=list)
    change_sets: list[IssueChangeSetResponse] = Field(default_factory=list)
    created_at: str
    updated_at: str
    resolved_at: str | None


class ProfileIssueEnvelope(StrictModel):
    issue: ProfileIssueResponse


class ProfileIssueListEnvelope(StrictModel):
    issues: list[ProfileIssueResponse]


class IssueAnswerCreate(StrictModel):
    answer_text: str = Field(min_length=1, max_length=10000)


class IssueChangeSetUpdate(StrictModel):
    changes: dict[str, Any]


class IssueStatusUpdate(StrictModel):
    status: Literal["dismissed", "pending"]


ContextView = Literal["full", "search", "evaluation", "chat"]


class ContextRevisionResponse(StrictModel):
    id: str
    revision: int
    content: str
    source_type: Literal["generated", "user_edit", "restored", "migration"]
    status: Literal["draft", "current", "history"]
    dependency_versions: dict[str, Any]
    created_at: str
    updated_at: str


class ContextHeadResponse(StrictModel):
    id: str
    profile_id: str
    resume_version_id: str
    view: ContextView
    stale: bool
    dependency_versions: dict[str, Any]
    current_revision: ContextRevisionResponse | None
    draft_revision: ContextRevisionResponse | None
    history: list[ContextRevisionResponse] = Field(default_factory=list)
    created_at: str
    updated_at: str


class ContextHeadEnvelope(StrictModel):
    context: ContextHeadResponse


class ContextSaveRequest(StrictModel):
    content: str = Field(min_length=1)


class ContextDraftUpdate(StrictModel):
    content: str = Field(min_length=1)


class ContextTaskResolutionRequest(StrictModel):
    stale_action: Literal["regenerate", "use_current", "cancel"] | None = None


class ContextTaskResolutionResponse(StrictModel):
    status: Literal["ready", "confirmation_required", "cancelled"]
    context: ContextHeadResponse | None = None


class SearchKeywordPayload(StrictModel):
    keyword: str = Field(min_length=1, max_length=160)
    reason: str = Field(default="", max_length=1000)
    enabled: bool = True
    sort_order: int = 0


class SearchKeywordResponse(SearchKeywordPayload):
    id: str
    filter_strategy_id: str
    source_type: Literal["user", "ai", "migration"]
    created_at: str
    updated_at: str


class SearchKeywordListEnvelope(StrictModel):
    keywords: list[SearchKeywordResponse]


class SearchKeywordEnvelope(StrictModel):
    keyword: SearchKeywordResponse


class SearchKeywordOrderUpdate(StrictModel):
    keyword_ids: list[str]


class StrategyChangeSetApply(StrictModel):
    mode: Literal["update_current", "save_as_new"]
    name: str | None = Field(default=None, max_length=120)


class StrategyChangeSetResponse(StrictModel):
    id: str
    profile_id: str
    resume_version_id: str
    strategy_type: Literal["filter", "recommendation", "search_keywords"]
    target_strategy_id: str | None
    payload: dict[str, Any]
    status: Literal["draft", "applied", "discarded"]
    operation_run_id: str | None
    created_at: str
    updated_at: str
    applied_at: str | None


class StrategyChangeSetEnvelope(StrictModel):
    change_set: StrategyChangeSetResponse


class StrategyChangeSetListEnvelope(StrictModel):
    change_sets: list[StrategyChangeSetResponse]
