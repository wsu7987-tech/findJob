from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


CompanyType = Literal["unknown", "direct", "outsourcing"]
CompanySource = Literal["capture", "manual", "mcp", "migration"]


class CompanyCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    company_type: CompanyType = "unknown"
    notes: str = ""


class CompanyUpdateRequest(BaseModel):
    canonical_name: str | None = Field(default=None, min_length=1, max_length=160)
    company_type: CompanyType | None = None
    notes: str | None = None


class CompanyBlacklistRequest(BaseModel):
    blacklisted: bool
    reason: str = ""


class CompanyAliasRequest(BaseModel):
    alias_name: str = Field(min_length=1, max_length=160)


class CompanyResponse(BaseModel):
    id: str
    canonical_name: str
    normalized_name: str
    company_type: CompanyType
    classification_source: CompanySource
    notes: str
    is_blacklisted: bool
    blacklist_reason: str
    blacklisted_at: str | None
    version: int
    aliases: list[dict[str, str]] = Field(default_factory=list)
    job_count: int = 0
    applied_job_count: int = 0
    last_detail_at: str | None = None
    last_evaluated_at: str | None = None
    last_applied_at: str | None = None
    created_at: str
    updated_at: str


class CompanyEnvelope(BaseModel):
    company: CompanyResponse


class CompanyListEnvelope(BaseModel):
    items: list[CompanyResponse]
    total: int
    page: int
    page_size: int


class CompanyAliasDeleteResponse(BaseModel):
    deleted: bool
    id: str


class JobApplicationRequest(BaseModel):
    applied: bool = True
    applied_at: str | None = None
    note: str = ""


class JobApplicationStatusRequest(BaseModel):
    status: Literal[
        "pending_greeting", "pending_application", "communicating", "offer", "rejected", "closed"
    ] | None = None
    note: str = ""


class JobApplicationResponse(BaseModel):
    job_id: str
    company_id: str | None
    status: Literal[
        "pending_greeting", "pending_application", "communicating", "offer", "rejected", "closed"
    ] | None
    source: Literal["boss_action", "manual", "mcp", "migration"]
    applied_at: str
    note: str
