from __future__ import annotations

from dataclasses import dataclass


@dataclass
class _DummyRewriteResult:
    rewritten_question: str
    used_history: bool = False
    requires_history: bool = False
    intent: str = "answer"
    risk_flags: list[str] | None = None
    confidence: float = 0.7
    strategy: str = "test"


def test_answer_question_persists_session_and_uses_history_for_follow_up(
    configured_client,
    monkeypatch,
) -> None:
    from backend.app.schemas.qa import QAAnswerRequest
    from backend.app.services import qa as qa_service
    from backend.app.services.retrieval_types import CitationPackItem, RetrievalResult

    captured_rewrite_calls: list[dict[str, object]] = []
    captured_query_texts: list[str] = []

    def _fake_rewrite_question(*, question, mode, history):
        captured_rewrite_calls.append(
            {
                "question": question,
                "mode": mode,
                "history": history,
            }
        )
        if len(captured_rewrite_calls) == 1:
            return _DummyRewriteResult(rewritten_question=question, used_history=False)
        assert len(history) == 2
        assert history[0]["role"] == "user"
        assert history[1]["role"] == "assistant"
        assert "vector embedding" in history[0]["content"].lower()
        return _DummyRewriteResult(
            rewritten_question="What are the three distance metrics for vector embedding?",
            used_history=True,
            requires_history=True,
            intent="follow_up",
            risk_flags=["uses_session_history"],
            confidence=0.82,
        )

    def _fake_retrieval_context(**kwargs):
        captured_query_texts.append(kwargs["query"].text)
        return RetrievalResult(
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
                    title="All in RAG",
                    section_title="6.1.1",
                    source_type="markdown",
                    source_name="all-in-rag-ch3.md",
                    source_value="all-in-rag-ch3.md",
                    created_at="2026-04-23T00:00:00Z",
                    snippet="Vector embedding turns complex objects into dense numerical vectors.",
                    context_snippet="Distance metrics include cosine similarity, dot product and euclidean distance.",
                    expanded_context_snippet="Distance metrics include cosine similarity, dot product and euclidean distance.",
                )
            ],
        )

    class DummyAnswerProvider:
        def answer(self, *, question, mode, evidence_citations, grounded_items):
            del mode
            del grounded_items
            return qa_service.AnswerArtifact(
                answer=f"Answer for: {question}",
                answer_status="grounded",
                confidence=0.88,
                citation_ids=[evidence_citations[0]["citation_id"]],
                suggested_queries=[],
                quality_meta={"provider": "dummy"},
            )

    monkeypatch.setattr(qa_service, "rewrite_qa_question", _fake_rewrite_question)
    monkeypatch.setattr(qa_service, "build_retrieval_context", _fake_retrieval_context)
    monkeypatch.setattr(qa_service, "create_answer_provider", lambda _config: DummyAnswerProvider())

    first = qa_service.answer_question(
        db=configured_client.app.state.db,
        config=configured_client.app.state.config,
        payload=QAAnswerRequest(question="What is vector embedding?"),
    )

    second = qa_service.answer_question(
        db=configured_client.app.state.db,
        config=configured_client.app.state.config,
        payload=QAAnswerRequest(
            session_id=first["session_id"],
            question="What are its three distance metrics?",
        ),
    )

    assert first["session_id"]
    assert second["session_id"] == first["session_id"]
    assert first["rewritten_question"] == "What is vector embedding?"
    assert second["rewritten_question"] == "What are the three distance metrics for vector embedding?"
    assert second["rewrite"]["requires_history"] is True
    assert second["rewrite"]["intent"] == "follow_up"
    assert second["rewrite"]["risk_flags"] == ["uses_session_history"]
    assert captured_query_texts == [
        "What is vector embedding?",
        "What are the three distance metrics for vector embedding?",
    ]

    detail = qa_service.get_qa_session_detail(
        db=configured_client.app.state.db,
        session_id=first["session_id"],
    )
    assert detail["session_id"] == first["session_id"]
    assert detail["mode"] == "answer"
    assert [message["role"] for message in detail["messages"]] == [
        "user",
        "assistant",
        "user",
        "assistant",
    ]
    assert detail["messages"][3]["rewritten_question"] == "What are the three distance metrics for vector embedding?"
    assert detail["messages"][3]["rewrite"]["requires_history"] is True
    assert detail["messages"][3]["rewrite"]["intent"] == "follow_up"
    assert detail["messages"][3]["verification"]["status"] == "passed"
    assert detail["messages"][3]["retry_count"] == 0

    sessions = qa_service.list_qa_sessions(db=configured_client.app.state.db)
    assert sessions[0]["session_id"] == first["session_id"]
    assert sessions[0]["message_count"] == 4


def test_delete_qa_session_removes_persisted_messages(configured_client, monkeypatch) -> None:
    from backend.app.schemas.qa import QAAnswerRequest
    from backend.app.services import qa as qa_service
    from backend.app.services.retrieval_types import CitationPackItem, RetrievalResult

    monkeypatch.setattr(
        qa_service,
        "rewrite_qa_question",
        lambda **kwargs: _DummyRewriteResult(
            rewritten_question=str(kwargs["question"]),
            used_history=False,
        ),
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
                )
            ],
        ),
    )

    class DummyAnswerProvider:
        def answer(self, *, question, mode, evidence_citations, grounded_items):
            del question, mode, grounded_items
            return qa_service.AnswerArtifact(
                answer="Alpha answer",
                answer_status="grounded",
                confidence=0.8,
                citation_ids=[evidence_citations[0]["citation_id"]],
                suggested_queries=[],
                quality_meta={"provider": "dummy"},
            )

    monkeypatch.setattr(qa_service, "create_answer_provider", lambda _config: DummyAnswerProvider())

    answer = qa_service.answer_question(
        db=configured_client.app.state.db,
        config=configured_client.app.state.config,
        payload=QAAnswerRequest(question="What is alpha?"),
    )

    deleted = qa_service.delete_qa_session(
        db=configured_client.app.state.db,
        session_id=answer["session_id"],
    )
    assert deleted is True
    assert qa_service.list_qa_sessions(db=configured_client.app.state.db) == []


def test_rewrite_qa_question_keeps_self_contained_question_without_history_injection() -> None:
    from backend.app.services import qa as qa_service

    question = "在Milvus中，Alias（别名）的一个关键应用场景是什么？"
    result = qa_service.rewrite_qa_question(
        question=question,
        mode="answer",
        history=[
            {
                "role": "user",
                "content": "一个Collection最多可以有多少个Partition？",
            },
            {
                "role": "assistant",
                "content": "一个Collection最多可以有1024个Partition。",
            },
        ],
    )

    assert result["rewritten_question"] == question
    assert result["used_history"] is False


def test_answer_question_passes_mode_to_provider(configured_client, monkeypatch) -> None:
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

    captured: dict[str, object] = {}

    class DummyAnswerProvider:
        def answer(self, *, question, mode, evidence_citations, grounded_items):
            del grounded_items
            captured["mode"] = mode
            return qa_service.AnswerArtifact(
                answer=f"{mode}: {question}",
                answer_status="grounded",
                confidence=0.77,
                citation_ids=[evidence_citations[0]["citation_id"]],
                suggested_queries=[],
                quality_meta={"provider": "dummy"},
            )

    monkeypatch.setattr(qa_service, "create_answer_provider", lambda _config: DummyAnswerProvider())

    result = qa_service.answer_question(
        db=configured_client.app.state.db,
        config=configured_client.app.state.config,
        payload=QAAnswerRequest(
            question="Summarize alpha",
            mode="summary",
        ),
    )

    assert captured["mode"] == "summary"
    assert result["mode"] == "summary"
    assert result["answer"] == "summary: Summarize alpha"


def test_answer_question_uses_structured_query_rewrite_provider(
    configured_client,
    monkeypatch,
) -> None:
    from backend.app.schemas.qa import QAAnswerRequest
    from backend.app.services import qa as qa_service
    from backend.app.services.retrieval_types import CitationPackItem, RetrievalResult

    captured_query_texts: list[str] = []

    class DummyRewriteProvider:
        def rewrite(self, *, question, mode, history, heuristic_rewrite):
            assert question == "What are its metrics?"
            assert mode == "answer"
            assert heuristic_rewrite["rewritten_question"] == "What are its metrics?"
            return qa_service.QueryRewriteArtifact(
                rewritten_question="What are vector embedding distance metrics?",
                requires_history=True,
                intent="follow_up",
                risk_flags=["llm_rewrite"],
                confidence=0.91,
                strategy="llm",
            )

    def _fake_retrieval_context(**kwargs):
        captured_query_texts.append(kwargs["query"].text)
        return RetrievalResult(
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
                    title="All in RAG",
                    section_title="6.1.2",
                    source_type="markdown",
                    source_name="all-in-rag-ch3.md",
                    source_value="all-in-rag-ch3.md",
                    created_at="2026-04-23T00:00:00Z",
                    snippet="Distance metrics include cosine similarity, dot product and euclidean distance.",
                    context_snippet="Distance metrics include cosine similarity, dot product and euclidean distance.",
                    expanded_context_snippet="Distance metrics include cosine similarity, dot product and euclidean distance.",
                )
            ],
        )

    class DummyAnswerProvider:
        def answer(self, *, question, mode, evidence_citations, grounded_items):
            del mode, grounded_items
            return qa_service.AnswerArtifact(
                answer=f"Answer for: {question}",
                answer_status="grounded",
                confidence=0.88,
                citation_ids=[evidence_citations[0]["citation_id"]],
                suggested_queries=[],
                quality_meta={"provider": "dummy"},
            )

    monkeypatch.setattr(qa_service, "create_query_rewrite_provider", lambda _config: DummyRewriteProvider())
    monkeypatch.setattr(qa_service, "build_retrieval_context", _fake_retrieval_context)
    monkeypatch.setattr(qa_service, "create_answer_provider", lambda _config: DummyAnswerProvider())

    result = qa_service.answer_question(
        db=configured_client.app.state.db,
        config=configured_client.app.state.config,
        payload=QAAnswerRequest(question="What are its metrics?"),
    )

    assert captured_query_texts == ["What are vector embedding distance metrics?"]
    assert result["rewritten_question"] == "What are vector embedding distance metrics?"
    assert result["rewrite"]["strategy"] == "llm"
    assert result["rewrite"]["requires_history"] is True
    assert result["rewrite"]["risk_flags"] == ["llm_rewrite"]


def test_rewrite_qa_question_does_not_treat_partition_as_follow_up() -> None:
    from backend.app.services import qa as qa_service

    question = "在Milvus中，Partition（分区）的作用是什么？"
    result = qa_service.rewrite_qa_question(
        question=question,
        mode="answer",
        history=[
            {
                "role": "user",
                "content": "在Milvus中，Collection（集合）可以被比喻成什么？",
            },
            {
                "role": "assistant",
                "content": "Collection 可以被比喻成数据库中的表。",
            },
        ],
    )

    assert result["rewritten_question"] == question
    assert result["used_history"] is False


def test_rewrite_qa_question_does_not_treat_jiqi_as_follow_up() -> None:
    from backend.app.services import qa as qa_service

    question = "静态词嵌入技术的代表模型及其局限性是什么？"
    result = qa_service.rewrite_qa_question(
        question=question,
        mode="answer",
        history=[
            {
                "role": "user",
                "content": "在RAG流程中，Embedding如何支撑语义检索？",
            },
            {
                "role": "assistant",
                "content": "Embedding 通过向量相似度支撑语义检索。",
            },
        ],
    )

    assert result["rewritten_question"] == question
    assert result["used_history"] is False
