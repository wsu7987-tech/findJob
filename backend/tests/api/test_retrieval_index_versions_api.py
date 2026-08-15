from __future__ import annotations

from fastapi.testclient import TestClient


def test_retrieval_index_versions_list_includes_legacy_default(configured_client: TestClient) -> None:
    response = configured_client.get("/api/retrieval/index-versions")

    assert response.status_code == 200
    payload = response.json()
    assert payload["items"][0]["version_tag"] == "legacy"
    assert payload["items"][0]["status"] == "active"


def test_retrieval_index_versions_reject_activate_before_rebuild(configured_client: TestClient) -> None:
    create_response = configured_client.post(
        "/api/retrieval/index-versions",
        json={"version_tag": "phase2-hybrid"},
    )

    assert create_response.status_code == 200
    created = create_response.json()
    assert created["version_tag"] == "phase2-hybrid"
    assert created["status"] == "candidate"

    activate_response = configured_client.post(
        f"/api/retrieval/index-versions/{created['id']}/activate"
    )

    assert activate_response.status_code == 409
    assert activate_response.json()["error_category"] == "VALIDATION_FAILED"
    assert "rebuild" in activate_response.json()["error_message"].lower()


def test_retrieval_index_versions_can_rebuild_candidate(configured_client: TestClient) -> None:
    from backend.app.dependencies import get_database

    db = configured_client.app.dependency_overrides.get(get_database)
    if db is None:
        db = configured_client.app.state.db

    with db.connect() as connection:
        connection.execute(
            """
            INSERT INTO knowledge_items (
              id, source_type, source_value, title, raw_content, source_name, created_at, updated_at
            ) VALUES (?, 'text', 'source-1', 'Title', 'Candidate rebuild content', 'source-1', '2026-04-17T00:00:00Z', '2026-04-17T00:00:00Z')
            """,
            ("ki-1",),
        )
        connection.execute(
            """
            INSERT INTO document_chunks (
              id, knowledge_item_id, parent_chunk_id, chunk_level, section_title, content, position, token_estimate, created_at
            ) VALUES
              ('parent-1', 'ki-1', NULL, 'parent', 'Section A', 'Parent content', 0, 3, '2026-04-17T00:00:00Z'),
              ('child-1', 'ki-1', 'parent-1', 'child', 'Section A', 'Candidate rebuild content', 0, 3, '2026-04-17T00:00:00Z')
            """
        )

    create_response = configured_client.post(
        "/api/retrieval/index-versions",
        json={"version_tag": "phase2-hybrid"},
    )
    version_id = create_response.json()["id"]

    rebuild_response = configured_client.post(
        f"/api/retrieval/index-versions/{version_id}/rebuild"
    )

    assert rebuild_response.status_code == 200
    payload = rebuild_response.json()
    assert payload["knowledge_item_count"] == 1
    assert payload["chunk_count"] == 1


def test_retrieval_index_versions_can_activate_after_rebuild(configured_client: TestClient) -> None:
    from backend.app.dependencies import get_database

    db = configured_client.app.dependency_overrides.get(get_database)
    if db is None:
        db = configured_client.app.state.db

    with db.connect() as connection:
        connection.execute(
            """
            INSERT INTO knowledge_items (
              id, source_type, source_value, title, raw_content, source_name, created_at, updated_at
            ) VALUES (?, 'text', 'source-activate', 'Title', 'Activate rebuild content', 'source-activate', '2026-04-17T00:00:00Z', '2026-04-17T00:00:00Z')
            """,
            ("ki-activate",),
        )
        connection.execute(
            """
            INSERT INTO document_chunks (
              id, knowledge_item_id, parent_chunk_id, chunk_level, section_title, content, position, token_estimate, created_at
            ) VALUES
              ('parent-activate', 'ki-activate', NULL, 'parent', 'Section A', 'Parent content', 0, 3, '2026-04-17T00:00:00Z'),
              ('child-activate', 'ki-activate', 'parent-activate', 'child', 'Section A', 'Activate rebuild content', 0, 3, '2026-04-17T00:00:00Z')
            """
        )

    create_response = configured_client.post(
        "/api/retrieval/index-versions",
        json={"version_tag": "phase2-ready"},
    )
    version_id = create_response.json()["id"]

    rebuild_response = configured_client.post(
        f"/api/retrieval/index-versions/{version_id}/rebuild"
    )
    activate_response = configured_client.post(
        f"/api/retrieval/index-versions/{version_id}/activate"
    )

    assert rebuild_response.status_code == 200
    assert activate_response.status_code == 200
    assert activate_response.json()["version_tag"] == "phase2-ready"
    assert activate_response.json()["status"] == "active"
