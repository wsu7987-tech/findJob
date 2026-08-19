from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


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
    pages: int = Field(default=1, ge=1, le=10)
    include_details: bool = False
    prefer_current_page: bool = True


class BossSearchPageResponse(BaseModel):
    url: str
    status: BossBrowserStatusResponse


class BossCaptureTaskResponse(BaseModel):
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
    job_ids: list[str] = Field(min_length=1)
    force: bool = False


class BossDetailSuggestionRequest(BaseModel):
    mode: Literal["strategy", "ai"] = "strategy"
    command: str = ""
    filter_strategy_id: str | None = None
    recommendation_strategy_id: str | None = None
    extra_requirement: str = ""


class BossFilterApplicationRequest(BaseModel):
    strategy_id: str


class BossDeliveryEvaluationRequest(BaseModel):
    recommendation_strategy_id: str
    filter_strategy_id: str | None = None
    extra_requirement: str = ""


class BossJobFilterResult(BaseModel):
    job_id: str
    status: Literal["pass", "reject", "review"]
    reasons: list[str] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
    strategy_id: str | None = None


class BossJobDeliveryEvaluation(BaseModel):
    job_id: str
    decision: Literal["recommend", "review", "reject"]
    confidence: float
    reasons: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    missing_fields: list[str] = Field(default_factory=list)
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


class BossCaptureHistoryResponse(BaseModel):
    items: list[dict[str, Any]] = Field(default_factory=list)
    total: int
    page: int
    page_size: int
