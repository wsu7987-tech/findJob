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
    current_action_id: str | None = None
    current_epoch: int | None = Field(default=None, ge=0)
    page_kind: str = "other"
    page_state: str = "waiting"
    logged_in: bool = False
    risk_state: str = "none"


class BossExecutorControlRequest(BaseModel):
    command: Literal["allow", "pause", "resume", "emergency_stop"]


class BossNavigationOpenRequest(BaseModel):
    job_id: str = Field(min_length=1, max_length=160)
    source_context: Literal["capture", "history", "review"]


class BossPageStatusRequest(BaseModel):
    execution_epoch: int = Field(ge=1)
    state: Literal["ready", "waiting", "unsupported", "mismatch", "unavailable"]
    logged_in: bool
    page_kind: str
    encrypt_job_id: str = ""
    contacted: bool | None = None
    observed_at: int | None = Field(default=None, ge=0)
    reason: str = Field(default="", max_length=500)


class BossDispatchStartedRequest(BaseModel):
    execution_epoch: int = Field(ge=1)


class BossActionCompleteRequest(BaseModel):
    execution_epoch: int = Field(ge=1)
    outcome: Literal["accepted", "succeeded", "failed", "unknown"]
    contacted: bool | None = None
    status_code: str = Field(default="", max_length=100)
    message: str = Field(default="", max_length=500)
    evidence: dict[str, Any] = Field(default_factory=dict)


class BossReturnToReviewRequest(BaseModel):
    reason: str = Field(default="用户退回待确认", max_length=300)


class BossManualVerifyUnknownRequest(BaseModel):
    contacted: bool
    note: str = Field(default="用户人工核验BOSS岗位页面", max_length=300)
