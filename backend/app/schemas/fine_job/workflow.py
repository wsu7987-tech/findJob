from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


ReviewStatus = Literal["pending", "approved", "rejected", "dismissed"]
ActionStatus = Literal[
    "queued", "leased", "succeeded", "failed", "blocked", "unknown", "cancelled"
]


class FineJobReviewItemResponse(BaseModel):
    id: str
    job_id: str
    evaluation_id: str
    action_type: Literal["start_conversation"]
    status: ReviewStatus
    ai_decision: Literal["recommend", "review", "reject"]
    draft_message: str
    final_message: str
    resolution_note: str
    auto_approved: bool
    job_title: str
    company_name: str
    job_link: str
    evaluation: dict[str, Any]
    created_at: str
    updated_at: str
    resolved_at: str | None = None


class FineJobReviewItemListEnvelope(BaseModel):
    items: list[FineJobReviewItemResponse]
    total: int


class FineJobReviewApproveRequest(BaseModel):
    message: str = ""
    allow_override: bool = False


class FineJobReviewRejectRequest(BaseModel):
    note: str = ""


class FineJobAutomationActionResponse(BaseModel):
    id: str
    job_id: str
    evaluation_id: str
    review_item_id: str
    action_type: Literal["start_conversation", "BOSS_DEFAULT_GREETING"]
    status: ActionStatus
    idempotency_key: str
    payload: dict[str, Any]
    lease_owner: str | None = None
    lease_expires_at: str | None = None
    attempt_count: int
    last_error: str | None = None
    job_title: str
    company_name: str
    created_at: str
    updated_at: str
    completed_at: str | None = None
    execution_state: str = "queued"
    execution_epoch: int = 0
    queue_position: int = 0
    page_open_attempts: int = 0
    page_deadline_at: str | None = None
    dispatch_started_at: str | None = None
    request_accepted_at: str | None = None
    verification_state: str = "not_required"
    verification_method: str = "none"
    verification_delay_seconds: int | None = None
    verification_due_at: str | None = None
    verification_started_at: str | None = None
    verification_completed_at: str | None = None
    verification_attempts: int = 0
    cooldown_seconds: int | None = None
    next_eligible_at: str | None = None
    last_status_code: str | None = None
    result: dict[str, Any] = Field(default_factory=dict)
    navigation_task_id: str | None = None


class FineJobAutomationActionEnvelope(BaseModel):
    action: FineJobAutomationActionResponse


class FineJobOptionalAutomationActionEnvelope(BaseModel):
    action: FineJobAutomationActionResponse | None = None


class FineJobAutomationActionListEnvelope(BaseModel):
    actions: list[FineJobAutomationActionResponse]
    total: int


class FineJobActionClaimRequest(BaseModel):
    worker_id: str = Field(min_length=1, max_length=120)
    lease_seconds: int = Field(default=60, ge=15, le=600)


class FineJobActionCompleteRequest(BaseModel):
    worker_id: str = Field(min_length=1, max_length=120)
    status: Literal["succeeded", "failed", "blocked", "unknown"]
    message: str = ""
