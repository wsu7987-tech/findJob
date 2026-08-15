from __future__ import annotations

from fastapi.testclient import TestClient


def test_retrieval_search_rejects_empty_query(configured_client: TestClient) -> None:
    response = configured_client.post(
        "/api/retrieval/search",
        json={"query": "   ", "limit": 5},
    )

    assert response.status_code == 422


def test_retrieval_search_returns_child_hits_parent_contexts_and_scores(
    configured_client: TestClient,
    monkeypatch,
) -> None:
    from backend.app.services.retrieval_types import (
        ChildChunkHit,
        CitationPackItem,
        ParentContext,
        RetrievalResult,
    )

    def _fake_build_retrieval_context(*, db, config, query):
        return RetrievalResult(
            query_text=query.text,
            filters=query.filters,
            child_hits=[
                ChildChunkHit(
                    chunk_id="child-1",
                    knowledge_item_id="ki-1",
                    parent_chunk_id="parent-1",
                    section_title="Section A",
                    content="Alpha content",
                    source_type="text",
                    title="Alpha report",
                    source_name="alpha.txt",
                    source_value="alpha.txt",
                    created_at="2026-04-18T00:00:00Z",
                    category="research",
                    user_tags=["alpha"],
                    ai_tags=["report"],
                    vector_score=0.9,
                    metadata_keyword_score=1.0,
                    content_keyword_score=0.5,
                    final_score=0.885,
                )
            ],
            parent_contexts={
                "parent-1": ParentContext(
                    parent_chunk_id="parent-1",
                    knowledge_item_id="ki-1",
                    section_title="Section A",
                    content="Parent alpha context",
                    title="Alpha report",
                    source_type="text",
                    source_name="alpha.txt",
                    source_value="alpha.txt",
                    created_at="2026-04-18T00:00:00Z",
                    category="research",
                    user_tags=["alpha"],
                    ai_tags=["report"],
                )
            },
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
                    created_at="2026-04-18T00:00:00Z",
                    snippet="Alpha content",
                    context_snippet="Parent alpha context",
                    expanded_context_snippet="Alpha lead context\n\nAlpha content\n\nAlpha trailing context",
                )
            ],
        )

    monkeypatch.setattr(
        "backend.app.routers.retrieval.build_retrieval_context",
        _fake_build_retrieval_context,
        raising=False,
    )

    response = configured_client.post(
        "/api/retrieval/search",
        json={
            "query": "alpha report",
            "limit": 5,
                "filters": {
                    "source_types": ["text"],
                    "keyword": "alpha report",
                    "category": "research",
                    "user_tags": ["alpha"],
                    "ai_tags": ["report"],
                },
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["query"] == "alpha report"
    assert payload["applied_filters"]["keyword"] == "alpha report"
    assert payload["applied_filters"]["category"] == "research"
    assert payload["applied_filters"]["user_tags"] == ["alpha"]
    assert payload["applied_filters"]["ai_tags"] == ["report"]
    assert payload["child_hits"][0]["vector_score"] == 0.9
    assert payload["child_hits"][0]["metadata_keyword_score"] == 1.0
    assert payload["child_hits"][0]["content_keyword_score"] == 0.5
    assert payload["child_hits"][0]["final_score"] == 0.885
    assert payload["parent_contexts"]["parent-1"]["content"] == "Parent alpha context"
    assert payload["citations"][0]["citation_id"] == "cite-1"
    assert payload["citations"][0]["context_snippet"] == "Parent alpha context"
    assert payload["citations"][0]["expanded_context_snippet"] == "Alpha lead context\n\nAlpha content\n\nAlpha trailing context"


def test_retrieval_router_is_registered(configured_client: TestClient) -> None:
    route_paths = {route.path for route in configured_client.app.routes}
    assert "/api/retrieval/search" in route_paths


def test_retrieval_search_returns_empty_payload_when_service_has_no_hits(
    configured_client: TestClient,
    monkeypatch,
) -> None:
    from backend.app.services.retrieval_types import RetrievalResult

    monkeypatch.setattr(
        "backend.app.routers.retrieval.build_retrieval_context",
        lambda **kwargs: RetrievalResult(
            query_text=kwargs["query"].text,
            filters=kwargs["query"].filters,
            child_hits=[],
            parent_contexts={},
        ),
        raising=False,
    )

    response = configured_client.post(
        "/api/retrieval/search",
        json={"query": "nothing"},
    )

    assert response.status_code == 200
    assert response.json()["child_hits"] == []
    assert response.json()["parent_contexts"] == {}


def test_retrieval_search_passes_filters_into_retrieval_query(
    configured_client: TestClient,
    monkeypatch,
) -> None:
    from backend.app.services.retrieval_types import RetrievalResult

    captured_queries = []

    def _fake_build_retrieval_context(*, db, config, query):
        captured_queries.append(query)
        return RetrievalResult(
            query_text=query.text,
            filters=query.filters,
            child_hits=[],
            parent_contexts={},
        )

    monkeypatch.setattr(
        "backend.app.routers.retrieval.build_retrieval_context",
        _fake_build_retrieval_context,
        raising=False,
    )

    response = configured_client.post(
        "/api/retrieval/search",
        json={
            "query": "alpha report",
            "limit": 7,
            "filters": {
                "source_types": ["text"],
                "created_at_from": "2026-04-01T00:00:00Z",
                "created_at_to": "2026-04-30T23:59:59Z",
                "knowledge_item_ids": ["ki-1"],
                "keyword": "alpha",
                "category": "research",
                "user_tags": ["alpha"],
                "ai_tags": ["report"],
            },
        },
    )

    assert response.status_code == 200
    assert captured_queries
    assert captured_queries[0].limit == 7
    assert captured_queries[0].filters.source_types == ["text"]
    assert captured_queries[0].filters.created_at_from == "2026-04-01T00:00:00Z"
    assert captured_queries[0].filters.created_at_to == "2026-04-30T23:59:59Z"
    assert captured_queries[0].filters.knowledge_item_ids == ["ki-1"]
    assert captured_queries[0].filters.keyword == "alpha"
    assert captured_queries[0].filters.category == "research"
    assert captured_queries[0].filters.user_tags == ["alpha"]
    assert captured_queries[0].filters.ai_tags == ["report"]


def test_retrieval_search_rejects_invalid_source_type(configured_client: TestClient) -> None:
    response = configured_client.post(
        "/api/retrieval/search",
        json={
            "query": "alpha report",
            "filters": {"source_types": ["video"]},
        },
    )

    assert response.status_code == 422
