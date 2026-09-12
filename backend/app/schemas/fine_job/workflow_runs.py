from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class DeepJobSearchConfig(BaseModel):
    filter_strategy_id: str = Field(min_length=1, max_length=100)
    recommendation_strategy_id: str = Field(min_length=1, max_length=100)
    codex_model: str = Field(min_length=1, max_length=128)
    codex_reasoning_effort: Literal["minimal", "low", "medium", "high", "xhigh"]
    analysis_guidance: str = Field(default="", max_length=4000)
    recommend_target: int | None = Field(default=None, ge=1, le=100)
    review_target: int | None = Field(default=None, ge=1, le=100)
    target_mode: Literal["any", "all"] = "all"
    # 兼容已有调用方；创建后统一归一为 recommend_target。
    target_count: int | None = Field(default=None, ge=1, le=100)
    analyze_all_candidates: bool = False
    stop_after_current_batch: bool = False
    analysis_batch_size: int = Field(default=5, ge=1, le=20)
    execution_policy_after_analysis_batch: Literal["auto_continue", "wait_for_user"] = "auto_continue"
    execution_policy_codex_handoff: Literal["auto", "manual"] = "auto"
    candidate_target_count: int | None = Field(default=None, ge=1, le=500)
    allowed_search_keywords: list[str] = Field(min_length=1, max_length=50)
    allowed_cities: list[str] = Field(min_length=1, max_length=20)
    source_policy: Literal["fresh_only"] = "fresh_only"
    allow_historical_jobs: bool = False
    min_depth: int = Field(default=5, ge=1, le=100)
    scroll_batch_size: int = Field(default=3, ge=1, le=10)
    max_depth: int = Field(default=20, ge=1, le=200)
    low_yield_streak_limit: int = Field(default=3, ge=1, le=20)
    jd_batch_size: int | None = Field(default=None, ge=1, le=20)
    context_soft_budget_characters: int = Field(default=12000, ge=1000, le=200000)
    applied_feedback_ids: list[str] = Field(default_factory=list, max_length=100)
    applied_preference_ids: list[str] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def normalize_recommend_target(self) -> "DeepJobSearchConfig":
        if self.recommend_target is None and self.target_count is None:
            raise ValueError("recommend_target 为必填字段。")
        if (
            self.recommend_target is not None
            and self.target_count is not None
            and self.recommend_target != self.target_count
        ):
            raise ValueError("recommend_target 与 target_count 必须一致。")
        self.recommend_target = self.recommend_target or self.target_count
        return self


class WorkflowRunCreateRequest(BaseModel):
    task_type: Literal["deep_job_search"]
    deep_job_search: DeepJobSearchConfig
    created_from: str = Field(default="task_cockpit", min_length=1, max_length=80)


class WorkflowRunCodexSessionRequest(BaseModel):
    codex_session_ref: str = Field(min_length=1, max_length=200)
    codex_runtime_id: str | None = Field(default=None, max_length=200)


class WorkflowAnalysisHandoffClaimRequest(WorkflowRunCodexSessionRequest):
    handoff_kind: Literal["initial", "next"]
    retry_handoff_attempt_id: str | None = Field(default=None, min_length=1, max_length=100)


class WorkflowAnalysisHandoffRequest(BaseModel):
    analysis_batch_id: str = Field(min_length=1, max_length=100)
    handoff_attempt_id: str = Field(min_length=1, max_length=100)
    codex_session_ref: str = Field(min_length=1, max_length=200)
    release_reason: Literal["transport_failure", "full_retry"] | None = None


class WorkflowAnalysisHandoffStartAckRequest(BaseModel):
    analysis_batch_id: str = Field(min_length=1, max_length=100)
    handoff_attempt_id: str = Field(min_length=1, max_length=100)


class WorkflowAnalysisGuidanceUpdateRequest(BaseModel):
    analysis_guidance: str = Field(max_length=4000)


class WorkflowAnalysisFeedbackRequest(BaseModel):
    sentiment: Literal["expected", "unexpected"]
    reason: Literal[
        "technical_direction",
        "salary",
        "company",
        "experience_or_education",
        "work_schedule",
        "location",
        "jd_understanding",
        "candidate_understanding",
        "other",
    ] | None = None
    note: str = Field(default="", max_length=1000)


class WorkflowAnalysisSaveRequest(BaseModel):
    decision: Literal["recommend", "review", "reject"]
    confidence: float = Field(default=0, ge=0, le=1)
    summary: str = Field(default="", max_length=2000)
    reasons: list[str] = Field(default_factory=list, max_length=30)
    risks: list[str] = Field(default_factory=list, max_length=30)
    strengths: list[str] = Field(default_factory=list, max_length=30)
    gaps: list[str] = Field(default_factory=list, max_length=30)
    hard_requirements: list[object] = Field(default_factory=list, max_length=30)
    match_dimensions: dict[str, object] = Field(default_factory=dict)
    missing_information: list[str] = Field(default_factory=list, max_length=30)
    jd_evidence: list[str] = Field(default_factory=list, max_length=30)
    candidate_evidence: list[str] = Field(default_factory=list, max_length=30)
