from __future__ import annotations

from backend.app.services.retrieval_types import (
    ChildChunkHit,
    CitationPackItem,
    ParentContext,
    RetrievalFilters,
    RetrievalQuery,
    RetrievalResult,
)
from backend.app.db import Database
from backend.app.services.chunk_store import insert_document_chunks
from backend.app.services.chunking import DocumentChunk
from backend.app.services.retrieval_store import (
    fetch_child_chunk_rows,
    fetch_parent_context_rows,
)
from backend.app.services.retrieval import (
    build_retrieval_context,
    score_keyword_matches,
)


def test_retrieval_types_capture_expected_fields() -> None:
    filters = RetrievalFilters(
        source_types=["text"],
        created_at_from="2026-04-01T00:00:00Z",
        created_at_to="2026-04-30T23:59:59Z",
        knowledge_item_ids=["ki-1"],
        keyword="alpha report",
        category="research",
        user_tags=["alpha"],
        ai_tags=["report"],
    )
    query = RetrievalQuery(
        text="find alpha report",
        filters=filters,
        limit=5,
    )
    hit = ChildChunkHit(
        chunk_id="child-1",
        knowledge_item_id="ki-1",
        parent_chunk_id="parent-1",
        section_title="Section A",
        content="Alpha content",
        source_type="text",
        title="Alpha report",
        source_name="alpha.txt",
        source_value="alpha.txt",
        created_at="2026-04-17T00:00:00Z",
        category="research",
        user_tags=["alpha"],
        ai_tags=["report"],
        vector_score=0.9,
        metadata_keyword_score=1.0,
        content_keyword_score=0.5,
        final_score=0.86,
    )
    parent = ParentContext(
        parent_chunk_id="parent-1",
        knowledge_item_id="ki-1",
        section_title="Section A",
        content="Parent content",
        title="Alpha report",
        source_type="text",
        source_name="alpha.txt",
        source_value="alpha.txt",
        created_at="2026-04-17T00:00:00Z",
        category="research",
        user_tags=["alpha"],
        ai_tags=["report"],
    )
    result = RetrievalResult(
        query_text=query.text,
        filters=query.filters,
        child_hits=[hit],
        parent_contexts={"parent-1": parent},
        citations=[
            CitationPackItem(
                citation_id="cite-1",
                rank=1,
                knowledge_item_id="ki-1",
                chunk_id="child-1",
                parent_chunk_id="parent-1",
                title="Alpha report",
                section_title="Section A",
                source_type="text",
                source_name="alpha.txt",
                source_value="alpha.txt",
                created_at="2026-04-17T00:00:00Z",
                snippet="Alpha content",
                context_snippet="Parent content",
                expanded_context_snippet="Alpha content",
            )
        ],
    )

    assert result.query_text == "find alpha report"
    assert result.filters.keyword == "alpha report"
    assert result.filters.category == "research"
    assert result.child_hits[0].final_score == 0.86
    assert result.child_hits[0].user_tags == ["alpha"]
    assert result.parent_contexts["parent-1"].title == "Alpha report"
    assert result.citations[0].citation_id == "cite-1"


def test_fetch_child_chunk_rows_returns_joined_metadata(app_paths: dict[str, str]) -> None:
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
        content="Child alpha content",
        position=0,
        token_estimate=2,
    )

    with database.connect() as connection:
        connection.execute(
            """
            INSERT INTO knowledge_items (
              id, source_type, source_value, title, raw_content, source_name,
              capture_category, user_tags_json, ai_tags_json, created_at, updated_at
            ) VALUES (
              'ki-1', 'text', 'alpha.txt', 'Alpha report', 'raw', 'alpha.txt',
              'research', '["alpha"]', '["report"]',
              '2026-04-17T00:00:00Z', '2026-04-17T00:00:00Z'
            )
            """
        )
        insert_document_chunks(connection, [parent, child])
        rows = fetch_child_chunk_rows(connection, ["child-1"])

    assert len(rows) == 1
    assert rows[0]["chunk_id"] == "child-1"
    assert rows[0]["title"] == "Alpha report"
    assert rows[0]["source_type"] == "text"
    assert rows[0]["capture_category"] == "research"
    assert rows[0]["user_tags_json"] == '["alpha"]'
    assert rows[0]["ai_tags_json"] == '["report"]'


def test_fetch_parent_context_rows_returns_parent_chunks(app_paths: dict[str, str]) -> None:
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

    with database.connect() as connection:
        connection.execute(
            """
            INSERT INTO knowledge_items (
              id, source_type, source_value, title, raw_content, source_name,
              capture_category, user_tags_json, ai_tags_json, created_at, updated_at
            ) VALUES (
              'ki-1', 'text', 'alpha.txt', 'Alpha report', 'raw', 'alpha.txt',
              'research', '["alpha"]', '["report"]',
              '2026-04-17T00:00:00Z', '2026-04-17T00:00:00Z'
            )
            """
        )
        insert_document_chunks(connection, [parent])
        rows = fetch_parent_context_rows(connection, ["parent-1"])

    assert len(rows) == 1
    assert rows[0]["parent_chunk_id"] == "parent-1"
    assert rows[0]["content"] == "Parent content"
    assert rows[0]["capture_category"] == "research"


def test_score_keyword_matches_prefers_metadata_over_content() -> None:
    metadata_score, content_score = score_keyword_matches(
        keyword="alpha report",
        title="Alpha report",
        source_name="alpha.txt",
        source_value="alpha-source",
        content="report body without alpha in metadata fallback",
    )

    assert metadata_score > content_score
    assert metadata_score == 1.0


def test_build_retrieval_context_returns_ranked_hits_and_parent_context(
    configured_app_paths: dict[str, str],
    monkeypatch,
) -> None:
    from backend.app.config import load_config
    from backend.app.services.chunk_index import ChunkVectorRecord, ChunkVectorStore
    from backend.app.services.chunk_store import mark_chunk_indexed

    database = Database(configured_app_paths["sqlite_path"])
    database.initialize()
    config = load_config()

    parent = DocumentChunk(
        id="parent-1",
        knowledge_item_id="ki-1",
        parent_chunk_id=None,
        chunk_level="parent",
        section_title="Section A",
        content="Parent alpha context",
        position=0,
        token_estimate=4,
    )
    child_prev = DocumentChunk(
        id="child-0",
        knowledge_item_id="ki-1",
        parent_chunk_id="parent-1",
        chunk_level="child",
        section_title="Section A",
        content="Alpha lead context",
        position=0,
        token_estimate=3,
    )
    child = DocumentChunk(
        id="child-1",
        knowledge_item_id="ki-1",
        parent_chunk_id="parent-1",
        chunk_level="child",
        section_title="Section A",
        content="Alpha report body",
        position=1,
        token_estimate=4,
    )
    child_next = DocumentChunk(
        id="child-2",
        knowledge_item_id="ki-1",
        parent_chunk_id="parent-1",
        chunk_level="child",
        section_title="Section A",
        content="Alpha trailing context",
        position=2,
        token_estimate=3,
    )

    with database.connect() as connection:
        connection.execute(
            """
            INSERT INTO knowledge_items (
              id, source_type, source_value, title, raw_content, source_name,
              capture_category, user_tags_json, ai_tags_json, created_at, updated_at
            ) VALUES (
              'ki-1', 'text', 'alpha.txt', 'Alpha report', 'raw', 'alpha.txt',
              'research', '["alpha"]', '["report"]',
              '2026-04-17T00:00:00Z', '2026-04-17T00:00:00Z'
            )
            """
        )
        insert_document_chunks(connection, [parent, child_prev, child, child_next])
        mark_chunk_indexed(
            connection,
            chunk_id="child-1",
            embedding_provider="stub-embedding",
            embedding_model="stub-embedding-model",
            vector_point_id="child-1",
        )

    store = ChunkVectorStore(
        config=config,
        provider_name="stub-embedding",
        model_name="stub-embedding-model",
    )
    store.upsert_chunk(
        vector=[1.0, 0.0, 0.0],
        record=ChunkVectorRecord(
            chunk_id="child-1",
            knowledge_item_id="ki-1",
            parent_chunk_id="parent-1",
            section_title="Section A",
            position=0,
            content_preview="Alpha report body",
            source_type="text",
            created_at="2026-04-17T00:00:00Z",
            category="research",
            user_tags=["alpha"],
            ai_tags=["report"],
        ),
    )

    class DummyEmbeddingProvider:
        def embed_texts(self, texts: list[str]) -> list[list[float]]:
            return [[1.0, 0.0, 0.0] for _ in texts]

    monkeypatch.setattr(
        "backend.app.services.retrieval.create_embedding_provider",
        lambda _config: DummyEmbeddingProvider(),
    )

    result = build_retrieval_context(
        db=database,
        config=config,
        query=RetrievalQuery(
            text="alpha report",
            filters=RetrievalFilters(keyword="alpha report"),
            limit=5,
        ),
    )

    assert len(result.child_hits) == 1
    assert result.child_hits[0].chunk_id == "child-1"
    assert result.child_hits[0].category == "research"
    assert result.child_hits[0].user_tags == ["alpha"]
    assert result.child_hits[0].ai_tags == ["report"]
    assert result.parent_contexts["parent-1"].content == "Parent alpha context"
    assert len(result.citations) == 1
    assert result.citations[0].citation_id == "cite-1"
    assert result.citations[0].chunk_id == "child-1"
    assert result.citations[0].context_snippet == "Parent alpha context"
    assert result.citations[0].expanded_context_snippet == "Alpha lead context\n\nAlpha report body\n\nAlpha trailing context"


def test_build_retrieval_context_passes_filters_into_vector_recall(
    configured_app_paths: dict[str, str],
    monkeypatch,
) -> None:
    from backend.app.config import load_config

    database = Database(configured_app_paths["sqlite_path"])
    database.initialize()
    config = load_config()
    captured: dict[str, object] = {}

    class DummyEmbeddingProvider:
        def embed_texts(self, texts: list[str]) -> list[list[float]]:
            assert texts == ["alpha report"]
            return [[1.0, 0.0, 0.0]]

    def fake_search_related(self, vector, *, limit=5, filters=None):
        captured["limit"] = limit
        captured["filters"] = filters
        return []

    monkeypatch.setattr(
        "backend.app.services.retrieval.create_embedding_provider",
        lambda _config: DummyEmbeddingProvider(),
    )
    monkeypatch.setattr(
        "backend.app.services.retrieval.ChunkVectorStore.search_related",
        fake_search_related,
    )

    build_retrieval_context(
        db=database,
        config=config,
        query=RetrievalQuery(
            text="alpha report",
            filters=RetrievalFilters(
                source_types=["text"],
                created_at_from="2026-04-01T00:00:00Z",
                created_at_to="2026-04-30T23:59:59Z",
                knowledge_item_ids=["ki-1"],
                category="research",
                user_tags=["alpha"],
                ai_tags=["report"],
            ),
            limit=7,
        ),
    )

    assert captured["limit"] == 28
    assert captured["filters"] == {
        "source_types": ["text"],
        "created_at_from": "2026-04-01T00:00:00Z",
        "created_at_to": "2026-04-30T23:59:59Z",
        "knowledge_item_ids": ["ki-1"],
        "category": "research",
        "user_tags": ["alpha"],
        "ai_tags": ["report"],
    }


def test_build_retrieval_context_uses_precomputed_query_vector(
    configured_app_paths: dict[str, str],
    monkeypatch,
) -> None:
    from backend.app.config import load_config

    database = Database(configured_app_paths["sqlite_path"])
    database.initialize()
    config = load_config()
    captured: dict[str, object] = {}

    def fail_create_embedding_provider(_config):
        raise AssertionError("embedding provider should not be called when query_vector is provided")

    def fake_search_related(self, vector, *, limit=5, filters=None):
        captured["vector"] = vector
        return []

    monkeypatch.setattr(
        "backend.app.services.retrieval.create_embedding_provider",
        fail_create_embedding_provider,
    )
    monkeypatch.setattr(
        "backend.app.services.retrieval.ChunkVectorStore.search_related",
        fake_search_related,
    )

    result = build_retrieval_context(
        db=database,
        config=config,
        query=RetrievalQuery(
            text="alpha report",
            query_vector=[1.0, 0.0, 0.0],
            limit=3,
        ),
    )

    assert result.child_hits == []
    assert captured["vector"] == [1.0, 0.0, 0.0]


def test_build_retrieval_context_uses_keyword_as_filter_for_fts_and_final_hits(
    configured_app_paths: dict[str, str],
    monkeypatch,
) -> None:
    from backend.app.config import load_config

    database = Database(configured_app_paths["sqlite_path"])
    database.initialize()
    config = load_config()
    captured: dict[str, object] = {}

    with database.connect() as connection:
        connection.executemany(
            """
            INSERT INTO knowledge_items (
              id, source_type, source_value, title, raw_content, source_name,
              capture_category, user_tags_json, ai_tags_json, created_at, updated_at
            ) VALUES (?, 'text', ?, ?, 'raw', ?, 'research', '[]', '[]', '2026-04-17T00:00:00Z', '2026-04-17T00:00:00Z')
            """,
            [
                ("ki-alpha", "alpha-source", "Alpha report", "alpha-source"),
                ("ki-beta", "beta-source", "Beta report", "beta-source"),
            ],
        )
        connection.executemany(
            """
            INSERT INTO document_chunks (
              id, knowledge_item_id, parent_chunk_id, chunk_level, section_title, content, position, token_estimate, created_at
            ) VALUES (?, ?, NULL, 'parent', 'Section A', ?, 0, 3, '2026-04-17T00:00:00Z')
            """,
            [
                ("parent-alpha", "ki-alpha", "Parent alpha"),
                ("parent-beta", "ki-beta", "Parent beta"),
            ],
        )
        connection.executemany(
            """
            INSERT INTO document_chunks (
              id, knowledge_item_id, parent_chunk_id, chunk_level, section_title, content, position, token_estimate, created_at
            ) VALUES (?, ?, ?, 'child', 'Section A', ?, 0, 3, '2026-04-17T00:00:00Z')
            """,
            [
                ("child-alpha", "ki-alpha", "parent-alpha", "Alpha report body"),
                ("child-beta", "ki-beta", "parent-beta", "Beta report body"),
            ],
        )

    class DummyEmbeddingProvider:
        def embed_texts(self, texts: list[str]) -> list[list[float]]:
            return [[1.0, 0.0, 0.0] for _ in texts]

    def fake_search_child_chunk_rows_fts(connection, *, fts_query, filters, limit):
        captured["fts_query"] = fts_query
        return []

    monkeypatch.setattr(
        "backend.app.services.retrieval.create_embedding_provider",
        lambda _config: DummyEmbeddingProvider(),
    )
    monkeypatch.setattr(
        "backend.app.services.retrieval.search_child_chunk_rows_fts",
        fake_search_child_chunk_rows_fts,
    )
    monkeypatch.setattr(
        "backend.app.services.retrieval.ChunkVectorStore.search_related",
        lambda self, vector, limit, filters=None: [
            {"chunk_id": "child-alpha", "score": 0.9},
            {"chunk_id": "child-beta", "score": 0.88},
        ],
    )

    result = build_retrieval_context(
        db=database,
        config=config,
        query=RetrievalQuery(
            text="report",
            filters=RetrievalFilters(keyword="alpha"),
            limit=5,
        ),
    )

    assert captured["fts_query"] == '"alpha"'
    assert [hit.knowledge_item_id for hit in result.child_hits] == ["ki-alpha"]


def test_matches_filters_handles_offset_timestamps_consistently() -> None:
    from backend.app.services.retrieval import _matches_filters

    hit = ChildChunkHit(
        chunk_id="child-1",
        knowledge_item_id="ki-1",
        parent_chunk_id="parent-1",
        section_title=None,
        content="Alpha content",
        source_type="text",
        title="Alpha report",
        source_name="alpha.txt",
        source_value="alpha.txt",
        created_at="2026-04-17T01:00:00+08:00",
        category="research",
        user_tags=["alpha"],
        ai_tags=["report"],
        vector_score=0.9,
        metadata_keyword_score=1.0,
        content_keyword_score=1.0,
        final_score=0.935,
    )

    assert _matches_filters(
        hit,
        RetrievalFilters(
            created_at_from="2026-04-16T16:30:00Z",
            created_at_to="2026-04-16T17:30:00Z",
        ),
    )
    assert not _matches_filters(
        hit,
        RetrievalFilters(created_at_from="2026-04-16T17:30:01Z"),
    )


def test_build_retrieval_context_merges_lexical_hits_with_vector_hits(
    configured_app_paths: dict[str, str],
    monkeypatch,
) -> None:
    from backend.app.config import load_config
    from backend.app.services.chunk_store import rebuild_document_chunk_fts_for_item

    database = Database(configured_app_paths["sqlite_path"])
    database.initialize()
    config = load_config()

    parent_correct = DocumentChunk(
        id="parent-correct",
        knowledge_item_id="ki-correct",
        parent_chunk_id=None,
        chunk_level="parent",
        section_title="Section A",
        content="Correct parent context",
        position=0,
        token_estimate=4,
    )
    child_correct = DocumentChunk(
        id="child-correct",
        knowledge_item_id="ki-correct",
        parent_chunk_id="parent-correct",
        chunk_level="child",
        section_title="Section A",
        content="多模态嵌入可以统一图像和文本表示。",
        position=0,
        token_estimate=5,
    )
    parent_vector = DocumentChunk(
        id="parent-vector",
        knowledge_item_id="ki-vector",
        parent_chunk_id=None,
        chunk_level="parent",
        section_title="Section B",
        content="Vector parent context",
        position=0,
        token_estimate=4,
    )
    child_vector = DocumentChunk(
        id="child-vector",
        knowledge_item_id="ki-vector",
        parent_chunk_id="parent-vector",
        chunk_level="child",
        section_title="Section B",
        content="向量数据库介绍。",
        position=0,
        token_estimate=4,
    )

    with database.connect() as connection:
        connection.executemany(
            """
            INSERT INTO knowledge_items (
              id, source_type, source_value, title, raw_content, source_name,
              capture_category, user_tags_json, ai_tags_json, created_at, updated_at
            ) VALUES (?, 'url', ?, ?, 'raw', ?, 'research', '[]', '[]', '2026-04-17T00:00:00Z', '2026-04-17T00:00:00Z')
            """,
            [
                ("ki-correct", "https://example.com/multimodal", "多模态嵌入", "multimodal"),
                ("ki-vector", "https://example.com/vector-db", "向量数据库", "vector-db"),
            ],
        )
        insert_document_chunks(connection, [parent_correct, child_correct, parent_vector, child_vector])
        rebuild_document_chunk_fts_for_item(connection, knowledge_item_id="ki-correct")
        rebuild_document_chunk_fts_for_item(connection, knowledge_item_id="ki-vector")

    class DummyEmbeddingProvider:
        def embed_texts(self, texts: list[str]) -> list[list[float]]:
            return [[1.0, 0.0, 0.0] for _ in texts]

    monkeypatch.setattr(
        "backend.app.services.retrieval.create_embedding_provider",
        lambda _config: DummyEmbeddingProvider(),
    )
    monkeypatch.setattr(
        "backend.app.services.retrieval.ChunkVectorStore.search_related",
        lambda self, vector, limit, filters=None: [
            {"chunk_id": "child-vector", "score": 0.95},
        ],
    )

    result = build_retrieval_context(
        db=database,
        config=config,
        query=RetrievalQuery(text="为什么需要多模态嵌入", filters=RetrievalFilters(source_types=["url"]), limit=5),
    )

    returned_ids = [hit.knowledge_item_id for hit in result.child_hits]

    assert returned_ids[0] == "ki-correct"


def test_matches_filters_rejects_wrong_source_type() -> None:
    from backend.app.services.retrieval import _matches_filters

    hit = ChildChunkHit(
        chunk_id="child-1",
        knowledge_item_id="ki-1",
        parent_chunk_id="parent-1",
        section_title=None,
        content="Alpha content",
        source_type="pdf",
        title="Alpha report",
        source_name="alpha.pdf",
        source_value="alpha.pdf",
        created_at="2026-04-17T00:00:00Z",
        category="research",
        user_tags=["alpha"],
        ai_tags=["report"],
        vector_score=0.9,
        metadata_keyword_score=1.0,
        content_keyword_score=1.0,
        final_score=0.935,
    )

    assert _matches_filters(hit, RetrievalFilters(source_types=["text"])) is False


def test_matches_filters_supports_category_and_tag_filters() -> None:
    from backend.app.services.retrieval import _matches_filters

    hit = ChildChunkHit(
        chunk_id="child-1",
        knowledge_item_id="ki-1",
        parent_chunk_id="parent-1",
        section_title=None,
        content="Alpha content",
        source_type="text",
        title="Alpha report",
        source_name="alpha.txt",
        source_value="alpha.txt",
        created_at="2026-04-17T00:00:00Z",
        category="research",
        user_tags=["alpha", "priority"],
        ai_tags=["report"],
        vector_score=0.9,
        metadata_keyword_score=1.0,
        content_keyword_score=1.0,
        final_score=0.935,
    )

    assert _matches_filters(
        hit,
        RetrievalFilters(category="research", user_tags=["priority"], ai_tags=["report"]),
    )
    assert not _matches_filters(hit, RetrievalFilters(category="ops"))
    assert not _matches_filters(hit, RetrievalFilters(user_tags=["missing"]))
    assert not _matches_filters(hit, RetrievalFilters(ai_tags=["missing"]))


def test_rerank_child_hits_prefers_exact_title_and_section_matches() -> None:
    from backend.app.services.retrieval import _rerank_child_hits

    multimodal_hit = ChildChunkHit(
        chunk_id="child-multi",
        knowledge_item_id="ki-multi",
        parent_chunk_id="parent-multi",
        section_title="一、为什么需要多模态嵌入？",
        content="多模态嵌入用于统一图像和文本表征。",
        source_type="url",
        title="多模态嵌入",
        source_name="multimodal",
        source_value="https://example.com/multimodal",
        created_at="2026-04-17T00:00:00Z",
        category="research",
        user_tags=[],
        ai_tags=[],
        vector_score=0.30,
        metadata_keyword_score=0.6,
        content_keyword_score=0.2,
        final_score=0.36,
    )
    vector_hit = ChildChunkHit(
        chunk_id="child-vector",
        knowledge_item_id="ki-vector",
        parent_chunk_id="parent-vector",
        section_title="一、什么是向量嵌入？",
        content="向量嵌入用于语义相似度建模。",
        source_type="url",
        title="向量嵌入基础",
        source_name="vector",
        source_value="https://example.com/vector",
        created_at="2026-04-17T00:00:00Z",
        category="research",
        user_tags=[],
        ai_tags=[],
        vector_score=0.72,
        metadata_keyword_score=0.1,
        content_keyword_score=0.1,
        final_score=0.37,
    )

    reranked = _rerank_child_hits(
        [vector_hit, multimodal_hit],
        query_text="为什么需要多模态嵌入",
        lexical_support_by_item={},
        vector_support_by_item={},
    )

    assert reranked[0].knowledge_item_id == "ki-multi"


def test_rerank_child_hits_prefers_documents_with_more_supporting_hits() -> None:
    from backend.app.services.retrieval import _rerank_child_hits

    multimodal_primary = ChildChunkHit(
        chunk_id="child-multi-1",
        knowledge_item_id="ki-multi",
        parent_chunk_id="parent-multi",
        section_title="二、CLIP 模型浅析",
        content="CLIP 通过图文对比学习对齐多模态空间。",
        source_type="url",
        title="多模态嵌入",
        source_name="multimodal",
        source_value="https://example.com/multimodal",
        created_at="2026-04-17T00:00:00Z",
        category="research",
        user_tags=[],
        ai_tags=[],
        vector_score=0.55,
        metadata_keyword_score=0.9,
        content_keyword_score=0.3,
        final_score=0.56,
    )
    multimodal_secondary = ChildChunkHit(
        chunk_id="child-multi-2",
        knowledge_item_id="ki-multi",
        parent_chunk_id="parent-multi",
        section_title="三、BGE-VL 与 M3-Embedding",
        content="BGE-VL 和 M3-Embedding 覆盖多语言多模态场景。",
        source_type="url",
        title="多模态嵌入",
        source_name="multimodal",
        source_value="https://example.com/multimodal",
        created_at="2026-04-17T00:00:00Z",
        category="research",
        user_tags=[],
        ai_tags=[],
        vector_score=0.48,
        metadata_keyword_score=0.8,
        content_keyword_score=0.3,
        final_score=0.50,
    )
    milvus_hit = ChildChunkHit(
        chunk_id="child-milvus",
        knowledge_item_id="ki-milvus",
        parent_chunk_id="parent-milvus",
        section_title="多模态检索实践",
        content="Milvus 支持多模态检索与混合过滤。",
        source_type="url",
        title="Milvus介绍及多模态检索实践",
        source_name="milvus",
        source_value="https://example.com/milvus",
        created_at="2026-04-17T00:00:00Z",
        category="research",
        user_tags=[],
        ai_tags=[],
        vector_score=0.66,
        metadata_keyword_score=0.6,
        content_keyword_score=0.2,
        final_score=0.57,
    )

    reranked = _rerank_child_hits(
        [milvus_hit, multimodal_primary, multimodal_secondary],
        query_text="CLIP 模型和 BGE-VL、M3-Embedding 分别是什么",
        lexical_support_by_item={"ki-multi": 3, "ki-milvus": 1},
        vector_support_by_item={"ki-multi": 2, "ki-milvus": 1},
    )

    assert reranked[0].knowledge_item_id == "ki-multi"


def test_rerank_child_hits_prefers_hits_with_anchor_terms_from_query() -> None:
    from backend.app.services.retrieval import _rerank_child_hits

    clip_hit = ChildChunkHit(
        chunk_id="child-clip",
        knowledge_item_id="ki-multi",
        parent_chunk_id="parent-multi",
        section_title="二、CLIP 模型浅析",
        content="CLIP 采用双编码器架构，并通过对比学习对齐图文表示。",
        source_type="url",
        title="多模态嵌入",
        source_name="multimodal",
        source_value="https://example.com/multimodal",
        created_at="2026-04-17T00:00:00Z",
        category="research",
        user_tags=[],
        ai_tags=[],
        vector_score=0.0,
        metadata_keyword_score=0.1,
        content_keyword_score=0.5,
        final_score=0.35,
    )
    generic_hit = ChildChunkHit(
        chunk_id="child-generic",
        knowledge_item_id="ki-vector",
        parent_chunk_id="parent-vector",
        section_title="任务一：掩码语言模型",
        content="模型采用自监督训练策略学习上下文表示。",
        source_type="url",
        title="向量嵌入基础",
        source_name="vector",
        source_value="https://example.com/vector",
        created_at="2026-04-17T00:00:00Z",
        category="research",
        user_tags=[],
        ai_tags=[],
        vector_score=0.52,
        metadata_keyword_score=0.0,
        content_keyword_score=0.5,
        final_score=0.53,
    )

    reranked = _rerank_child_hits(
        [generic_hit, clip_hit],
        query_text="CLIP 模型采用的是什么架构？",
        lexical_support_by_item={"ki-multi": 2, "ki-vector": 1},
        vector_support_by_item={},
    )

    assert reranked[0].knowledge_item_id == "ki-multi"


def test_score_lexical_rows_uses_bm25_magnitude_not_only_rank() -> None:
    from backend.app.services.retrieval import _score_lexical_rows

    scores = _score_lexical_rows(
        [
            {"chunk_id": "chunk-strong", "lexical_score": -120.0},
            {"chunk_id": "chunk-weak", "lexical_score": -14.0},
        ]
    )

    assert scores["chunk-strong"] == 1.0
    assert scores["chunk-weak"] < 0.3


def test_prune_child_hits_drops_weak_tail_when_top_query_alignment_is_strong() -> None:
    from backend.app.services.retrieval import _prune_child_hits

    top_hit = ChildChunkHit(
        chunk_id="child-clip",
        knowledge_item_id="ki-multi",
        parent_chunk_id="parent-multi",
        section_title="二、CLIP 模型浅析",
        content="CLIP 采用双编码器架构，并通过对比学习对齐图文表示。",
        source_type="url",
        title="多模态嵌入",
        source_name="multimodal",
        source_value="https://example.com/multimodal",
        created_at="2026-04-17T00:00:00Z",
        category="research",
        user_tags=[],
        ai_tags=[],
        vector_score=0.0,
        metadata_keyword_score=0.0,
        content_keyword_score=0.5,
        final_score=0.6892,
    )
    tail_hit = ChildChunkHit(
        chunk_id="child-generic",
        knowledge_item_id="ki-vector",
        parent_chunk_id="parent-vector",
        section_title="任务一：掩码语言模型",
        content="模型采用自监督训练策略学习上下文表示。",
        source_type="url",
        title="向量嵌入基础",
        source_name="vector",
        source_value="https://example.com/vector",
        created_at="2026-04-17T00:00:00Z",
        category="research",
        user_tags=[],
        ai_tags=[],
        vector_score=0.4128,
        metadata_keyword_score=0.0,
        content_keyword_score=0.5,
        final_score=0.5891,
    )

    pruned = _prune_child_hits(
        [top_hit, tail_hit],
        limit=5,
        query_text="CLIP 模型采用的是什么架构？",
    )

    assert [hit.knowledge_item_id for hit in pruned] == ["ki-multi"]


def test_prune_child_hits_drops_tail_when_top_lexical_signal_dominates() -> None:
    from backend.app.services.retrieval import _prune_child_hits

    top_hit = ChildChunkHit(
        chunk_id="child-index",
        knowledge_item_id="ki-index",
        parent_chunk_id="parent-index",
        section_title="一、上下文扩展",
        content="使用小块文本检索精度更高，但上下文不足；大块文本上下文更完整，但会引入噪声。",
        source_type="url",
        title="索引优化",
        source_name="index",
        source_value="https://example.com/index",
        created_at="2026-04-17T00:00:00Z",
        category="research",
        user_tags=[],
        ai_tags=[],
        vector_score=0.6991,
        metadata_keyword_score=0.5,
        content_keyword_score=0.5,
        final_score=0.8946,
        lexical_score=1.0,
    )
    tail_hit = ChildChunkHit(
        chunk_id="child-vector",
        knowledge_item_id="ki-vector",
        parent_chunk_id="parent-vector",
        section_title="2.3 RAG 对嵌入技术的新要求",
        content="RAG 对嵌入技术提出了新的要求。",
        source_type="url",
        title="向量嵌入基础",
        source_name="vector",
        source_value="https://example.com/vector",
        created_at="2026-04-17T00:00:00Z",
        category="research",
        user_tags=[],
        ai_tags=[],
        vector_score=0.6385,
        metadata_keyword_score=0.5,
        content_keyword_score=0.5,
        final_score=0.8564,
        lexical_score=0.12,
    )

    pruned = _prune_child_hits(
        [top_hit, tail_hit],
        limit=5,
        query_text="在 RAG 系统中使用小块文本和大块文本进行检索分别面临什么问题？",
    )

    assert [hit.knowledge_item_id for hit in pruned] == ["ki-index"]


def test_build_retrieval_context_returns_empty_result_when_no_hits(
    configured_app_paths: dict[str, str],
    monkeypatch,
) -> None:
    from backend.app.config import load_config

    database = Database(configured_app_paths["sqlite_path"])
    database.initialize()
    config = load_config()

    class DummyEmbeddingProvider:
        def embed_texts(self, texts: list[str]) -> list[list[float]]:
            return [[1.0, 0.0, 0.0] for _ in texts]

    monkeypatch.setattr(
        "backend.app.services.retrieval.create_embedding_provider",
        lambda _config: DummyEmbeddingProvider(),
    )

    result = build_retrieval_context(
        db=database,
        config=config,
        query=RetrievalQuery(text="nothing", limit=5),
    )

    assert result.child_hits == []
    assert result.parent_contexts == {}
