from __future__ import annotations

from dataclasses import dataclass

from backend.app.errors import AppError
from backend.app.services.ai import create_embedding_provider
from backend.app.services.chunk_index import ChunkVectorRecord, ChunkVectorStore
from backend.app.services.vector_store import _collection_name
from backend.app.utils import new_id, utc_now


@dataclass(slots=True)
class RetrievalIndexVersionRecord:
    id: str
    index_scope: str
    version_tag: str
    collection_name: str
    embedding_provider: str
    embedding_model: str
    status: str
    created_at: str
    activated_at: str | None
    last_rebuilt_at: str | None
    last_rebuild_chunk_count: int
    is_legacy: bool = False


@dataclass(slots=True)
class RetrievalIndexRebuildReport:
    version_id: str
    version_tag: str
    knowledge_item_count: int
    chunk_count: int


def list_chunk_index_versions(connection, *, provider_name: str, model_name: str) -> list[RetrievalIndexVersionRecord]:
    rows = connection.execute(
        """
        SELECT
          id,
          index_scope,
          version_tag,
          collection_name,
          embedding_provider,
          embedding_model,
          status,
          created_at,
          activated_at,
          last_rebuilt_at,
          last_rebuild_chunk_count
        FROM retrieval_index_versions
        WHERE index_scope = 'chunk' AND embedding_provider = ? AND embedding_model = ?
        ORDER BY
          CASE status WHEN 'active' THEN 0 WHEN 'candidate' THEN 1 ELSE 2 END,
          created_at DESC,
          id DESC
        """,
        (provider_name, model_name),
    ).fetchall()
    items = [
        RetrievalIndexVersionRecord(
            id=str(row["id"]),
            index_scope=str(row["index_scope"]),
            version_tag=str(row["version_tag"]),
            collection_name=str(row["collection_name"]),
            embedding_provider=str(row["embedding_provider"]),
            embedding_model=str(row["embedding_model"]),
            status=str(row["status"]),
            created_at=str(row["created_at"]),
            activated_at=str(row["activated_at"]) if row["activated_at"] else None,
            last_rebuilt_at=str(row["last_rebuilt_at"]) if row["last_rebuilt_at"] else None,
            last_rebuild_chunk_count=int(row["last_rebuild_chunk_count"] or 0),
        )
        for row in rows
    ]

    active_explicit = next((item for item in items if item.status == "active"), None)
    legacy = RetrievalIndexVersionRecord(
        id="legacy",
        index_scope="chunk",
        version_tag="legacy",
        collection_name=_collection_name("chunk", provider_name, model_name),
        embedding_provider=provider_name,
        embedding_model=model_name,
        status="active" if active_explicit is None else "retired",
        created_at="",
        activated_at=None,
        last_rebuilt_at=None,
        last_rebuild_chunk_count=0,
        is_legacy=True,
    )
    return [legacy, *items] if active_explicit is None else [*items, legacy]


def create_chunk_index_version(
    connection,
    *,
    provider_name: str,
    model_name: str,
    version_tag: str,
) -> RetrievalIndexVersionRecord:
    normalized_tag = version_tag.strip()
    if not normalized_tag or normalized_tag.lower() == "legacy":
        raise AppError(
            status_code=400,
            error_category="VALIDATION_FAILED",
            error_message="version_tag must not be empty or reserved.",
        )
    now = utc_now()
    record = RetrievalIndexVersionRecord(
        id=new_id(),
        index_scope="chunk",
        version_tag=normalized_tag,
        collection_name=_collection_name("chunk", provider_name, model_name, version_tag=normalized_tag),
        embedding_provider=provider_name,
        embedding_model=model_name,
        status="candidate",
        created_at=now,
        activated_at=None,
        last_rebuilt_at=None,
        last_rebuild_chunk_count=0,
    )
    try:
        connection.execute(
            """
            INSERT INTO retrieval_index_versions (
              id, index_scope, version_tag, collection_name,
              embedding_provider, embedding_model, status, created_at, activated_at, last_rebuilt_at, last_rebuild_chunk_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.id,
                record.index_scope,
                record.version_tag,
                record.collection_name,
                record.embedding_provider,
                record.embedding_model,
                record.status,
                record.created_at,
                record.activated_at,
                record.last_rebuilt_at,
                record.last_rebuild_chunk_count,
            ),
        )
    except Exception as exc:
        raise AppError(
            status_code=409,
            error_category="VALIDATION_FAILED",
            error_message=f"Chunk index version already exists for version_tag: {normalized_tag}",
        ) from exc
    return record


def activate_chunk_index_version(
    connection,
    *,
    version_id: str,
    provider_name: str,
    model_name: str,
) -> RetrievalIndexVersionRecord:
    row = connection.execute(
        """
        SELECT
          id,
          index_scope,
          version_tag,
          collection_name,
          embedding_provider,
          embedding_model,
          status,
          created_at,
          activated_at,
          last_rebuilt_at,
          last_rebuild_chunk_count
        FROM retrieval_index_versions
        WHERE id = ? AND index_scope = 'chunk' AND embedding_provider = ? AND embedding_model = ?
        """,
        (version_id, provider_name, model_name),
    ).fetchone()
    if row is None:
        raise AppError(
            status_code=404,
            error_category="NOT_FOUND",
            error_message=f"Chunk index version not found: {version_id}",
        )
    if not row["last_rebuilt_at"]:
        raise AppError(
            status_code=409,
            error_category="VALIDATION_FAILED",
            error_message="Chunk index version must run rebuild before activation.",
        )

    connection.execute(
        """
        UPDATE retrieval_index_versions
        SET status = 'retired'
        WHERE index_scope = 'chunk' AND embedding_provider = ? AND embedding_model = ? AND status = 'active'
        """,
        (provider_name, model_name),
    )
    now = utc_now()
    connection.execute(
        """
        UPDATE retrieval_index_versions
        SET status = 'active', activated_at = ?
        WHERE id = ?
        """,
        (now, version_id),
    )
    return RetrievalIndexVersionRecord(
        id=str(row["id"]),
        index_scope=str(row["index_scope"]),
        version_tag=str(row["version_tag"]),
        collection_name=str(row["collection_name"]),
        embedding_provider=str(row["embedding_provider"]),
        embedding_model=str(row["embedding_model"]),
        status="active",
        created_at=str(row["created_at"]),
        activated_at=now,
        last_rebuilt_at=str(row["last_rebuilt_at"]) if row["last_rebuilt_at"] else None,
        last_rebuild_chunk_count=int(row["last_rebuild_chunk_count"] or 0),
    )


def resolve_active_chunk_index_version(
    connection,
    *,
    provider_name: str,
    model_name: str,
) -> str | None:
    row = connection.execute(
        """
        SELECT version_tag
        FROM retrieval_index_versions
        WHERE index_scope = 'chunk' AND embedding_provider = ? AND embedding_model = ? AND status = 'active'
        ORDER BY activated_at DESC, created_at DESC, id DESC
        LIMIT 1
        """,
        (provider_name, model_name),
    ).fetchone()
    if row is None:
        return None
    return str(row["version_tag"])


def rebuild_chunk_index_version(
    connection,
    *,
    config,
    version_id: str,
) -> RetrievalIndexRebuildReport:
    row = connection.execute(
        """
        SELECT
          id,
          version_tag,
          collection_name,
          embedding_provider,
          embedding_model,
          status
        FROM retrieval_index_versions
        WHERE id = ? AND index_scope = 'chunk'
        """,
        (version_id,),
    ).fetchone()
    if row is None:
        raise AppError(
            status_code=404,
            error_category="NOT_FOUND",
            error_message=f"Chunk index version not found: {version_id}",
        )

    provider_name = str(row["embedding_provider"])
    model_name = str(row["embedding_model"])
    version_tag = str(row["version_tag"])
    current_provider = config.embedding_provider or "stub-embedding"
    current_model = config.embedding_model or "stub-embedding-model"
    if provider_name != current_provider or model_name != current_model:
        raise AppError(
            status_code=409,
            error_category="CONFIG_INVALID",
            error_message="Current embedding config does not match the target index version.",
        )

    child_rows = connection.execute(
        """
        SELECT
          dc.id AS chunk_id,
          dc.knowledge_item_id,
          dc.parent_chunk_id,
          dc.section_title,
          dc.position,
          dc.content,
          ki.source_type,
          ki.created_at,
          ki.capture_category,
          ki.user_tags_json,
          ki.ai_tags_json
        FROM document_chunks AS dc
        JOIN knowledge_items AS ki ON ki.id = dc.knowledge_item_id
        WHERE dc.chunk_level = 'child'
        ORDER BY dc.knowledge_item_id ASC, dc.parent_chunk_id ASC, dc.position ASC, dc.id ASC
        """
    ).fetchall()

    vector_store = ChunkVectorStore(
        config=config,
        provider_name=provider_name,
        model_name=model_name,
        version_tag=version_tag,
    )
    vector_store.reset()

    if not child_rows:
        connection.execute(
            """
            UPDATE retrieval_index_versions
            SET last_rebuilt_at = ?, last_rebuild_chunk_count = 0
            WHERE id = ?
            """,
            (utc_now(), version_id),
        )
        return RetrievalIndexRebuildReport(
            version_id=str(row["id"]),
            version_tag=version_tag,
            knowledge_item_count=0,
            chunk_count=0,
        )

    embedding_provider = create_embedding_provider(config)
    vectors = embedding_provider.embed_texts([str(child_row["content"]) for child_row in child_rows])
    for child_row, vector in zip(child_rows, vectors, strict=True):
        vector_store.upsert_chunk(
            vector=vector,
            record=ChunkVectorRecord(
                chunk_id=str(child_row["chunk_id"]),
                knowledge_item_id=str(child_row["knowledge_item_id"]),
                parent_chunk_id=str(child_row["parent_chunk_id"] or ""),
                section_title=child_row["section_title"],
                position=int(child_row["position"]),
                content_preview=str(child_row["content"])[:200],
                source_type=str(child_row["source_type"]),
                created_at=str(child_row["created_at"]),
                category=child_row["capture_category"],
                user_tags=_parse_json_list(child_row["user_tags_json"]),
                ai_tags=_parse_json_list(child_row["ai_tags_json"]),
            ),
        )

    connection.execute(
        """
        UPDATE retrieval_index_versions
        SET last_rebuilt_at = ?, last_rebuild_chunk_count = ?
        WHERE id = ?
        """,
        (utc_now(), len(child_rows), version_id),
    )

    return RetrievalIndexRebuildReport(
        version_id=str(row["id"]),
        version_tag=version_tag,
        knowledge_item_count=len({str(child_row["knowledge_item_id"]) for child_row in child_rows}),
        chunk_count=len(child_rows),
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
