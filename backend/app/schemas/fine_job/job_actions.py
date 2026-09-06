from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


JobActionType = Literal[
    "respond_interview",
    "send_resume",
    "reply_recruiter",
    "review_draft",
    "followup_recruiter",
    "ask_rejection_reason",
]
JobActionPriority = Literal["urgent", "high", "normal", "low"]
JobActionState = Literal["active", "snoozed", "dismissed", "completed"]


class JobActionReplyTaskView(BaseModel):
    id: str
    action_kind: Literal["reply", "followup", "ask_rejection_reason"]
    status: Literal["awaiting_review"]
    based_on_message_id: str
    based_on_session_version: int
    draft_text: str
    final_text: str
    generated_at: str | None = None
    updated_at: str


class JobActionPrimaryAction(BaseModel):
    type: Literal["open_chat"] = "open_chat"
    label: str
    route_name: Literal["fine-job-chat"] = "fine-job-chat"
    query: dict[str, str]
    action_kind: Literal["reply", "followup", "ask_rejection_reason"] | None = None
    reply_task_id: str | None = None


class JobActionEvidence(BaseModel):
    trigger_type: Literal["message", "activity_event", "reply_task"]
    trigger_id: str
    message_ids: list[str]
    activity_event_ids: list[str]
    attention_insight_id: str | None = None


class ActionItemView(BaseModel):
    action_key: str
    job_id: str
    session_id: str
    action_type: JobActionType
    priority_tier: JobActionPriority
    title: str
    company_name: str
    stage: str
    waiting_on: str
    waiting_since_at: str | None = None
    due_at: str | None = None
    overdue_seconds: int
    reason_code: str
    reason_summary: str
    evidence: JobActionEvidence
    reply_task: JobActionReplyTaskView | None = None
    primary_action: JobActionPrimaryAction
    secondary_actions: list[Literal["snooze", "dismiss", "complete", "restore"]]
    state: JobActionState
    snoozed_until: str | None = None


class JobActionSummary(BaseModel):
    urgent: int
    high: int
    normal: int
    low: int
    snoozed: int


class JobActionListResponse(BaseModel):
    summary: JobActionSummary
    items: list[ActionItemView]
    generated_at: str


class JobActionSnoozeRequest(BaseModel):
    snoozed_until: datetime


class JobActionMutationResponse(BaseModel):
    action_key: str
    state: JobActionState | None = None
    snoozed_until: str | None = None
    item: ActionItemView | None = None


class JobActionGenerateDraftsRequest(BaseModel):
    action_keys: list[str] = Field(min_length=1, max_length=100)


class JobActionGenerateDraftItem(BaseModel):
    action_key: str
    status: Literal["created", "already_exists", "skipped", "failed"]
    reply_task_id: str | None = None
    error: str | None = None


class JobActionGenerateDraftsResponse(BaseModel):
    results: list[JobActionGenerateDraftItem]
