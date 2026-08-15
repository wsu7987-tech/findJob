from __future__ import annotations

from pathlib import Path
from threading import Thread
from time import sleep
from typing import Callable

from backend.app.config import AppConfig
from backend.app.db import Database
from backend.app.errors import AppError
from backend.app.services.pdf_draft_store import (
    PdfDraft,
    PdfDraftParseResult,
    PdfDraftStore,
)
from backend.app.services.pdf_parse.quality import evaluate_parse_quality
from backend.app.services.pdf_parse.service import build_default_pdf_parse_service
from backend.app.services.pdf_parse.types import PdfParsePage
from backend.app.services.pdf_reparse_job_store import PdfReparseJob, PdfReparseJobStore
from backend.app.services.pool import create_pool_item_from_saved_pdf_content


class PdfDraftService:
    def __init__(
        self,
        *,
        draft_store: PdfDraftStore,
        job_store: PdfReparseJobStore | None = None,
        parse_service: object,
        commit_pool_item: Callable[..., dict[str, object]] = create_pool_item_from_saved_pdf_content,
    ) -> None:
        self._draft_store = draft_store
        self._job_store = job_store or PdfReparseJobStore()
        self._parse_service = parse_service
        self._commit_pool_item = commit_pool_item

    def create_draft(self, *, file_path: str, title: str | None) -> PdfDraft:
        result = self._parse_service.parse_file(
            file_path=file_path,
            parser_name="auto",
            knowledge_item_id=None,
        )
        path = Path(file_path)
        return self._draft_store.create_draft(
            file_path=str(path),
            title=title,
            source_name=path.name,
            parse_result=self._build_parse_result_payload(result=result, status="saved"),
        )

    def start_create_draft(self, *, file_path: str, title: str | None) -> tuple[PdfDraft, PdfReparseJob]:
        path = Path(file_path)
        draft = self._draft_store.create_shell_draft(
            file_path=str(path),
            title=title,
            source_name=path.name,
        )
        preview_result = self._draft_store.add_parse_result(
            draft_id=draft.id,
            parse_result={
                "parser_name": "auto",
                "status": "running",
                "raw_text": "",
                "markdown_text": None,
                "preview_text": "",
                "page_count": 0,
                "char_count": 0,
                "quality_score": 0.0,
                "is_ocr": False,
                "warnings": [],
                "fallback_from": None,
                "fallback_reason": None,
                "preview_pages": [],
            },
        )
        request_id = self._draft_store.begin_reparse(draft.id)
        job = self._job_store.create_job(draft_id=draft.id, parser_name="auto")
        self._job_store.mark_running(
            job.id,
            total_pages=0,
            preview_result_id=preview_result.id,
        )
        worker = Thread(
            target=self._run_parse_job,
            args=(job.id, draft.id, request_id, draft.file_path, "auto", preview_result.id, "saved"),
            daemon=True,
        )
        worker.start()
        return self._require_draft(draft.id), job

    def get_draft(self, draft_id: str) -> PdfDraft | None:
        return self._draft_store.get_draft(draft_id)

    def start_reparse_draft(self, draft_id: str, *, parser_name: str) -> PdfReparseJob:
        draft = self._require_draft(draft_id)
        preview_result = self._draft_store.add_parse_result(
            draft_id=draft_id,
            parse_result={
                "parser_name": parser_name,
                "status": "running",
                "raw_text": "",
                "markdown_text": None,
                "preview_text": "",
                "page_count": 0,
                "char_count": 0,
                "quality_score": 0.0,
                "is_ocr": parser_name == "rapid_ocr",
                "warnings": [],
                "fallback_from": None,
                "fallback_reason": None,
                "preview_pages": [],
            },
        )
        request_id = self._draft_store.begin_reparse(draft_id)
        job = self._job_store.create_job(draft_id=draft_id, parser_name=parser_name)
        self._job_store.mark_running(
            job.id,
            total_pages=0,
            preview_result_id=preview_result.id,
        )

        worker = Thread(
            target=self._run_parse_job,
            args=(job.id, draft_id, request_id, draft.file_path, parser_name, preview_result.id, "preview"),
            daemon=True,
        )
        worker.start()
        return job

    def reparse_draft(self, draft_id: str, *, parser_name: str) -> PdfDraftParseResult:
        job = self.start_reparse_draft(draft_id, parser_name=parser_name)
        while True:
            latest = self._job_store.get_job(job.id)
            if latest is None:
                raise AppError(
                    status_code=404,
                    error_category="VALIDATION_FAILED",
                    error_message="PDF reparse job not found.",
                )
            if latest.status in {"completed", "failed", "cancelled"}:
                break
            sleep(0.01)

        draft = self._require_draft(draft_id)
        parse_result_id = latest.preview_result_id or draft.latest_preview_result_id
        if parse_result_id is None:
            raise AppError(
                status_code=500,
                error_category="INGEST_FAILED",
                error_message="PDF reparse completed without a preview result.",
            )
        for item in draft.parse_results:
            if item.id == parse_result_id:
                return item
        raise AppError(
            status_code=500,
            error_category="INGEST_FAILED",
            error_message="PDF reparse preview result is missing.",
        )

    def get_job(self, draft_id: str, job_id: str) -> PdfReparseJob:
        self._require_draft(draft_id)
        job = self._job_store.get_job(job_id)
        if job is None or job.draft_id != draft_id:
            raise AppError(
                status_code=404,
                error_category="VALIDATION_FAILED",
                error_message="PDF reparse job not found.",
            )
        return job

    def list_jobs(self, *, active_only: bool = False) -> list[PdfReparseJob]:
        return self._job_store.list_jobs(active_only=active_only)

    def cancel_job(self, draft_id: str, job_id: str) -> PdfReparseJob:
        self._require_draft(draft_id)
        job = self._job_store.request_cancel(job_id)
        if job is None or job.draft_id != draft_id:
            raise AppError(
                status_code=404,
                error_category="VALIDATION_FAILED",
                error_message="PDF reparse job not found.",
            )
        self._draft_store.cancel_reparse(draft_id)
        return job

    def get_preview_page(
        self,
        *,
        draft_id: str,
        parse_result_id: str,
        page_number: int,
    ):
        draft = self._require_draft(draft_id)
        parse_result = self._require_parse_result(draft, parse_result_id)
        page = self._draft_store.get_preview_page(draft_id, parse_result_id, page_number)
        if page is None:
            self._rehydrate_preview_pages(draft, parse_result)
            page = self._draft_store.get_preview_page(draft_id, parse_result_id, page_number)
        if page is None:
            raise AppError(
                status_code=500,
                error_category="INGEST_FAILED",
                error_message="Preview pages are missing for this parse result.",
            )
        return page

    def save_parse_result(self, draft_id: str, parse_result_id: str) -> PdfDraft:
        draft = self._require_draft(draft_id)
        if not any(item.id == parse_result_id for item in draft.parse_results):
            raise AppError(
                status_code=404,
                error_category="VALIDATION_FAILED",
                error_message="Draft parse result not found.",
            )
        self._draft_store.promote_saved_result(draft_id, parse_result_id)
        return self._require_draft(draft_id)

    def commit_draft(
        self,
        *,
        db: Database,
        config: AppConfig,
        draft_id: str,
        category: str | None = None,
        tags: list[str] | None = None,
        cleaned_text: str | None = None,
        cleaning_level: str | None = None,
    ) -> dict[str, object]:
        draft = self._require_draft(draft_id)
        saved_result = self._require_saved_result(draft)
        item = self._commit_pool_item(
            db,
            config,
            file_path=draft.file_path,
            title=draft.title,
            parse_result=saved_result,
            category=category,
            tags=tags or [],
            cleaned_text=cleaned_text,
            cleaning_level=cleaning_level,
        )
        self._draft_store.delete_draft(draft_id)
        self._job_store.delete_jobs_for_draft(draft_id)
        return item

    def delete_draft(self, draft_id: str) -> bool:
        deleted = self._draft_store.delete_draft(draft_id)
        if deleted:
            self._job_store.delete_jobs_for_draft(draft_id)
        return deleted

    def cancel_reparse(self, draft_id: str) -> bool:
        self._require_draft(draft_id)
        return self._draft_store.cancel_reparse(draft_id)

    def _run_parse_job(
        self,
        job_id: str,
        draft_id: str,
        request_id: str,
        file_path: str,
        parser_name: str,
        preview_result_id: str,
        success_status: str,
    ) -> None:
        processed_pages = 0
        total_pages = 0

        def on_page(page: PdfParsePage, incoming_total_pages: int) -> None:
            nonlocal processed_pages, total_pages
            total_pages = incoming_total_pages
            processed_pages = max(processed_pages, page.page_number)
            self._draft_store.add_preview_page(
                draft_id=draft_id,
                parse_result_id=preview_result_id,
                page_number=page.page_number,
                content_type=page.content_type,
                content=page.content,
            )
            self._job_store.mark_running(
                job_id,
                total_pages=incoming_total_pages,
                preview_result_id=preview_result_id,
            )
            self._job_store.update_progress(
                job_id,
                processed_pages=processed_pages,
                latest_available_page=page.page_number,
            )

        try:
            result = self._parse_service.parse_file(
                file_path=file_path,
                parser_name=parser_name,
                knowledge_item_id=None,
                cancel_check=lambda: self._draft_store.should_abort_reparse(draft_id, request_id),
                on_page=on_page,
            )
            if self._draft_store.should_abort_reparse(draft_id, request_id):
                self._draft_store.update_parse_result(
                    draft_id=draft_id,
                    parse_result_id=preview_result_id,
                    status="cancelled",
                )
                self._job_store.mark_cancelled(job_id)
                return

            payload = self._build_parse_result_payload(result=result, status=success_status)
            self._draft_store.update_parse_result(
                draft_id=draft_id,
                parse_result_id=preview_result_id,
                parser_name=payload["parser_name"],
                status=payload["status"],
                raw_text=payload["raw_text"],
                markdown_text=payload["markdown_text"],
                preview_text=payload["preview_text"],
                page_count=payload["page_count"],
                char_count=payload["char_count"],
                quality_score=payload["quality_score"],
                is_ocr=payload["is_ocr"],
                warnings=payload["warnings"],
                fallback_from=payload["fallback_from"],
                fallback_reason=payload["fallback_reason"],
            )
            if payload["preview_pages"]:
                for item in payload["preview_pages"]:
                    self._draft_store.add_preview_page(
                        draft_id=draft_id,
                        parse_result_id=preview_result_id,
                        page_number=item["page_number"],
                        content_type=item["content_type"],
                        content=item["content"],
                    )
            if success_status == "saved":
                self._draft_store.promote_saved_result(draft_id, preview_result_id)
            self._job_store.update_progress(
                job_id,
                processed_pages=result.page_count,
                latest_available_page=result.page_count,
            )
            self._job_store.mark_completed(job_id, preview_result_id=preview_result_id)
        except AppError as exc:
            if exc.error_category == "CANCELLED":
                self._draft_store.update_parse_result(
                    draft_id=draft_id,
                    parse_result_id=preview_result_id,
                    status="cancelled",
                )
                self._job_store.mark_cancelled(job_id)
                return
            self._draft_store.update_parse_result(
                draft_id=draft_id,
                parse_result_id=preview_result_id,
                status="failed",
                warnings=[exc.error_message],
            )
            self._job_store.mark_failed(job_id, error_message=exc.error_message)
        except Exception as exc:  # pragma: no cover - defensive guard for worker crashes
            self._draft_store.update_parse_result(
                draft_id=draft_id,
                parse_result_id=preview_result_id,
                status="failed",
                warnings=[str(exc)],
            )
            self._job_store.mark_failed(job_id, error_message=str(exc))
        finally:
            self._draft_store.complete_reparse(draft_id, request_id)

    @staticmethod
    def _build_parse_result_payload(
        *,
        result,
        status: str,
    ) -> dict[str, object]:
        quality = evaluate_parse_quality(
            parser_name=result.parser_name,
            raw_text=result.raw_text,
            markdown_text=result.markdown_text,
            page_count=result.page_count,
        )
        return {
            "parser_name": result.parser_name,
            "status": status,
            "raw_text": result.raw_text,
            "markdown_text": result.markdown_text,
            "preview_text": result.preview_text,
            "page_count": result.page_count,
            "char_count": result.char_count,
            "quality_score": quality.score,
            "is_ocr": result.is_ocr,
            "warnings": [*result.warnings, *quality.warnings],
            "fallback_from": result.fallback_from,
            "fallback_reason": result.fallback_reason or quality.fallback_reason,
            "preview_pages": [
                {
                    "page_number": item.page_number,
                    "content_type": item.content_type,
                    "content": item.content,
                }
                for item in (result.preview_pages or [])
            ],
        }

    def _require_draft(self, draft_id: str) -> PdfDraft:
        draft = self._draft_store.get_draft(draft_id)
        if draft is None:
            raise AppError(
                status_code=404,
                error_category="VALIDATION_FAILED",
                error_message="PDF draft not found.",
            )
        return draft

    @staticmethod
    def _require_parse_result(draft: PdfDraft, parse_result_id: str) -> PdfDraftParseResult:
        for item in draft.parse_results:
            if item.id == parse_result_id:
                return item
        raise AppError(
            status_code=404,
            error_category="VALIDATION_FAILED",
            error_message="Draft parse result not found.",
        )

    def _rehydrate_preview_pages(
        self,
        draft: PdfDraft,
        parse_result: PdfDraftParseResult,
    ) -> None:
        reparsed = self._parse_service.parse_file(
            file_path=draft.file_path,
            parser_name=parse_result.parser_name,
            knowledge_item_id=None,
        )
        preview_pages = reparsed.preview_pages or []
        if not preview_pages:
            fallback_content = parse_result.markdown_text or parse_result.raw_text or parse_result.preview_text
            if fallback_content:
                preview_pages = [
                    PdfParsePage(
                        page_number=1,
                        content_type="markdown" if parse_result.markdown_text else "text",
                        content=fallback_content,
                    )
                ]
        for item in preview_pages:
            self._draft_store.add_preview_page(
                draft_id=draft.id,
                parse_result_id=parse_result.id,
                page_number=item.page_number,
                content_type=item.content_type,
                content=item.content,
            )

    @staticmethod
    def _require_saved_result(draft: PdfDraft) -> PdfDraftParseResult:
        if not draft.saved_parse_result_id:
            raise AppError(
                status_code=400,
                error_category="VALIDATION_FAILED",
                error_message="PDF draft has no saved parse result.",
            )
        for item in draft.parse_results:
            if item.id == draft.saved_parse_result_id:
                return item
        raise AppError(
            status_code=400,
            error_category="VALIDATION_FAILED",
            error_message="PDF draft saved parse result is missing.",
        )


def build_pdf_draft_service(
    *,
    config: AppConfig,
    draft_store: PdfDraftStore,
    job_store: PdfReparseJobStore,
) -> PdfDraftService:
    return PdfDraftService(
        draft_store=draft_store,
        job_store=job_store,
        parse_service=build_default_pdf_parse_service(config),
    )
