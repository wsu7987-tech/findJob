from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


ProfileStatus = Literal["draft", "ready", "stale", "archived"]
SourceType = Literal["pdf", "markdown", "text", "project"]
SourceStatus = Literal[
    "uploaded",
    "recognizing",
    "analyzing",
    "ready",
    "review_required",
    "failed",
    "archived",
]
ExternalUse = Literal["prohibited", "summary_only", "allowed"]
FactStatus = Literal["proposed", "confirmed", "rejected", "conflicted", "stale"]
QuestionStatus = Literal[
    "pending",
    "proposed_answer",
    "answered",
    "confirmed",
    "declined",
    "conflicted",
    "stale",
]
AnalysisRunStatus = Literal[
    "pending",
    "running",
    "needs_confirmation",
    "applied",
    "failed",
    "cancelled",
    "stale",
]
AnalysisItemStatus = Literal[
    "pending",
    "accepted",
    "edited_and_accepted",
    "rejected",
    "deferred",
    "apply_failed",
    "applied",
]


class ProfileVersionVector(StrictModel):
    sources_version: int = Field(ge=1)
    facts_version: int = Field(ge=1)
    questions_version: int = Field(ge=1)
    answers_version: int = Field(ge=1)
    strategy_version: int = Field(ge=1)
    context_version: int = Field(ge=1)


class CandidateProfileCreate(StrictModel):
    display_name: str = Field(default="默认候选人", min_length=1, max_length=80)


class CandidateProfileUpdate(StrictModel):
    display_name: str = Field(min_length=1, max_length=80)
    status: ProfileStatus
    expected_versions: ProfileVersionVector | None = None


class CandidateProfileResponse(StrictModel):
    id: str
    display_name: str
    status: ProfileStatus
    versions: ProfileVersionVector
    created_at: str
    updated_at: str


class CandidateProfileEnvelope(StrictModel):
    profile: CandidateProfileResponse


class CandidateProfileListEnvelope(StrictModel):
    profiles: list[CandidateProfileResponse]


class ProfileSourceCreateText(StrictModel):
    source_type: Literal["markdown", "text", "project"]
    title: str = Field(min_length=1, max_length=160)
    content: str = Field(min_length=1)
    enabled: bool = True


class ProfileSourceCreateFile(StrictModel):
    file_path: str = Field(min_length=1)
    title: str | None = Field(default=None, max_length=160)
    enabled: bool = True


class ProfileSourceUpdate(StrictModel):
    title: str = Field(min_length=1, max_length=160)
    raw_text: str = ""
    enabled: bool = True
    expected_source_version: int = Field(ge=1)


class ProfileSourceResponse(StrictModel):
    id: str
    profile_id: str
    resume_family_id: str | None
    resume_version_id: str | None = None
    source_type: SourceType
    title: str
    file_path: str | None
    raw_text: str
    recognized_text: str
    editable_text: str
    normalized_markdown: str
    recognizer_name: str | None
    status: SourceStatus
    active_analysis_run_id: str | None
    enabled: bool
    source_version: int
    created_at: str
    updated_at: str


class ProfileSourceEnvelope(StrictModel):
    source: ProfileSourceResponse


class ProfileSourceListEnvelope(StrictModel):
    sources: list[ProfileSourceResponse]


class ResumeVersionPayload(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    resume_family_id: str | None = None
    parent_version_id: str | None = None
    role_family: str = Field(default="", max_length=120)
    version_type: Literal["base", "jd_tailored", "manual_variant", "language_variant"] = "base"
    target_job_id: str | None = None
    derived_reason: str = Field(default="", max_length=1000)
    based_on_content_version: int = Field(default=1, ge=1)
    campaign_id: str | None = None
    source_id: str | None = None
    content: str = ""
    fact_ids: list[str] = Field(default_factory=list)
    is_default: bool = False
    current_role: Literal["base", "derived"] | None = None
    origin_type: Literal["upload_base", "upload_derived", "ai_derived", "manual_copy"] | None = None
    derived_from_version_id: str | None = None
    target_job_snapshot: dict[str, Any] = Field(default_factory=dict)


class ResumeVersionUpdate(ResumeVersionPayload):
    expected_content_version: int = Field(ge=1)


class ResumeVersionResponse(ResumeVersionPayload):
    id: str
    profile_id: str
    status: Literal["draft", "confirmed", "stale", "archived"]
    content_version: int
    confirmed_at: str | None
    created_at: str
    updated_at: str
    deleted_at: str | None = None


class ResumeVersionEnvelope(StrictModel):
    resume_version: ResumeVersionResponse


class ResumeVersionListEnvelope(StrictModel):
    resume_versions: list[ResumeVersionResponse]


class ProfileFactPayload(StrictModel):
    scope_type: Literal["general", "resume_family"] = "general"
    scope_id: str | None = None
    domain: str = Field(min_length=1, max_length=80)
    entity_type: str = Field(min_length=1, max_length=80)
    entity_id: str = Field(min_length=1, max_length=120)
    field_key: str = Field(min_length=1, max_length=120)
    value: Any
    source_type: Literal["document", "user_answer", "manual", "ai_inference"]
    sort_order: int = 0
    valid_from: str | None = None
    valid_to: str | None = None
    date_precision: Literal["year", "month", "day", "unknown"] = "unknown"
    is_current: bool = False
    confidence: float = Field(default=1, ge=0, le=1)
    status: FactStatus = "proposed"
    conflict_group_id: str | None = None
    sensitivity: Literal["normal", "private", "sensitive"] = "normal"
    external_use: ExternalUse = "prohibited"
    disclosure_policy: dict[str, Any] = Field(default_factory=dict)
    valid_until: str | None = None
    confirmed_by: Literal["ai_extraction", "user"] | None = None
    analysis_operation_run_id: str | None = None
    source_content_version: int | None = Field(default=None, ge=1)
    applies_to_all_resumes: bool = False
    resume_version_ids: list[str] = Field(default_factory=list)


class ProfileFactUpdate(ProfileFactPayload):
    expected_facts_version: int = Field(ge=1)


class ProfileFactResponse(ProfileFactPayload):
    id: str
    profile_id: str
    created_at: str
    updated_at: str


class ProfileFactEnvelope(StrictModel):
    fact: ProfileFactResponse


class ProfileFactListEnvelope(StrictModel):
    facts: list[ProfileFactResponse]
    facts_version: int


class FactEvidencePayload(StrictModel):
    source_type: Literal["document", "question_answer", "manual"]
    source_id: str | None = None
    source_excerpt: str = ""
    extraction_method: str = Field(default="ai", max_length=80)
    confidence: float = Field(default=1, ge=0, le=1)


class FactEvidenceResponse(FactEvidencePayload):
    id: str
    fact_id: str
    created_at: str


class FactEvidenceEnvelope(StrictModel):
    evidence: FactEvidenceResponse


class FactEvidenceListEnvelope(StrictModel):
    evidence: list[FactEvidenceResponse]


class ProfileQuestionPayload(StrictModel):
    scope_type: Literal["general", "resume_family"] = "general"
    scope_id: str | None = None
    question_key: str = Field(min_length=1, max_length=120)
    question_text: str = Field(min_length=1, max_length=500)
    reason: str = Field(default="", max_length=1000)
    origin: Literal["default", "resume_analysis", "jd_analysis", "user"] = "user"
    answer_type: Literal["text", "number", "date", "range", "select", "multi_select", "boolean"] = "text"
    required_stage: Literal["search", "greeting", "application", "chat", "interview"] = "chat"
    priority: Literal["high", "medium", "low"] = "medium"
    proposed_answer: Any | None = None
    final_answer: Any | None = None
    status: QuestionStatus = "pending"
    external_use: ExternalUse = "prohibited"
    valid_until: str | None = None
    source_id: str | None = None
    job_id: str | None = None
    writes_to_field: str | None = None
    enabled: bool = True
    confirmed_by: Literal["ai_extraction", "user"] | None = None
    analysis_operation_run_id: str | None = None
    source_content_version: int | None = Field(default=None, ge=1)
    applies_to_all_resumes: bool = False
    resume_version_ids: list[str] = Field(default_factory=list)


class ProfileQuestionUpdate(ProfileQuestionPayload):
    expected_questions_version: int = Field(ge=1)


class ProfileQuestionResponse(ProfileQuestionPayload):
    id: str
    profile_id: str
    created_at: str
    updated_at: str


class ProfileQuestionEnvelope(StrictModel):
    question: ProfileQuestionResponse


class ProfileQuestionListEnvelope(StrictModel):
    questions: list[ProfileQuestionResponse]
    questions_version: int


class AnswerVariantPayload(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    scope_type: Literal["general", "role_family", "job"] = "general"
    scope_id: str | None = None
    answer_text: str = Field(min_length=1, max_length=5000)
    internal_note: str = Field(default="", max_length=5000)
    usage_condition: str = Field(default="", max_length=1000)
    generated_by: Literal["system", "ai", "user"] = "user"
    based_on_job_version: int | None = Field(default=None, ge=1)
    external_use: ExternalUse = "prohibited"
    disclosure_policy: dict[str, Any] = Field(default_factory=dict)


class AnswerVariantUpdate(AnswerVariantPayload):
    expected_answers_version: int = Field(ge=1)


class AnswerVariantResponse(AnswerVariantPayload):
    id: str
    question_id: str
    status: Literal["draft", "confirmed", "rejected", "stale"]
    created_at: str
    updated_at: str


class AnswerVariantEnvelope(StrictModel):
    answer_variant: AnswerVariantResponse


class AnswerVariantListEnvelope(StrictModel):
    answer_variants: list[AnswerVariantResponse]


class ProfileAnalysisRunCreate(StrictModel):
    source_ids: list[str] = Field(min_length=1)


class JobAnswerAnalysisCreate(StrictModel):
    question_keys: list[str] = Field(default_factory=list)


class ProfileAnalysisRunResponse(StrictModel):
    id: str
    profile_id: str
    source_ids: list[str]
    input_versions: ProfileVersionVector
    ai_model: str | None
    prompt_version: str
    status: AnalysisRunStatus
    quality: dict[str, Any]
    error_category: str | None
    error_message: str | None
    started_at: str | None
    completed_at: str | None
    created_at: str
    updated_at: str


class ProfileAnalysisRunEnvelope(StrictModel):
    analysis_run: ProfileAnalysisRunResponse


class AnalysisItemResponse(StrictModel):
    id: str
    analysis_run_id: str
    item_type: Literal["fact", "question", "answer_variant", "strategy", "search_query", "resume_version_suggestion"]
    source_refs: list[dict[str, Any]]
    payload: dict[str, Any]
    status: AnalysisItemStatus
    result_resource_type: str | None
    result_resource_id: str | None
    decision_note: str | None
    decided_at: str | None
    created_at: str
    updated_at: str


class AnalysisItemListEnvelope(StrictModel):
    items: list[AnalysisItemResponse]


class AnalysisItemUpdate(StrictModel):
    payload: dict[str, Any]
    expected_status: AnalysisItemStatus


class AnalysisItemDecision(StrictModel):
    expected_status: AnalysisItemStatus
    decision_note: str | None = Field(default=None, max_length=1000)


class AnalysisItemsApplyRequest(StrictModel):
    item_ids: list[str] = Field(min_length=1)
    expected_versions: ProfileVersionVector


class SearchQueryPayload(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    role_family: str = Field(default="", max_length=120)
    platform: str = Field(default="boss", max_length=40)
    keyword: str = Field(min_length=1, max_length=160)
    cities: list[str] = Field(default_factory=list)
    work_modes: list[str] = Field(default_factory=list)
    positive_terms: list[str] = Field(default_factory=list)
    excluded_terms: list[str] = Field(default_factory=list)
    priority: int = 0
    reason: str = Field(default="", max_length=1000)
    enabled: bool = True


class SearchQueryResponse(SearchQueryPayload):
    id: str
    campaign_id: str
    created_at: str
    updated_at: str


class SearchCampaignPayload(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    target_titles: list[str] = Field(default_factory=list)
    role_families: list[str] = Field(default_factory=list)
    cities: list[str] = Field(default_factory=list)
    districts: list[str] = Field(default_factory=list)
    work_modes: list[str] = Field(default_factory=list)
    salary: dict[str, Any] = Field(default_factory=dict)
    industries: list[str] = Field(default_factory=list)
    company_scales: list[str] = Field(default_factory=list)
    resume_version_id: str | None = None
    filter_strategy_id: str | None = None
    recommendation_strategy_id: str | None = None
    delivery_strategy_id: str | None = None
    excluded_terms: list[str] = Field(default_factory=list)


class SearchCampaignUpdate(SearchCampaignPayload):
    expected_campaign_version: int = Field(ge=1)


class SearchCampaignResponse(SearchCampaignPayload):
    id: str
    profile_id: str
    status: Literal["active", "paused", "archived"]
    campaign_version: int
    confirmed_at: str | None
    queries: list[SearchQueryResponse] = Field(default_factory=list)
    created_at: str
    updated_at: str


class SearchCampaignEnvelope(StrictModel):
    campaign: SearchCampaignResponse


class SearchCampaignListEnvelope(StrictModel):
    campaigns: list[SearchCampaignResponse]


class SearchQueriesReplaceRequest(StrictModel):
    queries: list[SearchQueryPayload]
    expected_campaign_version: int = Field(ge=1)


class ProfileContextResponse(StrictModel):
    profile_id: str
    resume_family_id: str | None = None
    view: Literal["full", "search", "evaluation", "chat"]
    versions: ProfileVersionVector
    artifact_version: int
    markdown: str
    generated_at: str


class ProfileContextEnvelope(StrictModel):
    context: ProfileContextResponse


class MigrationPreviewResponse(StrictModel):
    legacy_resumes: int
    confirmed_legacy_facts: int
    legacy_intents: int
    convertible_sources: int
    convertible_facts: int
    convertible_resume_versions: int
    convertible_campaigns: int
    skipped: list[dict[str, Any]] = Field(default_factory=list)


class MigrationApplyRequest(StrictModel):
    confirmation: Literal["MIGRATE_LEGACY_PROFILE"]
    profile_id: str = "default"


class MigrationApplyResponse(StrictModel):
    profile_id: str
    created_sources: int
    created_facts: int
    created_resume_versions: int
    created_campaigns: int
    created_queries: int
    skipped: list[dict[str, Any]] = Field(default_factory=list)
