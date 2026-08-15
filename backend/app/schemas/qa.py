from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.app.schemas.retrieval import RetrievalCitationResponse, RetrievalFilterRequest

QAMode = Literal["answer", "knowledge_point", "summary", "source"]


class QAAnswerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str
    session_id: str | None = None
    mode: QAMode = "answer"
    limit: int = Field(default=5, ge=1, le=10)
    filters: RetrievalFilterRequest | None = None

    @field_validator("question")
    @classmethod
    def validate_question(cls, value: str) -> str:
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("question must not be empty")
        return trimmed


class QAGroundedItemResponse(BaseModel):
    snapshot_id: str
    title: str
    final_category: str
    claim: str
    citation_ids: list[str]
    evidence_titles: list[str]


class QAVerificationResponse(BaseModel):
    status: Literal["passed", "failed", "skipped"] = "skipped"
    reason: str = "not_run"
    supported_terms: int = 0
    answer_terms: int = 0


class QARewriteResponse(BaseModel):
    rewritten_question: str
    requires_history: bool = False
    used_history: bool = False
    intent: str = "answer"
    risk_flags: list[str] = Field(default_factory=list)
    confidence: float = 0.0
    strategy: str = "heuristic"


class QAAnswerResponse(BaseModel):
    session_id: str
    mode: QAMode
    rewritten_question: str
    rewrite: QARewriteResponse
    question: str
    answer: str
    answer_status: Literal["grounded", "insufficient_evidence", "needs_clarification"]
    confidence: float
    applied_filters: RetrievalFilterRequest
    citations: list[RetrievalCitationResponse]
    used_grounded_items: list[QAGroundedItemResponse]
    suggested_queries: list[str]
    verification: QAVerificationResponse = QAVerificationResponse()
    retry_count: int = 0


class QAConversationMessageResponse(BaseModel):
    message_id: str
    role: Literal["user", "assistant"]
    content: str
    created_at: str
    question: str | None = None
    rewritten_question: str | None = None
    rewrite: QARewriteResponse | None = None
    answer_status: Literal["grounded", "insufficient_evidence", "needs_clarification"] | None = None
    confidence: float | None = None
    applied_filters: RetrievalFilterRequest | None = None
    citations: list[RetrievalCitationResponse] = Field(default_factory=list)
    used_grounded_items: list[QAGroundedItemResponse] = Field(default_factory=list)
    suggested_queries: list[str] = Field(default_factory=list)
    verification: QAVerificationResponse | None = None
    retry_count: int | None = None


class QASessionSummaryResponse(BaseModel):
    session_id: str
    title: str
    mode: QAMode
    created_at: str
    updated_at: str
    last_question: str | None = None
    message_count: int


class QASessionListResponse(BaseModel):
    items: list[QASessionSummaryResponse]


class QASessionDetailResponse(QASessionSummaryResponse):
    messages: list[QAConversationMessageResponse]


class QASessionDeleteResponse(BaseModel):
    deleted: bool
