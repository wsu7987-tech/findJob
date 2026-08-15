from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


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


class FineJobDeliveryRunEnvelope(BaseModel):
    run: FineJobDeliveryRunResponse


class FineJobDeliveryRunListEnvelope(BaseModel):
    runs: list[FineJobDeliveryRunResponse]


class FineJobDeliveryCandidateListEnvelope(BaseModel):
    candidates: list[FineJobDeliveryCandidateResponse]


class FineJobActionLogListEnvelope(BaseModel):
    logs: list[FineJobActionLogResponse]
