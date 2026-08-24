from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class BossChatRuntimeUpdateRequest(BaseModel):
    listen_enabled: bool | None = None
    generation_enabled: bool | None = None
    send_enabled: bool | None = None
    trigger_mode: Literal["immediate", "interval", "manual"] | None = None
    interval_minutes: Literal[0, 5, 10, 30, 60] | None = None

    @model_validator(mode="after")
    def validate_trigger_interval(self):
        if self.trigger_mode == "immediate" and self.interval_minutes not in (None, 0):
            raise ValueError("immediate 模式的 interval_minutes 必须为 0")
        return self


class BossChatHeartbeatRequest(BaseModel):
    account_uid: str = Field(min_length=1, max_length=80)
    tab_id: str = Field(min_length=1, max_length=120)
    leader_epoch: int = Field(ge=1)
    is_leader: bool
    lease_expires_at: str | None = None


class BossChatMessageEvent(BaseModel):
    platform_message_id: str = Field(min_length=1, max_length=160)
    direction: Literal["inbound", "outbound"]
    message_type: Literal["text", "image", "system", "unknown"] = "text"
    content: str = Field(default="", max_length=20_000)
    sender_uid: str = Field(default="", max_length=80)
    receiver_uid: str = Field(default="", max_length=80)
    client_mid: str = Field(default="", max_length=160)
    peer_uid: str = Field(min_length=1, max_length=80)
    encrypt_peer_uid: str = Field(default="", max_length=160)
    security_id: str = Field(default="", max_length=240)
    job_id: str | None = Field(default=None, max_length=160)
    encrypt_job_id: str = Field(default="", max_length=160)
    job_title: str = Field(default="", max_length=300)
    peer_name: str = Field(default="", max_length=160)
    company_name: str = Field(default="", max_length=240)
    sent_at: str
    observed_at: str
    source: Literal["websocket", "manual", "assistant"] = "websocket"
    raw_meta: dict[str, Any] = Field(default_factory=dict)


class BossChatEventItem(BaseModel):
    event_id: str = Field(min_length=1, max_length=160)
    event_type: Literal["message", "socket_state", "manual_takeover"]
    account_uid: str = Field(min_length=1, max_length=80)
    leader_epoch: int = Field(default=0, ge=0)
    message: BossChatMessageEvent | None = None
    payload: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_message(self):
        if self.event_type == "message" and self.message is None:
            raise ValueError("message 事件必须携带 message")
        return self


class BossChatEventBatchRequest(BaseModel):
    events: list[BossChatEventItem] = Field(min_length=1, max_length=100)


class BossChatGenerateRequest(BaseModel):
    instruction: str = Field(default="", max_length=2_000)


class BossChatReplyEditRequest(BaseModel):
    final_text: str = Field(min_length=1, max_length=5_000)


class BossChatReplyConfirmRequest(BaseModel):
    final_text: str = Field(min_length=1, max_length=5_000)
    based_on_message_id: str = Field(min_length=1, max_length=160)
    based_on_session_version: int = Field(ge=1)


class BossChatReasonRequest(BaseModel):
    reason: str = Field(default="用户操作", max_length=300)


class BossChatClaimActionRequest(BaseModel):
    account_uid: str = Field(min_length=1, max_length=80)
    tab_id: str = Field(min_length=1, max_length=120)
    leader_epoch: int = Field(ge=1)


class BossChatDispatchStartedRequest(BaseModel):
    execution_epoch: int = Field(ge=1)


class BossChatActionCompleteRequest(BaseModel):
    execution_epoch: int = Field(ge=1)
    outcome: Literal["accepted", "failed", "unknown"]
    platform_message_id: str = Field(default="", max_length=160)
    client_mid: str = Field(default="", max_length=160)
    status_code: str = Field(default="", max_length=100)
    message: str = Field(default="", max_length=500)
    evidence: dict[str, Any] = Field(default_factory=dict)
