from __future__ import annotations

from threading import Thread
from time import sleep
from urllib.parse import urlparse

from backend.app.config import AppConfig
from backend.app.db import Database
from backend.app.errors import AppError
from backend.app.schemas.web_drafts import WebDraftParserName
from backend.app.services.pool import create_pool_item_from_saved_web_content
from backend.app.services.web_capture.content_extractor import paginate_markdown
from backend.app.services.web_capture.service import (
    WebCaptureService,
    build_default_web_capture_service,
)
from backend.app.services.web_draft_store import (
    WebDraft,
    WebDraftParseResult,
    WebDraftPreviewPage,
    WebDraftStore,
)
from backend.app.services.web_reparse_job_store import WebReparseJob, WebReparseJobStore


class WebDraftService:
    def __init__(
        self,
        *,
        draft_store: WebDraftStore,
        job_store: WebReparseJobStore | None = None,
        capture_service: WebCaptureService | object | None = None,
        commit_pool_item=create_pool_item_from_saved_web_content,
    ) -> None:
        self._draft_store = draft_store
        self._job_store = job_store or WebReparseJobStore()
        self._capture_service = capture_service or build_default_web_capture_service()
        self._commit_pool_item = commit_pool_item

    def create_draft(
        self,
        *,
        url: str,
        title: str | None,
        session_profile_id: str | None,
    ) -> WebDraft:
        captured = self._capture_service.capture_url(
            url=url,
            parser_name="playwright_dom",
            session_profile_id=session_profile_id,
        )
        draft = self._draft_store.create_shell_draft(
            url=url,
            title=title or str(captured.get("title") or ""),
            source_name=str(captured.get("source_name") or (urlparse(url).netloc or url)),
            session_profile_id=session_profile_id,
        )
        result = self._draft_store.add_parse_result(
            draft_id=draft.id,
            parse_result=self._build_parse_result_payload(captured=captured, status="saved"),
        )
        self._draft_store.promote_saved_result(draft.id, result.id)
        return self._require_draft(draft.id)

    def start_create_draft(
        self,
        *,
        url: str,
        title: str | None,
        session_profile_id: str | None,
    ) -> tuple[WebDraft, WebReparseJob]:
        draft = self._draft_store.create_shell_draft(
            url=url,
            title=title,
            source_name=urlparse(url).netloc or url,
            session_profile_id=session_profile_id,
        )
        preview_result = self._draft_store.add_parse_result(
            draft_id=draft.id,
            parse_result={
                "parser_name": "playwright_dom",
                "status": "running",
                "raw_text": "",
                "markdown_text": None,
                "preview_text": "",
                "section_count": 0,
                "char_count": 0,
                "quality_score": 0.0,
                "warnings": [],
                "auth_mode": "none",
                "preview_pages": [],
            },
        )
        job = self._job_store.create_job(draft_id=draft.id, parser_name="playwright_dom")
        self._job_store.mark_running(job.id, total_pages=0, preview_result_id=preview_result.id)
        Thread(
            target=self._run_capture_job,
            args=(job.id, draft.id, url, title, session_profile_id, preview_result.id, "saved"),
            daemon=True,
        ).start()
        return self._require_draft(draft.id), job

    def get_draft(self, draft_id: str) -> WebDraft | None:
        return self._draft_store.get_draft(draft_id)

    def start_reparse_draft(
        self,
        draft_id: str,
        *,
        parser_name: WebDraftParserName,
        session_profile_id: str | None,
    ) -> WebReparseJob:
        draft = self._require_draft(draft_id)
        resolved_session_profile_id = session_profile_id or draft.session_profile_id
        preview_result = self._draft_store.add_parse_result(
            draft_id=draft_id,
            parse_result={
                "parser_name": parser_name,
                "status": "running",
                "raw_text": "",
                "markdown_text": None,
                "preview_text": "",
                "section_count": 0,
                "char_count": 0,
                "quality_score": 0.0,
                "warnings": [],
                "auth_mode": "none",
                "preview_pages": [],
            },
        )
        job = self._job_store.create_job(draft_id=draft_id, parser_name=parser_name)
        self._job_store.mark_running(job.id, total_pages=0, preview_result_id=preview_result.id)
        Thread(
            target=self._run_capture_job,
            args=(
                job.id,
                draft.id,
                draft.url,
                draft.title,
                resolved_session_profile_id,
                preview_result.id,
                "preview",
            ),
            daemon=True,
        ).start()
        return job

    def reparse_draft(
        self,
        draft_id: str,
        *,
        parser_name: WebDraftParserName,
        session_profile_id: str | None,
    ) -> WebDraftParseResult:
        job = self.start_reparse_draft(
            draft_id,
            parser_name=parser_name,
            session_profile_id=session_profile_id,
        )
        while True:
            latest = self._job_store.get_job(job.id)
            if latest is None:
                raise AppError(
                    status_code=404,
                    error_category="VALIDATION_FAILED",
                    error_message="Web reparse job not found.",
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
                error_message="Web reparse completed without a preview result.",
            )
        return self._require_parse_result(draft, parse_result_id)

    def get_job(self, draft_id: str, job_id: str) -> WebReparseJob:
        self._require_draft(draft_id)
        job = self._job_store.get_job(job_id)
        if job is None or job.draft_id != draft_id:
            raise AppError(
                status_code=404,
                error_category="VALIDATION_FAILED",
                error_message="Web reparse job not found.",
            )
        return job

    def list_jobs(self, *, active_only: bool = False) -> list[WebReparseJob]:
        return self._job_store.list_jobs(active_only=active_only)

    def cancel_job(self, draft_id: str, job_id: str) -> WebReparseJob:
        self._require_draft(draft_id)
        job = self._job_store.request_cancel(job_id)
        if job is None or job.draft_id != draft_id:
            raise AppError(
                status_code=404,
                error_category="VALIDATION_FAILED",
                error_message="Web reparse job not found.",
            )
        return job

    def get_preview_page(
        self,
        *,
        draft_id: str,
        parse_result_id: str,
        page_number: int,
    ) -> WebDraftPreviewPage:
        draft = self._require_draft(draft_id)
        parse_result = self._require_parse_result(draft, parse_result_id)
        page = self._draft_store.get_preview_page(draft_id, parse_result_id, page_number)
        if page is None:
            self._rehydrate_preview_pages(draft_id, parse_result)
            page = self._draft_store.get_preview_page(draft_id, parse_result_id, page_number)
        if page is None:
            raise AppError(
                status_code=500,
                error_category="INGEST_FAILED",
                error_message="Preview pages are missing for this web parse result.",
            )
        return page

    def save_parse_result(self, draft_id: str, parse_result_id: str) -> WebDraft:
        draft = self._require_draft(draft_id)
        self._require_parse_result(draft, parse_result_id)
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
            url=draft.url,
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

    def _run_capture_job(
        self,
        job_id: str,
        draft_id: str,
        url: str,
        requested_title: str | None,
        session_profile_id: str | None,
        preview_result_id: str,
        success_status: str,
    ) -> None:
        try:
            captured = self._capture_service.capture_url(
                url=url,
                parser_name="playwright_dom",
                session_profile_id=session_profile_id,
                cancel_check=lambda: self._should_cancel(job_id),
            )
            if self._should_cancel(job_id):
                self._draft_store.update_parse_result(
                    draft_id=draft_id,
                    parse_result_id=preview_result_id,
                    status="cancelled",
                )
                self._job_store.mark_cancelled(job_id)
                return

            self._draft_store.update_draft_metadata(
                draft_id,
                title=requested_title or str(captured.get("title") or ""),
                source_name=str(captured.get("source_name") or (urlparse(url).netloc or url)),
                session_profile_id=session_profile_id,
            )

            payload = self._build_parse_result_payload(captured=captured, status=success_status)
            preview_pages = list(payload.pop("preview_pages"))
            self._draft_store.update_parse_result(
                draft_id=draft_id,
                parse_result_id=preview_result_id,
                **payload,
            )
            self._job_store.mark_running(
                job_id,
                total_pages=len(preview_pages),
                preview_result_id=preview_result_id,
            )
            for index, item in enumerate(preview_pages, start=1):
                self._draft_store.add_preview_page(
                    draft_id=draft_id,
                    parse_result_id=preview_result_id,
                    page_number=int(item["page_number"]),
                    content_type=str(item["content_type"]),
                    content=str(item["content"]),
                )
                self._job_store.update_progress(
                    job_id,
                    processed_pages=index,
                    latest_available_page=int(item["page_number"]),
                )
            if success_status == "saved":
                self._draft_store.promote_saved_result(draft_id, preview_result_id)
            self._job_store.mark_completed(job_id, preview_result_id=preview_result_id)
        except AppError as exc:
            if exc.error_category == "CANCELLED":
                self._draft_store.update_parse_result(
                    draft_id=draft_id,
                    parse_result_id=preview_result_id,
                    status="cancelled",
                    warnings=[exc.error_message],
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
        except Exception as exc:  # pragma: no cover - defensive guard
            self._draft_store.update_parse_result(
                draft_id=draft_id,
                parse_result_id=preview_result_id,
                status="failed",
                warnings=[str(exc)],
            )
            self._job_store.mark_failed(job_id, error_message=str(exc))

    @staticmethod
    def _build_parse_result_payload(
        *,
        captured: dict[str, object],
        status: str,
    ) -> dict[str, object]:
        raw_text = str(captured.get("raw_text") or "")
        preview_pages = list(captured.get("preview_pages") or [])
        return {
            "parser_name": "playwright_dom",
            "status": status,
            "raw_text": raw_text,
            "markdown_text": captured.get("markdown_text"),
            "preview_text": str(captured.get("preview_text") or ""),
            "section_count": len(preview_pages),
            "char_count": len(raw_text),
            "quality_score": _score_web_capture(raw_text),
            "warnings": [str(item) for item in (captured.get("warnings") or [])],
            "auth_mode": str(captured.get("auth_mode") or "none"),
            "preview_pages": preview_pages,
        }

    def _rehydrate_preview_pages(self, draft_id: str, parse_result: WebDraftParseResult) -> None:
        for item in _fallback_preview_pages(parse_result):
            self._draft_store.add_preview_page(
                draft_id=draft_id,
                parse_result_id=parse_result.id,
                page_number=item.page_number,
                content_type=item.content_type,
                content=item.content,
            )

    def _should_cancel(self, job_id: str) -> bool:
        job = self._job_store.get_job(job_id)
        return bool(job and job.cancel_requested)

    def _require_draft(self, draft_id: str) -> WebDraft:
        draft = self._draft_store.get_draft(draft_id)
        if draft is None:
            raise AppError(
                status_code=404,
                error_category="VALIDATION_FAILED",
                error_message="Web draft not found.",
            )
        return draft

    @staticmethod
    def _require_parse_result(draft: WebDraft, parse_result_id: str) -> WebDraftParseResult:
        for item in draft.parse_results:
            if item.id == parse_result_id:
                return item
        raise AppError(
            status_code=404,
            error_category="VALIDATION_FAILED",
            error_message="Draft parse result not found.",
        )

    @staticmethod
    def _require_saved_result(draft: WebDraft) -> WebDraftParseResult:
        if not draft.saved_parse_result_id:
            raise AppError(
                status_code=400,
                error_category="VALIDATION_FAILED",
                error_message="Web draft has no saved parse result.",
            )
        for item in draft.parse_results:
            if item.id == draft.saved_parse_result_id:
                return item
        raise AppError(
            status_code=400,
            error_category="VALIDATION_FAILED",
            error_message="Web draft saved parse result is missing.",
        )


def _score_web_capture(raw_text: str) -> float:
    if not raw_text.strip():
        return 0.0
    if len(raw_text) >= 2000:
        return 0.95
    if len(raw_text) >= 500:
        return 0.85
    return 0.7


def _fallback_preview_pages(parse_result: WebDraftParseResult) -> list[WebDraftPreviewPage]:
    if parse_result.markdown_text:
        return [
            WebDraftPreviewPage(
                page_number=item.page_number,
                content_type=item.content_type,
                content=item.content,
            )
            for item in paginate_markdown(parse_result.markdown_text)
        ]

    content = parse_result.raw_text or parse_result.preview_text
    if not content:
        return []

    page_size = 1800
    return [
        WebDraftPreviewPage(
            page_number=(index // page_size) + 1,
            content_type="text",
            content=content[index : index + page_size],
        )
        for index in range(0, len(content), page_size)
    ]
