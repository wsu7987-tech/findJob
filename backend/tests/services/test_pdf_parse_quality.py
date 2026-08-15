from __future__ import annotations

from backend.app.services.pdf_parse.quality import evaluate_parse_quality


def test_evaluate_parse_quality_recommends_ocr_for_nearly_empty_text() -> None:
    result = evaluate_parse_quality(
        parser_name="pymupdf4llm_markdown",
        raw_text="  ",
        markdown_text="",
        page_count=3,
    )

    assert result.should_fallback_to_ocr is True
    assert result.fallback_reason == "low_char_count"


def test_evaluate_parse_quality_accepts_reasonable_markdown_body() -> None:
    result = evaluate_parse_quality(
        parser_name="pymupdf4llm_markdown",
        raw_text="Alpha\n\nBeta\n\nGamma",
        markdown_text="# Title\n\nAlpha\n\nBeta\n\nGamma",
        page_count=1,
    )

    assert result.should_fallback_to_ocr is False
    assert result.score > 0.0
