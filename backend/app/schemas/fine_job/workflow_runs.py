from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class DeepJobSearchConfig(BaseModel):
    filter_strategy_id: str = Field(min_length=1, max_length=100)
    target_count: int = Field(ge=1, le=100)
    candidate_target_count: int | None = Field(default=None, ge=1, le=500)
    allowed_search_keywords: list[str] = Field(min_length=1, max_length=50)
    allowed_cities: list[str] = Field(min_length=1, max_length=20)
    source_policy: Literal["fresh_only"] = "fresh_only"
    allow_historical_jobs: bool = False
    min_depth: int = Field(default=5, ge=1, le=100)
    scroll_batch_size: int = Field(default=3, ge=1, le=10)
    max_depth: int = Field(default=20, ge=1, le=200)
    low_yield_streak_limit: int = Field(default=3, ge=1, le=20)
    context_soft_budget_characters: int = Field(default=12000, ge=1000, le=200000)
    applied_feedback_ids: list[str] = Field(default_factory=list, max_length=100)
    applied_preference_ids: list[str] = Field(default_factory=list, max_length=100)


class WorkflowRunCreateRequest(BaseModel):
    task_type: Literal["deep_job_search"]
    deep_job_search: DeepJobSearchConfig
    created_from: str = Field(default="task_cockpit", min_length=1, max_length=80)


class WorkflowRunCodexSessionRequest(BaseModel):
    codex_session_ref: str = Field(min_length=1, max_length=200)
    codex_runtime_id: str | None = Field(default=None, max_length=200)
