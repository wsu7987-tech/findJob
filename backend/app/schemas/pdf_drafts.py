from __future__ import annotations

from typing import Literal

from pydantic import BaseModel
from backend.app.schemas.pool import PoolCommitMetadataRequest, PoolItemResponse


PdfDraftParserName = Literal["auto", "pymupdf4llm_markdown", "rapid_ocr"]


class PdfDraftCreateRequest(BaseModel):
    file_path: str
    title: str | None = None


class PdfDraftReparseRequest(BaseModel):
    parser_name: PdfDraftParserName


class PdfDraftParseResultResponse(BaseModel):
    id: str
    parser_name: str
    status: str
    raw_text: str
    markdown_text: str | None = None
    preview_text: str
    page_count: int
    char_count: int
    quality_score: float
    is_ocr: bool
    warnings: list[str]
    fallback_from: str | None = None
    fallback_reason: str | None = None
    created_at: str


class PdfDraftPreviewPageResponse(BaseModel):
    page_number: int
    content_type: str
    content: str


class PdfDraftResponse(BaseModel):
    id: str
    file_path: str
    title: str | None = None
    source_name: str
    created_at: str
    updated_at: str
    saved_parse_result_id: str | None = None
    latest_preview_result_id: str | None = None
    parse_results: list[PdfDraftParseResultResponse]


class PdfDraftEnvelope(BaseModel):
    draft: PdfDraftResponse


class PdfReparseJobResponse(BaseModel):
    id: str
    draft_id: str
    parser_name: str
    status: str
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    error_message: str | None = None
    processed_pages: int
    total_pages: int
    latest_available_page: int
    cancel_requested: bool
    preview_result_id: str | None = None


class PdfDraftReparseEnvelope(BaseModel):
    draft: PdfDraftResponse
    job: PdfReparseJobResponse


class PdfReparseJobEnvelope(BaseModel):
    job: PdfReparseJobResponse


class PdfReparseJobListEnvelope(BaseModel):
    jobs: list[PdfReparseJobResponse]


class PdfDraftPreviewPageEnvelope(BaseModel):
    page: PdfDraftPreviewPageResponse


class PoolCommitEnvelope(BaseModel):
    item: PoolItemResponse


class PdfDraftCommitRequest(PoolCommitMetadataRequest):
    pass


class PdfDraftDeleteResponse(BaseModel):
    deleted: bool


class PdfDraftCancelReparseResponse(BaseModel):
    cancel_requested: bool
