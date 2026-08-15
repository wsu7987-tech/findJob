from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from backend.app.errors import AppError
from backend.app.services.ingest import _markdown_to_text
from backend.app.services.pdf_parse.types import PdfParsePage, PdfParseResult

try:
    import pymupdf
except ImportError:  # pragma: no cover - exercised via runtime wiring
    pymupdf = SimpleNamespace(open=None)

try:
    import pymupdf4llm
except ImportError:  # pragma: no cover - exercised via runtime wiring
    pymupdf4llm = SimpleNamespace(to_markdown=None)


class PymupdfMarkdownParser:
    parser_name = "pymupdf4llm_markdown"

    def parse(self, file_path: Path, request=None) -> PdfParseResult:
        if not callable(getattr(pymupdf, "open", None)) or not callable(
            getattr(pymupdf4llm, "to_markdown", None)
        ):
            raise AppError(
                status_code=500,
                error_category="INGEST_FAILED",
                error_message="PyMuPDF parsing dependencies are not installed.",
            )

        document = pymupdf.open(file_path)
        page_count = int(getattr(document, "page_count", 0) or 0)
        preview_pages: list[PdfParsePage] = []
        on_page = getattr(request, "on_page", None)
        cancel_check = getattr(request, "cancel_check", None)
        if hasattr(document, "__iter__"):
            for index, page in enumerate(document, start=1):
                if callable(cancel_check) and cancel_check():
                    raise AppError(
                        status_code=409,
                        error_category="CANCELLED",
                        error_message="PDF reparse was cancelled.",
                    )
                page_markdown = page.get_text("markdown").strip()
                preview_page = PdfParsePage(
                    page_number=index,
                    content_type="markdown",
                    content=page_markdown,
                )
                preview_pages.append(preview_page)
                if callable(on_page):
                    on_page(preview_page, page_count)

        document = pymupdf.open(file_path)
        markdown_text = pymupdf4llm.to_markdown(
            document,
            header=False,
            footer=False,
            page_separators=True,
            ignore_images=True,
            write_images=False,
        )
        raw_text = _markdown_to_text(markdown_text)
        return PdfParseResult(
            parser_name=self.parser_name,
            raw_text=raw_text,
            markdown_text=markdown_text,
            preview_text=markdown_text[:4000],
            page_count=page_count,
            char_count=len(raw_text),
            quality_score=0.0,
            warnings=[],
            is_ocr=False,
            preview_pages=preview_pages,
        )
