from __future__ import annotations

from backend.app.services.pdf_parse.types import PdfParseQuality


def evaluate_parse_quality(
    *,
    parser_name: str,
    raw_text: str,
    markdown_text: str | None,
    page_count: int,
) -> PdfParseQuality:
    del parser_name

    char_count = len(raw_text.strip())
    avg_chars_per_page = char_count / max(page_count, 1)
    warnings: list[str] = []

    if char_count < 10:
        return PdfParseQuality(
            score=0.0,
            should_fallback_to_ocr=True,
            fallback_reason="low_char_count",
            warnings=["very little extractable text"],
        )

    has_markdown_body = bool(
        markdown_text and len(markdown_text.replace("#", "").strip()) >= 10
    )
    if avg_chars_per_page < 20:
        warnings.append("low characters per page")

    should_fallback = avg_chars_per_page < 20 and not has_markdown_body
    return PdfParseQuality(
        score=1.0 if has_markdown_body else 0.6,
        should_fallback_to_ocr=should_fallback,
        fallback_reason="low_body_signal" if should_fallback else None,
        warnings=warnings,
    )
