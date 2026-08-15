from __future__ import annotations

from backend.app.services.web_capture.image_ocr import (
    _decode_image,
    _default_ocr_factory,
    _extract_lines,
)
from backend.app.utils import utc_now


def extract_text_from_screenshot(image_base64: str) -> dict[str, object]:
    engine = _default_ocr_factory()
    image = _decode_image(image_base64)
    result = engine(image)
    raw_text = "\n".join(_extract_lines(result)).strip()
    return {
        "raw_text": raw_text,
        "captured_at": utc_now(),
        "warnings": [],
    }
