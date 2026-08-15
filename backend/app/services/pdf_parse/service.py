from __future__ import annotations

from pathlib import Path

from backend.app.config import AppConfig
from backend.app.db import Database
from backend.app.errors import AppError
from backend.app.services.document_refresh import refresh_document_chunks
from backend.app.services.pdf_parse.parsers.pymupdf_markdown import (
    PymupdfMarkdownParser,
)
from backend.app.services.pdf_parse.parsers.rapid_ocr import RapidOcrParser
from backend.app.services.pdf_parse.quality import evaluate_parse_quality
from backend.app.services.pdf_parse.store import (
    activate_parse_result,
    insert_parse_result,
)
from backend.app.services.pdf_parse.types import PdfParseRequest, PdfParseResult
from backend.app.utils import utc_now


class PdfParseService:
    def __init__(self, *, parsers: dict[str, object]) -> None:
        self._parsers = parsers

    def create_preview(
        self,
        *,
        db: Database,
        knowledge_item_id: str,
        file_path: str,
        parser_name: str,
    ) -> dict[str, object]:
        result = self.parse_file(
            file_path=file_path,
            parser_name=parser_name,
            knowledge_item_id=knowledge_item_id,
        )
        preview_id = self.persist_preview_result(
            db=db,
            knowledge_item_id=knowledge_item_id,
            result=result,
        )
        return {
            "id": preview_id,
            "parser_name": result.parser_name,
            "status": "preview",
            "preview_text": result.preview_text,
            "warning": "当前解析结果未保存前不会生效",
        }

    def parse_file(
        self,
        *,
        file_path: str | Path,
        parser_name: str,
        knowledge_item_id: str | None = None,
        cancel_check=None,
        on_page=None,
    ) -> PdfParseResult:
        path = Path(file_path)
        if parser_name == "auto":
            primary = self._parse_with_parser(
                parser_name="pymupdf4llm_markdown",
                file_path=path,
                knowledge_item_id=knowledge_item_id,
                cancel_check=cancel_check,
                on_page=on_page,
            )
            quality = evaluate_parse_quality(
                parser_name=primary.parser_name,
                raw_text=primary.raw_text,
                markdown_text=primary.markdown_text,
                page_count=primary.page_count,
            )
            if quality.should_fallback_to_ocr and "rapid_ocr" in self._parsers:
                fallback = self._parse_with_parser(
                    parser_name="rapid_ocr",
                    file_path=path,
                    knowledge_item_id=knowledge_item_id,
                    cancel_check=cancel_check,
                    on_page=on_page,
                )
                fallback.fallback_from = primary.parser_name
                fallback.fallback_reason = quality.fallback_reason
                return fallback
            return primary

        return self._parse_with_parser(
            parser_name=parser_name,
            file_path=path,
            knowledge_item_id=knowledge_item_id,
            cancel_check=cancel_check,
            on_page=on_page,
        )

    def persist_preview_result(
        self,
        *,
        db: Database,
        knowledge_item_id: str,
        result: PdfParseResult,
    ) -> str:
        quality = evaluate_parse_quality(
            parser_name=result.parser_name,
            raw_text=result.raw_text,
            markdown_text=result.markdown_text,
            page_count=result.page_count,
        )
        with db.connect() as connection:
            return insert_parse_result(
                connection,
                knowledge_item_id=knowledge_item_id,
                parser_name=result.parser_name,
                status="preview",
                raw_text=result.raw_text,
                markdown_text=result.markdown_text,
                preview_text=result.preview_text,
                page_count=result.page_count,
                char_count=result.char_count,
                quality_score=quality.score,
                is_ocr=result.is_ocr,
                warnings=[*result.warnings, *quality.warnings],
                fallback_from=result.fallback_from,
                fallback_reason=result.fallback_reason or quality.fallback_reason,
                created_at=utc_now(),
                saved_at=None,
            )

    def save_preview(
        self,
        *,
        db: Database,
        config: AppConfig,
        knowledge_item_id: str,
        parse_result_id: str,
    ) -> dict[str, object]:
        with db.connect() as connection:
            row = connection.execute(
                """
                SELECT parser_name, raw_text, preview_text
                FROM document_parse_results
                WHERE id = ? AND knowledge_item_id = ?
                """,
                (parse_result_id, knowledge_item_id),
            ).fetchone()
            if row is None:
                raise AppError(
                    status_code=404,
                    error_category="VALIDATION_FAILED",
                    error_message="Parse result not found.",
                )

            saved_at = utc_now()
            activate_parse_result(
                connection,
                knowledge_item_id=knowledge_item_id,
                parse_result_id=parse_result_id,
                raw_content=row["raw_text"],
                saved_at=saved_at,
            )
            refresh_document_chunks(
                connection=connection,
                config=config,
                knowledge_item_id=knowledge_item_id,
                raw_content=row["raw_text"],
            )

        return {
            "id": parse_result_id,
            "parser_name": row["parser_name"],
            "status": "saved",
            "preview_text": row["preview_text"],
        }

    def _parse_with_parser(
        self,
        *,
        parser_name: str,
        file_path: Path,
        knowledge_item_id: str | None,
        cancel_check=None,
        on_page=None,
    ) -> PdfParseResult:
        parser = self._parsers.get(parser_name)
        if parser is None:
            raise AppError(
                status_code=400,
                error_category="VALIDATION_FAILED",
                error_message=f"Unsupported PDF parser: {parser_name}",
            )
        return parser.parse(
            file_path,
            PdfParseRequest(
                parser_name=parser_name,
                knowledge_item_id=knowledge_item_id,
                cancel_check=cancel_check,
                on_page=on_page,
            ),
        )


def build_default_pdf_parse_service(config: AppConfig) -> PdfParseService:
    del config
    return PdfParseService(
        parsers={
            "pymupdf4llm_markdown": PymupdfMarkdownParser(),
            "rapid_ocr": RapidOcrParser(),
        }
    )
