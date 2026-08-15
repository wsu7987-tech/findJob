from __future__ import annotations

from pathlib import Path

import httpx

from backend.app.config import AppConfig
from backend.app.services.ai import (
    create_answer_provider,
    create_query_rewrite_provider,
    OpenAICompatibleEmbeddingProvider,
    OpenAICompatibleSummaryProvider,
    create_embedding_provider,
    create_summary_provider,
)
from backend.app.services import ai as ai_service
from backend.app.services.ai import RelatedContextItem


def _build_config() -> AppConfig:
    app_data_dir = Path("D:/agent/KnowledgeCurator/backend/.pytest-tmp/test-ai")
    return AppConfig(
        app_data_dir=app_data_dir,
        local_config_path=app_data_dir / "config.user.json",
        sqlite_path=app_data_dir / "knowledge-curator.db",
        qdrant_path=app_data_dir / "qdrant",
        output_root=app_data_dir / "outputs",
        summary_output_dir=app_data_dir / "outputs/summaries",
        report_output_dir=app_data_dir / "outputs/reports",
        llm_provider=None,
        llm_model=None,
        llm_base_url=None,
        llm_api_key=None,
        embedding_provider=None,
        embedding_model=None,
        embedding_base_url=None,
        embedding_api_key=None,
        fetch_concurrency=3,
        llm_concurrency=2,
        embedding_concurrency=2,
        fetch_timeout_seconds=30,
        llm_timeout_seconds=90,
        embedding_timeout_seconds=60,
        fetch_user_agent="KnowledgeCurator/0.1 (+https://localhost)",
        quick_capture_hotkey=None,
        quick_capture_screenshot_hotkey=None,
        close_to_tray=True,
        quick_capture_always_on_top=True,
    )


def test_create_summary_provider_accepts_deepseek_alias() -> None:
    config = _build_config()
    config.llm_provider = "deepseek"
    config.llm_model = "deepseek-chat"
    config.llm_base_url = "https://api.deepseek.com/v1"
    config.llm_api_key = "secret-key"

    provider = create_summary_provider(config)

    assert isinstance(provider, OpenAICompatibleSummaryProvider)
    assert provider.base_url == "https://api.deepseek.com/v1"
    assert provider.model_name == "deepseek-chat"


def test_create_embedding_provider_accepts_qianwen_alias() -> None:
    config = _build_config()
    config.embedding_provider = "qianwen"
    config.embedding_model = "text-embedding-v4"
    config.embedding_base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    config.embedding_api_key = "secret-key"

    provider = create_embedding_provider(config)

    assert isinstance(provider, OpenAICompatibleEmbeddingProvider)
    assert provider.base_url == "https://dashscope.aliyuncs.com/compatible-mode/v1"
    assert provider.model_name == "text-embedding-v4"


def test_openai_compatible_embedding_provider_batches_dashscope_requests(monkeypatch) -> None:
    provider = OpenAICompatibleEmbeddingProvider(
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        api_key="secret-key",
        model_name="text-embedding-v4",
        timeout_seconds=60,
    )
    texts = [f"text-{index}" for index in range(23)]
    batch_sizes: list[int] = []

    def fake_post_json(*, url: str, api_key: str, timeout_seconds: int, payload: dict[str, object]):
        assert url == "https://dashscope.aliyuncs.com/compatible-mode/v1/embeddings"
        assert api_key == "secret-key"
        assert timeout_seconds == 60
        batch = payload["input"]
        assert isinstance(batch, list)
        batch_sizes.append(len(batch))
        return {
            "data": [{"embedding": [float(index)]} for index, _ in enumerate(batch)]
        }

    monkeypatch.setattr(ai_service, "_post_json", fake_post_json)

    vectors = provider.embed_texts(texts)

    assert batch_sizes == [10, 10, 3]
    assert len(vectors) == 23


def test_openai_compatible_embedding_provider_truncates_dashscope_inputs(monkeypatch) -> None:
    provider = OpenAICompatibleEmbeddingProvider(
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        api_key="secret-key",
        model_name="text-embedding-v4",
        timeout_seconds=60,
    )
    captured_inputs: list[str] = []

    def fake_post_json(*, url: str, api_key: str, timeout_seconds: int, payload: dict[str, object]):
        batch = payload["input"]
        assert isinstance(batch, list)
        captured_inputs.extend(str(value) for value in batch)
        return {"data": [{"embedding": [0.1, 0.2]} for _ in batch]}

    monkeypatch.setattr(ai_service, "_post_json", fake_post_json)

    vectors = provider.embed_texts(["a" * 3000, ""])

    assert len(vectors) == 2
    assert len(captured_inputs) == 2
    assert len(captured_inputs[0]) == 2048
    assert captured_inputs[1]


def test_openai_compatible_embedding_provider_uses_larger_batches_for_general_compat(monkeypatch) -> None:
    provider = OpenAICompatibleEmbeddingProvider(
        base_url="https://api.deepseek.com/v1",
        api_key="secret-key",
        model_name="embedding-model",
        timeout_seconds=60,
    )
    texts = [f"text-{index}" for index in range(12)]
    batch_sizes: list[int] = []

    def fake_post_json(*, url: str, api_key: str, timeout_seconds: int, payload: dict[str, object]):
        batch = payload["input"]
        assert isinstance(batch, list)
        batch_sizes.append(len(batch))
        return {"data": [{"embedding": [0.1, 0.2]} for _ in batch]}

    monkeypatch.setattr(ai_service, "_post_json", fake_post_json)

    vectors = provider.embed_texts(texts)

    assert batch_sizes == [12]
    assert len(vectors) == 12


def test_post_json_retries_timeout_and_reuses_cached_client(monkeypatch) -> None:
    calls = {"count": 0}
    created_clients: list[object] = []

    class FakeClient:
        def post(self, url: str, json: dict[str, object]):
            calls["count"] += 1
            if calls["count"] == 1:
                raise httpx.ConnectTimeout("handshake timed out")
            return httpx.Response(
                200,
                json={"ok": True},
                request=httpx.Request("POST", url),
            )

    def fake_get_http_client(*, url: str, api_key: str, timeout_seconds: int):
        if not created_clients:
            created_clients.append(FakeClient())
        return created_clients[0]

    monkeypatch.setattr(ai_service, "_get_http_client", fake_get_http_client)
    monkeypatch.setattr(ai_service.time, "sleep", lambda _seconds: None)

    payload = ai_service._post_json(
        url="https://dashscope.aliyuncs.com/compatible-mode/v1/embeddings",
        api_key="secret-key",
        timeout_seconds=60,
        payload={"model": "text-embedding-v4", "input": ["alpha"]},
    )

    assert payload == {"ok": True}
    assert calls["count"] == 2
    assert len(created_clients) == 1


def test_get_http_client_reuses_same_client_for_same_endpoint(monkeypatch) -> None:
    monkeypatch.setattr(ai_service, "_HTTP_CLIENT_CACHE", {})

    first = ai_service._get_http_client(
        url="https://dashscope.aliyuncs.com/compatible-mode/v1/embeddings",
        api_key="secret-key",
        timeout_seconds=60,
    )
    second = ai_service._get_http_client(
        url="https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
        api_key="secret-key",
        timeout_seconds=60,
    )

    assert first is second
    first.close()


def test_stub_summary_provider_tracks_memory_and_evidence_counts() -> None:
    config = _build_config()
    provider = create_summary_provider(config)

    summary = provider.summarize(
        title="Current item",
        source_type="text",
        source_value="source-1",
        cleaning_level=None,
        raw_content="Summary content",
        related_items=[
            RelatedContextItem(
                snapshot_id="snapshot-1",
                knowledge_item_id="ki-1",
                title="Memory item",
                final_category="general",
                summary_text="Memory summary",
                score=0.8,
            )
        ],
        evidence_citations=[
            {
                "citation_id": "cite-1",
                "knowledge_item_id": "ki-1",
                "chunk_id": "chunk-1",
                "snippet": "evidence snippet",
            }
        ],
    )

    assert summary.quality_meta["memory_context_count"] == 1
    assert summary.quality_meta["evidence_citation_count"] == 1
    assert summary.one_sentence_takeaway
    assert summary.key_points
    assert summary.keywords
    assert summary.grounded_claims
    assert summary.grounded_claims[0]["citation_ids"] == ["cite-1"]
    assert summary.summary_segments
    assert summary.summary_segments[0]["citation_ids"] == ["cite-1"]


def test_create_answer_provider_returns_stub_provider_by_default() -> None:
    config = _build_config()

    provider = create_answer_provider(config)

    answer = provider.answer(
        question="What is alpha?",
        mode="answer",
        evidence_citations=[],
        grounded_items=[],
    )

    assert answer.answer_status == "insufficient_evidence"
    assert answer.citation_ids == []
    assert answer.suggested_queries


def test_stub_answer_provider_prefers_grounded_claim_when_available() -> None:
    config = _build_config()
    provider = create_answer_provider(config)

    answer = provider.answer(
        question="What is alpha?",
        mode="answer",
        evidence_citations=[
            {
                "citation_id": "cite-1",
                "snippet": "Alpha snippet",
                "context_snippet": "Alpha context",
            }
        ],
        grounded_items=[
            {
                "claim": "Alpha grounded claim",
                "title": "Alpha report",
                "final_category": "research",
                "citation_ids": ["summary-cite-1"],
                "evidence_titles": ["Alpha report"],
            }
        ],
    )

    assert answer.answer_status == "grounded"
    assert answer.citation_ids == ["cite-1"]
    assert "Alpha grounded claim" in answer.answer


def test_create_query_rewrite_provider_returns_structured_stub_payload() -> None:
    config = _build_config()
    provider = create_query_rewrite_provider(config)

    rewrite = provider.rewrite(
        question="它的局限是什么？",
        mode="answer",
        history=[],
        heuristic_rewrite={
            "rewritten_question": "静态词嵌入的局限是什么？",
            "requires_history": True,
            "used_history": True,
            "intent": "follow_up",
            "risk_flags": ["uses_session_history"],
            "confidence": 0.78,
        },
    )

    assert rewrite.rewritten_question == "静态词嵌入的局限是什么？"
    assert rewrite.requires_history is True
    assert rewrite.intent == "follow_up"
    assert rewrite.risk_flags == ["uses_session_history"]
    assert rewrite.strategy == "heuristic"
