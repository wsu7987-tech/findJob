from __future__ import annotations

from dataclasses import dataclass, field
from threading import RLock

from backend.app.utils import new_id, utc_now


@dataclass(slots=True)
class PdfDraftPreviewPage:
    page_number: int
    content_type: str
    content: str


@dataclass(slots=True)
class PdfDraftParseResult:
    id: str
    parser_name: str
    status: str
    raw_text: str
    markdown_text: str | None
    preview_text: str
    page_count: int
    char_count: int
    quality_score: float
    is_ocr: bool
    warnings: list[str]
    fallback_from: str | None
    fallback_reason: str | None
    created_at: str
    preview_pages: list[PdfDraftPreviewPage] = field(default_factory=list)


@dataclass(slots=True)
class PdfDraft:
    id: str
    file_path: str
    title: str | None
    source_name: str
    created_at: str
    updated_at: str
    saved_parse_result_id: str | None = None
    latest_preview_result_id: str | None = None
    active_parse_request_id: str | None = None
    cancel_requested: bool = False
    parse_results: list[PdfDraftParseResult] = field(default_factory=list)


class PdfDraftStore:
    def __init__(self) -> None:
        self._drafts: dict[str, PdfDraft] = {}
        self._lock = RLock()

    def create_shell_draft(
        self,
        *,
        file_path: str,
        title: str | None,
        source_name: str,
    ) -> PdfDraft:
        now = utc_now()
        draft = PdfDraft(
            id=new_id(),
            file_path=file_path,
            title=title,
            source_name=source_name,
            created_at=now,
            updated_at=now,
        )
        with self._lock:
            self._drafts[draft.id] = draft
        return draft

    def create_draft(
        self,
        *,
        file_path: str,
        title: str | None,
        source_name: str,
        parse_result: dict[str, object],
    ) -> PdfDraft:
        now = utc_now()
        draft_id = new_id()
        initial_result = self._build_parse_result(parse_result)
        draft = PdfDraft(
            id=draft_id,
            file_path=file_path,
            title=title,
            source_name=source_name,
            created_at=now,
            updated_at=now,
            saved_parse_result_id=initial_result.id,
            latest_preview_result_id=initial_result.id,
            parse_results=[initial_result],
        )
        with self._lock:
            self._drafts[draft_id] = draft
        return draft

    def list_drafts(self) -> list[PdfDraft]:
        with self._lock:
            drafts = list(self._drafts.values())
        drafts.sort(key=lambda item: (item.updated_at, item.id), reverse=True)
        return drafts

    def get_draft(self, draft_id: str) -> PdfDraft | None:
        with self._lock:
            return self._drafts.get(draft_id)

    def add_parse_result(
        self,
        *,
        draft_id: str,
        parse_result: dict[str, object],
    ) -> PdfDraftParseResult:
        with self._lock:
            draft = self._drafts[draft_id]
            result = self._build_parse_result(parse_result)
            draft.parse_results.append(result)
            draft.latest_preview_result_id = result.id
            draft.updated_at = utc_now()
            return result

    def add_preview_page(
        self,
        *,
        draft_id: str,
        parse_result_id: str,
        page_number: int,
        content_type: str,
        content: str,
    ) -> PdfDraftPreviewPage:
        with self._lock:
            draft = self._drafts[draft_id]
            target = self._require_parse_result(draft, parse_result_id)
            page = PdfDraftPreviewPage(
                page_number=page_number,
                content_type=content_type,
                content=content,
            )
            target.preview_pages = [
                item for item in target.preview_pages if item.page_number != page_number
            ]
            target.preview_pages.append(page)
            target.preview_pages.sort(key=lambda item: item.page_number)
            draft.updated_at = utc_now()
            return page

    def update_parse_result(
        self,
        *,
        draft_id: str,
        parse_result_id: str,
        **changes: object,
    ) -> PdfDraftParseResult:
        with self._lock:
            draft = self._drafts[draft_id]
            target = self._require_parse_result(draft, parse_result_id)
            for key, value in changes.items():
                if hasattr(target, key):
                    setattr(target, key, value)
            draft.updated_at = utc_now()
            return target

    def get_preview_page(
        self,
        draft_id: str,
        parse_result_id: str,
        page_number: int,
    ) -> PdfDraftPreviewPage | None:
        with self._lock:
            draft = self._drafts.get(draft_id)
            if draft is None:
                return None
            try:
                target = self._require_parse_result(draft, parse_result_id)
            except KeyError:
                return None
            for item in target.preview_pages:
                if item.page_number == page_number:
                    return item
            return None

    def begin_reparse(self, draft_id: str) -> str:
        with self._lock:
            draft = self._drafts[draft_id]
            request_id = new_id()
            draft.active_parse_request_id = request_id
            draft.cancel_requested = False
            draft.updated_at = utc_now()
            return request_id

    def cancel_reparse(self, draft_id: str) -> bool:
        with self._lock:
            draft = self._drafts.get(draft_id)
            if draft is None or draft.active_parse_request_id is None:
                return False
            draft.cancel_requested = True
            draft.updated_at = utc_now()
            return True

    def should_abort_reparse(self, draft_id: str, request_id: str) -> bool:
        with self._lock:
            draft = self._drafts.get(draft_id)
            if draft is None:
                return True
            return (
                draft.cancel_requested
                or draft.active_parse_request_id is None
                or draft.active_parse_request_id != request_id
            )

    def complete_reparse(self, draft_id: str, request_id: str) -> None:
        with self._lock:
            draft = self._drafts.get(draft_id)
            if draft is None or draft.active_parse_request_id != request_id:
                return
            draft.active_parse_request_id = None
            draft.cancel_requested = False
            draft.updated_at = utc_now()

    def promote_saved_result(
        self,
        draft_id: str,
        parse_result_id: str,
    ) -> PdfDraftParseResult:
        with self._lock:
            draft = self._drafts[draft_id]
            target = self._require_parse_result(draft, parse_result_id)
            target.status = "saved"
            draft.saved_parse_result_id = target.id
            draft.latest_preview_result_id = target.id
            draft.updated_at = utc_now()
            return target

    def delete_draft(self, draft_id: str) -> bool:
        with self._lock:
            return self._drafts.pop(draft_id, None) is not None

    @staticmethod
    def _build_parse_result(payload: dict[str, object]) -> PdfDraftParseResult:
        return PdfDraftParseResult(
            id=str(payload.get("id") or new_id()),
            parser_name=str(payload["parser_name"]),
            status=str(payload["status"]),
            raw_text=str(payload["raw_text"]),
            markdown_text=(
                None if payload.get("markdown_text") is None else str(payload["markdown_text"])
            ),
            preview_text=str(payload["preview_text"]),
            page_count=int(payload["page_count"]),
            char_count=int(payload["char_count"]),
            quality_score=float(payload["quality_score"]),
            is_ocr=bool(payload["is_ocr"]),
            warnings=[str(item) for item in (payload.get("warnings") or [])],
            fallback_from=(
                None if payload.get("fallback_from") is None else str(payload["fallback_from"])
            ),
            fallback_reason=(
                None
                if payload.get("fallback_reason") is None
                else str(payload["fallback_reason"])
            ),
            created_at=str(payload.get("created_at") or utc_now()),
            preview_pages=[
                PdfDraftPreviewPage(
                    page_number=int(item["page_number"]),
                    content_type=str(item["content_type"]),
                    content=str(item["content"]),
                )
                for item in (payload.get("preview_pages") or [])
            ],
        )

    @staticmethod
    def _require_parse_result(
        draft: PdfDraft,
        parse_result_id: str,
    ) -> PdfDraftParseResult:
        for item in draft.parse_results:
            if item.id == parse_result_id:
                return item
        raise KeyError(parse_result_id)
