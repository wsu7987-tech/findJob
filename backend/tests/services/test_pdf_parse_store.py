from __future__ import annotations

from backend.app.services.pdf_parse.store import (
    activate_parse_result,
    insert_parse_result,
)


def test_insert_parse_result_persists_preview_row(test_db) -> None:
    with test_db.connect() as connection:
        connection.execute(
            """
            INSERT INTO knowledge_items (
              id, source_type, source_value, title, raw_content, source_name, created_at, updated_at
            )
            VALUES ('ki-1', 'pdf', 'doc.pdf', 'Doc', '', 'doc.pdf', '2026-04-18T00:00:00Z', '2026-04-18T00:00:00Z')
            """
        )

        parse_result_id = insert_parse_result(
            connection,
            knowledge_item_id="ki-1",
            parser_name="pymupdf4llm_markdown",
            status="preview",
            raw_text="plain text",
            markdown_text="# Title\n\nBody",
            preview_text="# Title\n\nBody",
            page_count=2,
            char_count=10,
            quality_score=0.9,
            is_ocr=False,
            warnings=[],
            fallback_from=None,
            fallback_reason=None,
            created_at="2026-04-18T00:00:00Z",
            saved_at=None,
        )

        row = connection.execute(
            "SELECT status, saved_at FROM document_parse_results WHERE id = ?",
            (parse_result_id,),
        ).fetchone()

    assert row["status"] == "preview"
    assert row["saved_at"] is None


def test_activate_parse_result_updates_active_pointer(test_db) -> None:
    with test_db.connect() as connection:
        connection.execute(
            """
            INSERT INTO knowledge_items (
              id, source_type, source_value, title, raw_content, source_name, created_at, updated_at
            )
            VALUES ('ki-1', 'pdf', 'doc.pdf', 'Doc', '', 'doc.pdf', '2026-04-18T00:00:00Z', '2026-04-18T00:00:00Z')
            """
        )
        parse_result_id = insert_parse_result(
            connection,
            knowledge_item_id="ki-1",
            parser_name="rapid_ocr",
            status="preview",
            raw_text="ocr text",
            markdown_text=None,
            preview_text="ocr text",
            page_count=1,
            char_count=8,
            quality_score=0.8,
            is_ocr=True,
            warnings=["ocr fallback"],
            fallback_from="pymupdf4llm_markdown",
            fallback_reason="low_char_count",
            created_at="2026-04-18T00:00:00Z",
            saved_at=None,
        )

        activate_parse_result(
            connection,
            knowledge_item_id="ki-1",
            parse_result_id=parse_result_id,
            raw_content="ocr text",
            saved_at="2026-04-18T00:05:00Z",
        )

        item = connection.execute(
            "SELECT active_parse_result_id, raw_content FROM knowledge_items WHERE id = 'ki-1'"
        ).fetchone()

    assert item["active_parse_result_id"] == parse_result_id
    assert item["raw_content"] == "ocr text"
