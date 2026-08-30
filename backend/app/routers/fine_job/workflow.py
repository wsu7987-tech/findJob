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
    FineJobReviewArchiveRequest,
    FineJobReviewBatchRequest,
    FineJobReviewBatchResponse,
    FineJobReviewItemListEnvelope,
    FineJobReviewItemResponse,
    FineJobReviewRejectRequest,
    ReviewDecision,
    ReviewStatus,
)
from backend.app.services.fine_job.workflow import (
    approve_review_item,
    archive_review_item,
    batch_review_items,
    claim_next_action,
    complete_action,
    list_automation_actions,
    list_review_items,
    reject_review_item,
    restore_review_item,
)


router = APIRouter(prefix="/fine-job", tags=["fine-job-workflow"])


@router.get("/review-items", response_model=FineJobReviewItemListEnvelope)
def get_fine_job_review_items(
    status: ReviewStatus | None = None,
    decision: ReviewDecision | None = None,
    query: str = Query(default="", max_length=120),
    execution_state: str | None = Query(default=None, max_length=80),
    created_from: str | None = Query(default=None, max_length=40),
    created_to: str | None = Query(default=None, max_length=40),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    db: Database = Depends(get_database),
) -> FineJobReviewItemListEnvelope:
    return FineJobReviewItemListEnvelope(**list_review_items(
        db,
        status=status,
        decision=decision,
        query=query,
        execution_state=execution_state,
        created_from=created_from,
        created_to=created_to,
        page=page,
        page_size=page_size,
    ))


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


@router.post(
    "/review-items/{review_item_id}/archive",
    response_model=FineJobReviewItemResponse,
)
def archive_fine_job_review_item(
    review_item_id: str,
    payload: FineJobReviewArchiveRequest,
    db: Database = Depends(get_database),
) -> FineJobReviewItemResponse:
    return FineJobReviewItemResponse(
        **archive_review_item(db, review_item_id, note=payload.note)
    )


@router.post(
    "/review-items/{review_item_id}/restore",
    response_model=FineJobReviewItemResponse,
)
def restore_fine_job_review_item(
    review_item_id: str,
    db: Database = Depends(get_database),
) -> FineJobReviewItemResponse:
    return FineJobReviewItemResponse(**restore_review_item(db, review_item_id))


@router.post("/review-items/batch", response_model=FineJobReviewBatchResponse)
def batch_fine_job_review_items(
    payload: FineJobReviewBatchRequest,
    db: Database = Depends(get_database),
) -> FineJobReviewBatchResponse:
    return FineJobReviewBatchResponse(**batch_review_items(
        db,
        review_item_ids=payload.review_item_ids,
        operation=payload.operation,
        note=payload.note,
        allow_override=payload.allow_override,
    ))


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
