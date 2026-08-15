from __future__ import annotations

from pathlib import Path
import numpy as np


def test_pymupdf_markdown_parser_normalizes_markdown(monkeypatch) -> None:
    from backend.app.services.pdf_parse.parsers.pymupdf_markdown import (
        PymupdfMarkdownParser,
    )

    class DummyDocument:
        page_count = 2

    monkeypatch.setattr(
        "backend.app.services.pdf_parse.parsers.pymupdf_markdown.pymupdf.open",
        lambda path: DummyDocument(),
    )
    monkeypatch.setattr(
        "backend.app.services.pdf_parse.parsers.pymupdf_markdown.pymupdf4llm.to_markdown",
        lambda doc, **kwargs: "# Title\n\nAlpha body",
    )

    result = PymupdfMarkdownParser().parse(Path("dummy.pdf"), request=None)

    assert result.parser_name == "pymupdf4llm_markdown"
    assert result.markdown_text == "# Title\n\nAlpha body"
    assert result.raw_text == "Title\n\nAlpha body"
    assert result.page_count == 2


def test_pymupdf_markdown_parser_reports_missing_dependencies(monkeypatch) -> None:
    from backend.app.errors import AppError
    from backend.app.services.pdf_parse.parsers.pymupdf_markdown import (
        PymupdfMarkdownParser,
    )

    monkeypatch.setattr(
        "backend.app.services.pdf_parse.parsers.pymupdf_markdown.pymupdf",
        type("MissingPyMuPdf", (), {"open": None})(),
    )
    monkeypatch.setattr(
        "backend.app.services.pdf_parse.parsers.pymupdf_markdown.pymupdf4llm",
        type("MissingPyMuPdf4llm", (), {"to_markdown": None})(),
    )

    try:
        PymupdfMarkdownParser().parse(Path("dummy.pdf"), request=None)
    except AppError as exc:
        assert exc.error_category == "INGEST_FAILED"
        assert "PyMuPDF parsing dependencies are not installed" in exc.error_message
    else:
        raise AssertionError("Expected parser to raise AppError when dependencies are missing.")


def test_rapid_ocr_parser_reconstructs_page_text(monkeypatch) -> None:
    from backend.app.services.pdf_parse.parsers.rapid_ocr import RapidOcrParser
    from backend.app.services.pdf_parse.types import PdfParseRequest

    monkeypatch.setattr(
        "backend.app.services.pdf_parse.parsers.rapid_ocr.cv2",
        type(
            "HealthyCv2",
            (),
            {"resize": staticmethod(lambda *args, **kwargs: None), "cvtColor": staticmethod(lambda *args, **kwargs: None)},
        )(),
    )
    monkeypatch.setattr(
        "backend.app.services.pdf_parse.parsers.rapid_ocr.render_pdf_pages",
        lambda file_path: ["page-1-image"],
    )

    class DummyRapidOcrOutput:
        txts = ("alpha line", "beta line")

    class DummyRapidOcr:
        def __call__(self, image):
            return DummyRapidOcrOutput()

    parser = RapidOcrParser(ocr_factory=lambda: DummyRapidOcr())
    result = parser.parse(
        Path("scan.pdf"),
        request=PdfParseRequest(parser_name="rapid_ocr"),
    )

    assert result.parser_name == "rapid_ocr"
    assert result.is_ocr is True
    assert "alpha line" in result.raw_text
    assert "beta line" in result.raw_text


def test_rapid_ocr_parser_honors_cancel_check_between_pages(monkeypatch) -> None:
    from backend.app.errors import AppError
    from backend.app.services.pdf_parse.parsers.rapid_ocr import RapidOcrParser
    from backend.app.services.pdf_parse.types import PdfParseRequest

    monkeypatch.setattr(
        "backend.app.services.pdf_parse.parsers.rapid_ocr.cv2",
        type(
            "HealthyCv2",
            (),
            {"resize": staticmethod(lambda *args, **kwargs: None), "cvtColor": staticmethod(lambda *args, **kwargs: None)},
        )(),
    )
    monkeypatch.setattr(
        "backend.app.services.pdf_parse.parsers.rapid_ocr.render_pdf_pages",
        lambda file_path: ["page-1-image", "page-2-image"],
    )

    class DummyRapidOcrOutput:
        def __init__(self, *txts: str) -> None:
            self.txts = txts

    class DummyRapidOcr:
        def __init__(self) -> None:
            self.calls = 0

        def __call__(self, image):
            self.calls += 1
            return DummyRapidOcrOutput(f"page-{self.calls}")

    cancel_state = {"count": 0}

    def cancel_check() -> bool:
        cancel_state["count"] += 1
        return cancel_state["count"] >= 2

    parser = RapidOcrParser(ocr_factory=lambda: DummyRapidOcr())

    try:
        parser.parse(
            Path("scan.pdf"),
            request=PdfParseRequest(parser_name="rapid_ocr", cancel_check=cancel_check),
        )
    except AppError as exc:
        assert exc.error_category == "CANCELLED"
    else:
        raise AssertionError("Expected OCR parser to stop when cancel_check returns true.")


def test_render_pdf_pages_rasterizes_pages_for_ocr(monkeypatch) -> None:
    from backend.app.services.pdf_parse.parsers.rapid_ocr import render_pdf_pages

    class DummyPixmap:
        width = 2
        height = 1
        n = 3
        samples = bytes([255, 0, 0, 0, 255, 0])

    class DummyPage:
        def get_pixmap(self, matrix, alpha):
            assert alpha is False
            assert matrix is not None
            return DummyPixmap()

    class DummyDocument:
        def __iter__(self):
            yield DummyPage()

    class DummyPymupdf:
        @staticmethod
        def open(file_path):
            assert str(file_path).endswith(".pdf")
            return DummyDocument()

        @staticmethod
        def Matrix(x_scale, y_scale):
            return (x_scale, y_scale)

    monkeypatch.setattr(
        "backend.app.services.pdf_parse.parsers.rapid_ocr.pymupdf",
        DummyPymupdf(),
    )

    pages = render_pdf_pages(Path("scan.pdf"))

    assert len(pages) == 1
    assert isinstance(pages[0], np.ndarray)
    assert pages[0].shape == (1, 2, 3)
    assert pages[0].dtype == np.uint8


def test_render_pdf_pages_reports_missing_render_dependencies(monkeypatch) -> None:
    from backend.app.errors import AppError
    from backend.app.services.pdf_parse.parsers.rapid_ocr import render_pdf_pages

    monkeypatch.setattr(
        "backend.app.services.pdf_parse.parsers.rapid_ocr.pymupdf",
        type("MissingPyMuPdf", (), {"open": None, "Matrix": None})(),
    )

    try:
        render_pdf_pages(Path("scan.pdf"))
    except AppError as exc:
        assert exc.error_category == "INGEST_FAILED"
        assert "PyMuPDF is required for OCR page rendering" in exc.error_message
    else:
        raise AssertionError("Expected missing OCR render dependencies to raise AppError.")


def test_rapid_ocr_parser_reports_missing_dependencies(monkeypatch) -> None:
    from backend.app.errors import AppError
    from backend.app.services.pdf_parse.parsers.rapid_ocr import RapidOcrParser

    monkeypatch.setattr(
        "backend.app.services.pdf_parse.parsers.rapid_ocr.RapidOCR",
        None,
    )

    try:
        RapidOcrParser().parse(Path("scan.pdf"), request=None)
    except AppError as exc:
        assert exc.error_category == "INGEST_FAILED"
        assert "RapidOCR is not installed" in exc.error_message
    else:
        raise AssertionError("Expected parser to raise AppError when RapidOCR is missing.")


def test_rapid_ocr_parser_reports_broken_cv2_runtime(monkeypatch) -> None:
    from backend.app.errors import AppError
    from backend.app.services.pdf_parse.parsers.rapid_ocr import RapidOcrParser

    monkeypatch.setattr(
        "backend.app.services.pdf_parse.parsers.rapid_ocr.cv2",
        type("BrokenCv2", (), {"resize": None, "cvtColor": None})(),
    )
    monkeypatch.setattr(
        "backend.app.services.pdf_parse.parsers.rapid_ocr.render_pdf_pages",
        lambda file_path: ["page-1-image"],
    )

    class DummyRapidOcr:
        def __call__(self, image):
            raise AssertionError("OCR engine should not run when cv2 runtime is broken.")

    try:
        RapidOcrParser(ocr_factory=lambda: DummyRapidOcr()).parse(
            Path("scan.pdf"),
            request=None,
        )
    except AppError as exc:
        assert exc.error_category == "INGEST_FAILED"
        assert "OpenCV" in exc.error_message
    else:
        raise AssertionError("Expected parser to raise AppError when cv2 runtime is broken.")


def test_rapid_ocr_parser_surfaces_import_failure_reason(monkeypatch) -> None:
    from backend.app.errors import AppError
    from backend.app.services.pdf_parse.parsers.rapid_ocr import RapidOcrParser

    monkeypatch.setattr(
        "backend.app.services.pdf_parse.parsers.rapid_ocr.RapidOCR",
        None,
    )
    monkeypatch.setattr(
        "backend.app.services.pdf_parse.parsers.rapid_ocr.RAPID_OCR_IMPORT_ERROR",
        "No module named 'onnxruntime'",
    )

    try:
        RapidOcrParser().parse(Path("scan.pdf"), request=None)
    except AppError as exc:
        assert exc.error_category == "INGEST_FAILED"
        assert "onnxruntime" in exc.error_message
    else:
        raise AssertionError("Expected parser to include the OCR import failure reason.")


def test_rapid_ocr_parser_reuses_engine_instance(monkeypatch) -> None:
    from backend.app.services.pdf_parse.parsers.rapid_ocr import RapidOcrParser

    monkeypatch.setattr(
        "backend.app.services.pdf_parse.parsers.rapid_ocr.render_pdf_pages",
        lambda file_path: ["page-1-image"],
    )
    monkeypatch.setattr(
        "backend.app.services.pdf_parse.parsers.rapid_ocr.cv2",
        type(
            "HealthyCv2",
            (),
            {"resize": staticmethod(lambda *args, **kwargs: None), "cvtColor": staticmethod(lambda *args, **kwargs: None)},
        )(),
    )

    created = {"count": 0}

    class DummyRapidOcrOutput:
        txts = ("alpha line",)

    class DummyRapidOcr:
        def __call__(self, image):
            return DummyRapidOcrOutput()

    def factory():
        created["count"] += 1
        return DummyRapidOcr()

    parser = RapidOcrParser(ocr_factory=factory)

    first = parser.parse(Path("scan.pdf"), request=None)
    second = parser.parse(Path("scan.pdf"), request=None)

    assert created["count"] == 1
    assert first.raw_text == second.raw_text == "alpha line"


def test_render_pdf_pages_uses_lower_scale_for_large_documents(monkeypatch) -> None:
    from backend.app.services.pdf_parse.parsers.rapid_ocr import render_pdf_pages

    matrix_calls = []

    class DummyPixmap:
        width = 2
        height = 1
        n = 3
        samples = bytes([255, 0, 0, 0, 255, 0])

    class DummyPage:
        def get_pixmap(self, matrix, alpha):
            assert alpha is False
            assert matrix is not None
            return DummyPixmap()

    class DummyDocument:
        page_count = 80

        def __iter__(self):
            yield DummyPage()

    class DummyPymupdf:
        @staticmethod
        def open(file_path):
            assert str(file_path).endswith(".pdf")
            return DummyDocument()

        @staticmethod
        def Matrix(x_scale, y_scale):
            matrix_calls.append((x_scale, y_scale))
            return (x_scale, y_scale)

    monkeypatch.setattr(
        "backend.app.services.pdf_parse.parsers.rapid_ocr.pymupdf",
        DummyPymupdf(),
    )

    render_pdf_pages(Path("scan.pdf"))

    assert matrix_calls == [(1.0, 1.0)]


def test_parse_preview_does_not_activate_result(test_db) -> None:
    from backend.app.services.pdf_parse.service import PdfParseService
    from backend.app.services.pdf_parse.types import PdfParseResult

    with test_db.connect() as connection:
        connection.execute(
            """
            INSERT INTO knowledge_items (
              id, source_type, source_value, title, raw_content, source_name, created_at, updated_at
            )
            VALUES ('ki-1', 'pdf', 'doc.pdf', 'Doc', 'old', 'doc.pdf', '2026-04-18T00:00:00Z', '2026-04-18T00:00:00Z')
            """
        )

    service = PdfParseService(parsers={})
    preview_id = service.persist_preview_result(
        db=test_db,
        knowledge_item_id="ki-1",
        result=PdfParseResult(
            parser_name="pymupdf4llm_markdown",
            raw_text="new text",
            markdown_text="# New",
            preview_text="# New",
            page_count=1,
            char_count=7,
            quality_score=0.9,
            warnings=[],
            is_ocr=False,
        ),
    )

    with test_db.connect() as connection:
        row = connection.execute(
            "SELECT active_parse_result_id, raw_content FROM knowledge_items WHERE id = 'ki-1'"
        ).fetchone()

    assert preview_id
    assert row["active_parse_result_id"] is None
    assert row["raw_content"] == "old"


def test_save_preview_activates_result_and_rebuilds_chunks(test_db, monkeypatch) -> None:
    from backend.app.services.pdf_parse.service import PdfParseService

    refreshed = []
    monkeypatch.setattr(
        "backend.app.services.pdf_parse.service.refresh_document_chunks",
        lambda **kwargs: refreshed.append(kwargs["knowledge_item_id"]),
    )

    with test_db.connect() as connection:
        connection.execute(
            """
            INSERT INTO knowledge_items (
              id, source_type, source_value, title, raw_content, source_name, created_at, updated_at
            )
            VALUES ('ki-1', 'pdf', 'doc.pdf', 'Doc', 'old', 'doc.pdf', '2026-04-18T00:00:00Z', '2026-04-18T00:00:00Z')
            """
        )
        connection.execute(
            """
            INSERT INTO document_parse_results (
              id, knowledge_item_id, parser_name, status, raw_text, markdown_text, preview_text,
              page_count, char_count, quality_score, is_ocr, warnings_json, fallback_from,
              fallback_reason, created_at, saved_at
            )
            VALUES (
              'parse-1', 'ki-1', 'rapid_ocr', 'preview', 'ocr body', NULL, 'ocr body',
              1, 8, 0.8, 1, '[]', NULL, NULL, '2026-04-18T00:00:00Z', NULL
            )
            """
        )

    service = PdfParseService(parsers={})
    activated = service.save_preview(
        db=test_db,
        config=None,
        knowledge_item_id="ki-1",
        parse_result_id="parse-1",
    )

    assert activated["status"] == "saved"
    assert refreshed == ["ki-1"]
