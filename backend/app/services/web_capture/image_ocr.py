from __future__ import annotations

import base64
from typing import Any

from backend.app.errors import AppError

try:
    import numpy as np
except ImportError:  # pragma: no cover - runtime dependency
    np = None

try:
    import cv2
except ImportError:  # pragma: no cover - runtime dependency
    cv2 = None

try:
    from rapidocr import RapidOCR
    RAPID_OCR_IMPORT_ERROR = None
except ImportError:  # pragma: no cover - runtime dependency
    RapidOCR = None
    RAPID_OCR_IMPORT_ERROR = None
except Exception as exc:  # pragma: no cover - runtime dependency
    RapidOCR = None
    RAPID_OCR_IMPORT_ERROR = str(exc)


_MIN_WIDTH = 64
_MIN_HEIGHT = 64


def filter_ocr_candidates(elements: list[dict[str, Any]] | list[Any]) -> list[Any]:
    candidates: list[Any] = []
    for element in elements:
        if _is_decorative(element):
            continue
        width = _as_number(_element_value(element, "width"))
        height = _as_number(_element_value(element, "height"))
        if width is None or height is None:
            continue
        if width < _MIN_WIDTH or height < _MIN_HEIGHT:
            continue
        candidates.append(element)
    return candidates


def extract_ocr_segments(
    elements: list[dict[str, Any]] | list[Any],
    *,
    ocr_factory=None,
    image_decoder=None,
) -> list[dict[str, str]]:
    if not elements:
        return []

    engine = (ocr_factory or _default_ocr_factory)()
    decoder = image_decoder or _decode_image
    segments: list[dict[str, str]] = []
    for element in elements:
        screenshot_base64 = _element_value(element, "screenshot_base64")
        if not screenshot_base64:
            continue
        image = decoder(str(screenshot_base64))
        result = engine(image)
        text = "\n".join(_extract_lines(result)).strip()
        if not text:
            continue
        segments.append(
            {
                "anchor_text": str(_element_value(element, "anchor_text") or "").strip(),
                "text": text,
            }
        )
    return segments


def _is_decorative(element: dict[str, Any] | Any) -> bool:
    decorative = _element_value(element, "decorative")
    if isinstance(decorative, str):
        return decorative.strip().lower() in {"1", "true", "yes"}
    return bool(decorative)


def _element_value(element: dict[str, Any] | Any, key: str) -> Any:
    if isinstance(element, dict):
        return element.get(key)
    return getattr(element, key, None)


def _as_number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


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


def _decode_image(screenshot_base64: str):
    if np is None or cv2 is None:
        raise AppError(
            status_code=500,
            error_category="INGEST_FAILED",
            error_message="NumPy and OpenCV are required for web image OCR.",
        )
    binary = base64.b64decode(screenshot_base64)
    array = np.frombuffer(binary, dtype=np.uint8)
    image = cv2.imdecode(array, cv2.IMREAD_COLOR)
    if image is None:
        raise AppError(
            status_code=500,
            error_category="INGEST_FAILED",
            error_message="Failed to decode OCR image screenshot.",
        )
    return image


def _extract_lines(result: object) -> list[str]:
    txts = getattr(result, "txts", None)
    if isinstance(txts, (list, tuple)):
        return [text.strip() for text in txts if isinstance(text, str) and text.strip()]

    if isinstance(result, tuple) and len(result) >= 2 and isinstance(result[1], (list, tuple)):
        return [text.strip() for text in result[1] if isinstance(text, str) and text.strip()]

    return []
