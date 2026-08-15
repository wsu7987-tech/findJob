from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


WorkMode = Literal["any", "onsite", "hybrid", "remote"]


class FineJobIntentPayload(BaseModel):
    target_title: str = ""
    cities: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    expanded_keywords: list[str] = Field(default_factory=list)
    excluded_keywords: list[str] = Field(default_factory=list)
    salary_min: int | None = Field(default=None, ge=0)
    salary_max: int | None = Field(default=None, ge=0)
    work_mode: WorkMode = "any"
    notes: str = ""


class FineJobIntentResponse(FineJobIntentPayload):
    id: str
    ready: bool
    created_at: str
    updated_at: str


class FineJobIntentEnvelope(BaseModel):
    intent: FineJobIntentResponse | None
