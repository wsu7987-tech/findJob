from __future__ import annotations

from dataclasses import dataclass, field
from threading import RLock

from backend.app.utils import new_id, utc_now


@dataclass(slots=True)
class WebDraftPreviewPage:
    page_number: int
    content_type: str
    content: str


@dataclass(slots=True)
class WebDraftParseResult:
    id: str
    parser_name: str
    status: str
    raw_text: str
    markdown_text: str | None
    preview_text: str
    section_count: int
    char_count: int
    quality_score: float
    warnings: list[str]
    auth_mode: str
    created_at: str
    preview_pages: list[WebDraftPreviewPage] = field(default_factory=list)


@dataclass(slots=True)
class WebDraft:
    id: str
    url: str
    title: str | None
    source_name: str
    session_profile_id: str | None
    created_at: str
    updated_at: str
    saved_parse_result_id: str | None = None
    latest_preview_result_id: str | None = None
    parse_results: list[WebDraftParseResult] = field(default_factory=list)


class WebDraftStore:
    def __init__(self) -> None:
        self._drafts: dict[str, WebDraft] = {}
        self._lock = RLock()

    def create_shell_draft(
        self,
        *,
        url: str,
        title: str | None,
        source_name: str,
        session_profile_id: str | None = None,
    ) -> WebDraft:
        now = utc_now()
        draft = WebDraft(
            id=new_id(),
            url=url,
            title=title,
            source_name=source_name,
            session_profile_id=session_profile_id,
            created_at=now,
            updated_at=now,
        )
        with self._lock:
            self._drafts[draft.id] = draft
        return draft

    def add_parse_result(
        self,
        *,
        draft_id: str,
        parse_result: dict[str, object],
    ) -> WebDraftParseResult:
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
    ) -> WebDraftPreviewPage:
        with self._lock:
            draft = self._drafts[draft_id]
            target = self._require_parse_result(draft, parse_result_id)
            page = WebDraftPreviewPage(
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

    def get_draft(self, draft_id: str) -> WebDraft | None:
        with self._lock:
            return self._drafts.get(draft_id)

    def update_draft_metadata(
        self,
        draft_id: str,
        *,
        title: str | None = None,
        source_name: str | None = None,
        session_profile_id: str | None = None,
    ) -> WebDraft:
        with self._lock:
            draft = self._drafts[draft_id]
            if title is not None:
                draft.title = title
            if source_name is not None:
                draft.source_name = source_name
            if session_profile_id is not None:
                draft.session_profile_id = session_profile_id
            draft.updated_at = utc_now()
            return draft

    def get_preview_page(
        self,
        draft_id: str,
        parse_result_id: str,
        page_number: int,
    ) -> WebDraftPreviewPage | None:
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

    def promote_saved_result(
        self,
        draft_id: str,
        parse_result_id: str,
    ) -> WebDraftParseResult:
        with self._lock:
            draft = self._drafts[draft_id]
            target = self._require_parse_result(draft, parse_result_id)
            target.status = "saved"
            draft.saved_parse_result_id = target.id
            draft.latest_preview_result_id = target.id
            draft.updated_at = utc_now()
            return target

    def update_parse_result(
        self,
        *,
        draft_id: str,
        parse_result_id: str,
        **updates: object,
    ) -> WebDraftParseResult:
        with self._lock:
            draft = self._drafts[draft_id]
            target = self._require_parse_result(draft, parse_result_id)
            for key, value in updates.items():
                if not hasattr(target, key):
                    continue
                if key == "warnings" and value is not None:
                    setattr(target, key, [str(item) for item in value])
                    continue
                setattr(target, key, value)
            draft.updated_at = utc_now()
            return target

    def delete_draft(self, draft_id: str) -> bool:
        with self._lock:
            return self._drafts.pop(draft_id, None) is not None

    @staticmethod
    def _build_parse_result(payload: dict[str, object]) -> WebDraftParseResult:
        return WebDraftParseResult(
            id=str(payload.get("id") or new_id()),
            parser_name=str(payload["parser_name"]),
            status=str(payload["status"]),
            raw_text=str(payload["raw_text"]),
            markdown_text=(
                None if payload.get("markdown_text") is None else str(payload["markdown_text"])
            ),
            preview_text=str(payload["preview_text"]),
            section_count=int(payload["section_count"]),
            char_count=int(payload["char_count"]),
            quality_score=float(payload["quality_score"]),
            warnings=[str(item) for item in (payload.get("warnings") or [])],
            auth_mode=str(payload["auth_mode"]),
            created_at=str(payload.get("created_at") or utc_now()),
            preview_pages=[
                WebDraftPreviewPage(
                    page_number=int(item["page_number"]),
                    content_type=str(item["content_type"]),
                    content=str(item["content"]),
                )
                for item in (payload.get("preview_pages") or [])
            ],
        )

    @staticmethod
    def _require_parse_result(draft: WebDraft, parse_result_id: str) -> WebDraftParseResult:
        for item in draft.parse_results:
            if item.id == parse_result_id:
                return item
        raise KeyError(parse_result_id)
