from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from backend.app.schemas.evidence import EvidenceBundleResponse


class ResultDetailResponse(BaseModel):
    id: str
    knowledge_item_id: str
    summary_run_id: str
    title: str
    source_type: str
    source_value: str
    generated_category: str | None
    generated_tags: list[str]
    final_category: str | None
    final_tags: list[str]
    summary_text: str
    viewpoint_text: str | None
    controversy_text: str | None
    evidence_bundle: EvidenceBundleResponse
    summary_meta: dict[str, object] | None = None
    relation_meta: dict[str, object] | None = None
    markdown_path: str | None = None
    markdown_filename: str | None = None
    markdown_content: str | None = None
    created_at: str
    edited_at: str


class ResultPatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    final_category: str | None = None
    final_tags: list[str] | None = None


class ResultFeedbackRequest(BaseModel):
    feedback_value: Literal["useful", "useless"]


class ResultFeedbackResponse(BaseModel):
    saved: bool
