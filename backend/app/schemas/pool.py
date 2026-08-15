from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class PoolItemCreateRequest(BaseModel):
    source_type: Literal["url", "pdf", "markdown", "text"]
    source_value: str
    title: str | None = None
    raw_text: str | None = None
    capture_source: Literal["manual", "screenshot_ocr"] | None = None
    captured_at: str | None = None
    category: str | None = None
    tags: list[str] = Field(default_factory=list)


class PoolMetadataSuggestionRequest(BaseModel):
    source_type: Literal["url", "pdf", "markdown", "text"]
    source_value: str
    title: str | None = None
    raw_text: str | None = None


class PoolMetadataSuggestionResponse(BaseModel):
    category: str
    tags: list[str] = Field(default_factory=list)
    strategy: str


class PoolCommitMetadataRequest(BaseModel):
    category: str | None = None
    tags: list[str] = Field(default_factory=list)
    cleaned_text: str | None = None
    cleaning_level: Literal["basic", "enhanced"] | None = None


class PoolItemResponse(BaseModel):
    id: str
    knowledge_item_id: str
    result_snapshot_id: str | None = None
    title: str
    source_type: str
    source_value: str
    cleaning_level: Literal["basic", "enhanced"] | None = None
    current_status: str
    is_deleted: bool
    was_resummarized: bool
    display_updated_at: str


class PoolListResponse(BaseModel):
    items: list[PoolItemResponse]
    total: int


class PoolCreateResponse(BaseModel):
    item: PoolItemResponse


class DeletePoolItemResponse(BaseModel):
    deleted: bool


class ResummarizePoolItemResponse(BaseModel):
    accepted: bool


class SummaryPrecheckItem(BaseModel):
    id: str
    knowledge_item_id: str
    title: str
    source_type: str
    cleaning_level: Literal["basic", "enhanced"] | None = None
    current_status: str


class SummaryPrecheckResponse(BaseModel):
    items: list[SummaryPrecheckItem]
    count: int
    failed_retry_count: int = 0
    output_dir: str
    run_hint: str


class SummaryRunRequest(BaseModel):
    pool_ids: list[str] = Field(min_length=1)
