from __future__ import annotations

from pydantic import BaseModel, Field


class JobHuntRefreshWorkflowOptions(BaseModel):
    refresh_chat_list: bool = True
    refresh_chat_messages: bool = True
    refresh_related_jobs: bool = True
    analyze_conversations: bool = False
    generate_missing_suggestions: bool = False


class JobHuntRefreshScopeDiscoveryRequest(BaseModel):
    selected_since_time: str = Field(min_length=1, max_length=80)


class JobHuntRefreshRunCreateRequest(BaseModel):
    scope_id: str = Field(min_length=1, max_length=100)
    workflow_options: JobHuntRefreshWorkflowOptions
    trigger_source: str = Field(default="page", min_length=1, max_length=40)


class JobHuntRefreshCodexSessionRequest(BaseModel):
    codex_session_ref: str = Field(min_length=1, max_length=200)
