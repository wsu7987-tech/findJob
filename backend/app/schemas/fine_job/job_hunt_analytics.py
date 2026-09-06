from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


ContactOrigin = Literal[
    "finejob_auto",
    "candidate_initiated",
    "external_candidate_initiated",
    "recruiter_initiated",
    "unknown",
]
Granularity = Literal["day", "week"]
AnalyticsMetric = Literal[
    "candidate_contacts",
    "candidate_contact_replies",
    "recruiter_contacts",
    "resume_submitted",
    "resume_viewed",
    "under_review",
    "interview_scheduled",
    "rejected",
    "job_closed",
    "offer_received",
]


class JobHuntAnalyticsRange(BaseModel):
    from_date: str = Field(serialization_alias="from")
    to_date: str = Field(serialization_alias="to")
    timezone: str
    granularity: Granularity
    contact_origin: ContactOrigin | None = None


class JobHuntAnalyticsOverview(BaseModel):
    candidate_contacts: int
    recruiter_contacts: int
    candidate_contact_replies: int
    candidate_reply_rate: float | None = None
    resume_submitted: int
    resume_viewed: int
    under_review: int
    interview_scheduled: int
    rejected: int
    job_closed: int
    offer_received: int


class JobHuntTrendPoint(BaseModel):
    period_start: str
    candidate_contacts: int
    resume_submitted: int
    interview_scheduled: int
    rejected: int


class JobHuntFunnelStage(BaseModel):
    key: Literal[
        "candidate_contacts",
        "candidate_contact_replies",
        "resume_submitted",
        "resume_viewed",
        "interview_scheduled",
        "offer_received",
    ]
    count: int
    previous_rate: float | None = None
    total_rate: float | None = None


class JobHuntFunnel(BaseModel):
    available: bool
    unavailable_reason: str | None = None
    stages: list[JobHuntFunnelStage]


class JobHuntCurrentState(BaseModel):
    waiting_recruiter: int
    waiting_candidate: int
    followup_recommended: int
    under_review: int
    interview_scheduling: int


class JobHuntRejectionReasonBucket(BaseModel):
    category: str
    job_count: int


class JobHuntRejectionAnalysis(BaseModel):
    recruiter_explicit: list[JobHuntRejectionReasonBucket]
    ai_inferred: list[JobHuntRejectionReasonBucket]
    unknown: list[JobHuntRejectionReasonBucket]


class JobHuntSourcePerformanceItem(BaseModel):
    contact_origin: ContactOrigin
    job_count: int
    candidate_reply_rate: float | None = None
    resume_rate: float | None = None
    interview_rate: float | None = None
    offer_rate: float | None = None
    rejection_rate: float | None = None


class JobHuntAnalyticsDefinitions(BaseModel):
    count_basis: Literal["distinct_job"] = "distinct_job"
    funnel_basis: Literal["candidate_contact_cohort"] = "candidate_contact_cohort"
    historical_time_basis: Literal["event.occurred_at"] = "event.occurred_at"
    contact_basis: Literal["canonical_contact_anchor"] = "canonical_contact_anchor"
    event_order_basis: Literal[
        "occurred_at_created_at_id"
    ] = "occurred_at_created_at_id"
    current_state_is_snapshot: Literal[True] = True
    current_state_ignores_date_range: Literal[True] = True
    rate_scale: Literal["fraction"] = "fraction"


class JobHuntAnalyticsResponse(BaseModel):
    range: JobHuntAnalyticsRange
    overview: JobHuntAnalyticsOverview
    trend: list[JobHuntTrendPoint]
    funnel: JobHuntFunnel
    current_state: JobHuntCurrentState
    rejection_analysis: JobHuntRejectionAnalysis
    source_performance: list[JobHuntSourcePerformanceItem]
    definitions: JobHuntAnalyticsDefinitions
    generated_at: str


class JobHuntAnalyticsJobItem(BaseModel):
    job_id: str
    title: str
    company_name: str
    progress: str
    matched_at: str
    metric: AnalyticsMetric
    rejection_reason_source: Literal[
        "recruiter_explicit", "ai_inferred", "unknown"
    ] | None = None
    rejection_reason_category: str | None = None
    rejection_reason_summary: str | None = None


class JobHuntAnalyticsJobsResponse(BaseModel):
    metric: AnalyticsMetric
    total: int
    jobs: list[JobHuntAnalyticsJobItem]
