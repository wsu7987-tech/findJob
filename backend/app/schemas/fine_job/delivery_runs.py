from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class FineJobActionLogResponse(BaseModel):
    id: str
    level: str
    action_type: str
    message: str
    detail: dict[str, object]
    created_at: str
    category: str = "system"
    outcome: str = "info"
    job_id: str | None = None
    job_title: str | None = None
    company_name: str | None = None


class FineJobActionLogListEnvelope(BaseModel):
    logs: list[FineJobActionLogResponse]
    total: int = 0
    page: int = 1
    page_size: int = 50
    action_types: list[str] = Field(default_factory=list)


class FineJobActionLogCleanupRequest(BaseModel):
    before: str = Field(min_length=10, max_length=40)


class FineJobActionLogCleanupResponse(BaseModel):
    deleted: int
    before: str


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
