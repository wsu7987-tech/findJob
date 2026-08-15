from __future__ import annotations

import json
import threading
from pathlib import Path

from backend.app.config import AppConfig
from backend.app.db import Database
from backend.app.errors import AppError
from backend.app.graphs import build_summary_graph
from backend.app.services.ai import (
    RelatedContextItem,
    SummaryArtifact,
    create_embedding_provider,
    create_summary_provider,
    validate_provider_config,
)
from backend.app.services.pool import fetch_pool_entries_for_summary
from backend.app.services.summary_output import (
    build_summary_snapshot,
    resolve_summary_markdown_path,
    write_summary_markdown,
)
from backend.app.services.vector_store import SummaryVectorStore, VectorRecord
from backend.app.utils import new_id, utc_now


def ensure_runtime_config(config: AppConfig) -> None:
    missing = config.missing_runtime_fields()
    missing.extend(validate_provider_config(config))
    if missing:
        raise AppError(
            status_code=400,
            error_category="CONFIG_INVALID",
            error_message=f"Missing required config: {', '.join(missing)}",
        )


def build_summary_precheck(db: Database, config: AppConfig) -> dict[str, object]:
    ensure_runtime_config(config)
    with db.connect() as connection:
        rows = connection.execute(
            """
            SELECT
              pe.id,
              pe.knowledge_item_id,
              pe.current_status,
              ki.title,
              ki.source_type,
              ki.cleaning_level
            FROM pool_entries AS pe
            JOIN knowledge_items AS ki ON ki.id = pe.knowledge_item_id
            WHERE pe.is_deleted = 0
              AND pe.current_status IN ('pending', 'failed')
            ORDER BY pe.display_updated_at DESC, pe.id DESC
            """
        ).fetchall()

    items = [
        {
            "id": row["id"],
            "knowledge_item_id": row["knowledge_item_id"],
            "title": row["title"],
            "source_type": row["source_type"],
            "cleaning_level": row["cleaning_level"],
            "current_status": row["current_status"],
        }
        for row in rows
    ]
    return {
        "items": items,
        "count": len(items),
        "output_dir": str(config.summary_output_dir),
        "failed_retry_count": sum(1 for item in items if item["current_status"] == "failed"),
        "run_hint": f"Ready to summarize {len(items)} item(s)",
    }


def create_summary_run(
    db: Database,
    config: AppConfig,
    pool_ids: list[str],
) -> dict[str, object]:
    ensure_runtime_config(config)
    rows = _load_summary_rows(db, pool_ids)
    run_id = new_id()
    started_at = utc_now()
    _create_run_record(db, run_id=run_id, started_at=started_at, total_items=len(rows))
    initial_state = {
        "run_id": run_id,
        "started_at": started_at,
        "run_record_created": True,
        "pool_ids": list(pool_ids),
        "pending_pool_ids": [str(row["id"]) for row in rows],
        "items": [_serialize_summary_row(row) for row in rows],
        "succeeded_items": 0,
        "failed_items": 0,
        "status": "pending",
        "stage": "queued",
        "config_snapshot": serialize_config_snapshot(config),
    }
    _start_summary_run_thread(db=db, config=config, initial_state=initial_state)
    return {
        "run_id": run_id,
        "status": "pending",
        "stage": "queued",
    }


def _start_summary_run_thread(
    *,
    db: Database,
    config: AppConfig,
    initial_state: dict[str, object],
) -> None:
    thread = threading.Thread(
        target=_run_summary_graph_in_background,
        kwargs={
            "db": db,
            "config": config,
            "initial_state": initial_state,
        },
        daemon=True,
        name=f"summary-run-{initial_state['run_id']}",
    )
    thread.start()


def _run_summary_graph_in_background(
    *,
    db: Database,
    config: AppConfig,
    initial_state: dict[str, object],
) -> None:
    from backend.app.utils import utc_now

    run_id = str(initial_state["run_id"])
    try:
        graph = build_summary_graph(db, config)
        graph.invoke(initial_state)
    except Exception as exc:
        current_run = get_run(db, run_id)
        if str(current_run["status"]) in {"completed", "failed", "cancelled"}:
            return
        error_category = exc.error_category if isinstance(exc, AppError) else "RUN_FAILED"
        error_message = (
            exc.error_message if isinstance(exc, AppError) else "Summary run failed unexpectedly."
        )
        _update_run_state(
            db,
            run_id=run_id,
            status="failed",
            stage="failed",
            succeeded_items=int(current_run["succeeded_items"]),
            failed_items=max(1, int(current_run["failed_items"])),
            skipped_items=int(current_run["skipped_items"]),
            current_item_id=_optional_str(current_run["current_item_id"]),
            current_item_label=_optional_str(current_run["current_item_label"]),
            finished_at=utc_now(),
            error_category=error_category,
            error_message=error_message,
        )


def get_run(db: Database, run_id: str) -> dict[str, object]:
    with db.connect() as connection:
        row = connection.execute(
            """
            SELECT
              id,
              task_type,
              status,
              stage,
              total_items,
              succeeded_items,
              failed_items,
              skipped_items,
              current_item_id,
              current_item_label,
              error_category,
              error_message,
              started_at,
              finished_at,
              report_week_key,
              linked_report_version_id
            FROM run_records
            WHERE id = ?
            """,
            (run_id,),
        ).fetchone()

    payload = _serialize_run_row(row)
    if payload["task_type"] == "summary":
        payload["result_snapshots"] = _list_run_result_snapshots(db, run_id)
    return payload


def list_runs(
    db: Database,
    *,
    task_type: str | None = None,
    status: str | None = None,
) -> list[dict[str, object]]:
    where_clauses: list[str] = []
    params: list[str] = []

    if task_type:
        where_clauses.append("task_type = ?")
        params.append(task_type)
    if status:
        where_clauses.append("status = ?")
        params.append(status)

    where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

    with db.connect() as connection:
        rows = connection.execute(
            f"""
            SELECT
              id,
              task_type,
              status,
              stage,
              total_items,
              succeeded_items,
              failed_items,
              skipped_items,
              current_item_id,
              current_item_label,
              error_category,
              error_message,
              started_at,
              finished_at,
              report_week_key,
              linked_report_version_id
            FROM run_records
            {where_sql}
            ORDER BY started_at DESC, id DESC
            """,
            params,
        ).fetchall()

    return [_serialize_run_row(row) for row in rows]


def cancel_run(db: Database, run_id: str) -> dict[str, object]:
    current_run = get_run(db, run_id)
    if current_run["status"] in {"completed", "failed", "cancelled"}:
        return current_run

    _update_run_state(
        db,
        run_id=run_id,
        status="cancelled",
        stage="cancelled",
        succeeded_items=int(current_run["succeeded_items"]),
        failed_items=int(current_run["failed_items"]),
        skipped_items=int(current_run["skipped_items"]),
        current_item_id=_optional_str(current_run["current_item_id"]),
        current_item_label=_optional_str(current_run["current_item_label"]),
        finished_at=utc_now(),
        error_category="CANCELLED",
        error_message="Run cancelled by user.",
    )
    with db.connect() as connection:
        connection.execute(
            """
            UPDATE run_records
            SET cancel_requested = 1
            WHERE id = ?
            """,
            (run_id,),
        )
    return get_run(db, run_id)


def event_name_for_run(status: str) -> str:
    return {
        "completed": "run.completed",
        "failed": "run.failed",
        "cancelled": "run.cancelled",
    }.get(status, "run.updated")


def stream_run_event_payload(run_payload: dict[str, object]) -> str:
    return (
        f"event: {event_name_for_run(str(run_payload['status']))}\n"
        f"data: {json.dumps(run_payload, ensure_ascii=False)}\n\n"
    )


def _serialize_run_row(row) -> dict[str, object]:
    if row is None:
        raise AppError(
            status_code=404,
            error_category="VALIDATION_FAILED",
            error_message="Run not found.",
        )

    return {
        "run_id": row["id"],
        "task_type": row["task_type"],
        "status": row["status"],
        "stage": row["stage"],
        "total_items": row["total_items"],
        "succeeded_items": row["succeeded_items"],
        "failed_items": row["failed_items"],
        "skipped_items": row["skipped_items"],
        "current_item_id": row["current_item_id"],
        "current_item_label": row["current_item_label"],
        "error_category": row["error_category"],
        "error_message": row["error_message"],
        "updated_at": row["finished_at"] or row["started_at"],
        "finished_at": row["finished_at"],
        "report_week_key": row["report_week_key"],
        "linked_report_version_id": row["linked_report_version_id"],
    }


def _list_run_result_snapshots(db: Database, run_id: str) -> list[dict[str, object]]:
    with db.connect() as connection:
        rows = connection.execute(
            """
            SELECT
              s.id AS snapshot_id,
              s.knowledge_item_id,
              ki.title,
              s.final_category,
              s.created_at,
              s.markdown_path
            FROM item_result_snapshots AS s
            JOIN knowledge_items AS ki ON ki.id = s.knowledge_item_id
            WHERE s.summary_run_id = ?
            ORDER BY s.created_at DESC, s.id DESC
            """,
            (run_id,),
        ).fetchall()

    return [
        {
            "snapshot_id": str(row["snapshot_id"]),
            "knowledge_item_id": str(row["knowledge_item_id"]),
            "title": str(row["title"] or row["snapshot_id"]),
            "final_category": row["final_category"],
            "created_at": str(row["created_at"]),
            "markdown_path": row["markdown_path"],
            "markdown_filename": Path(str(row["markdown_path"])).name
            if row["markdown_path"]
            else None,
        }
        for row in rows
    ]


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def serialize_config_snapshot(config: AppConfig) -> dict[str, object]:
    return {
        "app_data_dir": str(config.app_data_dir),
        "local_config_path": str(config.local_config_path),
        "sqlite_path": str(config.sqlite_path),
        "qdrant_path": str(config.qdrant_path),
        "output_root": str(config.output_root),
        "summary_output_dir": str(config.summary_output_dir),
        "report_output_dir": str(config.report_output_dir),
        "llm_provider": config.llm_provider,
        "llm_model": config.llm_model,
        "llm_base_url": config.llm_base_url,
        "llm_api_key": config.llm_api_key,
        "embedding_provider": config.embedding_provider,
        "embedding_model": config.embedding_model,
        "embedding_base_url": config.embedding_base_url,
        "embedding_api_key": config.embedding_api_key,
        "fetch_concurrency": config.fetch_concurrency,
        "llm_concurrency": config.llm_concurrency,
        "embedding_concurrency": config.embedding_concurrency,
        "fetch_timeout_seconds": config.fetch_timeout_seconds,
        "llm_timeout_seconds": config.llm_timeout_seconds,
        "embedding_timeout_seconds": config.embedding_timeout_seconds,
        "fetch_user_agent": config.fetch_user_agent,
        "quick_capture_hotkey": config.quick_capture_hotkey,
        "quick_capture_screenshot_hotkey": config.quick_capture_screenshot_hotkey,
        "close_to_tray": config.close_to_tray,
        "quick_capture_always_on_top": config.quick_capture_always_on_top,
    }


def restore_config_snapshot(snapshot: dict[str, object]) -> AppConfig:
    return AppConfig(
        app_data_dir=Path(str(snapshot["app_data_dir"])),
        local_config_path=Path(str(snapshot["local_config_path"])),
        sqlite_path=Path(str(snapshot["sqlite_path"])),
        qdrant_path=Path(str(snapshot["qdrant_path"])),
        output_root=Path(str(snapshot["output_root"])),
        summary_output_dir=Path(str(snapshot["summary_output_dir"])),
        report_output_dir=Path(str(snapshot["report_output_dir"])),
        llm_provider=_optional_str(snapshot.get("llm_provider")),
        llm_model=_optional_str(snapshot.get("llm_model")),
        llm_base_url=_optional_str(snapshot.get("llm_base_url")),
        llm_api_key=_optional_str(snapshot.get("llm_api_key")),
        embedding_provider=_optional_str(snapshot.get("embedding_provider")),
        embedding_model=_optional_str(snapshot.get("embedding_model")),
        embedding_base_url=_optional_str(snapshot.get("embedding_base_url")),
        embedding_api_key=_optional_str(snapshot.get("embedding_api_key")),
        fetch_concurrency=int(snapshot["fetch_concurrency"]),
        llm_concurrency=int(snapshot["llm_concurrency"]),
        embedding_concurrency=int(snapshot["embedding_concurrency"]),
        fetch_timeout_seconds=int(snapshot["fetch_timeout_seconds"]),
        llm_timeout_seconds=int(snapshot["llm_timeout_seconds"]),
        embedding_timeout_seconds=int(snapshot["embedding_timeout_seconds"]),
        fetch_user_agent=str(snapshot["fetch_user_agent"]),
        quick_capture_hotkey=_optional_str(snapshot.get("quick_capture_hotkey")),
        quick_capture_screenshot_hotkey=_optional_str(
            snapshot.get("quick_capture_screenshot_hotkey")
        ),
        close_to_tray=bool(snapshot.get("close_to_tray", False)),
        quick_capture_always_on_top=bool(
            snapshot.get("quick_capture_always_on_top", False)
        ),
    )


def serialize_related_context_items(items: list[RelatedContextItem]) -> list[dict[str, object]]:
    return [
        {
            "snapshot_id": item.snapshot_id,
            "knowledge_item_id": item.knowledge_item_id,
            "title": item.title,
            "final_category": item.final_category,
            "summary_text": item.summary_text,
            "score": item.score,
        }
        for item in items
    ]


def restore_related_context_items(items: list[dict[str, object]]) -> list[RelatedContextItem]:
    return [
        RelatedContextItem(
            snapshot_id=str(item["snapshot_id"]),
            knowledge_item_id=_optional_str(item.get("knowledge_item_id")),
            title=str(item["title"]),
            final_category=_optional_str(item.get("final_category")),
            summary_text=str(item.get("summary_text") or ""),
            score=float(item.get("score") or 0.0),
        )
        for item in items
    ]


def serialize_summary_artifact(summary: SummaryArtifact) -> dict[str, object]:
    return {
        "generated_category": summary.generated_category,
        "generated_tags": list(summary.generated_tags),
        "one_sentence_takeaway": summary.one_sentence_takeaway,
        "summary_text": summary.summary_text,
        "viewpoint_text": summary.viewpoint_text,
        "controversy_text": summary.controversy_text,
        "reading_focus": list(summary.reading_focus),
        "key_points": list(summary.key_points),
        "keywords": list(summary.keywords),
        "methods_or_process": list(summary.methods_or_process),
        "pitfalls_or_limits": list(summary.pitfalls_or_limits),
        "code_examples": list(summary.code_examples),
        "content_quality_score": summary.content_quality_score,
        "grounded_claims": list(summary.grounded_claims),
        "summary_segments": list(summary.summary_segments),
        "quality_meta": dict(summary.quality_meta),
    }


def restore_summary_artifact(payload: dict[str, object]) -> SummaryArtifact:
    return SummaryArtifact(
        generated_category=str(payload["generated_category"]),
        generated_tags=[str(tag) for tag in payload.get("generated_tags", [])],
        one_sentence_takeaway=_optional_str(payload.get("one_sentence_takeaway")),
        summary_text=str(payload["summary_text"]),
        viewpoint_text=_optional_str(payload.get("viewpoint_text")),
        controversy_text=_optional_str(payload.get("controversy_text")),
        reading_focus=[str(item) for item in payload.get("reading_focus", []) if str(item).strip()],
        key_points=[str(item) for item in payload.get("key_points", []) if str(item).strip()],
        keywords=list(payload.get("keywords") or []),
        methods_or_process=[
            str(item)
            for item in payload.get("methods_or_process", [])
            if str(item).strip()
        ],
        pitfalls_or_limits=[
            str(item)
            for item in payload.get("pitfalls_or_limits", [])
            if str(item).strip()
        ],
        code_examples=list(payload.get("code_examples") or []),
        content_quality_score=float(payload["content_quality_score"]),
        grounded_claims=[
            {
                "claim": str(item.get("claim") or ""),
                "citation_ids": [
                    str(citation_id)
                    for citation_id in item.get("citation_ids", [])
                    if str(citation_id).strip()
                ],
            }
            for item in payload.get("grounded_claims", [])
            if isinstance(item, dict) and str(item.get("claim") or "").strip()
        ],
        summary_segments=[
            {
                "text": str(item.get("text") or ""),
                "citation_ids": [
                    str(citation_id)
                    for citation_id in item.get("citation_ids", [])
                    if str(citation_id).strip()
                ],
            }
            for item in payload.get("summary_segments", [])
            if isinstance(item, dict) and str(item.get("text") or "").strip()
        ],
        quality_meta=dict(payload.get("quality_meta") or {}),
    )


def _load_summary_rows(db: Database, pool_ids: list[str]):
    with db.connect() as connection:
        rows = fetch_pool_entries_for_summary(connection, pool_ids)
    if len(rows) != len(pool_ids):
        raise AppError(
            status_code=400,
            error_category="VALIDATION_FAILED",
            error_message="One or more pool_ids are invalid.",
        )
    return rows


def _serialize_summary_row(row) -> dict[str, object]:
    cleaning_level = (
        row["cleaning_level"]
        if hasattr(row, "keys") and "cleaning_level" in row.keys()
        else row.get("cleaning_level")
        if isinstance(row, dict)
        else None
    )
    return {
        "id": row["id"],
        "knowledge_item_id": row["knowledge_item_id"],
        "current_status": row["current_status"],
        "title": row["title"],
        "source_type": row["source_type"],
        "source_value": row["source_value"],
        "cleaning_level": cleaning_level,
        "raw_content": row["raw_content"],
    }


def _create_run_record(
    db: Database,
    *,
    run_id: str,
    started_at: str,
    total_items: int,
) -> None:
    with db.connect() as connection:
        connection.execute(
            """
            INSERT INTO run_records (
              id, task_type, status, stage, started_at, total_items,
              succeeded_items, failed_items, skipped_items
            )
            VALUES (?, 'summary', 'pending', 'queued', ?, ?, 0, 0, 0)
            """,
            (run_id, started_at, total_items),
        )


def _update_run_state(
    db: Database,
    *,
    run_id: str,
    status: str,
    stage: str,
    succeeded_items: int,
    failed_items: int,
    skipped_items: int,
    current_item_id: str | None,
    current_item_label: str | None,
    finished_at: str | None = None,
    error_category: str | None = None,
    error_message: str | None = None,
) -> None:
    with db.connect() as connection:
        connection.execute(
            """
            UPDATE run_records
            SET status = ?,
                stage = ?,
                succeeded_items = ?,
                failed_items = ?,
                skipped_items = ?,
                current_item_id = ?,
                current_item_label = ?,
                finished_at = ?,
                error_category = ?,
                error_message = ?
            WHERE id = ?
            """,
            (
                status,
                stage,
                succeeded_items,
                failed_items,
                skipped_items,
                current_item_id,
                current_item_label,
                finished_at,
                error_category,
                error_message,
                run_id,
            ),
        )


def _update_pool_processing_state(
    db: Database,
    pool_entry_id: str,
    *,
    current_status: str,
    last_summary_status: str | None = None,
    summarized_at: str | None = None,
    error_category: str | None = None,
    error_message: str | None = None,
) -> None:
    display_updated_at = summarized_at or utc_now()
    with db.connect() as connection:
        connection.execute(
            """
            UPDATE pool_entries
            SET current_status = ?,
                last_summarized_at = ?,
                last_summary_status = ?,
                last_failed_category = ?,
                last_failed_message = ?,
                display_updated_at = ?
            WHERE id = ?
            """,
            (
                current_status,
                summarized_at,
                last_summary_status,
                error_category,
                error_message,
                display_updated_at,
                pool_entry_id,
            ),
        )


def _persist_snapshot(
    *,
    db: Database,
    config: AppConfig,
    run_id: str,
    knowledge_item_id: str,
    title: str,
    source_type: str,
    source_value: str,
    cleaning_level: str | None,
    raw_content: str,
    summary_provider,
    embedding_provider,
    vector_store: SummaryVectorStore,
) -> dict[str, object]:
    created_at = utc_now()
    retrieval_query = raw_content[:8000]
    retrieval_vector = embedding_provider.embed_texts([retrieval_query])[0]
    prompt_related_items = vector_store.search_related(retrieval_vector, limit=5)
    summary = summary_provider.summarize(
        title=title,
        source_type=source_type,
        source_value=source_value,
        cleaning_level=cleaning_level,
        raw_content=raw_content,
        related_items=prompt_related_items,
        evidence_citations=[],
    )
    summary_vector = embedding_provider.embed_texts([summary.summary_text])[0]
    related_items = vector_store.search_related(summary_vector, limit=5)
    snapshot = build_summary_snapshot(
        snapshot_id=new_id(),
        run_id=run_id,
        knowledge_item_id=knowledge_item_id,
        title=title,
        source_type=source_type,
        source_value=source_value,
        created_at=created_at,
        summary=summary,
        related_items=related_items,
    )
    try:
        snapshot["markdown_path"] = str(resolve_summary_markdown_path(snapshot, config.summary_output_dir))
        _insert_snapshot_record(
            db=db,
            snapshot=snapshot,
            knowledge_item_id=knowledge_item_id,
            run_id=run_id,
        )
        write_summary_markdown(snapshot, config.summary_output_dir)
        vector_store.upsert_snapshot(
            vector=summary_vector,
            record=VectorRecord(
                snapshot_id=str(snapshot["id"]),
                knowledge_item_id=knowledge_item_id,
                title=title,
                final_category=str(snapshot["final_category"]),
                summary_text=str(snapshot["summary_text"]),
            ),
        )
    except Exception:
        _delete_snapshot_record(db, snapshot["id"])
        _remove_file_if_exists(Path(str(snapshot["markdown_path"])))
        raise
    return snapshot


def _is_cancel_requested(db: Database, run_id: str) -> bool:
    with db.connect() as connection:
        row = connection.execute(
            "SELECT cancel_requested FROM run_records WHERE id = ?",
            (run_id,),
        ).fetchone()
    return bool(row and row["cancel_requested"])


def _remove_file_if_exists(path: Path) -> None:
    if path.exists():
        path.unlink()


def _insert_snapshot_record(
    *,
    db: Database,
    snapshot: dict[str, object],
    knowledge_item_id: str,
    run_id: str,
) -> None:
    with db.connect() as connection:
        connection.execute(
            """
            INSERT INTO item_result_snapshots (
              id, knowledge_item_id, summary_run_id, generated_category,
              generated_tags, final_category, final_tags, summary_text,
              viewpoint_text, controversy_text, content_quality_score,
              quality_meta, relation_meta, qdrant_point_id, markdown_path,
              created_at, edited_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot["id"],
                knowledge_item_id,
                run_id,
                snapshot["generated_category"],
                json.dumps(snapshot["generated_tags"], ensure_ascii=False),
                snapshot["final_category"],
                json.dumps(snapshot["final_tags"], ensure_ascii=False),
                snapshot["summary_text"],
                snapshot["viewpoint_text"],
                snapshot["controversy_text"],
                snapshot["content_quality_score"],
                json.dumps(snapshot["quality_meta"], ensure_ascii=False),
                json.dumps(snapshot["relation_meta"], ensure_ascii=False),
                snapshot["qdrant_point_id"],
                snapshot["markdown_path"],
                snapshot["created_at"],
                snapshot["edited_at"],
            ),
        )


def _delete_snapshot_record(db: Database, snapshot_id: str) -> None:
    with db.connect() as connection:
        connection.execute(
            "DELETE FROM item_result_snapshots WHERE id = ?",
            (snapshot_id,),
        )
