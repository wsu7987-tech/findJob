from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class FineJobResumeFact(BaseModel):
    id: str
    resume_id: str
    fact_type: str
    fact_key: str
    fact_value: str
    confidence: float
    source_text: str | None = None
    user_confirmed: bool
    sensitive: bool
    created_at: str
    updated_at: str


class ResumeFactUpdate(BaseModel):
    id: str | None = None
    fact_type: str
    fact_key: str
    fact_value: str
    confidence: float = 1
    source_text: str | None = None
    user_confirmed: bool = True
    sensitive: bool = False


class ResumeFactsSaveRequest(BaseModel):
    facts: list[ResumeFactUpdate]


class ResumeCreateFromFileRequest(BaseModel):
    file_path: str
    name: str | None = None
    parser_name: Literal["auto", "pymupdf4llm_markdown", "rapid_ocr"] = "auto"


class FineJobResumeResponse(BaseModel):
    id: str
    name: str
    file_path: str
    file_hash: str
    parser_name: str
    raw_text: str = Field(repr=False)
    markdown_text: str | None = Field(default=None, repr=False)
    preview_text: str
    page_count: int
    char_count: int
    quality_score: float
    is_ocr: bool
    warnings: list[str]
    fallback_from: str | None = None
    fallback_reason: str | None = None
    status: Literal["parsed", "failed"]
    created_at: str
    updated_at: str


class FineJobResumeSummary(BaseModel):
    id: str
    name: str
    file_path: str
    parser_name: str
    preview_text: str
    page_count: int
    char_count: int
    quality_score: float
    is_ocr: bool
    warnings: list[str]
    status: Literal["parsed", "failed"]
    created_at: str
    updated_at: str


class FineJobResumeEnvelope(BaseModel):
    resume: FineJobResumeResponse


class FineJobResumeListEnvelope(BaseModel):
    resumes: list[FineJobResumeSummary]


class FineJobResumeFactListEnvelope(BaseModel):
    facts: list[FineJobResumeFact]
