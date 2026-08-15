from __future__ import annotations

from collections.abc import Iterator

import pytest


def _time_sequence(*values: str) -> Iterator[str]:
    yield from values


def test_web_draft_store_promotes_saved_result() -> None:
    from backend.app.services.web_draft_store import WebDraftStore

    store = WebDraftStore()
    draft = store.create_shell_draft(
        url="https://example.com/article",
        title="Example",
        source_name="example.com",
    )
    result = store.add_parse_result(
        draft_id=draft.id,
        parse_result={
            "parser_name": "playwright_dom",
            "status": "preview",
            "raw_text": "alpha",
            "markdown_text": "# Alpha",
            "preview_text": "# Alpha",
            "section_count": 1,
            "char_count": 5,
            "quality_score": 0.9,
            "warnings": [],
            "auth_mode": "browser_profile",
            "preview_pages": [],
        },
    )

    draft_before = store.get_draft(draft.id)
    draft_before_updated_at = draft_before.updated_at if draft_before is not None else None
    store.promote_saved_result(draft.id, result.id)
    updated = store.get_draft(draft.id)

    assert draft_before is not None
    assert updated is not None
    assert updated.saved_parse_result_id == result.id
    assert updated.latest_preview_result_id == result.id
    assert updated.parse_results[0].status == "saved"
    assert draft_before_updated_at is not None
    assert updated.updated_at >= draft_before_updated_at


def test_web_draft_store_updates_latest_and_timestamp(monkeypatch: pytest.MonkeyPatch) -> None:
    import backend.app.services.web_draft_store as web_draft_store

    times = iter(
        _time_sequence(
            "2026-04-20T00:00:00Z",
            "2026-04-20T00:00:05Z",
            "2026-04-20T00:00:06Z",
        )
    )
    monkeypatch.setattr(web_draft_store, "utc_now", lambda: next(times))

    store = web_draft_store.WebDraftStore()
    draft = store.create_shell_draft(
        url="https://example.com/article",
        title=None,
        source_name="example.com",
    )

    result = store.add_parse_result(
        draft_id=draft.id,
        parse_result={
            "parser_name": "playwright_dom",
            "status": "preview",
            "raw_text": "alpha",
            "markdown_text": "# Alpha",
            "preview_text": "# Alpha",
            "section_count": 1,
            "char_count": 5,
            "quality_score": 0.9,
            "warnings": [],
            "auth_mode": "browser_profile",
            "preview_pages": [],
        },
    )

    updated = store.get_draft(draft.id)
    assert updated is not None
    assert updated.updated_at == "2026-04-20T00:00:06Z"
    assert updated.latest_preview_result_id == result.id
    assert updated.saved_parse_result_id is None
    assert updated.parse_results[-1].id == result.id


def test_web_draft_store_preview_pages_are_readable(monkeypatch: pytest.MonkeyPatch) -> None:
    import backend.app.services.web_draft_store as web_draft_store

    monkeypatch.setattr(web_draft_store, "utc_now", lambda: "2026-04-20T00:00:00Z")

    store = web_draft_store.WebDraftStore()
    draft = store.create_shell_draft(
        url="https://example.com/article",
        title="Example",
        source_name="example.com",
    )
    result = store.add_parse_result(
        draft_id=draft.id,
        parse_result={
            "parser_name": "playwright_dom",
            "status": "preview",
            "raw_text": "alpha",
            "markdown_text": "# Alpha",
            "preview_text": "# Alpha",
            "section_count": 1,
            "char_count": 5,
            "quality_score": 0.9,
            "warnings": [],
            "auth_mode": "browser_profile",
            "preview_pages": [],
        },
    )

    page = store.add_preview_page(
        draft_id=draft.id,
        parse_result_id=result.id,
        page_number=1,
        content_type="text",
        content="page one",
    )

    latest = store.get_preview_page(draft.id, result.id, 1)

    assert page.page_number == 1
    assert latest is not None
    assert latest.content_type == "text"
    assert latest.content == "page one"


def test_web_draft_store_preview_page_overwrite_replaces_existing_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import backend.app.services.web_draft_store as web_draft_store

    monkeypatch.setattr(web_draft_store, "utc_now", lambda: "2026-04-20T00:00:00Z")

    store = web_draft_store.WebDraftStore()
    draft = store.create_shell_draft(
        url="https://example.com/article",
        title="Example",
        source_name="example.com",
    )
    result = store.add_parse_result(
        draft_id=draft.id,
        parse_result={
            "parser_name": "playwright_dom",
            "status": "preview",
            "raw_text": "alpha",
            "markdown_text": "# Alpha",
            "preview_text": "# Alpha",
            "section_count": 1,
            "char_count": 5,
            "quality_score": 0.9,
            "warnings": [],
            "auth_mode": "browser_profile",
            "preview_pages": [],
        },
    )

    store.add_preview_page(
        draft_id=draft.id,
        parse_result_id=result.id,
        page_number=1,
        content_type="text",
        content="page one",
    )
    store.add_preview_page(
        draft_id=draft.id,
        parse_result_id=result.id,
        page_number=1,
        content_type="text",
        content="page one updated",
    )

    latest = store.get_preview_page(draft.id, result.id, 1)
    assert latest is not None
    assert latest.content == "page one updated"
    assert [page.content for page in store.get_draft(draft.id).parse_results[0].preview_pages] == [
        "page one updated"
    ]


def test_web_draft_store_preview_pages_are_sorted_by_page_number(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import backend.app.services.web_draft_store as web_draft_store

    monkeypatch.setattr(web_draft_store, "utc_now", lambda: "2026-04-20T00:00:00Z")

    store = web_draft_store.WebDraftStore()
    draft = store.create_shell_draft(
        url="https://example.com/article",
        title="Example",
        source_name="example.com",
    )
    result = store.add_parse_result(
        draft_id=draft.id,
        parse_result={
            "parser_name": "playwright_dom",
            "status": "preview",
            "raw_text": "alpha",
            "markdown_text": "# Alpha",
            "preview_text": "# Alpha",
            "section_count": 1,
            "char_count": 5,
            "quality_score": 0.9,
            "warnings": [],
            "auth_mode": "browser_profile",
            "preview_pages": [],
        },
    )

    store.add_preview_page(
        draft_id=draft.id,
        parse_result_id=result.id,
        page_number=3,
        content_type="text",
        content="page three",
    )
    store.add_preview_page(
        draft_id=draft.id,
        parse_result_id=result.id,
        page_number=1,
        content_type="text",
        content="page one",
    )
    store.add_preview_page(
        draft_id=draft.id,
        parse_result_id=result.id,
        page_number=2,
        content_type="text",
        content="page two",
    )

    latest = store.get_draft(draft.id)
    assert latest is not None
    assert [page.page_number for page in latest.parse_results[0].preview_pages] == [1, 2, 3]


def test_web_draft_store_missing_preview_page_lookup_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import backend.app.services.web_draft_store as web_draft_store

    monkeypatch.setattr(web_draft_store, "utc_now", lambda: "2026-04-20T00:00:00Z")

    store = web_draft_store.WebDraftStore()
    draft = store.create_shell_draft(
        url="https://example.com/article",
        title="Example",
        source_name="example.com",
    )
    result = store.add_parse_result(
        draft_id=draft.id,
        parse_result={
            "parser_name": "playwright_dom",
            "status": "preview",
            "raw_text": "alpha",
            "markdown_text": "# Alpha",
            "preview_text": "# Alpha",
            "section_count": 1,
            "char_count": 5,
            "quality_score": 0.9,
            "warnings": [],
            "auth_mode": "browser_profile",
            "preview_pages": [],
        },
    )

    assert store.get_preview_page("missing-draft", result.id, 1) is None
    assert store.get_preview_page(draft.id, "missing-result", 1) is None
    assert store.get_preview_page(draft.id, result.id, 99) is None


def test_web_draft_store_add_preview_page_updates_draft_parse_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import backend.app.services.web_draft_store as web_draft_store

    monkeypatch.setattr(web_draft_store, "utc_now", lambda: "2026-04-20T00:00:00Z")

    store = web_draft_store.WebDraftStore()
    draft = store.create_shell_draft(
        url="https://example.com/article",
        title="Example",
        source_name="example.com",
    )
    result = store.add_parse_result(
        draft_id=draft.id,
        parse_result={
            "parser_name": "playwright_dom",
            "status": "preview",
            "raw_text": "alpha",
            "markdown_text": "# Alpha",
            "preview_text": "# Alpha",
            "section_count": 1,
            "char_count": 5,
            "quality_score": 0.9,
            "warnings": [],
            "auth_mode": "browser_profile",
            "preview_pages": [],
        },
    )

    store.add_preview_page(
        draft_id=draft.id,
        parse_result_id=result.id,
        page_number=1,
        content_type="text",
        content="page one",
    )

    updated = store.get_draft(draft.id)
    assert updated is not None
    assert updated.parse_results[0].preview_pages[0].content == "page one"
