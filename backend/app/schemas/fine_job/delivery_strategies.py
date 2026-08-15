from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


AutomationLevel = Literal["assist", "semi_auto", "auto_greeting"]
ResumeSubmitMode = Literal["manual", "auto_on_invite"]
ContactShareMode = Literal["manual", "auto_after_match"]
InterviewAcceptMode = Literal["manual", "auto_in_selected_slots"]


class FineJobDeliveryStrategyPayload(BaseModel):
    automation_level: AutomationLevel = "assist"
    auto_greeting_enabled: bool = False
    daily_greeting_limit: int = Field(default=20, ge=1, le=500)
    hourly_greeting_limit: int = Field(default=5, ge=1, le=100)
    min_match_score: float = Field(default=0.72, ge=0, le=1)
    resume_submit_mode: ResumeSubmitMode = "manual"
    contact_share_mode: ContactShareMode = "manual"
    interview_accept_mode: InterviewAcceptMode = "manual"
    only_online_interview: bool = False
    pause_on_risk: bool = True
    notes: str = ""


class FineJobDeliveryStrategyResponse(FineJobDeliveryStrategyPayload):
    id: str
    ready: bool
    confirmed_at: str | None = None
    created_at: str
    updated_at: str


class FineJobDeliveryStrategyEnvelope(BaseModel):
    strategy: FineJobDeliveryStrategyResponse | None
