from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from backend.app.errors import AppError
from backend.app.services.fine_job.resume_text import clean_resume_text
from backend.app.services.pdf_parse.types import PdfParsePage, PdfParseResult

try:
    import numpy as np
except ImportError:  # pragma: no cover - 通过运行时装配覆盖
    np = None

try:
    from rapidocr import RapidOCR
    RAPID_OCR_IMPORT_ERROR = None
except ImportError:  # pragma: no cover - 通过运行时装配覆盖
    RapidOCR = None
    RAPID_OCR_IMPORT_ERROR = None
except Exception as exc:  # pragma: no cover - 不同环境的运行时导入失败情况不同
    RapidOCR = None
    RAPID_OCR_IMPORT_ERROR = str(exc)

try:
    import pymupdf
except ImportError:  # pragma: no cover - 通过运行时装配覆盖
    pymupdf = SimpleNamespace(open=None, Matrix=None)

try:
    import cv2
except ImportError:  # pragma: no cover - 通过运行时装配覆盖
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

        raw_text = clean_resume_text("\n".join(lines))
        return PdfParseResult(
            parser_name=self.parser_name,
            raw_text=raw_text,
            markdown_text=None,
            preview_text=raw_text,
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
        texts = [text.strip() for text in txts if isinstance(text, str) and text.strip()]
        boxes = getattr(result, "boxes", None)
        if not isinstance(boxes, (list, tuple)) and not hasattr(boxes, "__len__"):
            return texts
        if len(boxes) != len(txts):
            return texts

        positioned: list[tuple[str, float, float, float]] = []
        for text, box in zip(txts, boxes):
            if not isinstance(text, str) or not text.strip():
                continue
            geometry = RapidOcrParser._box_geometry(box)
            if geometry is None:
                return texts
            left, top, height = geometry
            positioned.append((text.strip(), left, top, height))
        if not positioned:
            return texts

        median_height = sorted(item[3] for item in positioned)[len(positioned) // 2]
        row_tolerance = max(6.0, median_height * 0.6)
        rows: list[dict[str, object]] = []
        for item in sorted(positioned, key=lambda value: (value[2], value[1])):
            row = next(
                (candidate for candidate in rows if abs(item[2] - float(candidate["top"])) <= row_tolerance),
                None,
            )
            if row is None:
                rows.append({"top": item[2], "items": [item]})
            else:
                row["items"].append(item)

        ordered: list[str] = []
        for row in sorted(rows, key=lambda candidate: float(candidate["top"])):
            items = sorted(row["items"], key=lambda value: value[1])
            # 同一文字行的多个识别框合并，避免姓名、公司名和职位被拆成多行。
            ordered.append(" ".join(item[0] for item in items))
        return ordered

    @staticmethod
    def _box_geometry(box: object) -> tuple[float, float, float] | None:
        points = box.tolist() if hasattr(box, "tolist") else box
        if not isinstance(points, (list, tuple)) or len(points) < 2:
            return None
        coordinates: list[tuple[float, float]] = []
        for point in points:
            values = point.tolist() if hasattr(point, "tolist") else point
            if not isinstance(values, (list, tuple)) or len(values) < 2:
                return None
            try:
                coordinates.append((float(values[0]), float(values[1])))
            except (TypeError, ValueError):
                return None
        left = min(point[0] for point in coordinates)
        top = min(point[1] for point in coordinates)
        bottom = max(point[1] for point in coordinates)
        return left, top, max(1.0, bottom - top)

    @staticmethod
    def _raise_if_cancelled(cancel_check) -> None:
        if callable(cancel_check) and cancel_check():
            raise AppError(
                status_code=409,
                error_category="CANCELLED",
                error_message="PDF reparse was cancelled.",
            )

