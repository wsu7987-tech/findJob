from __future__ import annotations

from fastapi import APIRouter, Query, Request

from backend.app.schemas.fine_job.job_journey import JobJourneyResponse, JobProgressView
from backend.app.services.fine_job.job_journey import get_job_journey
from backend.app.services.fine_job.job_progress import get_job_progress


router = APIRouter(prefix="/fine-job/jobs", tags=["fine-job-job-journey"])


@router.get("/{job_id}/journey", response_model=JobJourneyResponse)
def read_job_journey(request: Request, job_id: str) -> JobJourneyResponse:
    return JobJourneyResponse(**get_job_journey(request.app.state.db, job_id))


@router.get("/{job_id}/progress", response_model=JobProgressView | None)
def read_job_progress(
    request: Request,
    job_id: str,
    session_id: str | None = Query(default=None),
) -> JobProgressView | None:
    progress = get_job_progress(request.app.state.db, job_id, session_id=session_id)
    return JobProgressView(**progress) if progress else None
