from __future__ import annotations

from pydantic import BaseModel, Field


class QuickCaptureOcrRequest(BaseModel):
    image_base64: str


class QuickCaptureOcrResponse(BaseModel):
    raw_text: str
    captured_at: str
    warnings: list[str] = Field(default_factory=list)
