from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from backend.app.db import Database
from backend.app.dependencies import get_database
from backend.app.schemas.fine_job.workflow import (
    ActionStatus,
    FineJobActionClaimRequest,
    FineJobActionCompleteRequest,
    FineJobAutomationActionEnvelope,
    FineJobAutomationActionListEnvelope,
    FineJobOptionalAutomationActionEnvelope,
    FineJobReviewApproveRequest,
    FineJobReviewItemListEnvelope,
    FineJobReviewItemResponse,
    FineJobReviewRejectRequest,
    ReviewStatus,
)
from backend.app.services.fine_job.workflow import (
    approve_review_item,
    claim_next_action,
    complete_action,
    list_automation_actions,
    list_review_items,
    reject_review_item,
)


router = APIRouter(prefix="/fine-job", tags=["fine-job-workflow"])


@router.get("/review-items", response_model=FineJobReviewItemListEnvelope)
def get_fine_job_review_items(
    status: ReviewStatus | None = None,
    limit: int = Query(default=200, ge=1, le=500),
    db: Database = Depends(get_database),
) -> FineJobReviewItemListEnvelope:
    return FineJobReviewItemListEnvelope(**list_review_items(db, status=status, limit=limit))


@router.post(
    "/review-items/{review_item_id}/approve",
    response_model=FineJobAutomationActionEnvelope,
)
def approve_fine_job_review_item(
    review_item_id: str,
    payload: FineJobReviewApproveRequest,
    db: Database = Depends(get_database),
) -> FineJobAutomationActionEnvelope:
    _review, action = approve_review_item(
        db,
        review_item_id,
        message=payload.message,
        allow_override=payload.allow_override,
    )
    return FineJobAutomationActionEnvelope(action=action)


@router.post(
    "/review-items/{review_item_id}/reject",
    response_model=FineJobReviewItemResponse,
)
def reject_fine_job_review_item(
    review_item_id: str,
    payload: FineJobReviewRejectRequest,
    db: Database = Depends(get_database),
) -> FineJobReviewItemResponse:
    return FineJobReviewItemResponse(
        **reject_review_item(db, review_item_id, note=payload.note)
    )


@router.get("/automation-actions", response_model=FineJobAutomationActionListEnvelope)
def get_fine_job_automation_actions(
    status: ActionStatus | None = None,
    limit: int = Query(default=200, ge=1, le=500),
    db: Database = Depends(get_database),
) -> FineJobAutomationActionListEnvelope:
    return FineJobAutomationActionListEnvelope(
        **list_automation_actions(db, status=status, limit=limit)
    )


@router.post(
    "/automation-actions/claim",
    response_model=FineJobOptionalAutomationActionEnvelope,
)
def claim_fine_job_automation_action(
    payload: FineJobActionClaimRequest,
    db: Database = Depends(get_database),
) -> FineJobOptionalAutomationActionEnvelope:
    return FineJobOptionalAutomationActionEnvelope(
        action=claim_next_action(
            db,
            worker_id=payload.worker_id,
            lease_seconds=payload.lease_seconds,
        )
    )


@router.post(
    "/automation-actions/{action_id}/complete",
    response_model=FineJobAutomationActionEnvelope,
)
def complete_fine_job_automation_action(
    action_id: str,
    payload: FineJobActionCompleteRequest,
    db: Database = Depends(get_database),
) -> FineJobAutomationActionEnvelope:
    return FineJobAutomationActionEnvelope(
        action=complete_action(
            db,
            action_id,
            worker_id=payload.worker_id,
            status=payload.status,
            message=payload.message,
        )
    )
