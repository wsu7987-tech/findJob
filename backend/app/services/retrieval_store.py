from __future__ import annotations

from backend.app.services.retrieval_types import RetrievalFilters


def fetch_child_chunk_rows(connection, chunk_ids: list[str]):
    if not chunk_ids:
        return []
    placeholders = ",".join("?" for _ in chunk_ids)
    return connection.execute(
        f"""
        SELECT
          dc.id AS chunk_id,
          dc.knowledge_item_id,
          dc.parent_chunk_id,
          dc.section_title,
          dc.content,
          dc.position,
          ki.source_type,
          ki.title,
          ki.source_name,
          ki.source_value,
          ki.created_at,
          ki.capture_category,
          ki.user_tags_json,
          ki.ai_tags_json
        FROM document_chunks AS dc
        JOIN knowledge_items AS ki ON ki.id = dc.knowledge_item_id
        WHERE dc.id IN ({placeholders}) AND dc.chunk_level = 'child'
        """,
        chunk_ids,
    ).fetchall()


def fetch_adjacent_child_chunk_rows(
    connection,
    *,
    parent_chunk_id: str,
    center_position: int,
    window_size: int,
):
    return connection.execute(
        """
        SELECT
          id AS chunk_id,
          content,
          position
        FROM document_chunks
        WHERE parent_chunk_id = ?
          AND chunk_level = 'child'
          AND position BETWEEN ? AND ?
        ORDER BY position ASC, id ASC
        """,
        (
            parent_chunk_id,
            center_position - window_size,
            center_position + window_size,
        ),
    ).fetchall()


def fetch_parent_context_rows(connection, parent_chunk_ids: list[str]):
    if not parent_chunk_ids:
        return []
    placeholders = ",".join("?" for _ in parent_chunk_ids)
    return connection.execute(
        f"""
        SELECT
          dc.id AS parent_chunk_id,
          dc.knowledge_item_id,
          dc.section_title,
          dc.content,
          ki.title,
          ki.source_type,
          ki.source_name,
          ki.source_value,
          ki.created_at,
          ki.capture_category,
          ki.user_tags_json,
          ki.ai_tags_json
        FROM document_chunks AS dc
        JOIN knowledge_items AS ki ON ki.id = dc.knowledge_item_id
        WHERE dc.id IN ({placeholders}) AND dc.chunk_level = 'parent'
        """,
        parent_chunk_ids,
    ).fetchall()


def search_child_chunk_rows_fts(
    connection,
    *,
    fts_query: str,
    filters: RetrievalFilters,
    limit: int,
):
    if not fts_query or limit <= 0:
        return []

    where_clauses = ["document_chunks_fts MATCH ?", "dc.chunk_level = 'child'"]
    parameters: list[object] = [fts_query]

    if filters.source_types:
        placeholders = ",".join("?" for _ in filters.source_types)
        where_clauses.append(f"ki.source_type IN ({placeholders})")
        parameters.extend(filters.source_types)
    if filters.knowledge_item_ids:
        placeholders = ",".join("?" for _ in filters.knowledge_item_ids)
        where_clauses.append(f"ki.id IN ({placeholders})")
        parameters.extend(filters.knowledge_item_ids)
    if filters.category:
        where_clauses.append("ki.capture_category = ?")
        parameters.append(filters.category)
    if filters.created_at_from:
        where_clauses.append("julianday(ki.created_at) >= julianday(?)")
        parameters.append(filters.created_at_from)
    if filters.created_at_to:
        where_clauses.append("julianday(ki.created_at) <= julianday(?)")
        parameters.append(filters.created_at_to)
    if filters.user_tags:
        placeholders = ",".join("?" for _ in filters.user_tags)
        where_clauses.append(
            "EXISTS (SELECT 1 FROM json_each(COALESCE(ki.user_tags_json, '[]')) WHERE json_each.value IN "
            f"({placeholders}))"
        )
        parameters.extend(filters.user_tags)
    if filters.ai_tags:
        placeholders = ",".join("?" for _ in filters.ai_tags)
        where_clauses.append(
            "EXISTS (SELECT 1 FROM json_each(COALESCE(ki.ai_tags_json, '[]')) WHERE json_each.value IN "
            f"({placeholders}))"
        )
        parameters.extend(filters.ai_tags)

    parameters.append(limit)
    return connection.execute(
        f"""
        SELECT
          document_chunks_fts.chunk_id,
          bm25(document_chunks_fts, 5.0, 3.0, 1.0, 4.0) AS lexical_score
        FROM document_chunks_fts
        JOIN document_chunks AS dc ON dc.id = document_chunks_fts.chunk_id
        JOIN knowledge_items AS ki ON ki.id = dc.knowledge_item_id
        WHERE {' AND '.join(where_clauses)}
        ORDER BY lexical_score ASC, document_chunks_fts.chunk_id ASC
        LIMIT ?
        """,
        parameters,
    ).fetchall()
