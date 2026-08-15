from __future__ import annotations

import json
import time
from pathlib import Path

from fastapi.testclient import TestClient


def _wait_for_run_status(
    client: TestClient,
    run_id: str,
    *,
    terminal_statuses: tuple[str, ...] = ("completed", "failed", "cancelled"),
    timeout_seconds: float = 5.0,
) -> dict[str, object]:
    deadline = time.time() + timeout_seconds
    last_payload: dict[str, object] | None = None
    while time.time() < deadline:
        response = client.get(f"/api/runs/{run_id}")
        assert response.status_code == 200
        last_payload = response.json()
        if last_payload["status"] in terminal_statuses:
            return last_payload
        time.sleep(0.05)
    raise AssertionError(f"run {run_id} did not reach terminal status in time: {last_payload}")


def test_summary_precheck_requires_complete_config(client: TestClient) -> None:
    response = client.get("/api/summary/precheck")

    assert response.status_code == 400
    assert response.json() == {
        "error_category": "CONFIG_INVALID",
        "error_message": (
            "Missing required config: "
            "llm_provider, llm_model, embedding_provider, embedding_model"
        ),
    }


def test_summary_precheck_reports_missing_real_provider_fields(
    configured_client: TestClient,
) -> None:
    patch_response = configured_client.patch(
        "/api/config",
        json={
            "llm_provider": "openai-compatible",
            "llm_model": "gpt-4o-mini",
            "embedding_provider": "openai-compatible",
            "embedding_model": "text-embedding-3-small",
        },
    )

    assert patch_response.status_code == 200

    response = configured_client.get("/api/summary/precheck")

    assert response.status_code == 400
    assert response.json() == {
        "error_category": "CONFIG_INVALID",
        "error_message": (
            "Missing required config: "
            "llm_base_url, llm_api_key, embedding_base_url, embedding_api_key"
        ),
    }


def test_summary_precheck_excludes_succeeded_items(
    configured_client: TestClient,
) -> None:
    pending_response = configured_client.post(
        "/api/pool/items",
        json={
            "source_type": "text",
            "source_value": "pending-item",
            "title": "Pending item",
            "raw_text": "Pending item text.",
        },
    )
    succeeded_response = configured_client.post(
        "/api/pool/items",
        json={
            "source_type": "text",
            "source_value": "succeeded-item",
            "title": "Succeeded item",
            "raw_text": "Succeeded item text.",
        },
    )

    start_response = configured_client.post(
        "/api/summary/runs",
        json={"pool_ids": [succeeded_response.json()["item"]["id"]]},
    )
    assert start_response.status_code == 201
    _wait_for_run_status(configured_client, start_response.json()["run_id"])

    precheck_response = configured_client.get("/api/summary/precheck")

    assert precheck_response.status_code == 200
    payload = precheck_response.json()
    assert [item["id"] for item in payload["items"]] == [pending_response.json()["item"]["id"]]
    assert payload["failed_retry_count"] == 0


def test_summary_run_creates_run_snapshot_and_markdown(
    configured_client: TestClient,
    configured_app_paths: dict[str, Path],
    sqlite_connection,
) -> None:
    create_response = configured_client.post(
        "/api/pool/items",
        json={
            "source_type": "text",
            "source_value": "summary-note",
            "title": "Backend minimal loop",
            "raw_text": "A small piece of content that should produce one summary snapshot.",
        },
    )
    pool_item = create_response.json()["item"]

    precheck_response = configured_client.get("/api/summary/precheck")

    assert precheck_response.status_code == 200
    assert precheck_response.json() == {
        "items": [
            {
                "id": pool_item["id"],
                "knowledge_item_id": pool_item["knowledge_item_id"],
                "title": "Backend minimal loop",
                "source_type": "text",
                "cleaning_level": None,
                "current_status": "pending",
            }
        ],
        "count": 1,
        "failed_retry_count": 0,
        "output_dir": str(configured_app_paths["summary_output_dir"]),
        "run_hint": "Ready to summarize 1 item(s)",
    }

    start_response = configured_client.post(
        "/api/summary/runs",
        json={"pool_ids": [pool_item["id"]]},
    )

    assert start_response.status_code == 201
    start_payload = start_response.json()
    assert start_payload["status"] == "pending"
    assert start_payload["stage"] == "queued"
    assert start_payload["run_id"]

    run_payload = _wait_for_run_status(configured_client, start_payload["run_id"])
    assert run_payload["run_id"] == start_payload["run_id"]
    assert run_payload["task_type"] == "summary"
    assert run_payload["status"] == "completed"
    assert run_payload["stage"] == "completed"
    assert run_payload["total_items"] == 1
    assert run_payload["succeeded_items"] == 1
    assert run_payload["failed_items"] == 0
    assert run_payload["skipped_items"] == 0
    assert run_payload["current_item_id"] == pool_item["knowledge_item_id"]
    assert run_payload["current_item_label"] == "Backend minimal loop"
    assert run_payload["error_category"] is None
    assert run_payload["error_message"] is None
    assert run_payload["updated_at"]
    assert len(run_payload["result_snapshots"]) == 1

    snapshot_row = sqlite_connection.execute(
        "SELECT id, knowledge_item_id, summary_run_id, markdown_path, summary_text, relation_meta "
        "FROM item_result_snapshots"
    ).fetchone()

    assert snapshot_row is not None
    assert snapshot_row["knowledge_item_id"] == pool_item["knowledge_item_id"]
    assert snapshot_row["summary_run_id"] == start_payload["run_id"]
    assert snapshot_row["markdown_path"]
    assert snapshot_row["summary_text"] == (
        "A small piece of content that should produce one summary snapshot."
    )
    assert run_payload["result_snapshots"][0]["snapshot_id"] == str(snapshot_row["id"])
    assert run_payload["result_snapshots"][0]["knowledge_item_id"] == pool_item["knowledge_item_id"]
    assert run_payload["result_snapshots"][0]["title"] == "Backend minimal loop"
    assert run_payload["result_snapshots"][0]["final_category"] == "general"
    assert run_payload["result_snapshots"][0]["markdown_path"] == snapshot_row["markdown_path"]
    assert run_payload["result_snapshots"][0]["markdown_filename"].startswith("backend-minimal-loop-")
    relation_meta = json.loads(str(snapshot_row["relation_meta"]))
    assert "memory_context_items" in relation_meta
    assert relation_meta["evidence_citations"]
    assert relation_meta["grounded_claims"]
    assert relation_meta["summary_segments"]
    assert relation_meta["summary_segments"][0]["citation_ids"]
    assert relation_meta["grounded_claims"][0]["citation_ids"]
    assert relation_meta["evidence_citations"][0]["knowledge_item_id"] == pool_item["knowledge_item_id"]

    markdown_path = Path(snapshot_row["markdown_path"])
    assert markdown_path.exists()
    assert markdown_path.parent == configured_app_paths["summary_output_dir"]
    assert markdown_path.name.startswith("backend-minimal-loop-")
    assert markdown_path.name.endswith(".md")
    markdown_content = markdown_path.read_text(encoding="utf-8")
    for section in (
        "# Backend minimal loop",
        "## 来源信息",
        "## 摘要",
        "## 核心观点",
        "## 争议点",
        "## 分类",
        "## 标签",
        "## 记忆上下文",
        "## 证据引用",
        "## 证据支撑结论",
        "## 证据映射",
        "## 生成时间",
    ):
        assert section in markdown_content
    for expected_fragment in (
        "- 类型: text",
        "- 来源: summary-note",
        "A small piece of content that should produce one summary snapshot.",
        "Backend minimal loop",
        "general",
        "- text",
        "- backend-minimal-loop",
        "- [cite-1]",
        "cite-1",
        "A small piece of content that should produce one summary snapshot. [cite-1]",
    ):
        assert expected_fragment in markdown_content


def test_run_events_stream_returns_payload_isomorphic_to_run(
    configured_client: TestClient,
) -> None:
    create_response = configured_client.post(
        "/api/pool/items",
        json={
            "source_type": "text",
            "source_value": "sse-note",
            "title": "SSE parity",
            "raw_text": "A single item used to verify the event stream payload.",
        },
    )
    pool_item = create_response.json()["item"]

    start_response = configured_client.post(
        "/api/summary/runs",
        json={"pool_ids": [pool_item["id"]]},
    )
    run_id = start_response.json()["run_id"]

    run_response = configured_client.get(f"/api/runs/{run_id}")
    sse_response = configured_client.get(f"/api/runs/{run_id}/events")

    assert sse_response.status_code == 200
    assert sse_response.headers["content-type"].startswith("text/event-stream")

    event_names: list[str] = []
    event_payloads: list[dict[str, object]] = []
    for line in sse_response.text.splitlines():
        if line.startswith("event: "):
            event_names.append(line.removeprefix("event: "))
        if line.startswith("data: "):
            event_payloads.append(json.loads(line.removeprefix("data: ")))

    assert event_names
    assert set(event_names).issubset(
        {"run.updated", "run.completed", "run.failed", "run.cancelled"}
    )
    assert any(payload["run_id"] == run_response.json()["run_id"] for payload in event_payloads)
    assert any(payload["status"] in {"running", "completed"} for payload in event_payloads)


def test_summary_run_failure_keeps_failed_run_and_cleans_markdown(
    configured_client: TestClient,
    configured_client_no_raise: TestClient,
    configured_app_paths: dict[str, Path],
    monkeypatch,
    sqlite_connection,
) -> None:
    from backend.app.services import runs as runs_service

    create_response = configured_client.post(
        "/api/pool/items",
        json={
            "source_type": "text",
            "source_value": "summary-failure",
            "title": "Broken output",
            "raw_text": "This item will simulate a markdown persistence failure.",
        },
    )
    pool_item = create_response.json()["item"]

    generated_ids = iter(["run-failed-case", "snapshot-failed-case"])
    monkeypatch.setattr(runs_service, "new_id", lambda: next(generated_ids))

    def broken_snapshot_insert(**kwargs) -> None:
        raise RuntimeError("simulated snapshot insert failure")

    monkeypatch.setattr(runs_service, "_insert_snapshot_record", broken_snapshot_insert)

    start_response = configured_client_no_raise.post(
        "/api/summary/runs",
        json={"pool_ids": [pool_item["id"]]},
    )

    assert start_response.status_code == 201
    assert start_response.json()["status"] == "pending"
    assert start_response.json()["stage"] == "queued"

    run_payload = _wait_for_run_status(
        configured_client,
        "run-failed-case",
        terminal_statuses=("failed",),
    )
    assert run_payload["run_id"] == "run-failed-case"
    assert run_payload["task_type"] == "summary"
    assert run_payload["status"] == "failed"
    assert run_payload["stage"] == "failed"
    assert run_payload["total_items"] == 1
    assert run_payload["succeeded_items"] == 0
    assert run_payload["failed_items"] == 1
    assert run_payload["skipped_items"] == 0
    assert run_payload["current_item_id"] == pool_item["knowledge_item_id"]
    assert run_payload["current_item_label"] == "Broken output"
    assert run_payload["error_category"] == "OUTPUT_FAILED"
    assert run_payload["error_message"] == "Failed to persist summary output."
    assert run_payload["updated_at"]

    assert list(configured_app_paths["summary_output_dir"].glob("*.md")) == []
    assert (
        sqlite_connection.execute("SELECT COUNT(*) FROM item_result_snapshots").fetchone()[0]
        == 0
    )


def test_summary_run_uses_langgraph_execution_path(
    configured_client: TestClient,
    configured_client_no_raise: TestClient,
    monkeypatch,
) -> None:
    from backend.app.services import runs as runs_service

    create_response = configured_client.post(
        "/api/pool/items",
        json={
            "source_type": "text",
            "source_value": "graph-entry-note",
            "title": "Graph entry point",
            "raw_text": "Graph entry point content.",
        },
    )
    pool_item = create_response.json()["item"]

    captured_start_states: list[dict[str, object]] = []

    monkeypatch.setattr(
        runs_service,
        "_start_summary_run_thread",
        lambda **kwargs: captured_start_states.append(kwargs["initial_state"]),
    )
    monkeypatch.setattr(
        runs_service,
        "_load_summary_rows",
        lambda _db, _pool_ids: [
            {
                "id": pool_item["id"],
                "knowledge_item_id": pool_item["knowledge_item_id"],
                "current_status": "pending",
                "title": "Graph entry point",
                "source_type": "text",
                "source_value": "graph-entry-note",
                "raw_content": "Graph entry point content.",
            }
        ],
    )

    response = configured_client_no_raise.post("/api/summary/runs", json={"pool_ids": [pool_item["id"]]})

    assert response.status_code == 201
    payload = response.json()
    assert payload["status"] == "pending"
    assert payload["stage"] == "queued"
    assert payload["run_id"]
    assert captured_start_states
    assert captured_start_states[0]["pool_ids"] == [pool_item["id"]]


def test_summary_run_builds_config_snapshot_for_graph_state(
    configured_client: TestClient,
    monkeypatch,
) -> None:
    from backend.app.services import runs as runs_service

    create_response = configured_client.post(
        "/api/pool/items",
        json={
            "source_type": "text",
            "source_value": "graph-config-note",
            "title": "Graph config snapshot",
            "raw_text": "Graph config snapshot content.",
        },
    )
    pool_item = create_response.json()["item"]

    captured_states: list[dict[str, object]] = []

    monkeypatch.setattr(
        runs_service,
        "_start_summary_run_thread",
        lambda **kwargs: captured_states.append(kwargs["initial_state"]),
    )

    response = configured_client.post("/api/summary/runs", json={"pool_ids": [pool_item["id"]]})

    assert response.status_code == 201
    assert captured_states
    assert captured_states[0]["pool_ids"] == [pool_item["id"]]
    assert captured_states[0]["pending_pool_ids"] == [pool_item["id"]]
    assert captured_states[0]["config_snapshot"]["llm_provider"] == "stub-llm"
    assert captured_states[0]["config_snapshot"]["embedding_provider"] == "stub-embedding"


def test_load_current_item_prepares_retrieval_query_and_marks_running(monkeypatch) -> None:
    from backend.app.graphs import summary_nodes
    from backend.app.services import runs as runs_service

    captured_run_updates: list[tuple[tuple[object, ...], dict[str, object]]] = []
    captured_pool_updates: list[tuple[tuple[object, ...], dict[str, object]]] = []

    monkeypatch.setattr(
        runs_service,
        "_update_run_state",
        lambda *args, **kwargs: captured_run_updates.append((args, kwargs)),
    )
    monkeypatch.setattr(
        runs_service,
        "_update_pool_processing_state",
        lambda *args, **kwargs: captured_pool_updates.append((args, kwargs)),
    )

    state = {
        "run_id": "run-1",
        "succeeded_items": 0,
        "failed_items": 0,
        "current_item": {
            "id": "pool-1",
            "knowledge_item_id": "item-1",
            "title": "Title",
            "source_type": "text",
            "source_value": "fallback",
            "raw_content": "A" * 9000,
        },
    }

    result = summary_nodes.load_current_item(state, db=object())

    assert result["current_item_id"] == "item-1"
    assert result["current_item_label"] == "Title"
    assert result["retrieval_query"] == "A" * 8000
    assert captured_run_updates
    assert captured_pool_updates


def test_retrieve_prompt_context_stashes_related_items(monkeypatch) -> None:
    from backend.app.graphs import summary_nodes
    from backend.app.services.ai import RelatedContextItem
    from backend.app.services import runs as runs_service
    from backend.app.services.retrieval_types import (
        CitationPackItem,
        RetrievalFilters,
        RetrievalResult,
    )

    monkeypatch.setattr(runs_service, "restore_config_snapshot", lambda snapshot: snapshot)

    class DummyEmbeddingProvider:
        def embed_texts(self, texts: list[str]) -> list[list[float]]:
            assert texts == ["query text"]
            return [[0.25, 0.75]]

    class DummyVectorStore:
        def search_related(self, vector: list[float], *, limit: int = 5):
            assert vector == [0.25, 0.75]
            assert limit == 5
            return [
                RelatedContextItem(
                    snapshot_id="snapshot-self",
                    knowledge_item_id="item-1",
                    title="Current item old summary",
                    final_category="general",
                    summary_text="Current item historical summary",
                    score=0.93,
                ),
                RelatedContextItem(
                    snapshot_id="snapshot-1",
                    knowledge_item_id="item-2",
                    title="Related item",
                    final_category="general",
                    summary_text="Related summary",
                    score=0.88,
                )
            ]

    def fake_build_retrieval_context(*, db, config, query):
        assert db is not None
        assert config["embedding_provider"] == "stub-embedding"
        assert query.text == "query text"
        assert query.limit == 3
        assert query.filters == RetrievalFilters(knowledge_item_ids=["item-1"])
        return RetrievalResult(
            query_text=query.text,
            filters=query.filters,
            child_hits=[],
            parent_contexts={},
            citations=[
                CitationPackItem(
                    citation_id="cite-1",
                    rank=1,
                    knowledge_item_id="item-1",
                    chunk_id="chunk-1",
                    parent_chunk_id="parent-1",
                    title="Current item",
                    section_title="Section A",
                    source_type="text",
                    source_name="Current item",
                    source_value="query-source",
                    created_at="2026-04-22T00:00:00Z",
                    snippet="evidence snippet",
                    context_snippet="evidence context",
                    expanded_context_snippet="expanded evidence context",
                )
            ],
        )

    monkeypatch.setattr(summary_nodes, "create_embedding_provider", lambda _config: DummyEmbeddingProvider())
    monkeypatch.setattr(summary_nodes, "SummaryVectorStore", lambda **_kwargs: DummyVectorStore())
    monkeypatch.setattr(summary_nodes, "build_retrieval_context", fake_build_retrieval_context)

    result = summary_nodes.retrieve_prompt_context(
        {
            "config_snapshot": {"embedding_provider": "stub-embedding", "embedding_model": "stub-model"},
            "retrieval_query": "query text",
            "current_item_id": "item-1",
        },
        db=object(),
    )

    assert result["memory_related_items"] == [
        {
            "snapshot_id": "snapshot-1",
            "knowledge_item_id": "item-2",
            "title": "Related item",
            "final_category": "general",
            "summary_text": "Related summary",
            "score": 0.88,
        }
    ]
    assert result["evidence_citations"] == [
        {
            "citation_id": "cite-1",
            "rank": 1,
            "knowledge_item_id": "item-1",
            "chunk_id": "chunk-1",
            "parent_chunk_id": "parent-1",
            "title": "Current item",
            "section_title": "Section A",
            "source_type": "text",
            "source_name": "Current item",
            "source_value": "query-source",
            "created_at": "2026-04-22T00:00:00Z",
            "snippet": "evidence snippet",
            "context_snippet": "evidence context",
            "expanded_context_snippet": "expanded evidence context",
        }
    ]


def test_build_summary_snapshot_drops_claims_and_segments_with_unknown_citation_ids() -> None:
    from backend.app.services.ai import SummaryArtifact
    from backend.app.services.summary_output import build_summary_snapshot

    snapshot = build_summary_snapshot(
        snapshot_id="snapshot-1",
        run_id="run-1",
        knowledge_item_id="item-1",
        title="Current item",
        source_type="text",
        source_value="source-1",
        created_at="2026-04-23T00:00:00Z",
        summary=SummaryArtifact(
            generated_category="general",
            generated_tags=["text"],
            summary_text="summary text",
            viewpoint_text="viewpoint",
            controversy_text="none",
            content_quality_score=0.9,
            grounded_claims=[
                {"claim": "valid claim", "citation_ids": ["cite-1"]},
                {"claim": "invalid claim", "citation_ids": ["cite-missing"]},
            ],
            summary_segments=[
                {"text": "valid segment", "citation_ids": ["cite-1"]},
                {"text": "invalid segment", "citation_ids": ["cite-missing"]},
            ],
            quality_meta={},
        ),
        related_items=[],
        evidence_citations=[
            {
                "citation_id": "cite-1",
                "rank": 1,
                "knowledge_item_id": "item-1",
                "chunk_id": "chunk-1",
                "parent_chunk_id": "parent-1",
                "title": "Current item",
                "section_title": "Section A",
                "source_type": "text",
                "source_name": "Current item",
                "source_value": "source-1",
                "created_at": "2026-04-23T00:00:00Z",
                "snippet": "evidence snippet",
                "context_snippet": "evidence context",
                "expanded_context_snippet": "expanded evidence context",
            }
        ],
    )

    relation_meta = snapshot["relation_meta"]
    assert relation_meta["grounded_claims"] == [
        {
            "claim": "valid claim",
            "citation_ids": ["cite-1"],
            "evidence_titles": ["Current item"],
        }
    ]
    assert relation_meta["summary_segments"] == [
        {
            "text": "valid segment",
            "citation_ids": ["cite-1"],
            "evidence_titles": ["Current item"],
        }
    ]
    assert "cite-missing" not in snapshot["markdown_content"]


def test_generate_summary_artifact_prefers_evidence_context_over_memory_only(monkeypatch) -> None:
    from backend.app.graphs import summary_nodes
    from backend.app.services.ai import SummaryArtifact
    from backend.app.services import runs as runs_service

    monkeypatch.setattr(runs_service, "restore_config_snapshot", lambda snapshot: snapshot)
    monkeypatch.setattr(
        runs_service,
        "restore_related_context_items",
        lambda items: [
            type("RelatedItem", (), item)()
            for item in items
        ],
    )

    class DummySummaryProvider:
        def summarize(
            self,
            *,
            title,
            source_type,
            source_value,
            cleaning_level,
            raw_content,
            related_items,
            evidence_citations,
        ):
            assert title == "Current item"
            assert source_type == "text"
            assert source_value == "query-source"
            assert cleaning_level is None
            assert raw_content == "query body"
            assert len(related_items) == 1
            assert related_items[0].title == "Memory item"
            assert len(evidence_citations) == 1
            assert evidence_citations[0]["citation_id"] == "cite-1"
            return SummaryArtifact(
                generated_category="general",
                generated_tags=["text", "alpha"],
                summary_text="summary text",
                viewpoint_text="viewpoint",
                controversy_text="none",
                content_quality_score=0.9,
                grounded_claims=[
                    {
                        "claim": "summary text",
                        "citation_ids": ["cite-1"],
                    }
                ],
                summary_segments=[
                    {
                        "text": "summary text",
                        "citation_ids": ["cite-1"],
                    }
                ],
                quality_meta={
                    "memory_context_count": len(related_items),
                    "evidence_citation_count": len(evidence_citations),
                },
            )

    monkeypatch.setattr(summary_nodes, "create_summary_provider", lambda _config: DummySummaryProvider())

    result = summary_nodes.generate_summary_artifact(
        {
            "config_snapshot": {"llm_provider": "stub-llm"},
            "current_item": {
                "title": "Current item",
                "source_type": "text",
                "source_value": "query-source",
                "cleaning_level": None,
                "raw_content": "query body",
            },
            "memory_related_items": [
                {
                    "snapshot_id": "snapshot-1",
                    "knowledge_item_id": "item-1",
                    "title": "Memory item",
                    "final_category": "general",
                    "summary_text": "Memory summary",
                    "score": 0.88,
                }
            ],
            "evidence_citations": [
                {
                    "citation_id": "cite-1",
                    "rank": 1,
                    "knowledge_item_id": "item-1",
                    "chunk_id": "chunk-1",
                    "parent_chunk_id": "parent-1",
                    "title": "Current item",
                    "section_title": "Section A",
                    "source_type": "text",
                    "source_name": "Current item",
                    "source_value": "query-source",
                    "created_at": "2026-04-22T00:00:00Z",
                    "snippet": "evidence snippet",
                    "context_snippet": "evidence context",
                    "expanded_context_snippet": "expanded evidence context",
                }
            ],
        },
        db=object(),
    )

    assert result["summary_payload"]["summary_text"] == "summary text"
    assert result["summary_payload"]["grounded_claims"] == [
        {"claim": "summary text", "citation_ids": ["cite-1"]}
    ]
    assert result["summary_payload"]["summary_segments"] == [
        {"text": "summary text", "citation_ids": ["cite-1"]}
    ]
    assert result["summary_payload"]["quality_meta"]["memory_context_count"] == 1
    assert result["summary_payload"]["quality_meta"]["evidence_citation_count"] == 1


def test_build_summary_snapshot_renders_reader_guide_sections() -> None:
    from backend.app.services.ai import SummaryArtifact
    from backend.app.services.summary_output import build_summary_snapshot

    snapshot = build_summary_snapshot(
        snapshot_id="snapshot-reader-guide",
        run_id="run-1",
        knowledge_item_id="item-1",
        title="Current item",
        source_type="text",
        source_value="source-1",
        created_at="2026-04-23T00:00:00Z",
        summary=SummaryArtifact(
            generated_category="general",
            generated_tags=["text"],
            one_sentence_takeaway="一句话结论",
            summary_text="summary text",
            viewpoint_text="一句话结论",
            controversy_text=None,
            reading_focus=["先看定义"],
            key_points=["关键知识点"],
            keywords=[{"keyword": "index", "weight": 0.9}],
            methods_or_process=["先建索引"],
            pitfalls_or_limits=["注意参数边界"],
            code_examples=[],
            content_quality_score=0.9,
            grounded_claims=[{"claim": "valid claim", "citation_ids": ["cite-1"]}],
            summary_segments=[{"text": "valid segment", "citation_ids": ["cite-1"]}],
            quality_meta={
                "one_sentence_takeaway": "一句话结论",
                "reading_focus": ["先看定义"],
                "key_points": ["关键知识点"],
                "keywords": [{"keyword": "index", "weight": 0.9}],
                "methods_or_process": ["先建索引"],
                "pitfalls_or_limits": ["注意参数边界"],
                "reader_guide": {
                    "what_it_is": "这篇文档解释索引优化在做什么。",
                    "why_it_matters": "它决定检索速度和召回率的平衡。",
                    "how_to_apply": ["先理解目标", "再选索引", "最后调参数"],
                    "core_concepts": ["索引结构", "召回率", "查询延迟"],
                    "study_path": ["先看定义", "再看权衡", "最后看参数"],
                },
            },
        ),
        related_items=[],
        evidence_citations=[
            {
                "citation_id": "cite-1",
                "rank": 1,
                "knowledge_item_id": "item-1",
                "chunk_id": "chunk-1",
                "parent_chunk_id": "parent-1",
                "title": "Current item",
                "section_title": "Section A",
                "source_type": "text",
                "source_name": "Current item",
                "source_value": "source-1",
                "created_at": "2026-04-23T00:00:00Z",
                "snippet": "evidence snippet",
                "context_snippet": "evidence context",
                "expanded_context_snippet": "expanded evidence context",
            }
        ],
    )

    markdown_content = snapshot["markdown_content"]
    for section in (
        "## 学习导读",
        "### 是什么",
        "### 为什么重要",
        "### 怎么学",
        "### 核心概念",
        "### 阅读路径",
    ):
        assert section in markdown_content
    for fragment in (
        "这篇文档解释索引优化在做什么。",
        "它决定检索速度和召回率的平衡。",
        "- 先理解目标",
        "- 索引结构",
        "- 先看定义",
    ):
        assert fragment in markdown_content


def test_generate_summary_artifact_preserves_reader_guide_structure(monkeypatch) -> None:
    from backend.app.graphs import summary_nodes
    from backend.app.services import runs as runs_service
    from backend.app.services.ai import SummaryArtifact

    monkeypatch.setattr(runs_service, "restore_config_snapshot", lambda snapshot: snapshot)
    monkeypatch.setattr(
        runs_service,
        "restore_related_context_items",
        lambda items: [type("RelatedItem", (), item)() for item in items],
    )

    class DummySummaryProvider:
        def summarize(
            self,
            *,
            title,
            source_type,
            source_value,
            cleaning_level,
            raw_content,
            related_items,
            evidence_citations,
        ):
            return SummaryArtifact(
                generated_category="general",
                generated_tags=["text"],
                one_sentence_takeaway="一句话结论",
                summary_text="summary text",
                viewpoint_text="一句话结论",
                controversy_text=None,
                reading_focus=["先看定义"],
                key_points=["summary text"],
                keywords=[{"keyword": "alpha", "weight": 0.8}],
                methods_or_process=[],
                pitfalls_or_limits=[],
                code_examples=[],
                content_quality_score=0.9,
                grounded_claims=[{"claim": "summary text", "citation_ids": ["cite-1"]}],
                summary_segments=[{"text": "summary text", "citation_ids": ["cite-1"]}],
                quality_meta={
                    "memory_context_count": len(related_items),
                    "evidence_citation_count": len(evidence_citations),
                    "one_sentence_takeaway": "一句话结论",
                    "reading_focus": ["先看定义"],
                    "key_points": ["summary text"],
                    "keywords": [{"keyword": "alpha", "weight": 0.8}],
                    "reader_guide": {
                        "what_it_is": "这篇文档解释 alpha 在系统中的作用。",
                        "why_it_matters": "它有助于快速判断技术方案的边界。",
                        "how_to_apply": ["先看定义", "再看例子"],
                        "core_concepts": ["alpha", "边界"],
                        "study_path": ["先看问题定义", "再看证据"],
                    },
                    "methods_or_process": [],
                    "pitfalls_or_limits": [],
                    "code_examples": [],
                },
            )

    monkeypatch.setattr(summary_nodes, "create_summary_provider", lambda _config: DummySummaryProvider())

    result = summary_nodes.generate_summary_artifact(
        {
            "config_snapshot": {"llm_provider": "stub-llm"},
            "current_item": {
                "title": "Current item",
                "source_type": "text",
                "source_value": "query-source",
                "cleaning_level": None,
                "raw_content": "query body",
            },
            "memory_related_items": [
                {
                    "snapshot_id": "snapshot-1",
                    "knowledge_item_id": "item-1",
                    "title": "Memory item",
                    "final_category": "general",
                    "summary_text": "Memory summary",
                    "score": 0.88,
                }
            ],
            "evidence_citations": [
                {
                    "citation_id": "cite-1",
                    "rank": 1,
                    "knowledge_item_id": "item-1",
                    "chunk_id": "chunk-1",
                    "parent_chunk_id": "parent-1",
                    "title": "Current item",
                    "section_title": "Section A",
                    "source_type": "text",
                    "source_name": "Current item",
                    "source_value": "query-source",
                    "created_at": "2026-04-22T00:00:00Z",
                    "snippet": "evidence snippet",
                    "context_snippet": "evidence context",
                    "expanded_context_snippet": "expanded evidence context",
                }
            ],
        },
        db=object(),
    )

    reader_guide = result["summary_payload"]["quality_meta"]["reader_guide"]
    assert reader_guide["what_it_is"] == "这篇文档解释 alpha 在系统中的作用。"
    assert reader_guide["why_it_matters"] == "它有助于快速判断技术方案的边界。"
    assert reader_guide["how_to_apply"] == ["先看定义", "再看例子"]
    assert reader_guide["core_concepts"] == ["alpha", "边界"]
    assert reader_guide["study_path"] == ["先看问题定义", "再看证据"]


def test_build_summary_item_graph_returns_compiled_graph() -> None:
    from backend.app.graphs.summary_item_graph import build_summary_item_graph

    graph = build_summary_item_graph(db=object())

    assert hasattr(graph, "invoke")


def test_summary_run_creates_run_snapshot_and_markdown(
    configured_client: TestClient,
    configured_app_paths: dict[str, Path],
    sqlite_connection,
) -> None:
    create_response = configured_client.post(
        "/api/pool/items",
        json={
            "source_type": "text",
            "source_value": "summary-note",
            "title": "Backend minimal loop",
            "raw_text": "A small piece of content that should produce one summary snapshot.",
        },
    )
    pool_item = create_response.json()["item"]

    precheck_response = configured_client.get("/api/summary/precheck")

    assert precheck_response.status_code == 200
    assert precheck_response.json() == {
        "items": [
            {
                "id": pool_item["id"],
                "knowledge_item_id": pool_item["knowledge_item_id"],
                "title": "Backend minimal loop",
                "source_type": "text",
                "cleaning_level": None,
                "current_status": "pending",
            }
        ],
        "count": 1,
        "failed_retry_count": 0,
        "output_dir": str(configured_app_paths["summary_output_dir"]),
        "run_hint": "Ready to summarize 1 item(s)",
    }

    start_response = configured_client.post(
        "/api/summary/runs",
        json={"pool_ids": [pool_item["id"]]},
    )

    assert start_response.status_code == 201
    start_payload = start_response.json()
    assert start_payload["status"] == "pending"
    assert start_payload["stage"] == "queued"
    assert start_payload["run_id"]

    run_payload = _wait_for_run_status(configured_client, start_payload["run_id"])
    assert run_payload["run_id"] == start_payload["run_id"]
    assert run_payload["task_type"] == "summary"
    assert run_payload["status"] == "completed"
    assert run_payload["stage"] == "completed"
    assert run_payload["total_items"] == 1
    assert run_payload["succeeded_items"] == 1
    assert run_payload["failed_items"] == 0
    assert run_payload["skipped_items"] == 0
    assert run_payload["current_item_id"] == pool_item["knowledge_item_id"]
    assert run_payload["current_item_label"] == "Backend minimal loop"
    assert run_payload["error_category"] is None
    assert run_payload["error_message"] is None
    assert run_payload["updated_at"]
    assert len(run_payload["result_snapshots"]) == 1

    snapshot_row = sqlite_connection.execute(
        "SELECT id, knowledge_item_id, summary_run_id, markdown_path, summary_text, relation_meta "
        "FROM item_result_snapshots"
    ).fetchone()

    assert snapshot_row is not None
    assert snapshot_row["knowledge_item_id"] == pool_item["knowledge_item_id"]
    assert snapshot_row["summary_run_id"] == start_payload["run_id"]
    assert snapshot_row["markdown_path"]
    assert snapshot_row["summary_text"] == (
        "A small piece of content that should produce one summary snapshot."
    )
    assert run_payload["result_snapshots"][0]["snapshot_id"] == str(snapshot_row["id"])
    assert run_payload["result_snapshots"][0]["knowledge_item_id"] == pool_item["knowledge_item_id"]
    assert run_payload["result_snapshots"][0]["title"] == "Backend minimal loop"
    assert run_payload["result_snapshots"][0]["final_category"] == "general"
    assert run_payload["result_snapshots"][0]["markdown_path"] == snapshot_row["markdown_path"]
    assert run_payload["result_snapshots"][0]["markdown_filename"].startswith("backend-minimal-loop-")
    relation_meta = json.loads(str(snapshot_row["relation_meta"]))
    assert "memory_context_items" in relation_meta
    assert relation_meta["evidence_citations"]
    assert relation_meta["grounded_claims"]
    assert relation_meta["summary_segments"]
    assert relation_meta["summary_segments"][0]["citation_ids"]
    assert relation_meta["grounded_claims"][0]["citation_ids"]
    assert relation_meta["evidence_citations"][0]["knowledge_item_id"] == pool_item["knowledge_item_id"]

    markdown_path = Path(snapshot_row["markdown_path"])
    assert markdown_path.exists()
    assert markdown_path.parent == configured_app_paths["summary_output_dir"]
    assert markdown_path.name.startswith("backend-minimal-loop-")
    assert markdown_path.name.endswith(".md")
    markdown_content = markdown_path.read_text(encoding="utf-8")
    for section in (
        "# Backend minimal loop",
        "## 来源信息",
        "## 一句话结论",
        "## 摘要",
        "## 分类",
        "## 标签",
        "## 阅读重点",
        "## 关键知识点",
        "## 关键词",
        "## 方法或流程",
        "## 注意点与局限",
        "## 记忆上下文",
        "## 证据引用",
        "## 证据支撑摘要",
        "## 证据映射",
        "## 生成时间",
    ):
        assert section in markdown_content
    for expected_fragment in (
        "- 类型: text",
        "- 来源: summary-note",
        "A small piece of content that should produce one summary snapshot.",
        "Backend minimal loop",
        "general",
        "- text",
        "- backend-minimal-loop",
        "- [cite-1]",
        "cite-1",
        "A small piece of content that should produce one summary snapshot. [cite-1]",
    ):
        assert expected_fragment in markdown_content


def test_build_summary_snapshot_drops_claims_and_segments_with_unknown_citation_ids() -> None:
    from backend.app.services.ai import SummaryArtifact
    from backend.app.services.summary_output import build_summary_snapshot

    snapshot = build_summary_snapshot(
        snapshot_id="snapshot-1",
        run_id="run-1",
        knowledge_item_id="item-1",
        title="Current item",
        source_type="text",
        source_value="source-1",
        created_at="2026-04-23T00:00:00Z",
        summary=SummaryArtifact(
            generated_category="general",
            generated_tags=["text"],
            one_sentence_takeaway="一句话结论",
            summary_text="summary text",
            viewpoint_text="一句话结论",
            controversy_text=None,
            reading_focus=["先看定义"],
            key_points=["valid claim"],
            keywords=[{"keyword": "index", "weight": 0.9}],
            methods_or_process=[],
            pitfalls_or_limits=[],
            code_examples=[],
            content_quality_score=0.9,
            grounded_claims=[
                {"claim": "valid claim", "citation_ids": ["cite-1"]},
                {"claim": "invalid claim", "citation_ids": ["cite-missing"]},
            ],
            summary_segments=[
                {"text": "valid segment", "citation_ids": ["cite-1"]},
                {"text": "invalid segment", "citation_ids": ["cite-missing"]},
            ],
            quality_meta={"one_sentence_takeaway": "一句话结论", "key_points": ["valid claim"]},
        ),
        related_items=[],
        evidence_citations=[
            {
                "citation_id": "cite-1",
                "rank": 1,
                "knowledge_item_id": "item-1",
                "chunk_id": "chunk-1",
                "parent_chunk_id": "parent-1",
                "title": "Current item",
                "section_title": "Section A",
                "source_type": "text",
                "source_name": "Current item",
                "source_value": "source-1",
                "created_at": "2026-04-23T00:00:00Z",
                "snippet": "evidence snippet",
                "context_snippet": "evidence context",
                "expanded_context_snippet": "expanded evidence context",
            }
        ],
    )

    relation_meta = snapshot["relation_meta"]
    assert relation_meta["grounded_claims"] == [
        {
            "claim": "valid claim",
            "citation_ids": ["cite-1"],
            "evidence_titles": ["Current item"],
        }
    ]
    assert relation_meta["summary_segments"] == [
        {
            "text": "valid segment",
            "citation_ids": ["cite-1"],
            "evidence_titles": ["Current item"],
        }
    ]
    assert "cite-missing" not in snapshot["markdown_content"]


def test_generate_summary_artifact_prefers_evidence_context_over_memory_only(monkeypatch) -> None:
    from backend.app.graphs import summary_nodes
    from backend.app.services.ai import SummaryArtifact
    from backend.app.services import runs as runs_service

    monkeypatch.setattr(runs_service, "restore_config_snapshot", lambda snapshot: snapshot)
    monkeypatch.setattr(
        runs_service,
        "restore_related_context_items",
        lambda items: [type("RelatedItem", (), item)() for item in items],
    )

    class DummySummaryProvider:
        def summarize(
            self,
            *,
            title,
            source_type,
            source_value,
            cleaning_level,
            raw_content,
            related_items,
            evidence_citations,
        ):
            assert title == "Current item"
            assert source_type == "text"
            assert source_value == "query-source"
            assert cleaning_level is None
            assert raw_content == "query body"
            assert len(related_items) == 1
            assert related_items[0].title == "Memory item"
            assert len(evidence_citations) == 1
            assert evidence_citations[0]["citation_id"] == "cite-1"
            return SummaryArtifact(
                generated_category="general",
                generated_tags=["text", "alpha"],
                one_sentence_takeaway="一句话结论",
                summary_text="summary text",
                viewpoint_text="一句话结论",
                controversy_text=None,
                reading_focus=["先看问题定义"],
                key_points=["summary text"],
                keywords=[{"keyword": "alpha", "weight": 0.8}],
                methods_or_process=[],
                pitfalls_or_limits=[],
                code_examples=[],
                content_quality_score=0.9,
                grounded_claims=[
                    {
                        "claim": "summary text",
                        "citation_ids": ["cite-1"],
                    }
                ],
                summary_segments=[
                    {
                        "text": "summary text",
                        "citation_ids": ["cite-1"],
                    }
                ],
                quality_meta={
                    "memory_context_count": len(related_items),
                    "evidence_citation_count": len(evidence_citations),
                    "one_sentence_takeaway": "一句话结论",
                    "reading_focus": ["先看问题定义"],
                    "key_points": ["summary text"],
                    "keywords": [{"keyword": "alpha", "weight": 0.8}],
                    "methods_or_process": [],
                    "pitfalls_or_limits": [],
                    "code_examples": [],
                },
            )

    monkeypatch.setattr(summary_nodes, "create_summary_provider", lambda _config: DummySummaryProvider())

    result = summary_nodes.generate_summary_artifact(
        {
            "config_snapshot": {"llm_provider": "stub-llm"},
            "current_item": {
                "title": "Current item",
                "source_type": "text",
                "source_value": "query-source",
                "cleaning_level": None,
                "raw_content": "query body",
            },
            "memory_related_items": [
                {
                    "snapshot_id": "snapshot-1",
                    "knowledge_item_id": "item-1",
                    "title": "Memory item",
                    "final_category": "general",
                    "summary_text": "Memory summary",
                    "score": 0.88,
                }
            ],
            "evidence_citations": [
                {
                    "citation_id": "cite-1",
                    "rank": 1,
                    "knowledge_item_id": "item-1",
                    "chunk_id": "chunk-1",
                    "parent_chunk_id": "parent-1",
                    "title": "Current item",
                    "section_title": "Section A",
                    "source_type": "text",
                    "source_name": "Current item",
                    "source_value": "query-source",
                    "created_at": "2026-04-22T00:00:00Z",
                    "snippet": "evidence snippet",
                    "context_snippet": "evidence context",
                    "expanded_context_snippet": "expanded evidence context",
                }
            ],
        },
        db=object(),
    )

    assert result["summary_payload"]["summary_text"] == "summary text"
    assert result["summary_payload"]["one_sentence_takeaway"] == "一句话结论"
    assert result["summary_payload"]["grounded_claims"] == [
        {"claim": "summary text", "citation_ids": ["cite-1"]}
    ]
    assert result["summary_payload"]["summary_segments"] == [
        {"text": "summary text", "citation_ids": ["cite-1"]}
    ]
    assert result["summary_payload"]["quality_meta"]["memory_context_count"] == 1
    assert result["summary_payload"]["quality_meta"]["evidence_citation_count"] == 1
