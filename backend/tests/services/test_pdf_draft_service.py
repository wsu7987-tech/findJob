from __future__ import annotations

from pathlib import Path


class _FakeParseService:
    def parse_file(
        self,
        *,
        file_path,
        parser_name,
        knowledge_item_id=None,
        cancel_check=None,
        on_page=None,
    ):
        from backend.app.services.pdf_parse.types import PdfParsePage, PdfParseResult

        del knowledge_item_id
        name = Path(file_path).name
        if parser_name == "rapid_ocr":
            if callable(cancel_check) and cancel_check():
                raise AssertionError("cancelled too early")
            page = PdfParsePage(page_number=1, content_type="text", content=f"ocr:{name}")
            if callable(on_page):
                on_page(page, 1)
            return PdfParseResult(
                parser_name="rapid_ocr",
                raw_text=f"ocr:{name}",
                markdown_text=None,
                preview_text=f"ocr:{name}",
                page_count=1,
                char_count=len(f"ocr:{name}"),
                quality_score=0.73,
                warnings=["ocr"],
                is_ocr=True,
                preview_pages=[page],
            )
        pages = [
            PdfParsePage(page_number=1, content_type="markdown", content=f"# {name}"),
            PdfParsePage(page_number=2, content_type="markdown", content=f"## tail {name}"),
        ]
        if callable(on_page):
            for page in pages:
                on_page(page, 2)
        return PdfParseResult(
            parser_name="pymupdf4llm_markdown",
            raw_text=f"markdown:{name}",
            markdown_text=f"# {name}",
            preview_text=f"# {name}",
            page_count=2,
            char_count=len(f"markdown:{name}"),
            quality_score=0.93,
            warnings=[],
            is_ocr=False,
            preview_pages=pages,
        )


def test_create_pdf_draft_parses_and_sets_initial_saved_result() -> None:
    from backend.app.services.pdf_draft_service import PdfDraftService
    from backend.app.services.pdf_draft_store import PdfDraftStore

    service = PdfDraftService(
        draft_store=PdfDraftStore(),
        parse_service=_FakeParseService(),
    )

    draft = service.create_draft(file_path="D:/docs/demo.pdf", title="Demo")

    assert draft.saved_parse_result_id is not None
    assert draft.latest_preview_result_id == draft.saved_parse_result_id
    assert len(draft.parse_results) == 1
    assert draft.parse_results[0].status == "saved"
    assert draft.parse_results[0].raw_text == "markdown:demo.pdf"


def test_reparse_pdf_draft_creates_preview_only() -> None:
    from backend.app.services.pdf_draft_service import PdfDraftService
    from backend.app.services.pdf_draft_store import PdfDraftStore

    service = PdfDraftService(
        draft_store=PdfDraftStore(),
        parse_service=_FakeParseService(),
    )
    draft = service.create_draft(file_path="D:/docs/demo.pdf", title="Demo")

    preview = service.reparse_draft(draft.id, parser_name="rapid_ocr")

    assert preview.status == "preview"
    assert preview.id != draft.saved_parse_result_id
    updated = service.get_draft(draft.id)
    assert updated is not None
    assert updated.saved_parse_result_id != preview.id
    assert updated.latest_preview_result_id == preview.id


def test_commit_pdf_draft_creates_pool_item_from_saved_result(
    test_db,
    configured_app_paths,
    monkeypatch,
) -> None:
    from backend.app.config import load_config
    from backend.app.services.pdf_draft_service import PdfDraftService
    from backend.app.services.pdf_draft_store import PdfDraftStore

    monkeypatch.setattr("backend.app.services.pool._index_document_chunks", lambda *a, **k: None)

    service = PdfDraftService(
        draft_store=PdfDraftStore(),
        parse_service=_FakeParseService(),
    )
    draft = service.create_draft(file_path="D:/docs/demo.pdf", title="Demo")
    preview = service.reparse_draft(draft.id, parser_name="rapid_ocr")
    service.save_parse_result(draft.id, preview.id)

    item = service.commit_draft(
        db=test_db,
        config=load_config(),
        draft_id=draft.id,
    )

    assert item["source_type"] == "pdf"

    with test_db.connect() as connection:
        row = connection.execute(
            """
            SELECT raw_content
            FROM knowledge_items
            WHERE id = ?
            """,
            (item["knowledge_item_id"],),
        ).fetchone()

    assert row is not None
    assert row["raw_content"] == "ocr:demo.pdf"


def test_cancel_reparse_marks_draft_as_cancel_requested() -> None:
    from backend.app.services.pdf_draft_service import PdfDraftService
    from backend.app.services.pdf_draft_store import PdfDraftStore

    store = PdfDraftStore()
    service = PdfDraftService(
        draft_store=store,
        parse_service=_FakeParseService(),
    )
    draft = service.create_draft(file_path="D:/docs/demo.pdf", title="Demo")
    request_id = store.begin_reparse(draft.id)

    cancelled = service.cancel_reparse(draft.id)

    assert cancelled is True
    assert store.should_abort_reparse(draft.id, request_id) is True


def test_delete_and_commit_clear_jobs_for_draft(
    test_db,
    monkeypatch,
) -> None:
    from backend.app.config import load_config
    from backend.app.services.pdf_draft_service import PdfDraftService
    from backend.app.services.pdf_draft_store import PdfDraftStore
    from backend.app.services.pdf_reparse_job_store import PdfReparseJobStore

    monkeypatch.setattr("backend.app.services.pool._index_document_chunks", lambda *a, **k: None)

    job_store = PdfReparseJobStore()
    service = PdfDraftService(
        draft_store=PdfDraftStore(),
        job_store=job_store,
        parse_service=_FakeParseService(),
    )

    draft = service.create_draft(file_path="D:/docs/demo.pdf", title="Demo")
    service.start_reparse_draft(draft.id, parser_name="rapid_ocr")
    assert job_store.list_jobs(draft_id=draft.id)

    assert service.delete_draft(draft.id) is True
    assert job_store.list_jobs(draft_id=draft.id) == []

    draft = service.create_draft(file_path="D:/docs/demo.pdf", title="Demo")
    preview = service.reparse_draft(draft.id, parser_name="rapid_ocr")
    service.save_parse_result(draft.id, preview.id)
    service.start_reparse_draft(draft.id, parser_name="rapid_ocr")
    assert job_store.list_jobs(draft_id=draft.id)

    service.commit_draft(
        db=test_db,
        config=load_config(),
        draft_id=draft.id,
    )
    assert job_store.list_jobs(draft_id=draft.id) == []


def test_get_preview_page_rehydrates_missing_preview_pages() -> None:
    from backend.app.services.pdf_draft_service import PdfDraftService
    from backend.app.services.pdf_draft_store import PdfDraftStore

    store = PdfDraftStore()
    service = PdfDraftService(
        draft_store=store,
        parse_service=_FakeParseService(),
    )

    draft = service.create_draft(file_path="D:/docs/demo.pdf", title="Demo")
    parse_result = draft.parse_results[0]
    parse_result.preview_pages = []

    page = service.get_preview_page(
        draft_id=draft.id,
        parse_result_id=parse_result.id,
        page_number=2,
    )

    assert page.page_number == 2
    assert page.content == "## tail demo.pdf"
