from __future__ import annotations

from pydantic import BaseModel


class ActiveParseResultResponse(BaseModel):
    knowledge_item_id: str
    source_type: str
    source_value: str
    title: str | None = None
    canonical_content: str
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
    saved_at: str | None = None


class ActiveParseResultEnvelope(BaseModel):
    parse_result: ActiveParseResultResponse
