from __future__ import annotations

from fastapi import APIRouter, Request

from backend.app.schemas.fine_job.job_journey import JobJourneyResponse
from backend.app.services.fine_job.job_journey import get_job_journey


router = APIRouter(prefix="/fine-job/jobs", tags=["fine-job-job-journey"])


@router.get("/{job_id}/journey", response_model=JobJourneyResponse)
def read_job_journey(request: Request, job_id: str) -> JobJourneyResponse:
    return JobJourneyResponse(**get_job_journey(request.app.state.db, job_id))
