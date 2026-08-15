from __future__ import annotations

from fastapi import APIRouter, Depends

from backend.app.db import Database
from backend.app.dependencies import get_database
from backend.app.schemas.fine_job.job_intents import (
    FineJobIntentEnvelope,
    FineJobIntentPayload,
)
from backend.app.services.fine_job.job_intents import get_job_intent, save_job_intent


router = APIRouter(prefix="/fine-job/job-intent", tags=["fine-job-job-intent"])


@router.get("", response_model=FineJobIntentEnvelope)
def read_fine_job_intent(db: Database = Depends(get_database)) -> FineJobIntentEnvelope:
    return FineJobIntentEnvelope(intent=get_job_intent(db))


@router.put("", response_model=FineJobIntentEnvelope)
def save_fine_job_intent(
    payload: FineJobIntentPayload,
    db: Database = Depends(get_database),
) -> FineJobIntentEnvelope:
    return FineJobIntentEnvelope(intent=save_job_intent(db, payload))
