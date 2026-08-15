from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class RetrievalFilters:
    source_types: list[str] | None = None
    created_at_from: str | None = None
    created_at_to: str | None = None
    knowledge_item_ids: list[str] | None = None
    keyword: str | None = None
    category: str | None = None
    user_tags: list[str] | None = None
    ai_tags: list[str] | None = None


@dataclass(slots=True)
class RetrievalQuery:
    text: str
    filters: RetrievalFilters = field(default_factory=RetrievalFilters)
    limit: int = 5
    query_vector: list[float] | None = None


@dataclass(slots=True)
class ChildChunkHit:
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
    lexical_score: float = 0.0


@dataclass(slots=True)
class ParentContext:
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


@dataclass(slots=True)
class CitationPackItem:
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


@dataclass(slots=True)
class RetrievalResult:
    query_text: str
    filters: RetrievalFilters
    child_hits: list[ChildChunkHit]
    parent_contexts: dict[str, ParentContext]
    citations: list[CitationPackItem] = field(default_factory=list)
