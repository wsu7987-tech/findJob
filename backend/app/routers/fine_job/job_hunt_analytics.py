from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Query

from backend.app.db import Database
from backend.app.dependencies import get_database
from backend.app.schemas.fine_job.job_hunt_analytics import (
    AnalyticsMetric,
    JobHuntAnalyticsJobsResponse,
    JobHuntAnalyticsResponse,
)
from backend.app.services.fine_job.job_hunt_analytics import (
    get_job_hunt_analytics,
    get_job_hunt_analytics_jobs,
)


router = APIRouter(
    prefix="/fine-job/job-hunt",
    tags=["fine-job-job-hunt-analytics"],
)


@router.get("/analytics", response_model=JobHuntAnalyticsResponse)
def analytics(
    from_value: str = Query(alias="from"),
    to_value: str = Query(alias="to"),
    timezone_name: Literal["Asia/Shanghai"] = Query(
        default="Asia/Shanghai", alias="timezone"
    ),
    granularity: Literal["auto", "day", "week"] = Query(default="auto"),
    contact_origin: Literal[
        "finejob_auto",
        "candidate_initiated",
        "external_candidate_initiated",
        "recruiter_initiated",
        "unknown",
    ]
    | None = Query(default=None),
    db: Database = Depends(get_database),
) -> JobHuntAnalyticsResponse:
    result = get_job_hunt_analytics(
        db,
        from_value=from_value,
        to_value=to_value,
        timezone_name=timezone_name,
        granularity=granularity,
        contact_origin=contact_origin,
    )
    return JobHuntAnalyticsResponse(**result)


@router.get("/analytics/jobs", response_model=JobHuntAnalyticsJobsResponse)
def analytics_jobs(
    metric: AnalyticsMetric = Query(),
    from_value: str = Query(alias="from"),
    to_value: str = Query(alias="to"),
    timezone_name: Literal["Asia/Shanghai"] = Query(
        default="Asia/Shanghai", alias="timezone"
    ),
    contact_origin: Literal[
        "finejob_auto",
        "candidate_initiated",
        "external_candidate_initiated",
        "recruiter_initiated",
        "unknown",
    ]
    | None = Query(default=None),
    rejection_reason_source: Literal[
        "recruiter_explicit", "ai_inferred", "unknown"
    ]
    | None = Query(default=None),
    rejection_reason_category: str | None = Query(default=None, max_length=40),
    waiting_on: Literal["candidate", "recruiter", "none", "unknown"] | None = Query(
        default=None
    ),
    attention: str | None = Query(default=None, max_length=40),
    db: Database = Depends(get_database),
) -> JobHuntAnalyticsJobsResponse:
    result = get_job_hunt_analytics_jobs(
        db,
        metric=metric,
        from_value=from_value,
        to_value=to_value,
        timezone_name=timezone_name,
        contact_origin=contact_origin,
        rejection_reason_source=rejection_reason_source,
        rejection_reason_category=rejection_reason_category,
        waiting_on=waiting_on,
        attention=attention,
    )
    return JobHuntAnalyticsJobsResponse(**result)
