from __future__ import annotations

from fastapi.testclient import TestClient


def test_qa_answer_rejects_empty_question(configured_client: TestClient) -> None:
    response = configured_client.post(
        "/api/qa/answer",
        json={"question": "   "},
    )

    assert response.status_code == 422


def test_qa_answer_returns_structured_payload(
    configured_client: TestClient,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "backend.app.routers.qa.answer_question",
        lambda **kwargs: {
            "session_id": "session-1",
            "mode": "answer",
            "rewritten_question": kwargs["payload"].question,
            "rewrite": {
                "rewritten_question": kwargs["payload"].question,
                "requires_history": False,
                "used_history": False,
                "intent": "answer",
                "risk_flags": ["self_contained"],
                "confidence": 0.72,
                "strategy": "heuristic",
            },
            "question": kwargs["payload"].question,
            "answer": "Alpha answer",
            "answer_status": "grounded",
            "confidence": 0.82,
            "applied_filters": {
                "source_types": ["text"],
                "created_at_from": None,
                "created_at_to": None,
                "knowledge_item_ids": ["ki-1"],
                "keyword": "alpha",
                "category": "research",
                "user_tags": ["alpha"],
                "ai_tags": ["report"],
            },
            "citations": [
                {
                    "citation_id": "cite-1",
                    "rank": 1,
                    "knowledge_item_id": "ki-1",
                    "chunk_id": "chunk-1",
                    "parent_chunk_id": "parent-1",
                    "title": "Alpha report",
                    "section_title": "Section A",
                    "source_type": "text",
                    "source_name": "alpha.txt",
                    "source_value": "alpha.txt",
                    "created_at": "2026-04-23T00:00:00Z",
                    "snippet": "Alpha snippet",
                    "context_snippet": "Alpha context",
                    "expanded_context_snippet": "Alpha expanded context",
                }
            ],
            "used_grounded_items": [
                {
                    "snapshot_id": "snapshot-1",
                    "title": "Alpha report",
                    "final_category": "research",
                    "claim": "Alpha claim",
                    "citation_ids": ["cite-1"],
                    "evidence_titles": ["Alpha report"],
                }
            ],
            "suggested_queries": [],
        },
        raising=False,
    )

    response = configured_client.post(
        "/api/qa/answer",
        json={
            "question": "What is alpha?",
            "limit": 3,
            "filters": {
                "source_types": ["text"],
                "knowledge_item_ids": ["ki-1"],
                "keyword": "alpha",
                "category": "research",
                "user_tags": ["alpha"],
                "ai_tags": ["report"],
            },
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["session_id"] == "session-1"
    assert payload["mode"] == "answer"
    assert payload["rewritten_question"] == "What is alpha?"
    assert payload["rewrite"]["rewritten_question"] == "What is alpha?"
    assert payload["question"] == "What is alpha?"
    assert payload["answer"] == "Alpha answer"
    assert payload["answer_status"] == "grounded"
    assert payload["confidence"] == 0.82
    assert payload["applied_filters"]["knowledge_item_ids"] == ["ki-1"]
    assert payload["citations"][0]["citation_id"] == "cite-1"
    assert payload["used_grounded_items"][0]["claim"] == "Alpha claim"
    assert payload["suggested_queries"] == []


def test_qa_router_is_registered(configured_client: TestClient) -> None:
    route_paths = {route.path for route in configured_client.app.routes}
    assert "/api/qa/answer" in route_paths
    assert "/api/qa/sessions" in route_paths
    assert "/api/qa/sessions/{session_id}" in route_paths
