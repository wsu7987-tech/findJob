from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from backend.app.schemas.pool import PoolCommitMetadataRequest, PoolItemResponse


WebDraftParserName = Literal["playwright_dom"]


class WebDraftCreateRequest(BaseModel):
    url: str
    title: str | None = None
    session_profile_id: str | None = None


class WebDraftReparseRequest(BaseModel):
    parser_name: WebDraftParserName
    session_profile_id: str | None = None


class WebDraftPreviewPageResponse(BaseModel):
    page_number: int
    content_type: str
    content: str


class WebDraftParseResultResponse(BaseModel):
    id: str
    parser_name: WebDraftParserName
    status: str
    raw_text: str
    markdown_text: str | None = None
    preview_text: str
    section_count: int
    char_count: int
    quality_score: float
    warnings: list[str]
    auth_mode: str
    created_at: str


class WebDraftResponse(BaseModel):
    id: str
    url: str
    title: str | None = None
    source_name: str
    session_profile_id: str | None = None
    created_at: str
    updated_at: str
    saved_parse_result_id: str | None = None
    latest_preview_result_id: str | None = None
    parse_results: list[WebDraftParseResultResponse]


class WebDraftEnvelope(BaseModel):
    draft: WebDraftResponse


class WebReparseJobResponse(BaseModel):
    id: str
    draft_id: str
    parser_name: WebDraftParserName
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


class WebDraftReparseEnvelope(BaseModel):
    draft: WebDraftResponse
    job: WebReparseJobResponse


class WebDraftPreviewPageEnvelope(BaseModel):
    page: WebDraftPreviewPageResponse


class WebReparseJobEnvelope(BaseModel):
    job: WebReparseJobResponse


class WebReparseJobListEnvelope(BaseModel):
    jobs: list[WebReparseJobResponse]


class WebDraftDeleteResponse(BaseModel):
    deleted: bool


class WebDraftCommitEnvelope(BaseModel):
    item: PoolItemResponse


class WebDraftCommitRequest(PoolCommitMetadataRequest):
    pass
