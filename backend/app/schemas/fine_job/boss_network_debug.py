from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class BossNetworkDebugResponse(BaseModel):
    active: bool
    trace_id: str | None = None
    event_count: int = 0
    request_count: int = 0
    frame_count: int = 0
    marker_count: int = 0
    dropped_event_count: int = 0
    output_path: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    target_count: int = 0
    targets: list[dict[str, Any]] = Field(default_factory=list)
    error_message: str | None = None
    marker: dict[str, Any] | None = None
    evidence_complete: bool = False
    gap_reasons: list[str] = Field(default_factory=list)


class BossNetworkTraceMarkerRequest(BaseModel):
    marker: str = Field(min_length=1, max_length=64)
