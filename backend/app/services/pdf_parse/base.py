from __future__ import annotations

from pathlib import Path
from typing import Protocol

from backend.app.services.pdf_parse.types import PdfParseRequest, PdfParseResult


class PdfParser(Protocol):
    parser_name: str

    def parse(self, file_path: Path, request: PdfParseRequest | None) -> PdfParseResult:
        ...
