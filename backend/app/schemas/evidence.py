from __future__ import annotations

from pydantic import BaseModel


class MemoryContextItemResponse(BaseModel):
    snapshot_id: str
    knowledge_item_id: str
    title: str
    final_category: str
    score: float


class EvidenceCitationResponse(BaseModel):
    citation_id: str
    rank: int
    knowledge_item_id: str
    chunk_id: str
    parent_chunk_id: str
    title: str
    section_title: str
    source_type: str
    source_name: str
    source_value: str
    created_at: str
    snippet: str
    context_snippet: str
    expanded_context_snippet: str


class GroundedClaimResponse(BaseModel):
    claim: str
    citation_ids: list[str]
    evidence_titles: list[str]


class SummarySegmentResponse(BaseModel):
    text: str
    citation_ids: list[str]
    evidence_titles: list[str]


class EvidenceBundleResponse(BaseModel):
    memory_context_items: list[MemoryContextItemResponse]
    citations: list[EvidenceCitationResponse]
    grounded_claims: list[GroundedClaimResponse]
    summary_segments: list[SummarySegmentResponse]
    memory_context_count: int
    evidence_citation_count: int
    grounded_claim_count: int
