from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from backend.app.schemas.evidence import EvidenceBundleResponse


class ReportPrecheckResponse(BaseModel):
    week_key: str
    available_week_keys: list[str]
    existing_versions: list[int]
    next_version: int


class ReportRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    week_key: str | None = None


class ReportRunCreateResponse(BaseModel):
    run_id: str
    week_key: str
    version: int


class ReportVersionSummary(BaseModel):
    week_key: str
    version: int
    generated_at: str


class ReportVersionListResponse(BaseModel):
    items: list[ReportVersionSummary]


class ReportSnapshotItemResponse(BaseModel):
    snapshot_id: str
    title: str
    final_category: str
    created_at: str
    evidence_citation_count: int
    memory_context_count: int
    grounded_claim_count: int
    top_evidence_titles: list[str]
    top_grounded_claims: list[str]
    evidence_bundle: EvidenceBundleResponse


class ReportGroundedItemResponse(BaseModel):
    snapshot_id: str
    title: str
    final_category: str
    claim: str
    citation_ids: list[str]
    evidence_titles: list[str]


class ReportSnapshotPayloadResponse(BaseModel):
    category_stats: dict[str, int]
    source_distribution: dict[str, int]
    reading_trend: dict[str, int]
    evidence_citation_total: int
    grounded_claim_total: int
    grounded_items: list[ReportGroundedItemResponse]
    items: list[ReportSnapshotItemResponse]


class ReportVersionDetailResponse(BaseModel):
    id: str
    week_key: str
    version: int
    markdown_content: str
    snapshot_payload: ReportSnapshotPayloadResponse
    markdown_path: str | None
    generated_at: str
