from __future__ import annotations

import re

from backend.app.services.pdf_parse.types import PdfParseQuality


_MEANINGFUL_CHAR_RE = re.compile(r"[A-Za-z0-9\u3400-\u9fff]")


def evaluate_parse_quality(
    *,
    parser_name: str,
    raw_text: str,
    markdown_text: str | None,
    page_count: int,
) -> PdfParseQuality:
    del parser_name

    normalized_text = raw_text.strip()
    char_count = len(normalized_text)
    meaningful_char_count = len(_MEANINGFUL_CHAR_RE.findall(normalized_text))
    avg_chars_per_page = char_count / max(page_count, 1)
    warnings: list[str] = []

    if char_count < 10:
        return PdfParseQuality(
            score=0.0,
            should_fallback_to_ocr=True,
            fallback_reason="low_char_count",
            warnings=["very little extractable text"],
        )

    markdown_body = markdown_text or ""
    markdown_meaningful_count = len(_MEANINGFUL_CHAR_RE.findall(markdown_body))
    has_markdown_body = markdown_meaningful_count >= 10
    if avg_chars_per_page < 20:
        warnings.append("low characters per page")
    if meaningful_char_count < max(10, int(char_count * 0.35)):
        warnings.append("text contains little readable content")

    should_fallback = avg_chars_per_page < 20 and not has_markdown_body
    readable_ratio = meaningful_char_count / max(char_count, 1)
    score = 1.0 if has_markdown_body and readable_ratio >= 0.65 else 0.6
    if meaningful_char_count < 10:
        score = 0.0
    return PdfParseQuality(
        score=score,
        should_fallback_to_ocr=should_fallback,
        fallback_reason="low_body_signal" if should_fallback else None,
        warnings=warnings,
    )
