from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class RetrievalFilterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_types: list[Literal["url", "pdf", "markdown", "text"]] | None = None
    created_at_from: str | None = None
    created_at_to: str | None = None
    knowledge_item_ids: list[str] | None = None
    keyword: str | None = None
    category: str | None = None
    user_tags: list[str] | None = None
    ai_tags: list[str] | None = None


class RetrievalSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str
    limit: int = Field(default=5, ge=1, le=20)
    filters: RetrievalFilterRequest | None = None

    @field_validator("query")
    @classmethod
    def validate_query(cls, value: str) -> str:
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("query must not be empty")
        return trimmed


class RetrievalIndexVersionCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version_tag: str

    @field_validator("version_tag")
    @classmethod
    def validate_version_tag(cls, value: str) -> str:
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("version_tag must not be empty")
        return trimmed


class RetrievalChildHitResponse(BaseModel):
    chunk_id: str
    knowledge_item_id: str
    parent_chunk_id: str
    section_title: str | None
    content: str
    source_type: str
    title: str | None
    source_name: str
    source_value: str
    created_at: str
    category: str | None
    user_tags: list[str]
    ai_tags: list[str]
    vector_score: float
    metadata_keyword_score: float
    content_keyword_score: float
    final_score: float


class RetrievalParentContextResponse(BaseModel):
    parent_chunk_id: str
    knowledge_item_id: str
    section_title: str | None
    content: str
    title: str | None
    source_type: str
    source_name: str
    source_value: str
    created_at: str
    category: str | None
    user_tags: list[str]
    ai_tags: list[str]


class RetrievalCitationResponse(BaseModel):
    citation_id: str
    rank: int
    knowledge_item_id: str
    chunk_id: str
    parent_chunk_id: str
    title: str | None
    section_title: str | None
    source_type: str
    source_name: str
    source_value: str
    created_at: str
    snippet: str
    context_snippet: str
    expanded_context_snippet: str


class RetrievalSearchResponse(BaseModel):
    query: str
    applied_filters: RetrievalFilterRequest
    child_hits: list[RetrievalChildHitResponse]
    parent_contexts: dict[str, RetrievalParentContextResponse]
    citations: list[RetrievalCitationResponse]


class RetrievalIndexVersionResponse(BaseModel):
    id: str
    index_scope: str
    version_tag: str
    collection_name: str
    embedding_provider: str
    embedding_model: str
    status: str
    created_at: str
    activated_at: str | None
    last_rebuilt_at: str | None = None
    last_rebuild_chunk_count: int = 0
    is_legacy: bool


class RetrievalIndexVersionListResponse(BaseModel):
    items: list[RetrievalIndexVersionResponse]


class RetrievalIndexRebuildResponse(BaseModel):
    version_id: str
    version_tag: str
    knowledge_item_count: int
    chunk_count: int
