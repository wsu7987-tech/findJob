from __future__ import annotations

from dataclasses import dataclass

from backend.app.config import AppConfig
from backend.app.errors import AppError
from backend.app.services.vector_store import BaseJsonQdrantStore


@dataclass(slots=True)
class ChunkVectorRecord:
    chunk_id: str
    knowledge_item_id: str
    parent_chunk_id: str
    section_title: str | None
    position: int
    content_preview: str
    source_type: str
    created_at: str
    category: str | None
    user_tags: list[str]
    ai_tags: list[str]


class ChunkVectorStore(BaseJsonQdrantStore):
    def __init__(
        self,
        *,
        config: AppConfig,
        provider_name: str,
        model_name: str,
        version_tag: str | None = None,
    ) -> None:
        super().__init__(
            config=config,
            collection_prefix="chunk",
            provider_name=provider_name,
            model_name=model_name,
            version_tag=version_tag,
        )

    def search_related(
        self,
        vector: list[float],
        *,
        limit: int = 5,
        filters: dict[str, object] | None = None,
    ) -> list[dict[str, object]]:
        return self.search_payloads(vector, limit=limit, filters=filters)

    def upsert_chunk(self, *, vector: list[float], record: ChunkVectorRecord) -> None:
        if not vector:
            raise AppError(
                status_code=500,
                error_category="EMBEDDING_FAILED",
                error_message="Cannot upsert an empty chunk embedding vector.",
            )
        self.upsert_payload(
            point_id=record.chunk_id,
            vector=vector,
            payload={
                "chunk_id": record.chunk_id,
                "knowledge_item_id": record.knowledge_item_id,
                "parent_chunk_id": record.parent_chunk_id,
                "section_title": record.section_title,
                "position": record.position,
                "content_preview": record.content_preview,
                "source_type": record.source_type,
                "created_at": record.created_at,
                "category": record.category,
                "user_tags": record.user_tags,
                "ai_tags": record.ai_tags,
            },
        )

    def delete_for_knowledge_item(self, *, knowledge_item_id: str) -> None:
        self.delete_payloads_by_field(
            field_name="knowledge_item_id",
            field_value=knowledge_item_id,
        )
