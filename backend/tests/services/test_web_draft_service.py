from __future__ import annotations


class _FakeCaptureService:
    def capture_url(
        self,
        *,
        url,
        parser_name,
        session_profile_id,
        cancel_check=None,
    ):
        del parser_name, session_profile_id
        if callable(cancel_check) and cancel_check():
            raise AssertionError("cancelled too early")
        return {
            "title": "Example Title",
            "source_name": "example.com",
            "auth_mode": "browser_profile",
            "raw_text": f"alpha:{url}",
            "markdown_text": "# Alpha\n\nBody",
            "preview_text": "# Alpha",
            "preview_pages": [
                {"page_number": 1, "content_type": "markdown", "content": "# Alpha"},
                {"page_number": 2, "content_type": "markdown", "content": "Body"},
            ],
            "warnings": [],
        }


def test_create_web_draft_starts_job_and_exposes_preview() -> None:
    from backend.app.services.web_draft_service import WebDraftService
    from backend.app.services.web_draft_store import WebDraftStore
    from backend.app.services.web_reparse_job_store import WebReparseJobStore

    service = WebDraftService(
        draft_store=WebDraftStore(),
        job_store=WebReparseJobStore(),
        capture_service=_FakeCaptureService(),
    )

    draft, job = service.start_create_draft(
        url="https://example.com/a",
        title="Example",
        session_profile_id=None,
    )

    assert draft.id == job.draft_id
    assert job.status in {"running", "completed"}
    reparsed = service.get_draft(draft.id)
    assert reparsed is not None
    assert reparsed.latest_preview_result_id is not None
    assert reparsed.session_profile_id is None


def test_reparse_web_draft_creates_preview_only() -> None:
    from backend.app.services.web_draft_service import WebDraftService
    from backend.app.services.web_draft_store import WebDraftStore

    service = WebDraftService(
        draft_store=WebDraftStore(),
        capture_service=_FakeCaptureService(),
    )
    draft = service.create_draft(
        url="https://example.com/a",
        title="Example",
        session_profile_id=None,
    )

    preview = service.reparse_draft(
        draft.id,
        parser_name="playwright_dom",
        session_profile_id=None,
    )

    assert preview.status == "preview"
    assert preview.id != draft.saved_parse_result_id
    updated = service.get_draft(draft.id)
    assert updated is not None
    assert updated.saved_parse_result_id != preview.id
    assert updated.latest_preview_result_id == preview.id


def test_reparse_web_draft_reuses_selected_session_profile() -> None:
    from backend.app.services.web_draft_service import WebDraftService
    from backend.app.services.web_draft_store import WebDraftStore

    captured_session_ids: list[str | None] = []

    class _CaptureService:
        def capture_url(self, *, session_profile_id, **kwargs):
            captured_session_ids.append(session_profile_id)
            return {
                "title": "Example Title",
                "source_name": "example.com",
                "auth_mode": "browser_profile",
                "raw_text": "alpha",
                "markdown_text": "# Alpha",
                "preview_text": "# Alpha",
                "preview_pages": [{"page_number": 1, "content_type": "markdown", "content": "# Alpha"}],
                "warnings": [],
            }

    service = WebDraftService(
        draft_store=WebDraftStore(),
        capture_service=_CaptureService(),
    )
    draft = service.create_draft(
        url="https://example.com/a",
        title="Example",
        session_profile_id="session-1",
    )

    preview = service.reparse_draft(
        draft.id,
        parser_name="playwright_dom",
        session_profile_id=None,
    )

    assert preview.status == "preview"
    assert captured_session_ids == ["session-1", "session-1"]


def test_commit_web_draft_creates_url_pool_item(
    test_db,
    configured_app_paths,
    monkeypatch,
) -> None:
    from backend.app.config import load_config
    from backend.app.services.web_draft_service import WebDraftService
    from backend.app.services.web_draft_store import WebDraftStore

    monkeypatch.setattr("backend.app.services.pool._index_document_chunks", lambda *a, **k: None)

    service = WebDraftService(
        draft_store=WebDraftStore(),
        capture_service=_FakeCaptureService(),
    )
    draft = service.create_draft(
        url="https://example.com/a",
        title="Example",
        session_profile_id=None,
    )
    preview = service.reparse_draft(
        draft.id,
        parser_name="playwright_dom",
        session_profile_id=None,
    )
    service.save_parse_result(draft.id, preview.id)

    item = service.commit_draft(
        db=test_db,
        config=load_config(),
        draft_id=draft.id,
    )

    assert item["source_type"] == "url"

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
    assert row["raw_content"] == "alpha:https://example.com/a"


def test_get_preview_page_rehydrates_missing_preview_pages() -> None:
    from backend.app.services.web_draft_service import WebDraftService
    from backend.app.services.web_draft_store import WebDraftStore

    store = WebDraftStore()
    service = WebDraftService(
        draft_store=store,
        capture_service=_FakeCaptureService(),
    )

    draft = service.create_draft(
        url="https://example.com/a",
        title="Example",
        session_profile_id=None,
    )
    parse_result = draft.parse_results[0]
    parse_result.preview_pages = []

    page = service.get_preview_page(
        draft_id=draft.id,
        parse_result_id=parse_result.id,
        page_number=1,
    )

    assert page.page_number == 1
    assert page.content.startswith("# Alpha")
