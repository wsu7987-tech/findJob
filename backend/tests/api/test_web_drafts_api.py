from __future__ import annotations

from typing import get_type_hints

from fastapi.testclient import TestClient
import pytest

from backend.app.main import create_app
from backend.app.schemas.web_drafts import WebDraftParserName, WebReparseJobResponse
from backend.app.services.web_reparse_job_store import WebReparseJob


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


@pytest.fixture
def app_client() -> TestClient:
    return TestClient(create_app())


def test_web_draft_routes_are_registered(app_client: TestClient) -> None:
    response = app_client.get("/api/web/drafts/jobs")

    assert response.status_code == 200
    assert response.json() == {"jobs": []}


def test_web_reparse_job_parser_name_uses_schema_alias() -> None:
    assert get_type_hints(WebReparseJob)["parser_name"] is WebDraftParserName


def test_web_draft_jobs_route_serializes_non_empty_jobs(app_client: TestClient) -> None:
    job = WebReparseJob(
        id="job-1",
        draft_id="draft-1",
        parser_name="playwright_dom",
        status="queued",
        created_at="2026-04-20T00:00:00Z",
    )

    class _StubStore:
        def list_jobs(self, *, draft_id: str | None = None, active_only: bool = False):
            del draft_id, active_only
            return [job]

    app_client.app.state.web_reparse_job_store = _StubStore()

    response = app_client.get("/api/web/drafts/jobs")

    assert response.status_code == 200
    assert response.json() == {
        "jobs": [
            WebReparseJobResponse(
                id="job-1",
                draft_id="draft-1",
                parser_name="playwright_dom",
                status="queued",
                created_at="2026-04-20T00:00:00Z",
                started_at=None,
                finished_at=None,
                error_message=None,
                processed_pages=0,
                total_pages=0,
                latest_available_page=0,
                cancel_requested=False,
                preview_result_id=None,
            ).model_dump()
        ]
    }


def test_create_web_draft_returns_draft_payload(
    configured_client,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "backend.app.routers.web_drafts.build_default_web_capture_service",
        lambda: _FakeCaptureService(),
    )

    response = configured_client.post(
        "/api/web/drafts",
        json={"url": "https://example.com/a", "title": "Draft"},
    )

    assert response.status_code == 202
    payload = response.json()
    body = payload["draft"]
    assert payload["job"]["draft_id"] == body["id"]
    assert body["url"] == "https://example.com/a"
    assert body["parse_results"][0]["status"] in {"running", "saved"}


def test_reparse_web_draft_returns_preview_result(
    configured_client,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "backend.app.routers.web_drafts.build_default_web_capture_service",
        lambda: _FakeCaptureService(),
    )

    created = configured_client.post(
        "/api/web/drafts",
        json={"url": "https://example.com/a", "title": "Draft"},
    ).json()["draft"]

    response = configured_client.post(
        f"/api/web/drafts/{created['id']}/reparse",
        json={"parser_name": "playwright_dom", "session_profile_id": None},
    )

    assert response.status_code == 202
    payload = response.json()
    assert payload["job"]["draft_id"] == created["id"]
    assert payload["job"]["parser_name"] == "playwright_dom"

    job_id = payload["job"]["id"]
    job = configured_client.get(f"/api/web/drafts/{created['id']}/jobs/{job_id}")
    assert job.status_code == 200
    assert job.json()["job"]["status"] in {"running", "completed"}

    refreshed = configured_client.get(f"/api/web/drafts/{created['id']}").json()["draft"]
    parse_result_id = refreshed["latest_preview_result_id"]
    assert parse_result_id is not None
    page = configured_client.get(
        f"/api/web/drafts/{created['id']}/parse-results/{parse_result_id}/pages/1"
    )
    assert page.status_code == 200
    assert page.json()["page"]["page_number"] == 1


def test_save_web_draft_parse_result_promotes_saved_version(
    configured_client,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "backend.app.routers.web_drafts.build_default_web_capture_service",
        lambda: _FakeCaptureService(),
    )

    created = configured_client.post(
        "/api/web/drafts",
        json={"url": "https://example.com/a", "title": "Draft"},
    ).json()["draft"]
    reparsed = configured_client.post(
        f"/api/web/drafts/{created['id']}/reparse",
        json={"parser_name": "playwright_dom", "session_profile_id": None},
    ).json()["draft"]
    preview_id = reparsed["latest_preview_result_id"]

    response = configured_client.post(
        f"/api/web/drafts/{created['id']}/parse-results/{preview_id}/save"
    )

    assert response.status_code == 200
    body = response.json()["draft"]
    assert body["saved_parse_result_id"] == preview_id


def test_commit_web_draft_returns_pool_item(
    configured_client,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "backend.app.routers.web_drafts.build_default_web_capture_service",
        lambda: _FakeCaptureService(),
    )
    monkeypatch.setattr("backend.app.services.pool._index_document_chunks", lambda *a, **k: None)

    created = configured_client.post(
        "/api/web/drafts",
        json={"url": "https://example.com/a", "title": "Draft"},
    ).json()["draft"]
    reparsed = configured_client.post(
        f"/api/web/drafts/{created['id']}/reparse",
        json={"parser_name": "playwright_dom", "session_profile_id": None},
    ).json()["draft"]
    preview_id = reparsed["latest_preview_result_id"]
    configured_client.post(f"/api/web/drafts/{created['id']}/parse-results/{preview_id}/save")

    response = configured_client.post(f"/api/web/drafts/{created['id']}/commit")

    assert response.status_code == 201
    assert response.json()["item"]["source_type"] == "url"


def test_commit_web_draft_prefers_structured_markdown_for_canonical_content(
    configured_client,
    configured_app_paths,
    monkeypatch,
) -> None:
    import sqlite3

    monkeypatch.setattr(
        "backend.app.routers.web_drafts.build_default_web_capture_service",
        lambda: _FakeCaptureService(),
    )
    monkeypatch.setattr("backend.app.services.pool._index_document_chunks", lambda *a, **k: None)

    created = configured_client.post(
        "/api/web/drafts",
        json={"url": "https://example.com/a", "title": "Draft"},
    ).json()["draft"]

    response = configured_client.post(f"/api/web/drafts/{created['id']}/commit")

    assert response.status_code == 201
    knowledge_item_id = response.json()["item"]["knowledge_item_id"]

    connection = sqlite3.connect(configured_app_paths["sqlite_path"])
    connection.row_factory = sqlite3.Row
    try:
        row = connection.execute(
            """
            SELECT raw_content
            FROM knowledge_items
            WHERE id = ?
            """,
            (knowledge_item_id,),
        ).fetchone()
    finally:
        connection.close()

    assert row is not None
    assert row["raw_content"] == "# Alpha\n\nBody"


def test_commit_web_draft_persists_active_parse_result_for_inspection(
    configured_client,
    configured_app_paths,
    monkeypatch,
) -> None:
    import sqlite3

    monkeypatch.setattr(
        "backend.app.routers.web_drafts.build_default_web_capture_service",
        lambda: _FakeCaptureService(),
    )
    monkeypatch.setattr("backend.app.services.pool._index_document_chunks", lambda *a, **k: None)

    created = configured_client.post(
        "/api/web/drafts",
        json={"url": "https://example.com/a", "title": "Draft"},
    ).json()["draft"]
    reparsed = configured_client.post(
        f"/api/web/drafts/{created['id']}/reparse",
        json={"parser_name": "playwright_dom", "session_profile_id": None},
    ).json()["draft"]
    preview_id = reparsed["latest_preview_result_id"]
    configured_client.post(f"/api/web/drafts/{created['id']}/parse-results/{preview_id}/save")

    response = configured_client.post(f"/api/web/drafts/{created['id']}/commit")

    assert response.status_code == 201
    knowledge_item_id = response.json()["item"]["knowledge_item_id"]

    connection = sqlite3.connect(configured_app_paths["sqlite_path"])
    connection.row_factory = sqlite3.Row
    try:
        row = connection.execute(
            """
            SELECT
              ki.active_parse_result_id,
              dpr.parser_name,
              dpr.status,
              dpr.raw_text,
              dpr.markdown_text,
              dpr.preview_text,
              dpr.page_count,
              dpr.char_count,
              dpr.quality_score,
              dpr.is_ocr
            FROM knowledge_items AS ki
            JOIN document_parse_results AS dpr ON dpr.id = ki.active_parse_result_id
            WHERE ki.id = ?
            """,
            (knowledge_item_id,),
        ).fetchone()
    finally:
        connection.close()

    assert row is not None
    assert row["active_parse_result_id"] is not None
    assert row["parser_name"] == "playwright_dom"
    assert row["status"] == "saved"
    assert row["raw_text"] == "alpha:https://example.com/a"
    assert row["markdown_text"] == "# Alpha\n\nBody"
    assert row["preview_text"] == "# Alpha"
    assert row["page_count"] == 2
    assert row["char_count"] == len("alpha:https://example.com/a")
    assert row["quality_score"] > 0
    assert row["is_ocr"] == 0


def test_commit_web_draft_persists_category_and_tags(
    configured_client,
    configured_app_paths,
    monkeypatch,
) -> None:
    import sqlite3

    monkeypatch.setattr(
        "backend.app.routers.web_drafts.build_default_web_capture_service",
        lambda: _FakeCaptureService(),
    )
    monkeypatch.setattr("backend.app.services.pool._index_document_chunks", lambda *a, **k: None)

    created = configured_client.post(
        "/api/web/drafts",
        json={"url": "https://example.com/a", "title": "Draft"},
    ).json()["draft"]
    reparsed = configured_client.post(
        f"/api/web/drafts/{created['id']}/reparse",
        json={"parser_name": "playwright_dom", "session_profile_id": None},
    ).json()["draft"]
    preview_id = reparsed["latest_preview_result_id"]
    configured_client.post(f"/api/web/drafts/{created['id']}/parse-results/{preview_id}/save")

    response = configured_client.post(
        f"/api/web/drafts/{created['id']}/commit",
        json={"category": "research", "tags": ["url", "summary"]},
    )

    assert response.status_code == 201
    knowledge_item_id = response.json()["item"]["knowledge_item_id"]

    connection = sqlite3.connect(configured_app_paths["sqlite_path"])
    connection.row_factory = sqlite3.Row
    try:
        row = connection.execute(
            """
            SELECT capture_category, capture_tags_json
            FROM knowledge_items
            WHERE id = ?
            """,
            (knowledge_item_id,),
        ).fetchone()
    finally:
        connection.close()

    assert row is not None
    assert row["capture_category"] == "research"
    assert row["capture_tags_json"] == '["url", "summary"]'


def test_commit_web_draft_uses_cleaned_text_override(
    configured_client,
    configured_app_paths,
    monkeypatch,
) -> None:
    import sqlite3

    monkeypatch.setattr(
        "backend.app.routers.web_drafts.build_default_web_capture_service",
        lambda: _FakeCaptureService(),
    )
    monkeypatch.setattr("backend.app.services.pool._index_document_chunks", lambda *a, **k: None)

    created = configured_client.post(
        "/api/web/drafts",
        json={"url": "https://example.com/a", "title": "Draft"},
    ).json()["draft"]
    reparsed = configured_client.post(
        f"/api/web/drafts/{created['id']}/reparse",
        json={"parser_name": "playwright_dom", "session_profile_id": None},
    ).json()["draft"]
    preview_id = reparsed["latest_preview_result_id"]
    configured_client.post(f"/api/web/drafts/{created['id']}/parse-results/{preview_id}/save")

    response = configured_client.post(
        f"/api/web/drafts/{created['id']}/commit",
        json={"cleaned_text": "cleaned web body", "cleaning_level": "enhanced"},
    )

    assert response.status_code == 201
    knowledge_item_id = response.json()["item"]["knowledge_item_id"]

    connection = sqlite3.connect(configured_app_paths["sqlite_path"])
    connection.row_factory = sqlite3.Row
    try:
        row = connection.execute(
            """
            SELECT raw_content, cleaning_level
            FROM knowledge_items
            WHERE id = ?
            """,
            (knowledge_item_id,),
        ).fetchone()
    finally:
        connection.close()

    assert row is not None
    assert "cleaned web body" in row["raw_content"]
    assert "alpha:https://example.com/a" not in row["raw_content"]
    assert row["cleaning_level"] == "enhanced"


def test_commit_web_draft_restores_deleted_existing_source(
    configured_client,
    configured_app_paths,
    monkeypatch,
) -> None:
    import sqlite3

    monkeypatch.setattr(
        "backend.app.routers.web_drafts.build_default_web_capture_service",
        lambda: _FakeCaptureService(),
    )
    monkeypatch.setattr("backend.app.services.pool._index_document_chunks", lambda *a, **k: None)

    first_created = configured_client.post(
        "/api/web/drafts",
        json={"url": "https://example.com/restore", "title": "Draft"},
    ).json()["draft"]
    first_response = configured_client.post(f"/api/web/drafts/{first_created['id']}/commit")
    original_item = first_response.json()["item"]

    delete_response = configured_client.delete(f"/api/pool/items/{original_item['id']}")
    assert delete_response.status_code == 200

    second_created = configured_client.post(
        "/api/web/drafts",
        json={"url": "https://example.com/restore", "title": "Restored title"},
    ).json()["draft"]

    second_response = configured_client.post(
        f"/api/web/drafts/{second_created['id']}/commit",
        json={"category": "restored-category", "tags": ["restored-tag"]},
    )

    assert second_response.status_code == 201
    restored_item = second_response.json()["item"]
    assert restored_item["id"] == original_item["id"]
    assert restored_item["knowledge_item_id"] == original_item["knowledge_item_id"]
    assert restored_item["title"] == "Restored title"

    connection = sqlite3.connect(configured_app_paths["sqlite_path"])
    connection.row_factory = sqlite3.Row
    try:
        row = connection.execute(
            """
            SELECT
              ki.title,
              ki.capture_category,
              ki.user_tags_json,
              ki.active_parse_result_id,
              pe.is_deleted
            FROM knowledge_items AS ki
            JOIN pool_entries AS pe ON pe.knowledge_item_id = ki.id
            WHERE pe.id = ?
            """,
            (original_item["id"],),
        ).fetchone()
    finally:
        connection.close()

    assert row is not None
    assert row["title"] == "Restored title"
    assert row["capture_category"] == "restored-category"
    assert row["user_tags_json"] == '["restored-tag"]'
    assert row["active_parse_result_id"] is not None
    assert row["is_deleted"] == 0
