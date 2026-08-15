from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Literal


PdfParserKind = Literal["auto", "pymupdf4llm_markdown", "rapid_ocr"]
PdfPreviewContentType = Literal["markdown", "text"]


@dataclass(slots=True)
class PdfParsePage:
    page_number: int
    content_type: PdfPreviewContentType
    content: str


@dataclass(slots=True)
class PdfParseRequest:
    parser_name: PdfParserKind
    knowledge_item_id: str | None = None
    cancel_check: Callable[[], bool] | None = None
    on_page: Callable[[PdfParsePage, int], None] | None = None


@dataclass(slots=True)
class PdfParseResult:
    parser_name: str
    raw_text: str
    markdown_text: str | None
    preview_text: str
    page_count: int
    char_count: int
    quality_score: float
    warnings: list[str]
    is_ocr: bool
    fallback_from: str | None = None
    fallback_reason: str | None = None
    preview_pages: list[PdfParsePage] | None = None


@dataclass(slots=True)
class PdfParseQuality:
    score: float
    should_fallback_to_ocr: bool
    fallback_reason: str | None
    warnings: list[str]
