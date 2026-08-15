from __future__ import annotations

from fastapi import APIRouter, Depends

from backend.app.db import Database
from backend.app.dependencies import get_database
from backend.app.schemas.results import (
    ResultDetailResponse,
    ResultFeedbackRequest,
    ResultFeedbackResponse,
    ResultPatchRequest,
)
from backend.app.services.results import get_result, save_feedback, update_result


router = APIRouter(prefix="/results", tags=["results"])


@router.get("/{snapshot_id}", response_model=ResultDetailResponse)
def read_result(
    snapshot_id: str,
    db: Database = Depends(get_database),
) -> ResultDetailResponse:
    return ResultDetailResponse(**get_result(db, snapshot_id))


@router.patch("/{snapshot_id}", response_model=ResultDetailResponse)
def patch_result(
    snapshot_id: str,
    payload: ResultPatchRequest,
    db: Database = Depends(get_database),
) -> ResultDetailResponse:
    return ResultDetailResponse(**update_result(db, snapshot_id, payload))


@router.post("/{snapshot_id}/feedback", response_model=ResultFeedbackResponse)
def create_feedback(
    snapshot_id: str,
    payload: ResultFeedbackRequest,
    db: Database = Depends(get_database),
) -> ResultFeedbackResponse:
    return ResultFeedbackResponse(**save_feedback(db, snapshot_id, payload.feedback_value))
