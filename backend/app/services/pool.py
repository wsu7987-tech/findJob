from __future__ import annotations

from dataclasses import dataclass
import json
import sqlite3
from pathlib import Path

from backend.app.config import AppConfig
from backend.app.db import Database
from backend.app.errors import AppError
from backend.app.schemas.pool import PoolItemCreateRequest
from backend.app.services.ai import suggest_metadata
from backend.app.services.document_refresh import refresh_document_chunks
from backend.app.services.ingest import _markdown_to_text, ingest_source
from backend.app.services.pdf_parse.quality import evaluate_parse_quality
from backend.app.services.pdf_parse.service import build_default_pdf_parse_service
from backend.app.services.pdf_parse.store import (
    activate_parse_result,
    insert_parse_result,
)
from backend.app.services.web_capture.service import build_default_web_capture_service
from backend.app.utils import new_id, utc_now


@dataclass(slots=True)
class StoredParseResultSeed:
    parser_name: str
    raw_text: str
    markdown_text: str | None
    preview_text: str
    char_count: int
    quality_score: float
    created_at: str
    page_count: int = 0
    is_ocr: bool = False
    warnings: list[str] | None = None
    fallback_from: str | None = None
    fallback_reason: str | None = None


def list_pool_items(db: Database) -> list[dict[str, object]]:
    with db.connect() as connection:
        rows = connection.execute(
            """
            SELECT
              pe.id,
              pe.knowledge_item_id,
              latest_snapshot.id AS result_snapshot_id,
              ki.title,
              ki.source_type,
              ki.source_value,
              ki.cleaning_level,
              pe.current_status,
              pe.is_deleted,
              pe.was_resummarized,
              pe.display_updated_at
            FROM pool_entries AS pe
            JOIN knowledge_items AS ki ON ki.id = pe.knowledge_item_id
            LEFT JOIN item_result_snapshots AS latest_snapshot
              ON latest_snapshot.id = (
                SELECT s.id
                FROM item_result_snapshots AS s
                WHERE s.knowledge_item_id = pe.knowledge_item_id
                ORDER BY s.created_at DESC, s.id DESC
                LIMIT 1
              )
            WHERE pe.is_deleted = 0
            ORDER BY pe.display_updated_at DESC, pe.id DESC
            """
        ).fetchall()
    return [_serialize_pool_item(row) for row in rows]


def create_pool_item(
    db: Database,
    config: AppConfig,
    payload: PoolItemCreateRequest,
) -> dict[str, object]:
    if payload.source_type == "url" and not (payload.raw_text and payload.raw_text.strip()):
        return _create_url_pool_item_from_capture(
            db=db,
            config=config,
            payload=payload,
        )

    if payload.source_type == "markdown":
        return _create_markdown_pool_item(
            db=db,
            config=config,
            payload=payload,
        )

    ingested = ingest_source(
        config,
        source_type=payload.source_type,
        source_value=payload.source_value,
        raw_text=payload.raw_text,
        title=payload.title,
    )

    return _create_pool_item_from_ingested(
        db,
        config,
        source_type=payload.source_type,
        ingested=ingested,
        pdf_parse_seed=None,
        capture_source=payload.capture_source,
        captured_at=payload.captured_at,
        category=payload.category,
        tags=payload.tags,
        cleaning_level=None,
    )


def _create_url_pool_item_from_capture(
    *,
    db: Database,
    config: AppConfig,
    payload: PoolItemCreateRequest,
) -> dict[str, object]:
    capture_service = build_default_web_capture_service()
    captured = capture_service.capture_url(
        url=payload.source_value,
        parser_name="playwright_dom",
        session_profile_id=None,
    )
    parse_result = StoredParseResultSeed(
        parser_name="playwright_dom",
        raw_text=str(captured.get("raw_text") or ""),
        markdown_text=(
            None if captured.get("markdown_text") is None else str(captured.get("markdown_text"))
        ),
        preview_text=str(captured.get("preview_text") or ""),
        page_count=len(list(captured.get("preview_pages") or [])),
        char_count=len(str(captured.get("raw_text") or "")),
        quality_score=_score_canonical_content(
            str(captured.get("markdown_text") or captured.get("raw_text") or "")
        ),
        warnings=[str(item) for item in (captured.get("warnings") or [])],
        created_at=utc_now(),
    )
    return create_pool_item_from_saved_web_content(
        db,
        config,
        url=payload.source_value,
        title=payload.title or str(captured.get("title") or ""),
        parse_result=parse_result,
        category=payload.category,
        tags=payload.tags,
    )


def _create_markdown_pool_item(
    *,
    db: Database,
    config: AppConfig,
    payload: PoolItemCreateRequest,
) -> dict[str, object]:
    markdown_source = _load_markdown_source(
        source_value=payload.source_value,
        raw_text=payload.raw_text,
    )
    plain_text = _markdown_to_text(markdown_source)
    ingested = ingest_source(
        config,
        source_type="markdown",
        source_value=payload.source_value,
        raw_text=markdown_source,
        title=payload.title,
    )
    parse_result = StoredParseResultSeed(
        parser_name="inline_markdown",
        raw_text=plain_text,
        markdown_text=markdown_source,
        preview_text=markdown_source[:4000],
        page_count=max(1, len([block for block in markdown_source.split("\n\n") if block.strip()])),
        char_count=len(plain_text),
        quality_score=_score_canonical_content(markdown_source),
        warnings=[],
        created_at=utc_now(),
    )
    return _create_pool_item_from_ingested(
        db,
        config,
        source_type="markdown",
        ingested=ingested,
        pdf_parse_seed={
            "parse_result": parse_result,
            "activated_raw_content": ingested.raw_content,
        },
        capture_source=payload.capture_source,
        captured_at=payload.captured_at,
        category=payload.category,
        tags=payload.tags,
        cleaning_level=None,
    )


def create_pool_item_from_saved_pdf_content(
    db: Database,
    config: AppConfig,
    *,
    file_path: str,
    title: str | None,
    parse_result,
    category: str | None = None,
    tags: list[str] | None = None,
    cleaned_text: str | None = None,
    cleaning_level: str | None = None,
) -> dict[str, object]:
    canonical_content = _resolve_canonical_content(
        cleaned_text=cleaned_text,
        structured_text=parse_result.markdown_text,
        fallback_text=parse_result.raw_text,
    )
    ingested = ingest_source(
        config,
        source_type="pdf",
        source_value=file_path,
        raw_text=canonical_content,
        title=title,
    )
    return _create_pool_item_from_ingested(
        db,
        config,
        source_type="pdf",
        ingested=ingested,
        pdf_parse_seed={
            "parse_result": parse_result,
            "activated_raw_content": ingested.raw_content,
        },
        capture_source=None,
        captured_at=None,
        category=category,
        tags=tags or [],
        cleaning_level=cleaning_level,
    )


def create_pool_item_from_saved_web_content(
    db: Database,
    config: AppConfig,
    *,
    url: str,
    title: str | None,
    parse_result,
    category: str | None = None,
    tags: list[str] | None = None,
    cleaned_text: str | None = None,
    cleaning_level: str | None = None,
) -> dict[str, object]:
    canonical_content = _resolve_canonical_content(
        cleaned_text=cleaned_text,
        structured_text=parse_result.markdown_text,
        fallback_text=parse_result.raw_text,
    )
    ingested = ingest_source(
        config,
        source_type="url",
        source_value=url,
        raw_text=canonical_content,
        title=title,
    )
    return _create_pool_item_from_ingested(
        db,
        config,
        source_type="url",
        ingested=ingested,
        pdf_parse_seed={
            "parse_result": parse_result,
            "activated_raw_content": ingested.raw_content,
        },
        capture_source=None,
        captured_at=None,
        category=category,
        tags=tags or [],
        cleaning_level=cleaning_level,
    )


def _create_pool_item_from_ingested(
    db: Database,
    config: AppConfig,
    *,
    source_type: str,
    ingested,
    pdf_parse_seed=None,
    capture_source: str | None,
    captured_at: str | None,
    category: str | None,
    tags: list[str],
    cleaning_level: str | None,
) -> dict[str, object]:
    now = utc_now()
    knowledge_item_id = new_id()
    pool_entry_id = new_id()
    ai_tags = _derive_ai_tags(
        title=ingested.title,
        raw_content=ingested.raw_content,
        source_type=source_type,
        source_value=ingested.normalized_source_value,
    )

    try:
        with db.connect() as connection:
            existing = _find_existing_pool_entry(
                connection,
                source_type=source_type,
                source_value=ingested.normalized_source_value,
            )
            if existing is not None:
                return _restore_existing_pool_item(
                    connection,
                    config,
                    existing=existing,
                    now=now,
                    source_type=source_type,
                    ingested=ingested,
                    pdf_parse_seed=pdf_parse_seed,
                    capture_source=capture_source,
                    captured_at=captured_at,
                    category=category,
                    tags=tags,
                    ai_tags=ai_tags,
                    cleaning_level=cleaning_level,
                )
            connection.execute(
                """
                INSERT INTO knowledge_items (
                  id, source_type, source_value, title, raw_content, source_name,
                  capture_source, captured_at, capture_category, capture_tags_json,
                  user_tags_json, ai_tags_json,
                  cleaning_level,
                  created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    knowledge_item_id,
                    source_type,
                    ingested.normalized_source_value,
                    ingested.title,
                    ingested.raw_content,
                    ingested.source_name,
                    capture_source,
                    captured_at,
                    category,
                    json.dumps(tags, ensure_ascii=False),
                    json.dumps(tags, ensure_ascii=False),
                    json.dumps(ai_tags, ensure_ascii=False),
                    cleaning_level,
                    now,
                    now,
                ),
            )
            indexed_content = ingested.raw_content
            if pdf_parse_seed is not None:
                parse_result = (
                    pdf_parse_seed["parse_result"]
                    if isinstance(pdf_parse_seed, dict)
                    else pdf_parse_seed
                )
                activated_raw_content = (
                    pdf_parse_seed.get("activated_raw_content")
                    if isinstance(pdf_parse_seed, dict)
                    else None
                )
                indexed_content = _activate_saved_pdf_seed(
                    connection,
                    knowledge_item_id=knowledge_item_id,
                    parse_result=parse_result,
                    activated_raw_content=activated_raw_content,
                )
            elif source_type == "pdf" and not ingested.raw_content.strip():
                parsed = parse_and_activate_initial_pdf(
                    connection=connection,
                    config=config,
                    knowledge_item_id=knowledge_item_id,
                    file_path=Path(ingested.normalized_source_value),
                )
                indexed_content = str(parsed["raw_text"])
            connection.execute(
                """
                INSERT INTO pool_entries (
                  id, knowledge_item_id, current_status, is_deleted, added_at,
                  was_resummarized, display_updated_at
                )
                VALUES (?, ?, 'pending', 0, ?, 0, ?)
                """,
                (pool_entry_id, knowledge_item_id, now, now),
            )
            _index_document_chunks(
                connection,
                config,
                knowledge_item_id=knowledge_item_id,
                raw_content=indexed_content,
            )
    except sqlite3.IntegrityError as exc:
        raise _translate_integrity_error(exc) from exc
    except AppError:
        raise
    except Exception as exc:
        raise AppError(
            status_code=500,
            error_category="RETRIEVAL_FAILED",
            error_message=f"Failed to index document chunks: {exc}",
        ) from exc

    return {
        "id": pool_entry_id,
        "knowledge_item_id": knowledge_item_id,
        "title": ingested.title,
        "source_type": source_type,
        "source_value": ingested.normalized_source_value,
        "cleaning_level": cleaning_level,
        "current_status": "pending",
        "is_deleted": False,
        "was_resummarized": False,
        "display_updated_at": now,
    }


def _find_existing_pool_entry(connection, *, source_type: str, source_value: str):
    return connection.execute(
        """
        SELECT
          ki.id AS knowledge_item_id,
          pe.id AS pool_entry_id
        FROM knowledge_items AS ki
        LEFT JOIN pool_entries AS pe ON pe.knowledge_item_id = ki.id
        WHERE ki.source_type = ? AND ki.source_value = ?
        """,
        (source_type, source_value),
    ).fetchone()


def _restore_existing_pool_item(
    connection,
    config: AppConfig,
    *,
    existing,
    now: str,
    source_type: str,
    ingested,
    pdf_parse_seed,
    capture_source: str | None,
    captured_at: str | None,
    category: str | None,
    tags: list[str],
    ai_tags: list[str],
    cleaning_level: str | None,
) -> dict[str, object]:
    knowledge_item_id = str(existing["knowledge_item_id"])
    pool_entry_id = str(existing["pool_entry_id"] or new_id())
    connection.execute(
        """
        UPDATE knowledge_items
        SET
          title = ?,
          raw_content = ?,
          source_name = ?,
          capture_source = ?,
          captured_at = ?,
          capture_category = ?,
          capture_tags_json = ?,
          user_tags_json = ?,
          ai_tags_json = ?,
          cleaning_level = ?,
          updated_at = ?
        WHERE id = ?
        """,
        (
            ingested.title,
            ingested.raw_content,
            ingested.source_name,
            capture_source,
            captured_at,
            category,
            json.dumps(tags, ensure_ascii=False),
            json.dumps(tags, ensure_ascii=False),
            json.dumps(ai_tags, ensure_ascii=False),
            cleaning_level,
            now,
            knowledge_item_id,
        ),
    )

    indexed_content = ingested.raw_content
    if pdf_parse_seed is not None:
        parse_result = (
            pdf_parse_seed["parse_result"] if isinstance(pdf_parse_seed, dict) else pdf_parse_seed
        )
        activated_raw_content = (
            pdf_parse_seed.get("activated_raw_content") if isinstance(pdf_parse_seed, dict) else None
        )
        indexed_content = _activate_saved_pdf_seed(
            connection,
            knowledge_item_id=knowledge_item_id,
            parse_result=parse_result,
            activated_raw_content=activated_raw_content,
        )
    elif source_type == "pdf" and not ingested.raw_content.strip():
        parsed = parse_and_activate_initial_pdf(
            connection=connection,
            config=config,
            knowledge_item_id=knowledge_item_id,
            file_path=Path(ingested.normalized_source_value),
        )
        indexed_content = str(parsed["raw_text"])

    updated = connection.execute(
        """
        UPDATE pool_entries
        SET
          current_status = 'pending',
          is_deleted = 0,
          was_resummarized = 1,
          last_failed_category = NULL,
          last_failed_message = NULL,
          display_updated_at = ?
        WHERE knowledge_item_id = ?
        """,
        (now, knowledge_item_id),
    )
    if updated.rowcount == 0:
        connection.execute(
            """
            INSERT INTO pool_entries (
              id, knowledge_item_id, current_status, is_deleted, added_at,
              was_resummarized, display_updated_at
            )
            VALUES (?, ?, 'pending', 0, ?, 1, ?)
            """,
            (pool_entry_id, knowledge_item_id, now, now),
        )

    _index_document_chunks(
        connection,
        config,
        knowledge_item_id=knowledge_item_id,
        raw_content=indexed_content,
    )
    return {
        "id": pool_entry_id,
        "knowledge_item_id": knowledge_item_id,
        "title": ingested.title,
        "source_type": source_type,
        "source_value": ingested.normalized_source_value,
        "cleaning_level": cleaning_level,
        "current_status": "pending",
        "is_deleted": False,
        "was_resummarized": True,
        "display_updated_at": now,
    }


def _activate_saved_pdf_seed(
    connection,
    *,
    knowledge_item_id: str,
    parse_result,
    activated_raw_content: str | None = None,
) -> str:
    saved_at = utc_now()
    page_count = _parse_result_page_count(parse_result)
    parse_result_id = insert_parse_result(
        connection,
        knowledge_item_id=knowledge_item_id,
        parser_name=parse_result.parser_name,
        status="saved",
        raw_text=parse_result.raw_text,
        markdown_text=parse_result.markdown_text,
        preview_text=parse_result.preview_text,
        page_count=page_count,
        char_count=parse_result.char_count,
        quality_score=parse_result.quality_score,
        is_ocr=bool(getattr(parse_result, "is_ocr", False)),
        warnings=list(getattr(parse_result, "warnings", [])),
        fallback_from=getattr(parse_result, "fallback_from", None),
        fallback_reason=getattr(parse_result, "fallback_reason", None),
        created_at=parse_result.created_at,
        saved_at=saved_at,
    )
    activate_parse_result(
        connection,
        knowledge_item_id=knowledge_item_id,
        parse_result_id=parse_result_id,
        raw_content=activated_raw_content or parse_result.raw_text,
        saved_at=saved_at,
    )
    return activated_raw_content or parse_result.raw_text


def _parse_result_page_count(parse_result) -> int:
    if hasattr(parse_result, "page_count"):
        return int(parse_result.page_count)
    if hasattr(parse_result, "section_count"):
        return int(parse_result.section_count)
    return 0


def _load_markdown_source(*, source_value: str, raw_text: str | None) -> str:
    if raw_text and raw_text.strip():
        return raw_text
    stripped = source_value.strip()
    if stripped:
        path = Path(stripped).expanduser().resolve(strict=False)
        if path.exists() and path.is_file():
            return path.read_text(encoding="utf-8")
    return source_value


def _score_canonical_content(content: str) -> float:
    stripped = content.strip()
    if not stripped:
        return 0.0
    if len(stripped) >= 2000:
        return 0.95
    if len(stripped) >= 500:
        return 0.85
    return 0.7


def _derive_ai_tags(
    *,
    title: str,
    raw_content: str,
    source_type: str,
    source_value: str,
) -> list[str]:
    suggestion = suggest_metadata(
        title=title,
        raw_content=raw_content,
        source_type=source_type,
        source_value=source_value,
    )
    return suggestion.tags


def delete_pool_item(db: Database, item_id: str) -> None:
    now = utc_now()
    with db.connect() as connection:
        cursor = connection.execute(
            """
            UPDATE pool_entries
            SET is_deleted = 1, display_updated_at = ?
            WHERE id = ? AND is_deleted = 0
            """,
            (now, item_id),
        )
        if cursor.rowcount == 0:
            raise AppError(
                status_code=404,
                error_category="VALIDATION_FAILED",
                error_message="Pool item not found.",
            )


def resummarize_pool_item(db: Database, item_id: str) -> None:
    now = utc_now()
    with db.connect() as connection:
        cursor = connection.execute(
            """
            UPDATE pool_entries
            SET current_status = 'pending', was_resummarized = 1, display_updated_at = ?
            WHERE id = ? AND is_deleted = 0
            """,
            (now, item_id),
        )
        if cursor.rowcount == 0:
            raise AppError(
                status_code=404,
                error_category="VALIDATION_FAILED",
                error_message="Pool item not found.",
            )


def reingest_pool_item(db: Database, config: AppConfig, item_id: str) -> None:
    now = utc_now()
    with db.connect() as connection:
        row = connection.execute(
            """
            SELECT
              pe.knowledge_item_id,
              ki.raw_content
            FROM pool_entries AS pe
            JOIN knowledge_items AS ki ON ki.id = pe.knowledge_item_id
            WHERE pe.id = ?
            """,
            (item_id,),
        ).fetchone()
        if row is None:
            raise AppError(
                status_code=404,
                error_category="VALIDATION_FAILED",
                error_message="Pool item not found.",
            )
        _index_document_chunks(
            connection,
            config,
            knowledge_item_id=str(row["knowledge_item_id"]),
            raw_content=str(row["raw_content"] or ""),
        )
        connection.execute(
            """
            UPDATE pool_entries
            SET
              current_status = 'pending',
              is_deleted = 0,
              was_resummarized = 1,
              last_failed_category = NULL,
              last_failed_message = NULL,
              display_updated_at = ?
            WHERE id = ?
            """,
            (now, item_id),
        )


def fetch_pool_entries_for_summary(connection, pool_ids: list[str]):
    placeholders = ",".join("?" for _ in pool_ids)
    return connection.execute(
        f"""
        SELECT
          pe.id,
          pe.knowledge_item_id,
          pe.current_status,
          ki.title,
          ki.source_type,
          ki.source_value,
          ki.cleaning_level,
          ki.raw_content
        FROM pool_entries AS pe
        JOIN knowledge_items AS ki ON ki.id = pe.knowledge_item_id
        WHERE pe.id IN ({placeholders}) AND pe.is_deleted = 0
        ORDER BY pe.display_updated_at DESC, pe.id DESC
        """,
        pool_ids,
    ).fetchall()


def _serialize_pool_item(row) -> dict[str, object]:
    return {
        "id": row["id"],
        "knowledge_item_id": row["knowledge_item_id"],
        "result_snapshot_id": row["result_snapshot_id"] if "result_snapshot_id" in row.keys() else None,
        "title": row["title"],
        "source_type": row["source_type"],
        "source_value": row["source_value"],
        "cleaning_level": row["cleaning_level"],
        "current_status": row["current_status"],
        "is_deleted": bool(row["is_deleted"]),
        "was_resummarized": bool(row["was_resummarized"]),
        "display_updated_at": row["display_updated_at"],
    }


def _index_document_chunks(
    connection,
    config: AppConfig,
    *,
    knowledge_item_id: str,
    raw_content: str,
) -> None:
    refresh_document_chunks(
        connection=connection,
        config=config,
        knowledge_item_id=knowledge_item_id,
        raw_content=raw_content,
    )


def _resolve_canonical_content(
    *,
    cleaned_text: str | None,
    structured_text: str | None,
    fallback_text: str | None,
) -> str:
    for candidate in (cleaned_text, structured_text, fallback_text):
        if candidate is not None and candidate.strip():
            return candidate
    return ""


def parse_and_activate_initial_pdf(
    *,
    connection,
    config: AppConfig,
    knowledge_item_id: str,
    file_path: Path,
) -> dict[str, object]:
    service = build_default_pdf_parse_service(config)
    result = service.parse_file(
        file_path=file_path,
        parser_name="auto",
        knowledge_item_id=knowledge_item_id,
    )
    quality = evaluate_parse_quality(
        parser_name=result.parser_name,
        raw_text=result.raw_text,
        markdown_text=result.markdown_text,
        page_count=result.page_count,
    )
    created_at = utc_now()
    parse_result_id = insert_parse_result(
        connection,
        knowledge_item_id=knowledge_item_id,
        parser_name=result.parser_name,
        status="preview",
        raw_text=result.raw_text,
        markdown_text=result.markdown_text,
        preview_text=result.preview_text,
        page_count=result.page_count,
        char_count=result.char_count,
        quality_score=quality.score,
        is_ocr=result.is_ocr,
        warnings=[*result.warnings, *quality.warnings],
        fallback_from=result.fallback_from,
        fallback_reason=result.fallback_reason or quality.fallback_reason,
        created_at=created_at,
        saved_at=None,
    )
    activate_parse_result(
        connection,
        knowledge_item_id=knowledge_item_id,
        parse_result_id=parse_result_id,
        raw_content=_resolve_canonical_content(
            cleaned_text=None,
            structured_text=result.markdown_text,
            fallback_text=result.raw_text,
        ),
        saved_at=created_at,
    )
    return {
        "id": parse_result_id,
        "raw_text": _resolve_canonical_content(
            cleaned_text=None,
            structured_text=result.markdown_text,
            fallback_text=result.raw_text,
        ),
        "preview_text": result.preview_text,
        "parser_name": result.parser_name,
    }


def _translate_integrity_error(exc: sqlite3.IntegrityError) -> AppError:
    message = str(exc).lower()
    if (
        "unique constraint failed" in message
        and "knowledge_items.source_type, knowledge_items.source_value" in message
    ):
        return AppError(
            status_code=409,
            error_category="VALIDATION_FAILED",
            error_message="Pool item already exists for the same source.",
        )
    if "check constraint failed" in message and "source_type" in message:
        return AppError(
            status_code=400,
            error_category="VALIDATION_FAILED",
            error_message="Invalid source_type.",
        )
    return AppError(
        status_code=400,
        error_category="VALIDATION_FAILED",
        error_message="Pool item violates database constraints.",
    )
