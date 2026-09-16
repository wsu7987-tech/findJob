from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class SmartCaptureCreateRequest(BaseModel):
    filter_strategy_id: str = Field(min_length=1, max_length=100)
    allowed_search_keywords: list[str] = Field(min_length=1, max_length=50)
    allowed_cities: list[str] = Field(min_length=1, max_length=20)
    candidate_target_count: int = Field(default=15, ge=1, le=500)
    pages: int = Field(default=1, ge=1, le=10)
    include_details: bool = False
    prefer_current_page: bool = True
    filters: dict[str, str] = Field(default_factory=dict)
    source: Literal["boss_capture"] = "boss_capture"
