from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class BossNetworkDebugResponse(BaseModel):
    active: bool
    event_count: int = 0
    request_count: int = 0
    output_path: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    target_count: int = 0
    targets: list[dict[str, Any]] = Field(default_factory=list)
    error_message: str | None = None
