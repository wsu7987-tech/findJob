from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


DeliveryRunMode = Literal["dry_run", "live"]
DeliveryRunStatus = Literal["pending", "running", "completed", "failed", "paused", "cancelled"]


class DeliveryRunCreateRequest(BaseModel):
    mode: DeliveryRunMode = "dry_run"
    real_collect: bool = True


class FineJobDeliveryRunResponse(BaseModel):
    id: str
    mode: DeliveryRunMode
    status: DeliveryRunStatus
    stage: str
    searched_count: int
    skipped_count: int
    greeted_count: int
    error_count: int
    started_at: str
    updated_at: str
    finished_at: str | None = None
    error_message: str | None = None


class FineJobDeliveryCandidateResponse(BaseModel):
    id: str
    run_id: str
    platform: str
    keyword: str
    city: str
    job_url: str
    job_title: str
    company_name: str
    salary_text: str
    location_text: str
    experience_text: str
    education_text: str
    hr_active_text: str
    jd_text: str
    match_score: float | None = None
    decision: str
    reason: str
    created_at: str
    updated_at: str


class FineJobActionLogResponse(BaseModel):
    id: str
    run_id: str | None = None
    level: str
    action_type: str
    message: str
    detail: dict[str, object]
    created_at: str
    source: Literal["legacy_run", "main_workflow"] = "main_workflow"
    category: str = "system"
    outcome: str = "info"
    job_id: str | None = None
    job_title: str | None = None
    company_name: str | None = None


class FineJobDeliveryRunEnvelope(BaseModel):
    run: FineJobDeliveryRunResponse


class FineJobDeliveryRunListEnvelope(BaseModel):
    runs: list[FineJobDeliveryRunResponse]


class FineJobDeliveryCandidateListEnvelope(BaseModel):
    candidates: list[FineJobDeliveryCandidateResponse]


class FineJobActionLogListEnvelope(BaseModel):
    logs: list[FineJobActionLogResponse]
    total: int = 0
    page: int = 1
    page_size: int = 50
    action_types: list[str] = Field(default_factory=list)


class FineJobActionLogCleanupRequest(BaseModel):
    before: str = Field(min_length=10, max_length=40)
    source: Literal["all", "legacy_run", "main_workflow"] = "all"


class FineJobActionLogCleanupResponse(BaseModel):
    deleted: int
    before: str


class FineJobDeliveryRunDeleteResponse(BaseModel):
    deleted: bool
    id: str
    candidates_deleted: int
    logs_deleted: int


class FineJobOperationsDashboardResponse(BaseModel):
    generated_at: str
    metrics: dict[str, int]
    review_counts: dict[str, int]
    action_counts: dict[str, int]
    execution_counts: dict[str, int]
    capture_counts: dict[str, int]
    executor: dict[str, Any] | None = None
    current_task: dict[str, Any] | None = None
    queue: dict[str, Any]
    recent_issues: list[FineJobActionLogResponse]
    legacy_runs: list[FineJobDeliveryRunResponse]
