from __future__ import annotations

from backend.app.db import Database


def test_resolve_active_chunk_index_version_defaults_to_legacy(app_paths: dict[str, str]) -> None:
    from backend.app.services.retrieval_index_versions import resolve_active_chunk_index_version

    database = Database(app_paths["sqlite_path"])
    database.initialize()

    with database.connect() as connection:
        version = resolve_active_chunk_index_version(
            connection,
            provider_name="stub-embedding",
            model_name="stub-embedding-model",
        )

    assert version is None


def test_create_and_activate_chunk_index_version_roundtrip(app_paths: dict[str, str]) -> None:
    from backend.app.services.retrieval_index_versions import (
        activate_chunk_index_version,
        create_chunk_index_version,
        list_chunk_index_versions,
        rebuild_chunk_index_version,
        resolve_active_chunk_index_version,
    )
    from backend.app.config import load_config
    from backend.app.services.chunk_store import insert_document_chunks
    from backend.app.services.chunking import DocumentChunk

    database = Database(app_paths["sqlite_path"])
    database.initialize()
    config = load_config()
    parent = DocumentChunk(
        id="parent-1",
        knowledge_item_id="ki-1",
        parent_chunk_id=None,
        chunk_level="parent",
        section_title="Section A",
        content="Parent content",
        position=0,
        token_estimate=3,
    )
    child = DocumentChunk(
        id="child-1",
        knowledge_item_id="ki-1",
        parent_chunk_id="parent-1",
        chunk_level="child",
        section_title="Section A",
        content="Candidate rebuild content",
        position=0,
        token_estimate=3,
    )

    with database.connect() as connection:
        connection.execute(
            """
            INSERT INTO knowledge_items (
              id, source_type, source_value, title, raw_content, source_name, created_at, updated_at
            ) VALUES (?, 'text', 'source-1', 'Title', 'Candidate rebuild content', 'source-1', '2026-04-17T00:00:00Z', '2026-04-17T00:00:00Z')
            """,
            ("ki-1",),
        )
        insert_document_chunks(connection, [parent, child])
        candidate = create_chunk_index_version(
            connection,
            provider_name="stub-embedding",
            model_name="stub-embedding-model",
            version_tag="phase2-hybrid",
        )
        listed = list_chunk_index_versions(
            connection,
            provider_name="stub-embedding",
            model_name="stub-embedding-model",
        )
        rebuild_chunk_index_version(
            connection,
            config=config,
            version_id=candidate.id,
        )
        activate_chunk_index_version(
            connection,
            version_id=candidate.id,
            provider_name="stub-embedding",
            model_name="stub-embedding-model",
        )
        active_version = resolve_active_chunk_index_version(
            connection,
            provider_name="stub-embedding",
            model_name="stub-embedding-model",
        )
        relisted = list_chunk_index_versions(
            connection,
            provider_name="stub-embedding",
            model_name="stub-embedding-model",
        )

    assert candidate.version_tag == "phase2-hybrid"
    assert candidate.status == "candidate"
    assert listed[0].version_tag == "legacy"
    assert listed[1].version_tag == "phase2-hybrid"
    assert listed[1].collection_name.endswith("_v_phase2_hybrid")
    assert active_version == "phase2-hybrid"
    assert relisted[0].version_tag == "phase2-hybrid"
    assert relisted[0].status == "active"


def test_activate_chunk_index_version_rejects_unrebuilt_candidate(app_paths: dict[str, str]) -> None:
    from backend.app.errors import AppError
    from backend.app.services.retrieval_index_versions import (
        activate_chunk_index_version,
        create_chunk_index_version,
    )

    database = Database(app_paths["sqlite_path"])
    database.initialize()

    with database.connect() as connection:
        candidate = create_chunk_index_version(
            connection,
            provider_name="stub-embedding",
            model_name="stub-embedding-model",
            version_tag="phase2-hybrid",
        )
        try:
            activate_chunk_index_version(
                connection,
                version_id=candidate.id,
                provider_name="stub-embedding",
                model_name="stub-embedding-model",
            )
        except AppError as exc:
            assert exc.status_code == 409
            assert exc.error_category == "VALIDATION_FAILED"
            assert "rebuild" in exc.error_message.lower()
        else:
            raise AssertionError("expected activate_chunk_index_version to reject unrebuilt candidate")


def test_rebuild_chunk_index_version_populates_candidate_collection_without_mutating_chunks(
    configured_app_paths: dict[str, str],
) -> None:
    from backend.app.config import load_config
    from backend.app.services.ai import create_embedding_provider
    from backend.app.services.chunk_index import ChunkVectorStore
    from backend.app.services.chunk_store import insert_document_chunks
    from backend.app.services.chunking import DocumentChunk
    from backend.app.services.retrieval_index_versions import (
        create_chunk_index_version,
        rebuild_chunk_index_version,
    )

    database = Database(configured_app_paths["sqlite_path"])
    database.initialize()
    config = load_config()

    parent = DocumentChunk(
        id="parent-1",
        knowledge_item_id="ki-1",
        parent_chunk_id=None,
        chunk_level="parent",
        section_title="Section A",
        content="Parent content",
        position=0,
        token_estimate=3,
    )
    child = DocumentChunk(
        id="child-1",
        knowledge_item_id="ki-1",
        parent_chunk_id="parent-1",
        chunk_level="child",
        section_title="Section A",
        content="Candidate rebuild content",
        position=0,
        token_estimate=3,
    )

    with database.connect() as connection:
        connection.execute(
            """
            INSERT INTO knowledge_items (
              id, source_type, source_value, title, raw_content, source_name, created_at, updated_at
            ) VALUES (?, 'text', 'source-1', 'Title', 'Candidate rebuild content', 'source-1', '2026-04-17T00:00:00Z', '2026-04-17T00:00:00Z')
            """,
            ("ki-1",),
        )
        insert_document_chunks(connection, [parent, child])
        candidate = create_chunk_index_version(
            connection,
            provider_name="stub-embedding",
            model_name="stub-embedding-model",
            version_tag="phase2-hybrid",
        )
        report = rebuild_chunk_index_version(
            connection,
            config=config,
            version_id=candidate.id,
        )
        chunk_count = connection.execute(
            "SELECT COUNT(*) FROM document_chunks WHERE knowledge_item_id = 'ki-1'"
        ).fetchone()[0]

    candidate_store = ChunkVectorStore(
        config=config,
        provider_name="stub-embedding",
        model_name="stub-embedding-model",
        version_tag="phase2-hybrid",
    )
    query_vector = create_embedding_provider(config).embed_texts(["Candidate rebuild content"])[0]
    candidate_results = candidate_store.search_related(query_vector, limit=5)

    assert report.knowledge_item_count == 1
    assert report.chunk_count == 1
    assert chunk_count == 2
    assert any(result["chunk_id"] == "child-1" for result in candidate_results)
