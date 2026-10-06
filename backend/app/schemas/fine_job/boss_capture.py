from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class CapturePacing(BaseModel):
    list_min_seconds: float = Field(default=12, ge=12, allow_inf_nan=False)
    list_max_seconds: float = Field(default=22, ge=12, allow_inf_nan=False)
    detail_min_seconds: float = Field(default=10, ge=10, allow_inf_nan=False)
    detail_max_seconds: float = Field(default=25, ge=10, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_intervals(self):
        if self.list_min_seconds > self.list_max_seconds or self.detail_min_seconds > self.detail_max_seconds:
            raise ValueError("采集间隔下限不能超过上限")
        return self


class BossBrowserStatusResponse(BaseModel):
    running: bool
    cdp_port: int
    current_url: str | None = None
    current_title: str | None = None
    is_search_page: bool = False


class BossCityResponse(BaseModel):
    name: str
    code: str


class BossCityListResponse(BaseModel):
    cities: list[BossCityResponse]


class BossSearchPageRequest(BaseModel):
    keyword: str = Field(min_length=1)
    city: str = Field(min_length=1)
    filters: dict[str, str] = Field(default_factory=dict)


class BossCapturePayload(BossSearchPageRequest):
    operation_id: str | None = None
    pages: int = Field(default=1, ge=1, le=10)
    include_details: bool = False
    prefer_current_page: bool = True
    filter_strategy_id: str | None = None


class BossContinueCaptureRequest(BaseModel):
    operation_id: str | None = None
    pages: int = Field(default=1, ge=1, le=10)


class BossSearchPageResponse(BaseModel):
    url: str
    status: BossBrowserStatusResponse


class BossCaptureTaskResponse(BaseModel):
    state_version: int = 1
    server_now: str | None = None
    capture_pacing: CapturePacing = Field(default_factory=CapturePacing)
    scope_id: str | None = None
    activity: Literal["loading", "scrolling", "collecting", "cooling"] | None = None
    activity_scope: str | None = None
    activity_message: str = ""
    activity_started_at: str | None = None
    activity_deadline_at: str | None = None
    capture_validity: Literal["VALID_NONEMPTY", "VALID_EMPTY", "INVALID", "PARTIAL_INTERRUPTED"] | None = None
    validity_reason: str = ""
    filters_confirmed: bool = False
    range_complete: bool = False
    window_id: str | None = None
    attempted_pages: int = 0
    succeeded_pages: int = 0
    failed_pages: int = 0
    recovery_reason: str | None = None
    detail_phase_id: str | None = None
    detail_phase_job_ids: list[str] | None = None
    list_phase_processed: int = 0
    list_phase_baseline: int = 0
    list_phase_id: str | None = None
    id: str
    status: Literal["queued", "running", "completed", "failed"]
    stage: str
    message: str
    keyword: str
    city: str
    pages: int
    auto_details: bool
    used_current_page: bool = False
    source_url: str | None = None
    progress_current: int = 0
    progress_total: int = 0
    jobs_collected: int = 0
    details_completed: int = 0
    details_failed: int = 0
    duplicate_jobs_count: int = 0
    continuation_available: bool = False
    has_more: bool = True
    last_added_jobs: int = 0
    total_pages_loaded: int = 0
    stop_requested: bool = False
    pause_requested: bool = False
    capture_source: Literal["smart", "custom"] = "custom"
    workflow_run_id: str | None = None
    smart_capture_id: str | None = None
    current_job: dict[str, Any] | None = None
    estimated_seconds_min: int = 0
    estimated_seconds_max: int = 0
    jobs: list[dict[str, Any]] = Field(default_factory=list)
    jobs_path: str | None = None
    details_path: str | None = None
    created_at: str
    updated_at: str
    finished_at: str | None = None
    error_message: str | None = None


class BossDetailCaptureRequest(BaseModel):
    operation_id: str | None = None
    job_ids: list[str] = Field(min_length=1)
    force: bool = False
    manual_override: bool = False


class BossHistoryDetailCaptureRequest(BaseModel):
    operation_id: str | None = None
    manual_override: bool = False


class BossDetailSuggestionRequest(BaseModel):
    mode: Literal["strategy", "ai"] = "strategy"
    command: str = ""
    filter_strategy_id: str | None = None
    recommendation_strategy_id: str | None = None
    extra_requirement: str = ""
    context_stale_action: Literal["regenerate", "use_current", "cancel"] | None = None


class BossFilterApplicationRequest(BaseModel):
    strategy_id: str


class BossDeliveryEvaluationRequest(BaseModel):
    recommendation_strategy_id: str
    filter_strategy_id: str | None = None
    extra_requirement: str = ""
    context_stale_action: Literal["regenerate", "use_current", "cancel"] | None = None
    job_ids: list[str] | None = None
    manual_override: bool = False


class BossJobFilterResult(BaseModel):
    job_id: str
    status: Literal["pass", "reject", "review", "exclude"]
    reasons: list[str] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    strategy_id: str | None = None
    strategy_filter_status: Literal["pass", "reject", "review", "exclude"] | None = None
    final_filter_status: Literal["pass", "reject", "review", "exclude"] | None = None
    cooldown_excluded: bool = False
    cooldown_reasons: list[str] = Field(default_factory=list)


class BossJobDeliveryEvaluation(BaseModel):
    evaluation_version: Literal["2.0"] = "2.0"
    job_id: str
    decision: Literal["recommend", "review", "reject"]
    confidence: float
    summary: str = ""
    reasons: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    hard_requirements: list[dict[str, Any]] = Field(default_factory=list)
    match_dimensions: dict[str, float] = Field(default_factory=dict)
    strengths: list[str] = Field(default_factory=list)
    gaps: list[dict[str, Any]] = Field(default_factory=list)
    resume_suggestions: list[dict[str, Any]] = Field(default_factory=list)
    greeting_draft: dict[str, Any] = Field(default_factory=dict)
    source: Literal["rules", "llm"]


class BossDetailSuggestionResponse(BaseModel):
    selected_job_ids: list[str]
    task: BossCaptureTaskResponse


class BossFilterApplicationResponse(BaseModel):
    selected_job_ids: list[str]
    results: list[BossJobFilterResult]
    task: BossCaptureTaskResponse


class BossDeliveryEvaluationResponse(BaseModel):
    evaluations: list[BossJobDeliveryEvaluation]
    task: BossCaptureTaskResponse


class BossHistoryDeliveryEvaluationResponse(BaseModel):
    evaluation: BossJobDeliveryEvaluation
    job: dict[str, Any]


class BossCaptureHistoryResponse(BaseModel):
    items: list[dict[str, Any]] = Field(default_factory=list)
    total: int
    page: int
    page_size: int
