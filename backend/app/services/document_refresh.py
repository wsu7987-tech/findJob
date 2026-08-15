from __future__ import annotations

from sqlite3 import Connection

from backend.app.config import AppConfig
from backend.app.services.ai import create_embedding_provider
from backend.app.services.chunk_index import ChunkVectorRecord, ChunkVectorStore
from backend.app.services.retrieval_index_versions import resolve_active_chunk_index_version
from backend.app.services.chunk_store import (
    delete_document_chunks_for_item,
    insert_document_chunks,
    mark_chunk_indexed,
    rebuild_document_chunk_fts_for_item,
)
from backend.app.services.chunking import build_document_chunks


def refresh_document_chunks(
    *,
    connection: Connection,
    config: AppConfig,
    knowledge_item_id: str,
    raw_content: str,
) -> None:
    delete_document_chunks_for_item(connection, knowledge_item_id=knowledge_item_id)

    provider_name = config.embedding_provider or "stub-embedding"
    model_name = config.embedding_model or "stub-embedding-model"
    version_tag = resolve_active_chunk_index_version(
        connection,
        provider_name=provider_name,
        model_name=model_name,
    )
    vector_store = ChunkVectorStore(
        config=config,
        provider_name=provider_name,
        model_name=model_name,
        version_tag=version_tag,
    )
    vector_store.delete_for_knowledge_item(knowledge_item_id=knowledge_item_id)

    parent_chunks, child_chunks = build_document_chunks(
        knowledge_item_id=knowledge_item_id,
        raw_content=raw_content,
    )
    insert_document_chunks(connection, [*parent_chunks, *child_chunks])
    rebuild_document_chunk_fts_for_item(connection, knowledge_item_id=knowledge_item_id)

    if not child_chunks:
        return

    embedding_provider = create_embedding_provider(config)
    child_vectors = embedding_provider.embed_texts([chunk.content for chunk in child_chunks])
    knowledge_item_row = connection.execute(
        """
        SELECT source_type, created_at, capture_category, user_tags_json, ai_tags_json
        FROM knowledge_items
        WHERE id = ?
        """,
        (knowledge_item_id,),
    ).fetchone()
    source_type = str(knowledge_item_row["source_type"]) if knowledge_item_row is not None else "text"
    created_at = str(knowledge_item_row["created_at"]) if knowledge_item_row is not None else ""
    category = knowledge_item_row["capture_category"] if knowledge_item_row is not None else None
    user_tags = _parse_json_list(knowledge_item_row["user_tags_json"] if knowledge_item_row is not None else None)
    ai_tags = _parse_json_list(knowledge_item_row["ai_tags_json"] if knowledge_item_row is not None else None)

    for chunk, vector in zip(child_chunks, child_vectors, strict=True):
        vector_store.upsert_chunk(
            vector=vector,
            record=ChunkVectorRecord(
                chunk_id=chunk.id,
                knowledge_item_id=chunk.knowledge_item_id,
                parent_chunk_id=chunk.parent_chunk_id or "",
                section_title=chunk.section_title,
                position=chunk.position,
                content_preview=chunk.content[:200],
                source_type=source_type,
                created_at=created_at,
                category=category,
                user_tags=user_tags,
                ai_tags=ai_tags,
            ),
        )
        mark_chunk_indexed(
            connection,
            chunk_id=chunk.id,
            embedding_provider=provider_name,
            embedding_model=model_name,
            vector_point_id=chunk.id,
        )


def _parse_json_list(value: object) -> list[str]:
    import json

    if value in (None, ""):
        return []
    if isinstance(value, str):
        parsed = json.loads(value)
        if isinstance(parsed, list):
            return [str(item) for item in parsed]
    return []
