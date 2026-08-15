from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from backend.app.errors import AppError
from backend.app.services.pdf_parse.types import PdfParsePage, PdfParseResult

try:
    import numpy as np
except ImportError:  # pragma: no cover - exercised via runtime wiring
    np = None

try:
    from rapidocr import RapidOCR
    RAPID_OCR_IMPORT_ERROR = None
except ImportError:  # pragma: no cover - exercised via runtime wiring
    RapidOCR = None
    RAPID_OCR_IMPORT_ERROR = None
except Exception as exc:  # pragma: no cover - runtime import failures vary by env
    RapidOCR = None
    RAPID_OCR_IMPORT_ERROR = str(exc)

try:
    import pymupdf
except ImportError:  # pragma: no cover - exercised via runtime wiring
    pymupdf = SimpleNamespace(open=None, Matrix=None)

try:
    import cv2
except ImportError:  # pragma: no cover - exercised via runtime wiring
    cv2 = None


def _select_render_scale(page_count: int) -> float:
    if page_count >= 60:
        return 1.0
    if page_count >= 20:
        return 1.25
    return 2.0


def _ensure_cv2_runtime() -> None:
    required_ops = ("resize", "cvtColor")
    if cv2 is None or any(not callable(getattr(cv2, op, None)) for op in required_ops):
        raise AppError(
            status_code=500,
            error_category="INGEST_FAILED",
            error_message=(
                "OpenCV runtime is missing or broken. "
                "Install exactly one compatible OpenCV package, preferably "
                "'opencv-python-headless', and remove conflicting opencv-* packages."
            ),
        )


def render_pdf_pages(file_path: Path) -> list[object]:
    if not callable(getattr(pymupdf, "open", None)) or not callable(
        getattr(pymupdf, "Matrix", None)
    ):
        raise AppError(
            status_code=500,
            error_category="INGEST_FAILED",
            error_message="PyMuPDF is required for OCR page rendering.",
        )
    if np is None:
        raise AppError(
            status_code=500,
            error_category="INGEST_FAILED",
            error_message="NumPy is required for OCR page rendering.",
        )

    document = pymupdf.open(file_path)
    try:
        page_count = int(getattr(document, "page_count", 0) or 0)
        scale = _select_render_scale(page_count)
        matrix = pymupdf.Matrix(scale, scale)
        page_images: list[object] = []
        for page in document:
            pixmap = page.get_pixmap(matrix=matrix, alpha=False)
            channels = max(1, int(getattr(pixmap, "n", 3)))
            image = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(
                pixmap.height,
                pixmap.width,
                channels,
            )
            if channels == 4:
                image = image[:, :, :3]
            elif channels == 1:
                image = np.repeat(image, 3, axis=2)
            page_images.append(image.copy())
        return page_images
    finally:
        close = getattr(document, "close", None)
        if callable(close):
            close()


class RapidOcrParser:
    parser_name = "rapid_ocr"

    def __init__(self, *, ocr_factory=None) -> None:
        self._engine = None
        if ocr_factory is not None:
            self._ocr_factory = ocr_factory
        else:
            self._ocr_factory = self._default_ocr_factory

    def parse(self, file_path: Path, request=None) -> PdfParseResult:
        ocr_engine = self._get_ocr_engine()
        _ensure_cv2_runtime()
        page_images = render_pdf_pages(file_path)
        lines: list[str] = []
        preview_pages: list[PdfParsePage] = []
        cancel_check = getattr(request, "cancel_check", None)
        on_page = getattr(request, "on_page", None)
        total_pages = len(page_images)
        for index, image in enumerate(page_images, start=1):
            self._raise_if_cancelled(cancel_check)
            page_lines = self._extract_lines(ocr_engine(image))
            lines.extend(page_lines)
            page = PdfParsePage(
                page_number=index,
                content_type="text",
                content="\n".join(page_lines),
            )
            preview_pages.append(page)
            if callable(on_page):
                on_page(page, total_pages)

        raw_text = "\n".join(lines)
        return PdfParseResult(
            parser_name=self.parser_name,
            raw_text=raw_text,
            markdown_text=None,
            preview_text=raw_text[:4000],
            page_count=len(page_images),
            char_count=len(raw_text),
            quality_score=0.0,
            warnings=[],
            is_ocr=True,
            preview_pages=preview_pages,
        )

    def _get_ocr_engine(self):
        if self._engine is None:
            self._engine = self._ocr_factory()
        return self._engine

    @staticmethod
    def _default_ocr_factory():
        if RapidOCR is None:
            detail = (
                f" Import failed detail: {RAPID_OCR_IMPORT_ERROR}"
                if RAPID_OCR_IMPORT_ERROR
                else ""
            )
            raise AppError(
                status_code=500,
                error_category="INGEST_FAILED",
                error_message=f"RapidOCR is not installed or failed to import.{detail}",
            )
        return RapidOCR(
            params={
                "Global.use_cls": False,
                "Global.min_height": 20,
                "Det.limit_side_len": 384,
                "Global.max_side_len": 1280,
            }
        )

    @staticmethod
    def _extract_lines(result: object) -> list[str]:
        txts = getattr(result, "txts", None)
        if not isinstance(txts, (list, tuple)):
            return []
        return [text.strip() for text in txts if isinstance(text, str) and text.strip()]

    @staticmethod
    def _raise_if_cancelled(cancel_check) -> None:
        if callable(cancel_check) and cancel_check():
            raise AppError(
                status_code=409,
                error_category="CANCELLED",
                error_message="PDF reparse was cancelled.",
            )

    @staticmethod
    def _looks_like_ocr_line(candidate: object) -> bool:
        return (
            isinstance(candidate, (list, tuple))
            and len(candidate) == 2
            and isinstance(candidate[1], (list, tuple))
            and len(candidate[1]) == 2
            and isinstance(candidate[1][0], str)
        )
