from __future__ import annotations

import json

from fastapi import APIRouter, Depends

from backend.app.db import Database
from backend.app.dependencies import get_database
from backend.app.errors import AppError
from backend.app.schemas.parse_results import (
    ActiveParseResultEnvelope,
    ActiveParseResultResponse,
)


router = APIRouter(prefix="/items", tags=["parse-results"])


@router.get("/{knowledge_item_id}/parse-result", response_model=ActiveParseResultEnvelope)
def get_active_parse_result(
    knowledge_item_id: str,
    db: Database = Depends(get_database),
) -> ActiveParseResultEnvelope:
    with db.connect() as connection:
        row = connection.execute(
            """
            SELECT
              ki.id AS knowledge_item_id,
              ki.source_type,
              ki.source_value,
              ki.title,
              ki.raw_content AS canonical_content,
              dpr.id,
              dpr.parser_name,
              dpr.status,
              dpr.raw_text,
              dpr.markdown_text,
              dpr.preview_text,
              dpr.page_count,
              dpr.char_count,
              dpr.quality_score,
              dpr.is_ocr,
              dpr.warnings_json,
              dpr.fallback_from,
              dpr.fallback_reason,
              dpr.created_at,
              dpr.saved_at
            FROM knowledge_items AS ki
            JOIN document_parse_results AS dpr ON dpr.id = ki.active_parse_result_id
            WHERE ki.id = ?
            """,
            (knowledge_item_id,),
        ).fetchone()

    if row is None:
        raise AppError(
            status_code=404,
            error_category="VALIDATION_FAILED",
            error_message="Active parse result not found.",
        )

    return ActiveParseResultEnvelope(
        parse_result=ActiveParseResultResponse(
            knowledge_item_id=str(row["knowledge_item_id"]),
            source_type=str(row["source_type"]),
            source_value=str(row["source_value"]),
            title=row["title"],
            canonical_content=str(row["canonical_content"] or ""),
            id=str(row["id"]),
            parser_name=str(row["parser_name"]),
            status=str(row["status"]),
            raw_text=str(row["raw_text"]),
            markdown_text=(
                None if row["markdown_text"] is None else str(row["markdown_text"])
            ),
            preview_text=str(row["preview_text"]),
            page_count=int(row["page_count"]),
            char_count=int(row["char_count"]),
            quality_score=float(row["quality_score"]),
            is_ocr=bool(row["is_ocr"]),
            warnings=_parse_warnings(row["warnings_json"]),
            fallback_from=row["fallback_from"],
            fallback_reason=row["fallback_reason"],
            created_at=str(row["created_at"]),
            saved_at=None if row["saved_at"] is None else str(row["saved_at"]),
        )
    )


def _parse_warnings(value: object) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, str):
        parsed = json.loads(value)
        if isinstance(parsed, list):
            return [str(item) for item in parsed]
    return []
