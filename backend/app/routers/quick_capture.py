from __future__ import annotations

from fastapi import APIRouter

from backend.app.schemas.quick_capture import (
    QuickCaptureOcrRequest,
    QuickCaptureOcrResponse,
)
from backend.app.services.quick_capture_ocr import extract_text_from_screenshot


router = APIRouter(prefix="/quick-capture", tags=["quick-capture"])


@router.post("/ocr", response_model=QuickCaptureOcrResponse)
def ocr_quick_capture(payload: QuickCaptureOcrRequest) -> QuickCaptureOcrResponse:
    result = extract_text_from_screenshot(payload.image_base64)
    return QuickCaptureOcrResponse(**result)
