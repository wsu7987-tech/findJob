from __future__ import annotations


def test_create_draft_stores_initial_saved_result() -> None:
    from backend.app.services.pdf_draft_store import PdfDraftStore

    store = PdfDraftStore()
    draft = store.create_draft(
        file_path="D:/docs/demo.pdf",
        title="Demo PDF",
        source_name="demo.pdf",
        parse_result={
            "parser_name": "pymupdf4llm_markdown",
            "status": "saved",
            "raw_text": "alpha body",
            "markdown_text": "# Alpha",
            "preview_text": "# Alpha",
            "page_count": 2,
            "char_count": 10,
            "quality_score": 0.91,
            "is_ocr": False,
            "warnings": [],
            "fallback_from": None,
            "fallback_reason": None,
        },
    )

    assert draft.saved_parse_result_id is not None
    assert draft.latest_preview_result_id == draft.saved_parse_result_id
    assert len(draft.parse_results) == 1
    assert draft.parse_results[0].status == "saved"
    assert draft.parse_results[0].parser_name == "pymupdf4llm_markdown"


def test_add_preview_and_promote_saved_result_updates_pointers() -> None:
    from backend.app.services.pdf_draft_store import PdfDraftStore

    store = PdfDraftStore()
    draft = store.create_draft(
        file_path="D:/docs/demo.pdf",
        title=None,
        source_name="demo.pdf",
        parse_result={
            "parser_name": "pymupdf4llm_markdown",
            "status": "saved",
            "raw_text": "alpha body",
            "markdown_text": "# Alpha",
            "preview_text": "# Alpha",
            "page_count": 2,
            "char_count": 10,
            "quality_score": 0.91,
            "is_ocr": False,
            "warnings": [],
            "fallback_from": None,
            "fallback_reason": None,
        },
    )

    preview = store.add_parse_result(
        draft_id=draft.id,
        parse_result={
            "parser_name": "rapid_ocr",
            "status": "preview",
            "raw_text": "ocr body",
            "markdown_text": None,
            "preview_text": "ocr body",
            "page_count": 2,
            "char_count": 8,
            "quality_score": 0.72,
            "is_ocr": True,
            "warnings": ["ocr"],
            "fallback_from": None,
            "fallback_reason": None,
        },
    )

    updated = store.get_draft(draft.id)
    assert updated is not None
    assert updated.saved_parse_result_id == draft.saved_parse_result_id
    assert updated.latest_preview_result_id == preview.id
    assert updated.parse_results[-1].status == "preview"

    promoted = store.promote_saved_result(draft.id, preview.id)

    assert promoted.id == preview.id
    assert promoted.status == "saved"
    promoted_draft = store.get_draft(draft.id)
    assert promoted_draft is not None
    assert promoted_draft.saved_parse_result_id == preview.id
    assert promoted_draft.latest_preview_result_id == preview.id


def test_delete_draft_removes_it_from_store() -> None:
    from backend.app.services.pdf_draft_store import PdfDraftStore

    store = PdfDraftStore()
    draft = store.create_draft(
        file_path="D:/docs/demo.pdf",
        title=None,
        source_name="demo.pdf",
        parse_result={
            "parser_name": "pymupdf4llm_markdown",
            "status": "saved",
            "raw_text": "alpha body",
            "markdown_text": "# Alpha",
            "preview_text": "# Alpha",
            "page_count": 2,
            "char_count": 10,
            "quality_score": 0.91,
            "is_ocr": False,
            "warnings": [],
            "fallback_from": None,
            "fallback_reason": None,
        },
    )

    assert store.delete_draft(draft.id) is True
    assert store.get_draft(draft.id) is None


def test_begin_and_cancel_reparse_tracks_active_request() -> None:
    from backend.app.services.pdf_draft_store import PdfDraftStore

    store = PdfDraftStore()
    draft = store.create_draft(
        file_path="D:/docs/demo.pdf",
        title=None,
        source_name="demo.pdf",
        parse_result={
            "parser_name": "pymupdf4llm_markdown",
            "status": "saved",
            "raw_text": "alpha body",
            "markdown_text": "# Alpha",
            "preview_text": "# Alpha",
            "page_count": 2,
            "char_count": 10,
            "quality_score": 0.91,
            "is_ocr": False,
            "warnings": [],
            "fallback_from": None,
            "fallback_reason": None,
        },
    )

    request_id = store.begin_reparse(draft.id)

    updated = store.get_draft(draft.id)
    assert updated is not None
    assert updated.active_parse_request_id == request_id
    assert updated.cancel_requested is False
    assert store.should_abort_reparse(draft.id, request_id) is False

    assert store.cancel_reparse(draft.id) is True
    assert store.should_abort_reparse(draft.id, request_id) is True

    store.complete_reparse(draft.id, request_id)
    completed = store.get_draft(draft.id)
    assert completed is not None
    assert completed.active_parse_request_id is None
    assert completed.cancel_requested is False


def test_preview_pages_can_be_added_and_retrieved() -> None:
    from backend.app.services.pdf_draft_store import PdfDraftStore

    store = PdfDraftStore()
    draft = store.create_draft(
        file_path="D:/docs/demo.pdf",
        title="Demo PDF",
        source_name="demo.pdf",
        parse_result={
            "parser_name": "pymupdf4llm_markdown",
            "status": "saved",
            "raw_text": "alpha body",
            "markdown_text": "# Alpha",
            "preview_text": "# Alpha",
            "page_count": 1,
            "char_count": 10,
            "quality_score": 0.91,
            "is_ocr": False,
            "warnings": [],
            "fallback_from": None,
            "fallback_reason": None,
            "preview_pages": [],
        },
    )

    running = store.add_parse_result(
        draft_id=draft.id,
        parse_result={
            "parser_name": "rapid_ocr",
            "status": "running",
            "raw_text": "",
            "markdown_text": None,
            "preview_text": "",
            "page_count": 0,
            "char_count": 0,
            "quality_score": 0.0,
            "is_ocr": True,
            "warnings": [],
            "fallback_from": None,
            "fallback_reason": None,
            "preview_pages": [],
        },
    )

    store.add_preview_page(
        draft_id=draft.id,
        parse_result_id=running.id,
        page_number=1,
        content_type="text",
        content="page one",
    )

    page = store.get_preview_page(draft.id, running.id, 1)

    assert page is not None
    assert page.page_number == 1
    assert page.content_type == "text"
    assert page.content == "page one"
