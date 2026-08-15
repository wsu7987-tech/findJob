from __future__ import annotations

import sqlite3

from fastapi.testclient import TestClient


def test_create_and_list_pool_items(configured_client: TestClient) -> None:
    create_response = configured_client.post(
        "/api/pool/items",
        json={
            "source_type": "text",
            "source_value": "note-1",
            "title": "First note",
            "raw_text": "A short note for the summary pool.",
        },
    )

    assert create_response.status_code == 201
    created_item = create_response.json()["item"]
    assert created_item["source_type"] == "text"
    assert created_item["source_value"] == "note-1"
    assert created_item["cleaning_level"] is None
    assert created_item["current_status"] == "pending"
    assert created_item["is_deleted"] is False
    assert created_item["was_resummarized"] is False

    list_response = configured_client.get("/api/pool/items")

    assert list_response.status_code == 200
    assert list_response.json() == {
        "items": [created_item],
        "total": 1,
    }


def test_create_text_pool_item_generates_ai_tags_separately_from_user_tags(
    configured_client: TestClient,
    configured_app_paths: dict[str, str],
) -> None:
    response = configured_client.post(
        "/api/pool/items",
        json={
            "source_type": "text",
            "source_value": "ai-tags-note",
            "title": "Backend API indexing note",
            "raw_text": "This note discusses backend API design and database indexing.",
            "tags": ["manual-tag"],
        },
    )

    assert response.status_code == 201

    knowledge_item_id = response.json()["item"]["knowledge_item_id"]
    connection = sqlite3.connect(configured_app_paths["sqlite_path"])
    connection.row_factory = sqlite3.Row
    try:
        row = connection.execute(
            """
            SELECT user_tags_json, ai_tags_json
            FROM knowledge_items
            WHERE id = ?
            """,
            (knowledge_item_id,),
        ).fetchone()
    finally:
        connection.close()

    assert row is not None
    assert row["user_tags_json"] == '["manual-tag"]'
    assert row["ai_tags_json"] != "[]"
    assert "backend" in row["ai_tags_json"].lower()


def test_create_text_pool_item_persists_quick_capture_metadata(
    configured_client: TestClient,
    configured_app_paths: dict[str, str],
) -> None:
    response = configured_client.post(
        "/api/pool/items",
        json={
            "source_type": "text",
            "source_value": "quick-capture",
            "title": "Inbox note",
            "raw_text": "Body text",
            "capture_source": "screenshot_ocr",
            "captured_at": "2026-04-21T10:30:00+08:00",
            "category": "Research",
            "tags": ["ocr", "competitor"],
        },
    )

    assert response.status_code == 201

    knowledge_item_id = response.json()["item"]["knowledge_item_id"]
    connection = sqlite3.connect(configured_app_paths["sqlite_path"])
    connection.row_factory = sqlite3.Row
    try:
        row = connection.execute(
            """
            SELECT
              capture_source,
              captured_at,
              capture_category,
              capture_tags_json,
              user_tags_json,
              ai_tags_json,
              raw_content
            FROM knowledge_items
            WHERE id = ?
            """,
            (knowledge_item_id,),
        ).fetchone()
    finally:
        connection.close()

    assert row is not None
    assert row["capture_source"] == "screenshot_ocr"
    assert row["captured_at"] == "2026-04-21T10:30:00+08:00"
    assert row["capture_category"] == "Research"
    assert row["capture_tags_json"] == '["ocr", "competitor"]'
    assert row["user_tags_json"] == '["ocr", "competitor"]'
    assert row["ai_tags_json"] != "[]"
    assert row["raw_content"] == "Body text"


def test_delete_pool_item_hides_it_from_default_listing(
    configured_client: TestClient,
) -> None:
    create_response = configured_client.post(
        "/api/pool/items",
        json={
            "source_type": "text",
            "source_value": "note-to-delete",
            "title": "Delete me",
            "raw_text": "This item should disappear from the default pool list.",
        },
    )
    item_id = create_response.json()["item"]["id"]

    delete_response = configured_client.delete(f"/api/pool/items/{item_id}")

    assert delete_response.status_code == 200
    assert delete_response.json() == {"deleted": True}

    list_response = configured_client.get("/api/pool/items")

    assert list_response.status_code == 200
    assert list_response.json() == {
        "items": [],
        "total": 0,
    }


def test_create_pool_item_restores_deleted_item_for_same_source(
    configured_client: TestClient,
    configured_app_paths: dict[str, str],
) -> None:
    first_response = configured_client.post(
        "/api/pool/items",
        json={
            "source_type": "text",
            "source_value": "note-restore",
            "title": "Original title",
            "raw_text": "original body",
            "category": "original-category",
            "tags": ["old-tag"],
        },
    )
    original_item = first_response.json()["item"]
    delete_response = configured_client.delete(f"/api/pool/items/{original_item['id']}")

    assert delete_response.status_code == 200

    second_response = configured_client.post(
        "/api/pool/items",
        json={
            "source_type": "text",
            "source_value": "note-restore",
            "title": "Updated title",
            "raw_text": "updated body",
            "category": "updated-category",
            "tags": ["new-tag"],
        },
    )

    assert second_response.status_code == 201
    restored_item = second_response.json()["item"]
    assert restored_item["id"] == original_item["id"]
    assert restored_item["knowledge_item_id"] == original_item["knowledge_item_id"]
    assert restored_item["title"] == "Updated title"
    assert restored_item["is_deleted"] is False
    assert restored_item["current_status"] == "pending"

    connection = sqlite3.connect(configured_app_paths["sqlite_path"])
    connection.row_factory = sqlite3.Row
    try:
        row = connection.execute(
            """
            SELECT
              ki.title,
              ki.raw_content,
              ki.capture_category,
              ki.user_tags_json,
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
    assert row["title"] == "Updated title"
    assert row["raw_content"] == "updated body"
    assert row["capture_category"] == "updated-category"
    assert row["user_tags_json"] == '["new-tag"]'
    assert row["is_deleted"] == 0


def test_resummarize_marks_pool_item_for_processing(
    configured_client: TestClient,
) -> None:
    create_response = configured_client.post(
        "/api/pool/items",
        json={
            "source_type": "text",
            "source_value": "note-resummarize",
            "title": "Resummarize me",
            "raw_text": "The pool entry should be marked for another summary run.",
        },
    )
    item_id = create_response.json()["item"]["id"]

    resummarize_response = configured_client.post(
        f"/api/pool/items/{item_id}/resummarize"
    )

    assert resummarize_response.status_code == 202
    assert resummarize_response.json() == {"accepted": True}

    list_response = configured_client.get("/api/pool/items")
    updated_item = list_response.json()["items"][0]

    assert updated_item["id"] == item_id
    assert updated_item["current_status"] == "pending"
    assert updated_item["was_resummarized"] is True


def test_reingest_pool_item_rebuilds_document_chunks_and_keeps_item_visible(
    configured_client: TestClient,
    monkeypatch,
) -> None:
    captured_calls: list[tuple[str, str]] = []

    def _fake_refresh(*, connection, config, knowledge_item_id: str, raw_content: str) -> None:
        del connection, config
        captured_calls.append((knowledge_item_id, raw_content))

    monkeypatch.setattr("backend.app.services.pool.refresh_document_chunks", _fake_refresh)

    create_response = configured_client.post(
        "/api/pool/items",
        json={
            "source_type": "text",
            "source_value": "note-reingest",
            "title": "Reingest me",
            "raw_text": "The pool entry should rebuild chunk embeddings.",
        },
    )
    item = create_response.json()["item"]
    captured_calls.clear()

    reingest_response = configured_client.post(
        f"/api/pool/items/{item['id']}/reingest"
    )

    assert reingest_response.status_code == 202
    assert reingest_response.json() == {"accepted": True}
    assert captured_calls == [
        (
            item["knowledge_item_id"],
            "The pool entry should rebuild chunk embeddings.",
        )
    ]

    list_response = configured_client.get("/api/pool/items")
    updated_item = list_response.json()["items"][0]

    assert updated_item["id"] == item["id"]
    assert updated_item["current_status"] == "pending"
    assert updated_item["was_resummarized"] is True


def test_create_pool_item_rejects_invalid_source_type(
    configured_client: TestClient,
) -> None:
    response = configured_client.post(
        "/api/pool/items",
        json={
            "source_type": "video",
            "source_value": "note-invalid",
            "title": "Invalid source",
            "raw_text": "This should be rejected before duplicate handling.",
        },
    )

    assert response.status_code == 422
    response_text = response.text.lower()
    assert "source_type" in response_text
    assert "already exists" not in response_text


def test_create_pool_item_persists_document_chunks(
    configured_client: TestClient,
    configured_app_paths: dict[str, str],
) -> None:
    response = configured_client.post(
        "/api/pool/items",
        json={
            "source_type": "text",
            "source_value": "chunked-note",
            "title": "Chunked note",
            "raw_text": "## A\n"
            + ("Alpha sentence. " * 80)
            + "\n\n## B\n"
            + ("Beta sentence. " * 80),
        },
    )

    assert response.status_code == 201
    knowledge_item_id = response.json()["item"]["knowledge_item_id"]

    connection = sqlite3.connect(configured_app_paths["sqlite_path"])
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """
            SELECT chunk_level, parent_chunk_id, vector_point_id
            FROM document_chunks
            WHERE knowledge_item_id = ?
            ORDER BY chunk_level, position
            """,
            (knowledge_item_id,),
        ).fetchall()
    finally:
        connection.close()

    assert any(row["chunk_level"] == "parent" for row in rows)
    assert any(row["chunk_level"] == "child" for row in rows)
    assert all(
        row["vector_point_id"] is None for row in rows if row["chunk_level"] == "parent"
    )
    assert all(
        row["vector_point_id"] is not None for row in rows if row["chunk_level"] == "child"
    )


def test_create_pool_item_rolls_back_when_chunk_indexing_fails(
    configured_client_no_raise: TestClient,
    configured_app_paths: dict[str, str],
    monkeypatch,
) -> None:
    from backend.app.services import pool as pool_service

    def _boom(*args, **kwargs):
        raise RuntimeError("chunk indexing exploded")

    monkeypatch.setattr(pool_service, "_index_document_chunks", _boom)

    response = configured_client_no_raise.post(
        "/api/pool/items",
        json={
            "source_type": "text",
            "source_value": "broken-chunked-note",
            "title": "Broken chunked note",
            "raw_text": "Chunk me but fail indexing.",
        },
    )

    assert response.status_code == 500

    connection = sqlite3.connect(configured_app_paths["sqlite_path"])
    try:
        knowledge_items = connection.execute("SELECT COUNT(*) FROM knowledge_items").fetchone()[0]
        pool_entries = connection.execute("SELECT COUNT(*) FROM pool_entries").fetchone()[0]
        document_chunks = connection.execute("SELECT COUNT(*) FROM document_chunks").fetchone()[0]
    finally:
        connection.close()

    assert knowledge_items == 0
    assert pool_entries == 0
    assert document_chunks == 0


def test_create_pool_item_for_pdf_uses_saved_parse_result_content(
    configured_client: TestClient,
    configured_app_paths: dict[str, str],
    monkeypatch,
) -> None:
    pdf_path = configured_app_paths["app_data_dir"] / "doc.pdf"
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    pdf_path.write_bytes(b"%PDF-1.4 fake")

    monkeypatch.setattr(
        "backend.app.services.pool.parse_and_activate_initial_pdf",
        lambda **kwargs: {
            "raw_text": "pdf parsed body",
            "preview_text": "pdf parsed body",
            "parser_name": "pymupdf4llm_markdown",
        },
    )

    response = configured_client.post(
        "/api/pool/items",
        json={"source_type": "pdf", "source_value": str(pdf_path)},
    )

    assert response.status_code == 201


def test_create_url_pool_item_uses_structured_web_capture_content(
    configured_client: TestClient,
    configured_app_paths: dict[str, str],
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
                    {"page_number": 2, "content_type": "markdown", "content": "Structured body"},
                ],
                "warnings": [],
            }

    monkeypatch.setattr(
        "backend.app.services.pool.build_default_web_capture_service",
        lambda: _FakeCaptureService(),
    )
    monkeypatch.setattr("backend.app.services.pool._index_document_chunks", lambda *a, **k: None)

    response = configured_client.post(
        "/api/pool/items",
        json={"source_type": "url", "source_value": "https://example.com/a", "title": "Draft"},
    )

    assert response.status_code == 201
    knowledge_item_id = response.json()["item"]["knowledge_item_id"]

    connection = sqlite3.connect(configured_app_paths["sqlite_path"])
    connection.row_factory = sqlite3.Row
    try:
        row = connection.execute(
            """
            SELECT
              ki.raw_content,
              ki.active_parse_result_id,
              dpr.parser_name,
              dpr.raw_text,
              dpr.markdown_text
            FROM knowledge_items AS ki
            JOIN document_parse_results AS dpr ON dpr.id = ki.active_parse_result_id
            WHERE ki.id = ?
            """,
            (knowledge_item_id,),
        ).fetchone()
    finally:
        connection.close()

    assert row is not None
    assert row["raw_content"] == "# Captured\n\nStructured body"
    assert row["active_parse_result_id"] is not None
    assert row["parser_name"] == "playwright_dom"
    assert row["raw_text"] == "plain:https://example.com/a"
    assert row["markdown_text"] == "# Captured\n\nStructured body"


def test_create_markdown_pool_item_persists_inline_markdown_parse_result(
    configured_client: TestClient,
    configured_app_paths: dict[str, str],
    monkeypatch,
) -> None:
    monkeypatch.setattr("backend.app.services.pool._index_document_chunks", lambda *a, **k: None)

    response = configured_client.post(
        "/api/pool/items",
        json={
            "source_type": "markdown",
            "source_value": "note.md",
            "title": "Draft",
            "raw_text": "# Inline title\n\nBody paragraph",
        },
    )

    assert response.status_code == 201
    knowledge_item_id = response.json()["item"]["knowledge_item_id"]

    connection = sqlite3.connect(configured_app_paths["sqlite_path"])
    connection.row_factory = sqlite3.Row
    try:
        row = connection.execute(
            """
            SELECT
              ki.raw_content,
              ki.active_parse_result_id,
              dpr.parser_name,
              dpr.raw_text,
              dpr.markdown_text
            FROM knowledge_items AS ki
            JOIN document_parse_results AS dpr ON dpr.id = ki.active_parse_result_id
            WHERE ki.id = ?
            """,
            (knowledge_item_id,),
        ).fetchone()
    finally:
        connection.close()

    assert row is not None
    assert row["raw_content"] == "# Inline title\n\nBody paragraph"
    assert row["active_parse_result_id"] is not None
    assert row["parser_name"] == "inline_markdown"
    assert row["raw_text"] == "Inline title\n\nBody paragraph"
    assert row["markdown_text"] == "# Inline title\n\nBody paragraph"


def test_suggest_pool_metadata_returns_category_and_tags(
    configured_client: TestClient,
) -> None:
    response = configured_client.post(
        "/api/pool/metadata-suggestions",
        json={
            "source_type": "text",
            "source_value": "note-1",
            "title": "Backend agent workflow note",
            "raw_text": (
                "This note discusses backend API design, database indexing, "
                "and engineering trade-offs."
            ),
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["category"] == "engineering"
    assert "backend" in payload["tags"]
    assert payload["strategy"] == "heuristic"
