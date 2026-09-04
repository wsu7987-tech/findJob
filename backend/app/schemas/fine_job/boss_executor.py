from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class BossPairingCodeResponse(BaseModel):
    code: str
    expires_at: str


class BossExecutorPairRequest(BaseModel):
    code: str = Field(min_length=6, max_length=20)
    label: str = Field(default="FineJob BOSS 执行器", max_length=80)
    protocol_version: str = Field(default="1.1", max_length=20)
    plugin_version: str = Field(min_length=1, max_length=40)
    capabilities: list[str] = Field(default_factory=list, max_length=20)


class BossExecutorPairResponse(BaseModel):
    executor_id: str
    token: str
    protocol_version: str


class BossExecutorHeartbeatRequest(BaseModel):
    protocol_version: str = Field(max_length=20)
    plugin_version: str = Field(max_length=40)
    capabilities: list[str] = Field(default_factory=list, max_length=20)
    browser_connected: bool = True
    risk_state: str = "none"


class BossExecutorControlRequest(BaseModel):
    command: Literal["start", "pause"]


class BossExecutorSettingsRequest(BaseModel):
    task_cooldown_max_seconds: int = Field(ge=4, le=600)
    page_load_wait_max_seconds: int = Field(ge=3, le=600)


class BossNavigationOpenRequest(BaseModel):
    job_id: str = Field(min_length=1, max_length=160)
    source_context: Literal["capture", "history", "review", "queue"]


class BossTaskMatchRequest(BaseModel):
    execution_epoch: int = Field(ge=0)


class BossTaskCompleteRequest(BaseModel):
    execution_epoch: int = Field(ge=0)
    outcome: Literal["accepted", "succeeded", "failed", "unknown"]
    contacted: bool | None = None
    status_code: str = Field(default="", max_length=100)
    message: str = Field(default="", max_length=500)
    evidence: dict[str, Any] = Field(default_factory=dict)


class BossTestTaskCreateRequest(BaseModel):
    job_id: str = Field(min_length=1, max_length=160)
    close_page_after_completion: bool = False
    delay_seconds: int = Field(default=3, ge=1, le=600)


class BossTestJobUpdateRequest(BaseModel):
    encrypt_job_id: str = Field(default="", max_length=160)
    job_link: str = Field(min_length=8, max_length=1000)


class BossReturnToReviewRequest(BaseModel):
    reason: str = Field(default="用户退回待确认", max_length=300)
