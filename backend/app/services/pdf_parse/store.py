from __future__ import annotations

import json
from sqlite3 import Connection, Row

from backend.app.utils import new_id


def insert_parse_result(
    connection: Connection,
    *,
    knowledge_item_id: str,
    parser_name: str,
    status: str,
    raw_text: str,
    markdown_text: str | None,
    preview_text: str,
    page_count: int,
    char_count: int,
    quality_score: float,
    is_ocr: bool,
    warnings: list[str],
    fallback_from: str | None,
    fallback_reason: str | None,
    created_at: str,
    saved_at: str | None,
) -> str:
    parse_result_id = new_id()
    connection.execute(
        """
        INSERT INTO document_parse_results (
          id,
          knowledge_item_id,
          parser_name,
          status,
          raw_text,
          markdown_text,
          preview_text,
          page_count,
          char_count,
          quality_score,
          is_ocr,
          warnings_json,
          fallback_from,
          fallback_reason,
          created_at,
          saved_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            parse_result_id,
            knowledge_item_id,
            parser_name,
            status,
            raw_text,
            markdown_text,
            preview_text,
            page_count,
            char_count,
            quality_score,
            1 if is_ocr else 0,
            json.dumps(warnings),
            fallback_from,
            fallback_reason,
            created_at,
            saved_at,
        ),
    )
    return parse_result_id


def activate_parse_result(
    connection: Connection,
    *,
    knowledge_item_id: str,
    parse_result_id: str,
    raw_content: str,
    saved_at: str,
) -> None:
    connection.execute(
        """
        UPDATE document_parse_results
        SET status = 'saved', saved_at = ?
        WHERE id = ? AND knowledge_item_id = ?
        """,
        (saved_at, parse_result_id, knowledge_item_id),
    )
    connection.execute(
        """
        UPDATE knowledge_items
        SET active_parse_result_id = ?, raw_content = ?, updated_at = ?
        WHERE id = ?
        """,
        (parse_result_id, raw_content, saved_at, knowledge_item_id),
    )


def get_active_parse_result(
    connection: Connection,
    *,
    knowledge_item_id: str,
) -> Row | None:
    return connection.execute(
        """
        SELECT dpr.*
        FROM knowledge_items AS ki
        JOIN document_parse_results AS dpr ON dpr.id = ki.active_parse_result_id
        WHERE ki.id = ?
        """,
        (knowledge_item_id,),
    ).fetchone()
