from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictOutputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class KeywordOutput(StrictOutputModel):
    keyword: str
    weight: float = Field(ge=0, le=1)


class CitationClaimOutput(StrictOutputModel):
    claim: str
    citation_ids: list[str]


class SummarySegmentOutput(StrictOutputModel):
    text: str
    citation_ids: list[str]


class CodeExampleOutput(StrictOutputModel):
    language: str
    snippet: str
    citation_ids: list[str]


class SummaryLightweightOutput(StrictOutputModel):
    generated_category: str
    generated_tags: list[str]
    one_sentence_takeaway: str | None
    summary_text: str
    key_points: list[str]
    content_quality_score: float = Field(ge=0, le=1)


class SummaryOutput(StrictOutputModel):
    generated_category: str
    generated_tags: list[str]
    one_sentence_takeaway: str | None
    summary_text: str
    key_points: list[str]
    content_quality_score: float = Field(ge=0, le=1)
    reading_focus: list[str]
    keywords: list[KeywordOutput]
    methods_or_process: list[str]
    pitfalls_or_limits: list[str]
    code_examples: list[CodeExampleOutput]
    grounded_claims: list[CitationClaimOutput]
    summary_segments: list[SummarySegmentOutput]


class AnswerOutput(StrictOutputModel):
    answer: str
    answer_status: Literal["grounded", "insufficient_evidence", "needs_clarification"]
    confidence: float = Field(ge=0, le=1)
    citation_ids: list[str]
    suggested_queries: list[str]


class QueryRewriteOutput(StrictOutputModel):
    rewritten_question: str
    requires_history: bool
    intent: Literal[
        "answer",
        "knowledge_point",
        "summary",
        "source",
        "follow_up",
        "unknown",
    ]
    risk_flags: list[str]
    confidence: float = Field(ge=0, le=1)
