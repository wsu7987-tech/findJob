from __future__ import annotations

import sqlite3

from fastapi.testclient import TestClient


def test_parse_result_route_is_registered(configured_client: TestClient) -> None:
    route_paths = {route.path for route in configured_client.app.routes}
    assert "/api/items/{knowledge_item_id}/parse-result" in route_paths


def test_get_active_parse_result_returns_saved_markdown_item_parse_result(
    configured_client: TestClient,
    monkeypatch,
) -> None:
    monkeypatch.setattr("backend.app.services.pool._index_document_chunks", lambda *a, **k: None)

    create_response = configured_client.post(
        "/api/pool/items",
        json={
            "source_type": "markdown",
            "source_value": "note.md",
            "title": "Draft",
            "raw_text": "# Inline title\n\nBody paragraph",
        },
    )

    assert create_response.status_code == 201
    knowledge_item_id = create_response.json()["item"]["knowledge_item_id"]

    response = configured_client.get(f"/api/items/{knowledge_item_id}/parse-result")

    assert response.status_code == 200
    payload = response.json()["parse_result"]
    assert payload["knowledge_item_id"] == knowledge_item_id
    assert payload["source_type"] == "markdown"
    assert payload["parser_name"] == "inline_markdown"
    assert payload["status"] == "saved"
    assert payload["raw_text"] == "Inline title\n\nBody paragraph"
    assert payload["markdown_text"] == "# Inline title\n\nBody paragraph"
    assert payload["canonical_content"] == "# Inline title\n\nBody paragraph"


def test_get_active_parse_result_returns_saved_url_parse_result(
    configured_client: TestClient,
    monkeypatch,
) -> None:
    class _FakeCaptureService:
        def capture_url(
            self,
            *,
            url,
            parser_name,
            session_profile_id,
            cancel_check=None,
        ):
            del parser_name, session_profile_id, cancel_check
            return {
                "title": "Captured page",
                "source_name": "example.com",
                "auth_mode": "none",
                "raw_text": f"plain:{url}",
                "markdown_text": "# Captured\n\nStructured body",
                "preview_text": "# Captured",
                "preview_pages": [
                    {"page_number": 1, "content_type": "markdown", "content": "# Captured"},
                ],
                "warnings": [],
            }

    monkeypatch.setattr(
        "backend.app.services.pool.build_default_web_capture_service",
        lambda: _FakeCaptureService(),
    )
    monkeypatch.setattr("backend.app.services.pool._index_document_chunks", lambda *a, **k: None)

    create_response = configured_client.post(
        "/api/pool/items",
        json={"source_type": "url", "source_value": "https://example.com/a"},
    )

    assert create_response.status_code == 201
    knowledge_item_id = create_response.json()["item"]["knowledge_item_id"]

    response = configured_client.get(f"/api/items/{knowledge_item_id}/parse-result")

    assert response.status_code == 200
    payload = response.json()["parse_result"]
    assert payload["source_type"] == "url"
    assert payload["parser_name"] == "playwright_dom"
    assert payload["raw_text"] == "plain:https://example.com/a"
    assert payload["markdown_text"] == "# Captured\n\nStructured body"
    assert payload["canonical_content"] == "# Captured\n\nStructured body"


def test_get_active_parse_result_returns_not_found_when_item_has_no_parse_result(
    configured_client: TestClient,
    configured_app_paths: dict[str, str],
    monkeypatch,
) -> None:
    monkeypatch.setattr("backend.app.services.pool._index_document_chunks", lambda *a, **k: None)
    create_response = configured_client.post(
        "/api/pool/items",
        json={
            "source_type": "text",
            "source_value": "plain-note",
            "title": "Plain note",
            "raw_text": "No parse result for plain text items.",
        },
    )

    assert create_response.status_code == 201
    knowledge_item_id = create_response.json()["item"]["knowledge_item_id"]

    connection = sqlite3.connect(configured_app_paths["sqlite_path"])
    connection.row_factory = sqlite3.Row
    try:
        row = connection.execute(
            "SELECT active_parse_result_id FROM knowledge_items WHERE id = ?",
            (knowledge_item_id,),
        ).fetchone()
    finally:
        connection.close()

    assert row is not None
    assert row["active_parse_result_id"] is None

    response = configured_client.get(f"/api/items/{knowledge_item_id}/parse-result")

    assert response.status_code == 404
    assert response.json()["error_message"] == "Active parse result not found."
