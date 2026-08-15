from __future__ import annotations

from fastapi import APIRouter, Depends, status

from backend.app.config import AppConfig
from backend.app.db import Database
from backend.app.dependencies import get_config, get_database
from backend.app.errors import AppError
from backend.app.schemas.pdf_parse import PdfParseResultEnvelope, PdfReparseRequest
from backend.app.services.pdf_parse.service import build_default_pdf_parse_service
from backend.app.services.pdf_parse.store import (
    get_active_parse_result as fetch_active_parse_result,
)


router = APIRouter(prefix="/pdf", tags=["pdf"])


def get_active_parse_result(db: Database, knowledge_item_id: str) -> dict[str, object]:
    with db.connect() as connection:
        row = fetch_active_parse_result(
            connection,
            knowledge_item_id=knowledge_item_id,
        )
    if row is None:
        raise AppError(
            status_code=404,
            error_category="VALIDATION_FAILED",
            error_message="Active parse result not found.",
        )
    return {
        "id": row["id"],
        "parser_name": row["parser_name"],
        "status": row["status"],
        "preview_text": row["preview_text"],
    }


def create_pdf_parse_preview(
    db: Database,
    config: AppConfig,
    knowledge_item_id: str,
    parser_name: str,
) -> dict[str, object]:
    with db.connect() as connection:
        item = connection.execute(
            """
            SELECT source_type, source_value
            FROM knowledge_items
            WHERE id = ?
            """,
            (knowledge_item_id,),
        ).fetchone()
    if item is None or item["source_type"] != "pdf":
        raise AppError(
            status_code=404,
            error_category="VALIDATION_FAILED",
            error_message="PDF knowledge item not found.",
        )
    service = build_default_pdf_parse_service(config)
    return service.create_preview(
        db=db,
        knowledge_item_id=knowledge_item_id,
        file_path=str(item["source_value"]),
        parser_name=parser_name,
    )


def save_pdf_parse_result(
    db: Database,
    config: AppConfig,
    knowledge_item_id: str,
    parse_result_id: str,
) -> dict[str, object]:
    service = build_default_pdf_parse_service(config)
    return service.save_preview(
        db=db,
        config=config,
        knowledge_item_id=knowledge_item_id,
        parse_result_id=parse_result_id,
    )


@router.get(
    "/items/{knowledge_item_id}/parse-result",
    response_model=PdfParseResultEnvelope,
)
def get_pdf_parse_result(
    knowledge_item_id: str,
    db: Database = Depends(get_database),
) -> PdfParseResultEnvelope:
    return PdfParseResultEnvelope(
        parse_result=get_active_parse_result(db, knowledge_item_id)
    )


@router.post(
    "/items/{knowledge_item_id}/reparse",
    response_model=PdfParseResultEnvelope,
    status_code=status.HTTP_202_ACCEPTED,
)
def reparse_pdf_item(
    knowledge_item_id: str,
    payload: PdfReparseRequest,
    db: Database = Depends(get_database),
    config: AppConfig = Depends(get_config),
) -> PdfParseResultEnvelope:
    return PdfParseResultEnvelope(
        parse_result=create_pdf_parse_preview(
            db,
            config,
            knowledge_item_id,
            payload.parser_name,
        )
    )


@router.post(
    "/items/{knowledge_item_id}/parse-results/{parse_result_id}/save",
    response_model=PdfParseResultEnvelope,
)
def save_pdf_item_parse_result(
    knowledge_item_id: str,
    parse_result_id: str,
    db: Database = Depends(get_database),
    config: AppConfig = Depends(get_config),
) -> PdfParseResultEnvelope:
    return PdfParseResultEnvelope(
        parse_result=save_pdf_parse_result(
            db,
            config,
            knowledge_item_id,
            parse_result_id,
        )
    )
