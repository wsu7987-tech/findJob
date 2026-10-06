from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from backend.app.schemas.fine_job.smart_capture_execution_config import (
    execution_config_validation_errors,
)


class SmartCaptureCreateRequest(BaseModel):
    operation_id: str | None = None
    filter_strategy_id: str = Field(min_length=1, max_length=100)
    allowed_search_keywords: list[str] = Field(min_length=1, max_length=50)
    allowed_cities: list[str] = Field(min_length=1, max_length=20)
    candidate_target_count: int = Field(default=15, ge=1, le=500)
    prefer_current_page: bool = True
    filters: dict[str, str] = Field(default_factory=dict)
    delivery_target_enabled: bool = False
    recommendation_strategy_id: str | None = Field(default=None, min_length=1, max_length=100)
    recommend_target: int | None = Field(default=None, ge=1, le=100)
    review_target: int | None = Field(default=None, ge=1, le=100)
    target_mode: Literal["any", "all"] = "all"
    analyze_all_candidates: bool = False
    stop_after_current_batch: bool = False
    analysis_batch_size: int = Field(default=5, ge=1, le=20)
    execution_policy_after_analysis_batch: Literal["auto_continue", "wait_for_user"] = "auto_continue"
    execution_policy_codex_handoff: Literal["auto", "manual"] = "auto"
    analysis_guidance: str = Field(default="", max_length=4000)
    codex_model: str | None = Field(default=None, min_length=1, max_length=128)
    codex_reasoning_effort: Literal["minimal", "low", "medium", "high", "xhigh"] | None = None
    context_soft_budget_characters: int = Field(default=12000, ge=1000, le=200000)
    min_depth: int = Field(default=1, ge=1, le=100)
    scroll_batch_size: int = Field(default=3, ge=1, le=10)
    max_depth: int = Field(default=20, ge=1, le=200)
    low_yield_streak_limit: int = Field(default=3, ge=1, le=20)
    source: Literal["boss_capture"] = "boss_capture"

    @model_validator(mode="after")
    def validate_execution_config(self) -> "SmartCaptureCreateRequest":
        errors = execution_config_validation_errors(self.model_dump())
        if errors:
            raise ValueError("；".join(errors))
        return self


class SmartCaptureControlRequest(BaseModel):
    operation_id: str | None = None
    transition_id: str | None = Field(default=None, min_length=1, max_length=160)


class SmartCaptureManualAnalysisBatchRequest(BaseModel):
    recommendation_strategy_id: str | None = Field(default=None, min_length=1, max_length=100)
    job_ids: list[str] = Field(min_length=1, max_length=20)
    analysis_batch_size: int = Field(default=5, ge=1, le=20)
    codex_model: str | None = Field(default=None, min_length=1, max_length=128)
    codex_reasoning_effort: Literal["minimal", "low", "medium", "high", "xhigh"] | None = None


class SmartCaptureCodexSessionRequest(BaseModel):
    codex_session_ref: str = Field(min_length=1, max_length=200)
    codex_runtime_id: str | None = Field(default=None, max_length=200)
    analysis_batch_id: str | None = Field(default=None, max_length=100)


class SmartCaptureHandoffClaimRequest(SmartCaptureCodexSessionRequest):
    handoff_kind: Literal["initial", "next"] = "initial"
    retry_handoff_attempt_id: str | None = Field(default=None, min_length=1, max_length=100)


class SmartCaptureHandoffRequest(BaseModel):
    analysis_batch_id: str = Field(min_length=1, max_length=100)
    handoff_attempt_id: str = Field(min_length=1, max_length=100)
    codex_session_ref: str = Field(min_length=1, max_length=200)
    release_reason: Literal["transport_failure", "full_retry"] | None = None


class SmartCaptureHandoffStartAckRequest(BaseModel):
    analysis_batch_id: str = Field(min_length=1, max_length=100)
    handoff_attempt_id: str = Field(min_length=1, max_length=100)


class SmartCaptureAnalysisGuidanceUpdateRequest(BaseModel):
    analysis_guidance: str = Field(max_length=4000)


class SmartCaptureAnalysisFeedbackRequest(BaseModel):
    sentiment: Literal["expected", "unexpected"]
    reason: str | None = Field(default=None, max_length=100)
    note: str = Field(default="", max_length=1000)


class SmartCaptureAnalysisSaveRequest(BaseModel):
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
    evaluation_id: str | None = Field(default=None, max_length=200)
