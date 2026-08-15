from __future__ import annotations

from types import SimpleNamespace

from backend.app.db import Database
from backend.app.services.chunk_store import insert_document_chunks, list_document_chunks
from backend.app.services.chunking import DocumentChunk


def test_insert_document_chunks_persists_parent_and_child_rows(
    app_paths: dict[str, str],
) -> None:
    database = Database(app_paths["sqlite_path"])
    database.initialize()

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
        content="Child content",
        position=0,
        token_estimate=2,
    )

    with database.connect() as connection:
        connection.execute(
            """
            INSERT INTO knowledge_items (
              id, source_type, source_value, title, raw_content, source_name, created_at, updated_at
            ) VALUES (?, 'text', 'source-1', 'Title', 'Raw', 'source-1', '2026-04-17T00:00:00Z', '2026-04-17T00:00:00Z')
            """,
            ("ki-1",),
        )
        insert_document_chunks(connection, [parent, child])
        rows = list_document_chunks(connection, "ki-1")

    assert [row["id"] for row in rows] == ["parent-1", "child-1"]
    assert rows[1]["parent_chunk_id"] == "parent-1"


def test_chunk_vector_store_upserts_child_records(
    configured_app_paths: dict[str, str],
) -> None:
    from backend.app.config import load_config
    from backend.app.services.chunk_index import ChunkVectorRecord, ChunkVectorStore

    config = load_config()
    store = ChunkVectorStore(
        config=config,
        provider_name="stub-embedding",
        model_name="stub-embedding-model",
    )

    store.upsert_chunk(
        vector=[0.1, 0.2, 0.3],
        record=ChunkVectorRecord(
            chunk_id="child-1",
            knowledge_item_id="ki-1",
            parent_chunk_id="parent-1",
            section_title="Section A",
            position=0,
            content_preview="Child content",
            source_type="text",
            created_at="2026-04-17T00:00:00Z",
            category="research",
            user_tags=["alpha"],
            ai_tags=["report"],
        ),
    )

    results = store.search_related([0.1, 0.2, 0.3], limit=1)

    assert len(results) == 1
    assert results[0]["chunk_id"] == "child-1"
    assert results[0]["parent_chunk_id"] == "parent-1"


def test_chunk_vector_store_search_applies_payload_filters(
    configured_app_paths: dict[str, str],
) -> None:
    from backend.app.config import load_config
    from backend.app.services.chunk_index import ChunkVectorRecord, ChunkVectorStore

    config = load_config()
    store = ChunkVectorStore(
        config=config,
        provider_name="stub-embedding",
        model_name="stub-embedding-model",
    )

    store.upsert_chunk(
        vector=[0.9, 0.1, 0.0],
        record=ChunkVectorRecord(
            chunk_id="child-1",
            knowledge_item_id="ki-1",
            parent_chunk_id="parent-1",
            section_title="Section A",
            position=0,
            content_preview="Child content",
            source_type="text",
            created_at="2026-04-17T00:00:00Z",
            category="research",
            user_tags=["alpha"],
            ai_tags=["report"],
        ),
    )
    store.upsert_chunk(
        vector=[0.9, 0.1, 0.0],
        record=ChunkVectorRecord(
            chunk_id="child-2",
            knowledge_item_id="ki-2",
            parent_chunk_id="parent-2",
            section_title="Section B",
            position=0,
            content_preview="Other content",
            source_type="url",
            created_at="2026-04-18T00:00:00Z",
            category="ops",
            user_tags=["beta"],
            ai_tags=["guide"],
        ),
    )

    results = store.search_related(
        [0.9, 0.1, 0.0],
        limit=5,
        filters={
            "source_types": ["text"],
            "knowledge_item_ids": ["ki-1"],
            "category": "research",
            "user_tags": ["alpha"],
            "ai_tags": ["report"],
            "created_at_from": "2026-04-17T00:00:00Z",
            "created_at_to": "2026-04-17T23:59:59Z",
        },
    )

    assert [item["chunk_id"] for item in results] == ["child-1"]


def test_rebuild_document_chunk_fts_for_item_persists_child_rows(
    configured_app_paths: dict[str, str],
) -> None:
    from backend.app.services.chunk_store import rebuild_document_chunk_fts_for_item
    from backend.app.services.retrieval_store import search_child_chunk_rows_fts
    from backend.app.services.retrieval_types import RetrievalFilters

    database = Database(configured_app_paths["sqlite_path"])
    database.initialize()

    parent = DocumentChunk(
        id="parent-fts-1",
        knowledge_item_id="ki-fts-1",
        parent_chunk_id=None,
        chunk_level="parent",
        section_title="Section A",
        content="Parent content",
        position=0,
        token_estimate=3,
    )
    child = DocumentChunk(
        id="child-fts-1",
        knowledge_item_id="ki-fts-1",
        parent_chunk_id="parent-fts-1",
        chunk_level="child",
        section_title="Section A",
        content="多模态嵌入可以统一图像和文本表示。",
        position=0,
        token_estimate=5,
    )

    with database.connect() as connection:
        connection.execute(
            """
            INSERT INTO knowledge_items (
              id, source_type, source_value, title, raw_content, source_name, created_at, updated_at
            ) VALUES (?, 'url', 'https://example.com/multimodal', '多模态嵌入', 'Raw', 'example', '2026-04-17T00:00:00Z', '2026-04-17T00:00:00Z')
            """,
            ("ki-fts-1",),
        )
        insert_document_chunks(connection, [parent, child])
        rebuild_document_chunk_fts_for_item(connection, knowledge_item_id="ki-fts-1")
        rows = search_child_chunk_rows_fts(
            connection,
            fts_query='"多模态嵌入" OR "图像"',
            filters=RetrievalFilters(source_types=["url"]),
            limit=5,
        )

    assert len(rows) == 1
    assert rows[0]["chunk_id"] == "child-fts-1"


def test_search_child_chunk_rows_fts_applies_tag_filters(
    configured_app_paths: dict[str, str],
) -> None:
    from backend.app.services.chunk_store import rebuild_document_chunk_fts_for_item
    from backend.app.services.retrieval_store import search_child_chunk_rows_fts
    from backend.app.services.retrieval_types import RetrievalFilters

    database = Database(configured_app_paths["sqlite_path"])
    database.initialize()

    parent = DocumentChunk(
        id="parent-fts-tag-1",
        knowledge_item_id="ki-fts-tag-1",
        parent_chunk_id=None,
        chunk_level="parent",
        section_title="Section A",
        content="Parent content",
        position=0,
        token_estimate=3,
    )
    child = DocumentChunk(
        id="child-fts-tag-1",
        knowledge_item_id="ki-fts-tag-1",
        parent_chunk_id="parent-fts-tag-1",
        chunk_level="child",
        section_title="Section A",
        content="Alpha report body",
        position=0,
        token_estimate=3,
    )

    with database.connect() as connection:
        connection.execute(
            """
            INSERT INTO knowledge_items (
              id, source_type, source_value, title, raw_content, source_name,
              user_tags_json, ai_tags_json, created_at, updated_at
            ) VALUES (
              ?, 'text', 'alpha-source', 'Alpha report', 'Raw', 'alpha-source',
              '["alpha"]', '["report"]',
              '2026-04-17T00:00:00Z', '2026-04-17T00:00:00Z'
            )
            """,
            ("ki-fts-tag-1",),
        )
        insert_document_chunks(connection, [parent, child])
        rebuild_document_chunk_fts_for_item(connection, knowledge_item_id="ki-fts-tag-1")
        rows = search_child_chunk_rows_fts(
            connection,
            fts_query='"alpha" OR "report"',
            filters=RetrievalFilters(user_tags=["alpha"], ai_tags=["report"]),
            limit=5,
        )
        missing_rows = search_child_chunk_rows_fts(
            connection,
            fts_query='"alpha" OR "report"',
            filters=RetrievalFilters(user_tags=["missing"]),
            limit=5,
        )

    assert [row["chunk_id"] for row in rows] == ["child-fts-tag-1"]
    assert missing_rows == []


def test_database_initialize_backfills_document_chunk_fts_when_empty(
    configured_app_paths: dict[str, str],
) -> None:
    database = Database(configured_app_paths["sqlite_path"])
    database.initialize()

    parent = DocumentChunk(
        id="parent-backfill-1",
        knowledge_item_id="ki-backfill-1",
        parent_chunk_id=None,
        chunk_level="parent",
        section_title="Section A",
        content="Parent content",
        position=0,
        token_estimate=3,
    )
    child = DocumentChunk(
        id="child-backfill-1",
        knowledge_item_id="ki-backfill-1",
        parent_chunk_id="parent-backfill-1",
        chunk_level="child",
        section_title="Section A",
        content="多模态嵌入可以统一图像和文本表示。",
        position=0,
        token_estimate=5,
    )

    with database.connect() as connection:
        connection.execute(
            """
            INSERT INTO knowledge_items (
              id, source_type, source_value, title, raw_content, source_name, created_at, updated_at
            ) VALUES (?, 'url', 'https://example.com/backfill', '多模态嵌入', 'Raw', 'example', '2026-04-17T00:00:00Z', '2026-04-17T00:00:00Z')
            """,
            ("ki-backfill-1",),
        )
        insert_document_chunks(connection, [parent, child])

    database.initialize()

    with database.connect() as connection:
        count = connection.execute("SELECT COUNT(*) FROM document_chunks_fts").fetchone()[0]

    assert count == 1


def test_chunk_vector_store_search_supports_query_points_only_client(
    configured_app_paths: dict[str, str],
) -> None:
    from backend.app.config import load_config
    from backend.app.services.chunk_index import ChunkVectorStore

    class QueryPointsOnlyClient:
        def query_points(self, *, collection_name: str, query: list[float], limit: int, with_payload: bool):
            assert collection_name == "chunk_stub_embedding_stub_embedding_model"
            assert query == [0.1, 0.2, 0.3]
            assert limit == 1
            assert with_payload is True
            return SimpleNamespace(
                points=[
                    SimpleNamespace(
                        id="child-2",
                        score=0.91,
                        payload={
                            "chunk_id": "child-2",
                            "knowledge_item_id": "ki-2",
                            "parent_chunk_id": "parent-2",
                            "section_title": "Section B",
                            "position": 0,
                            "content_preview": "Preview",
                        },
                    )
                ]
            )

    config = load_config()
    store = ChunkVectorStore(
        config=config,
        provider_name="stub-embedding",
        model_name="stub-embedding-model",
    )
    store.client = QueryPointsOnlyClient()
    store._collection_exists = lambda: True  # type: ignore[method-assign]

    results = store.search_related([0.1, 0.2, 0.3], limit=1)

    assert len(results) == 1
    assert results[0]["chunk_id"] == "child-2"
    assert results[0]["knowledge_item_id"] == "ki-2"
    assert results[0]["score"] == 0.91


def test_chunk_vector_store_appends_version_to_collection_name(configured_app_paths: dict[str, str]) -> None:
    from backend.app.config import load_config
    from backend.app.services.chunk_index import ChunkVectorStore

    config = load_config()
    store = ChunkVectorStore(
        config=config,
        provider_name="stub-embedding",
        model_name="stub-embedding-model",
        version_tag="phase2-hybrid",
    )

    assert store.collection_name == "chunk_stub_embedding_stub_embedding_model_v_phase2_hybrid"
