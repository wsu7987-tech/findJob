from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class PdfReparseRequest(BaseModel):
    parser_name: Literal["auto", "pymupdf4llm_markdown", "rapid_ocr"]


class PdfParseResultResponse(BaseModel):
    id: str
    parser_name: str
    status: str
    preview_text: str
    warning: str | None = None


class PdfParseResultEnvelope(BaseModel):
    parse_result: PdfParseResultResponse
