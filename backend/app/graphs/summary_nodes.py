from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from backend.app.errors import AppError
from backend.app.graphs.summary_state import SummaryGraphState
from backend.app.services.retrieval import build_retrieval_context
from backend.app.services.retrieval_types import RetrievalFilters, RetrievalQuery
from backend.app.services.ai import create_embedding_provider, create_summary_provider
from backend.app.services.summary_output import (
    build_summary_snapshot,
    resolve_summary_markdown_path,
    write_summary_markdown,
)
from backend.app.services.vector_store import SummaryVectorStore


def _runtime_value(config, field_name: str, default=None):
    if isinstance(config, dict):
        return config.get(field_name, default)
    return getattr(config, field_name, default)


def _handle_item_failure(state: SummaryGraphState, *, db, exc: Exception) -> None:
    from backend.app.services.runs import _update_pool_processing_state, _update_run_state
    from backend.app.utils import utc_now

    item = dict(state["current_item"] or {})
    current_item_id = str(item["knowledge_item_id"])
    current_item_label = str(item["title"])
    if isinstance(exc, AppError):
        error_category = exc.error_category
        error_message = exc.error_message
        status_code = exc.status_code
    else:
        error_category = "OUTPUT_FAILED"
        error_message = "Failed to persist summary output."
        status_code = 500

    _update_pool_processing_state(
        db,
        str(item["id"]),
        current_status="failed",
        last_summary_status="failed",
        error_category=error_category,
        error_message=error_message,
    )
    _update_run_state(
        db,
        run_id=str(state["run_id"]),
        status="failed",
        stage="failed",
        succeeded_items=int(state.get("succeeded_items", 0)),
        failed_items=int(state.get("failed_items", 0)) + 1,
        skipped_items=0,
        current_item_id=current_item_id,
        current_item_label=current_item_label,
        finished_at=utc_now(),
        error_category=error_category,
        error_message=error_message,
    )
    if isinstance(exc, AppError):
        raise exc
    raise AppError(
        status_code=status_code,
        error_category=error_category,
        error_message=error_message,
    ) from exc


def bootstrap_run(state: SummaryGraphState, *, db) -> SummaryGraphState:
    from backend.app.services.runs import _create_run_record, new_id
    from backend.app.utils import utc_now

    run_id = state.get("run_id") or new_id()
    started_at = state.get("started_at") or utc_now()
    items = list(state.get("items", []))
    pending_pool_ids = list(state.get("pending_pool_ids", []))
    if not state.get("run_record_created"):
        _create_run_record(db, run_id=run_id, started_at=started_at, total_items=len(items))
    return {
        "run_id": run_id,
        "started_at": started_at,
        "run_record_created": True,
        "pending_pool_ids": pending_pool_ids,
        "succeeded_items": int(state.get("succeeded_items", 0)),
        "failed_items": int(state.get("failed_items", 0)),
        "skipped_items": 0,
        "status": "running",
        "stage": "summarizing",
    }


def pick_next_item(state: SummaryGraphState, *, db) -> SummaryGraphState:
    from backend.app.services.runs import _is_cancel_requested

    run_id = str(state["run_id"])
    pending_pool_ids = list(state.get("pending_pool_ids", []))
    if _is_cancel_requested(db, run_id):
        return {
            "status": "cancelled",
            "stage": "cancelled",
            "current_pool_id": None,
            "current_item": None,
            "skipped_items": len(pending_pool_ids),
        }

    if not pending_pool_ids:
        return {
            "status": "completed",
            "stage": "completed",
            "current_pool_id": None,
            "current_item": None,
            "skipped_items": 0,
        }

    current_pool_id = pending_pool_ids[0]
    current_item = next(
        item for item in state.get("items", []) if str(item["id"]) == current_pool_id
    )
    return {
        "status": "running",
        "stage": "summarizing",
        "current_pool_id": current_pool_id,
        "current_item": current_item,
        "current_item_id": str(current_item["knowledge_item_id"]),
        "current_item_label": str(current_item["title"]),
    }


def load_current_item(state: SummaryGraphState, *, db) -> SummaryGraphState:
    from backend.app.services.runs import (
        _update_run_state,
        _update_pool_processing_state,
    )

    item = dict(state["current_item"] or {})
    current_item_id = str(item["knowledge_item_id"])
    current_item_label = str(item["title"])
    _update_run_state(
        db,
        run_id=str(state["run_id"]),
        status="running",
        stage="summarizing",
        succeeded_items=int(state.get("succeeded_items", 0)),
        failed_items=int(state.get("failed_items", 0)),
        skipped_items=0,
        current_item_id=current_item_id,
        current_item_label=current_item_label,
    )
    _update_pool_processing_state(db, str(item["id"]), current_status="running")
    return {
        "current_item_id": current_item_id,
        "current_item_label": current_item_label,
        "retrieval_query": str(item["raw_content"] or item["source_value"])[:8000],
    }


def retrieve_prompt_context(state: SummaryGraphState, *, db) -> SummaryGraphState:
    from backend.app.services.runs import (
        restore_config_snapshot,
        serialize_related_context_items,
    )

    runtime_config = restore_config_snapshot(state["config_snapshot"])
    embedding_provider = create_embedding_provider(runtime_config)
    vector_store = SummaryVectorStore(
        config=runtime_config,
        provider_name=_runtime_value(runtime_config, "embedding_provider", "stub-embedding")
        or "stub-embedding",
        model_name=_runtime_value(runtime_config, "embedding_model", "stub-embedding-model")
        or "stub-embedding-model",
    )
    try:
        retrieval_vector = embedding_provider.embed_texts([str(state["retrieval_query"])])[0]
        current_item_id = state.get("current_item_id")
        prompt_related_items = [
            item
            for item in vector_store.search_related(retrieval_vector, limit=5)
            if str(item.knowledge_item_id or "") != str(current_item_id or "")
        ]
        retrieval_result = build_retrieval_context(
            db=db,
            config=runtime_config,
            query=RetrievalQuery(
                text=str(state["retrieval_query"]),
                query_vector=list(retrieval_vector),
                filters=RetrievalFilters(
                    knowledge_item_ids=[str(current_item_id)] if current_item_id else None
                ),
                limit=3,
            ),
        )
        return {
            "memory_related_items": serialize_related_context_items(prompt_related_items),
            "evidence_citations": [asdict(citation) for citation in retrieval_result.citations],
        }
    except Exception as exc:
        _handle_item_failure(state, db=db, exc=exc)


def generate_summary_artifact(state: SummaryGraphState, *, db) -> SummaryGraphState:
    from backend.app.services.runs import (
        restore_config_snapshot,
        restore_related_context_items,
        serialize_summary_artifact,
    )

    item = dict(state["current_item"] or {})
    runtime_config = restore_config_snapshot(state["config_snapshot"])
    summary_provider = create_summary_provider(runtime_config)
    try:
        summary = summary_provider.summarize(
            title=str(item["title"]),
            source_type=str(item["source_type"]),
            source_value=str(item["source_value"]),
            cleaning_level=(
                str(item["cleaning_level"])
                if item.get("cleaning_level") is not None
                else None
            ),
            raw_content=str(item["raw_content"] or item["source_value"]),
            related_items=restore_related_context_items(
                list(state.get("memory_related_items", state.get("prompt_related_items", [])))
            ),
            evidence_citations=list(state.get("evidence_citations", [])),
        )
        return {"summary_payload": serialize_summary_artifact(summary)}
    except Exception as exc:
        _handle_item_failure(state, db=db, exc=exc)


def retrieve_relations(state: SummaryGraphState, *, db) -> SummaryGraphState:
    from backend.app.services.runs import (
        restore_config_snapshot,
        restore_summary_artifact,
        serialize_related_context_items,
    )

    runtime_config = restore_config_snapshot(state["config_snapshot"])
    embedding_provider = create_embedding_provider(runtime_config)
    vector_store = SummaryVectorStore(
        config=runtime_config,
        provider_name=_runtime_value(runtime_config, "embedding_provider", "stub-embedding")
        or "stub-embedding",
        model_name=_runtime_value(runtime_config, "embedding_model", "stub-embedding-model")
        or "stub-embedding-model",
    )
    try:
        summary = restore_summary_artifact(dict(state["summary_payload"] or {}))
        summary_vector = embedding_provider.embed_texts([summary.summary_text])[0]
        related_items = vector_store.search_related(summary_vector, limit=5)
        return {
            "summary_vector": summary_vector,
            "related_items": serialize_related_context_items(related_items),
        }
    except Exception as exc:
        _handle_item_failure(state, db=db, exc=exc)


def persist_current_item(state: SummaryGraphState, *, db) -> SummaryGraphState:
    from backend.app.services.runs import (
        _delete_snapshot_record,
        _insert_snapshot_record,
        _remove_file_if_exists,
        _update_pool_processing_state,
        new_id,
        restore_config_snapshot,
        restore_related_context_items,
        restore_summary_artifact,
    )
    from backend.app.services.vector_store import VectorRecord
    from backend.app.utils import utc_now

    item = dict(state["current_item"] or {})
    runtime_config = restore_config_snapshot(state["config_snapshot"])
    summary = restore_summary_artifact(dict(state["summary_payload"] or {}))
    related_items = restore_related_context_items(list(state.get("related_items", [])))
    created_at = utc_now()
    snapshot = build_summary_snapshot(
        snapshot_id=new_id(),
        run_id=str(state["run_id"]),
        knowledge_item_id=str(item["knowledge_item_id"]),
        title=str(item["title"]),
        source_type=str(item["source_type"]),
        source_value=str(item["source_value"]),
        created_at=created_at,
        summary=summary,
        related_items=related_items,
        evidence_citations=list(state.get("evidence_citations", [])),
    )
    vector_store = SummaryVectorStore(
        config=runtime_config,
        provider_name=_runtime_value(runtime_config, "embedding_provider", "stub-embedding")
        or "stub-embedding",
        model_name=_runtime_value(runtime_config, "embedding_model", "stub-embedding-model")
        or "stub-embedding-model",
    )
    try:
        snapshot["markdown_path"] = str(
            resolve_summary_markdown_path(snapshot, runtime_config.summary_output_dir)
        )
        _insert_snapshot_record(
            db=db,
            snapshot=snapshot,
            knowledge_item_id=str(item["knowledge_item_id"]),
            run_id=str(state["run_id"]),
        )
        write_summary_markdown(snapshot, runtime_config.summary_output_dir)
        vector_store.upsert_snapshot(
            vector=list(state["summary_vector"] or []),
            record=VectorRecord(
                snapshot_id=str(snapshot["id"]),
                knowledge_item_id=str(item["knowledge_item_id"]),
                title=str(item["title"]),
                final_category=str(snapshot["final_category"]),
                summary_text=str(snapshot["summary_text"]),
            ),
        )
    except Exception as exc:
        _delete_snapshot_record(db, str(snapshot["id"]))
        _remove_file_if_exists(Path(str(snapshot["markdown_path"])))
        _handle_item_failure(state, db=db, exc=exc)

    _update_pool_processing_state(
        db,
        str(item["id"]),
        current_status="succeeded",
        last_summary_status="completed",
        summarized_at=str(snapshot["created_at"]),
    )
    pending_pool_ids = list(state.get("pending_pool_ids", []))
    return {
        "pending_pool_ids": pending_pool_ids[1:],
        "current_item_id": str(item["knowledge_item_id"]),
        "current_item_label": str(item["title"]),
        "succeeded_items": int(state.get("succeeded_items", 0)) + 1,
        "failed_items": int(state.get("failed_items", 0)),
        "status": "running",
        "stage": "summarizing",
        "retrieval_query": None,
        "memory_related_items": [],
        "prompt_related_items": [],
        "evidence_citations": [],
        "summary_payload": None,
        "summary_vector": None,
        "related_items": [],
    }


def finalize_completed_run(state: SummaryGraphState, *, db) -> SummaryGraphState:
    from backend.app.services.runs import _update_run_state
    from backend.app.utils import utc_now

    _update_run_state(
        db,
        run_id=str(state["run_id"]),
        status="completed",
        stage="completed",
        succeeded_items=int(state.get("succeeded_items", 0)),
        failed_items=int(state.get("failed_items", 0)),
        skipped_items=0,
        current_item_id=state.get("current_item_id"),
        current_item_label=state.get("current_item_label"),
        finished_at=utc_now(),
        error_category=None,
        error_message=None,
    )
    return {"status": "completed", "stage": "completed"}


def finalize_cancelled_run(state: SummaryGraphState, *, db) -> SummaryGraphState:
    from backend.app.services.runs import _update_run_state
    from backend.app.utils import utc_now

    _update_run_state(
        db,
        run_id=str(state["run_id"]),
        status="cancelled",
        stage="cancelled",
        succeeded_items=int(state.get("succeeded_items", 0)),
        failed_items=int(state.get("failed_items", 0)),
        skipped_items=int(state.get("skipped_items", 0)),
        current_item_id=state.get("current_item_id"),
        current_item_label=state.get("current_item_label"),
        finished_at=utc_now(),
        error_category="CANCELLED",
        error_message="Run cancelled by user.",
    )
    return {"status": "cancelled", "stage": "cancelled"}
