from __future__ import annotations

import json


def _seed_snapshot(
    db,
    *,
    knowledge_item_id: str,
    title: str,
    created_at: str,
    relation_meta: dict[str, object],
) -> None:
    with db.connect() as connection:
        connection.execute(
            """
            INSERT INTO knowledge_items (
              id, source_type, source_value, title, raw_content, source_name,
              capture_category, user_tags_json, ai_tags_json, created_at, updated_at
            ) VALUES (?, 'text', ?, ?, ?, ?, 'research', '[]', '[]', ?, ?)
            """,
            (
                knowledge_item_id,
                f"{knowledge_item_id}.txt",
                title,
                f"{title} raw content",
                f"{knowledge_item_id}.txt",
                created_at,
                created_at,
            ),
        )
        connection.execute(
            """
            INSERT INTO run_records (
              id, task_type, status, stage, started_at, finished_at, total_items,
              succeeded_items, failed_items, skipped_items, cancel_requested
            ) VALUES (?, 'summary', 'completed', 'completed', ?, ?, 1, 1, 0, 0, 0)
            """,
            (f"run-{knowledge_item_id}", created_at, created_at),
        )
        connection.execute(
            """
            INSERT INTO item_result_snapshots (
              id, knowledge_item_id, summary_run_id, generated_category, generated_tags,
              final_category, final_tags, summary_text, viewpoint_text, controversy_text,
              content_quality_score, quality_meta, relation_meta, qdrant_point_id,
              markdown_path, created_at, edited_at
            ) VALUES (?, ?, ?, 'research', '[]', 'research', '[]', ?, ?, ?, 0.9, '{}', ?, ?, ?, ?, ?)
            """,
            (
                f"snapshot-{knowledge_item_id}",
                knowledge_item_id,
                f"run-{knowledge_item_id}",
                f"{title} summary",
                f"{title} viewpoint",
                "none",
                json.dumps(relation_meta, ensure_ascii=False),
                f"qdrant-{knowledge_item_id}",
                f"summaries/{knowledge_item_id}.md",
                created_at,
                created_at,
            ),
        )


def test_answer_question_returns_insufficient_evidence_without_llm_call(
    configured_client,
    monkeypatch,
) -> None:
    from backend.app.schemas.qa import QAAnswerRequest
    from backend.app.services import qa as qa_service
    from backend.app.services.retrieval_types import RetrievalFilters, RetrievalResult

    monkeypatch.setattr(
        qa_service,
        "build_retrieval_context",
        lambda **kwargs: RetrievalResult(
            query_text=kwargs["query"].text,
            filters=RetrievalFilters(),
            child_hits=[],
            parent_contexts={},
            citations=[],
        ),
    )
    monkeypatch.setattr(
        qa_service,
        "create_answer_provider",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("LLM should not be called")),
    )

    result = qa_service.answer_question(
        db=configured_client.app.state.db,
        config=configured_client.app.state.config,
        payload=QAAnswerRequest(question="What is alpha?"),
    )

    assert result["answer_status"] == "insufficient_evidence"
    assert result["citations"] == []
    assert result["used_grounded_items"] == []
    assert result["suggested_queries"]


def test_answer_question_returns_filtered_citations_and_grounded_items(
    configured_client,
    monkeypatch,
) -> None:
    from backend.app.schemas.qa import QAAnswerRequest
    from backend.app.services import qa as qa_service
    from backend.app.services.retrieval_types import (
        CitationPackItem,
        RetrievalResult,
    )

    _seed_snapshot(
        configured_client.app.state.db,
        knowledge_item_id="ki-1",
        title="Alpha report",
        created_at="2026-04-23T00:00:00Z",
        relation_meta={
            "memory_context_items": [],
            "evidence_citations": [
                {
                    "citation_id": "summary-cite-1",
                    "rank": 1,
                    "knowledge_item_id": "ki-1",
                    "chunk_id": "chunk-s1",
                    "parent_chunk_id": "parent-s1",
                    "title": "Alpha report",
                    "section_title": "Section A",
                    "source_type": "text",
                    "source_name": "alpha.txt",
                    "source_value": "alpha.txt",
                    "created_at": "2026-04-23T00:00:00Z",
                    "snippet": "Alpha evidence",
                    "context_snippet": "Alpha context",
                    "expanded_context_snippet": "Alpha expanded context",
                }
            ],
            "grounded_claims": [
                {
                    "claim": "Alpha claim from summary",
                    "citation_ids": ["summary-cite-1"],
                }
            ],
            "summary_segments": [],
        },
    )

    monkeypatch.setattr(
        qa_service,
        "build_retrieval_context",
        lambda **kwargs: RetrievalResult(
            query_text=kwargs["query"].text,
            filters=kwargs["query"].filters,
            child_hits=[],
            parent_contexts={},
            citations=[
                CitationPackItem(
                    citation_id="cite-1",
                    rank=1,
                    knowledge_item_id="ki-1",
                    chunk_id="chunk-1",
                    parent_chunk_id="parent-1",
                    title="Alpha report",
                    section_title="Section A",
                    source_type="text",
                    source_name="alpha.txt",
                    source_value="alpha.txt",
                    created_at="2026-04-23T00:00:00Z",
                    snippet="Alpha snippet",
                    context_snippet="Alpha context",
                    expanded_context_snippet="Alpha expanded context",
                ),
                CitationPackItem(
                    citation_id="cite-2",
                    rank=2,
                    knowledge_item_id="ki-1",
                    chunk_id="chunk-2",
                    parent_chunk_id="parent-1",
                    title="Alpha report",
                    section_title="Section B",
                    source_type="text",
                    source_name="alpha.txt",
                    source_value="alpha.txt",
                    created_at="2026-04-23T00:00:00Z",
                    snippet="Alpha snippet 2",
                    context_snippet="Alpha context 2",
                    expanded_context_snippet="Alpha expanded context 2",
                ),
            ],
        ),
    )

    class DummyAnswerProvider:
        def answer(self, *, question, mode, evidence_citations, grounded_items):
            del mode
            assert question == "What is alpha?"
            assert len(evidence_citations) == 2
            assert grounded_items[0]["claim"] == "Alpha claim from summary"
            return qa_service.AnswerArtifact(
                answer="Alpha answer",
                answer_status="grounded",
                confidence=0.84,
                citation_ids=["cite-2"],
                suggested_queries=[],
                quality_meta={"provider": "dummy"},
            )

    monkeypatch.setattr(qa_service, "create_answer_provider", lambda _config: DummyAnswerProvider())

    result = qa_service.answer_question(
        db=configured_client.app.state.db,
        config=configured_client.app.state.config,
        payload=QAAnswerRequest(question="What is alpha?"),
    )

    assert result["answer"] == "Alpha answer"
    assert result["answer_status"] == "grounded"
    assert result["confidence"] == 0.84
    assert [citation["citation_id"] for citation in result["citations"]] == ["cite-2"]
    assert result["used_grounded_items"][0]["claim"] == "Alpha claim from summary"


def test_answer_question_degrades_when_provider_returns_unknown_citation_ids(
    configured_client,
    monkeypatch,
) -> None:
    from backend.app.schemas.qa import QAAnswerRequest
    from backend.app.services import qa as qa_service
    from backend.app.services.retrieval_types import CitationPackItem, RetrievalResult

    monkeypatch.setattr(
        qa_service,
        "build_retrieval_context",
        lambda **kwargs: RetrievalResult(
            query_text=kwargs["query"].text,
            filters=kwargs["query"].filters,
            child_hits=[],
            parent_contexts={},
            citations=[
                CitationPackItem(
                    citation_id="cite-1",
                    rank=1,
                    knowledge_item_id="ki-1",
                    chunk_id="chunk-1",
                    parent_chunk_id="parent-1",
                    title="Alpha report",
                    section_title="Section A",
                    source_type="text",
                    source_name="alpha.txt",
                    source_value="alpha.txt",
                    created_at="2026-04-23T00:00:00Z",
                    snippet="Alpha snippet",
                    context_snippet="Alpha context",
                    expanded_context_snippet="Alpha expanded context",
                )
            ],
        ),
    )

    class DummyAnswerProvider:
        def answer(self, *, question, mode, evidence_citations, grounded_items):
            del question, mode, grounded_items
            return qa_service.AnswerArtifact(
                answer="Hallucinated answer",
                answer_status="grounded",
                confidence=0.91,
                citation_ids=["cite-missing"],
                suggested_queries=[],
                quality_meta={"provider": "dummy"},
            )

    monkeypatch.setattr(qa_service, "create_answer_provider", lambda _config: DummyAnswerProvider())

    result = qa_service.answer_question(
        db=configured_client.app.state.db,
        config=configured_client.app.state.config,
        payload=QAAnswerRequest(question="What is alpha?"),
    )

    assert result["answer_status"] == "insufficient_evidence"
    assert result["citations"] == []
    assert result["suggested_queries"]


def test_answer_question_degrades_when_answer_is_not_supported_by_citations(
    configured_client,
    monkeypatch,
) -> None:
    from backend.app.schemas.qa import QAAnswerRequest
    from backend.app.services import qa as qa_service
    from backend.app.services.retrieval_types import CitationPackItem, RetrievalResult

    monkeypatch.setattr(
        qa_service,
        "build_retrieval_context",
        lambda **kwargs: RetrievalResult(
            query_text=kwargs["query"].text,
            filters=kwargs["query"].filters,
            child_hits=[],
            parent_contexts={},
            citations=[
                CitationPackItem(
                    citation_id="cite-1",
                    rank=1,
                    knowledge_item_id="ki-1",
                    chunk_id="chunk-1",
                    parent_chunk_id="parent-1",
                    title="Alpha report",
                    section_title="Section A",
                    source_type="text",
                    source_name="alpha.txt",
                    source_value="alpha.txt",
                    created_at="2026-04-23T00:00:00Z",
                    snippet="Alpha evidence explains cosine similarity.",
                    context_snippet="Alpha evidence explains cosine similarity.",
                    expanded_context_snippet="Alpha evidence explains cosine similarity.",
                )
            ],
        ),
    )

    class DummyAnswerProvider:
        def answer(self, *, question, mode, evidence_citations, grounded_items):
            del question, mode, evidence_citations, grounded_items
            return qa_service.AnswerArtifact(
                answer="Beta database migration requires a blue green deploy.",
                answer_status="grounded",
                confidence=0.91,
                citation_ids=["cite-1"],
                suggested_queries=[],
                quality_meta={"provider": "dummy"},
            )

    monkeypatch.setattr(qa_service, "create_answer_provider", lambda _config: DummyAnswerProvider())

    result = qa_service.answer_question(
        db=configured_client.app.state.db,
        config=configured_client.app.state.config,
        payload=QAAnswerRequest(question="What does alpha evidence explain?"),
    )

    assert result["answer_status"] == "insufficient_evidence"
    assert result["citations"] == []
    assert result["retry_count"] == 1
    assert result["verification"]["status"] == "failed"


def test_answer_question_retries_once_and_returns_supported_retry_answer(
    configured_client,
    monkeypatch,
) -> None:
    from backend.app.schemas.qa import QAAnswerRequest
    from backend.app.services import qa as qa_service
    from backend.app.services.retrieval_types import CitationPackItem, RetrievalResult

    query_limits: list[int] = []

    def _fake_retrieval_context(**kwargs):
        query_limits.append(kwargs["query"].limit)
        if len(query_limits) == 1:
            citation = CitationPackItem(
                citation_id="cite-1",
                rank=1,
                knowledge_item_id="ki-1",
                chunk_id="chunk-1",
                parent_chunk_id="parent-1",
                title="Alpha report",
                section_title="Section A",
                source_type="text",
                source_name="alpha.txt",
                source_value="alpha.txt",
                created_at="2026-04-23T00:00:00Z",
                snippet="Alpha evidence explains cosine similarity.",
                context_snippet="Alpha evidence explains cosine similarity.",
                expanded_context_snippet="Alpha evidence explains cosine similarity.",
            )
        else:
            citation = CitationPackItem(
                citation_id="cite-2",
                rank=1,
                knowledge_item_id="ki-2",
                chunk_id="chunk-2",
                parent_chunk_id="parent-2",
                title="Beta report",
                section_title="Section B",
                source_type="text",
                source_name="beta.txt",
                source_value="beta.txt",
                created_at="2026-04-23T00:00:00Z",
                snippet="Beta evidence explains vector database migration.",
                context_snippet="Beta evidence explains vector database migration.",
                expanded_context_snippet="Beta evidence explains vector database migration.",
            )
        return RetrievalResult(
            query_text=kwargs["query"].text,
            filters=kwargs["query"].filters,
            child_hits=[],
            parent_contexts={},
            citations=[citation],
        )

    class DummyAnswerProvider:
        def answer(self, *, question, mode, evidence_citations, grounded_items):
            del question, mode, grounded_items
            if evidence_citations[0]["citation_id"] == "cite-1":
                return qa_service.AnswerArtifact(
                    answer="Gamma deployment is the required answer.",
                    answer_status="grounded",
                    confidence=0.91,
                    citation_ids=["cite-1"],
                    suggested_queries=[],
                    quality_meta={"provider": "dummy"},
                )
            return qa_service.AnswerArtifact(
                answer="Beta evidence explains vector database migration.",
                answer_status="grounded",
                confidence=0.86,
                citation_ids=["cite-2"],
                suggested_queries=[],
                quality_meta={"provider": "dummy"},
            )

    monkeypatch.setattr(qa_service, "build_retrieval_context", _fake_retrieval_context)
    monkeypatch.setattr(qa_service, "create_answer_provider", lambda _config: DummyAnswerProvider())

    result = qa_service.answer_question(
        db=configured_client.app.state.db,
        config=configured_client.app.state.config,
        payload=QAAnswerRequest(question="What does beta evidence explain?", limit=5),
    )

    assert query_limits == [5, 10]
    assert result["answer_status"] == "grounded"
    assert result["answer"] == "Beta evidence explains vector database migration."
    assert [citation["citation_id"] for citation in result["citations"]] == ["cite-2"]
    assert result["retry_count"] == 1
    assert result["verification"]["status"] == "passed"


def test_answer_verifier_supports_chinese_term_list_answers() -> None:
    from backend.app.services import qa as qa_service

    verification = qa_service._verify_answer_support(
        answer_artifact=qa_service.AnswerArtifact(
            answer="存储层、索引层、查询层、服务层",
            answer_status="grounded",
            confidence=0.8,
            citation_ids=["cite-1"],
            suggested_queries=[],
            quality_meta={},
        ),
        selected_citations=[
            {
                "citation_id": "cite-1",
                "title": "向量数据库",
                "section_title": "架构",
                "snippet": "向量数据库通常采用存储层、索引层、查询层和服务层四层架构。",
                "context_snippet": "",
                "expanded_context_snippet": "",
            }
        ],
    )

    assert verification["status"] == "passed"


def test_answer_verifier_accepts_grounded_item_support() -> None:
    from backend.app.services import qa as qa_service

    verification = qa_service._verify_answer_support(
        answer_artifact=qa_service.AnswerArtifact(
            answer="存储层、索引层、查询层、服务层",
            answer_status="grounded",
            confidence=0.8,
            citation_ids=["cite-1"],
            suggested_queries=[],
            quality_meta={},
        ),
        selected_citations=[
            {
                "citation_id": "cite-1",
                "title": "向量数据库",
                "section_title": "概览",
                "snippet": "向量数据库用于相似性搜索。",
                "context_snippet": "",
                "expanded_context_snippet": "",
            }
        ],
        grounded_items=[
            {
                "title": "向量数据库",
                "claim": "向量数据库通常采用存储层、索引层、查询层和服务层四层架构。",
                "evidence_titles": ["向量数据库"],
            }
        ],
    )

    assert verification["status"] == "passed"
