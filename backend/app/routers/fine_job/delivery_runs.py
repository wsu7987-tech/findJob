from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from backend.app.db import Database
from backend.app.dependencies import get_database
from backend.app.schemas.fine_job.delivery_runs import (
    FineJobActionLogCleanupRequest,
    FineJobActionLogCleanupResponse,
    FineJobActionLogListEnvelope,
    FineJobOperationsDashboardResponse,
)
from backend.app.services.fine_job.delivery_runs import (
    cleanup_action_logs,
    get_operations_dashboard,
    query_action_logs,
)


router = APIRouter(prefix="/fine-job/delivery-runs", tags=["fine-job-operations"])


@router.get("/logs/recent", response_model=FineJobActionLogListEnvelope)
def list_recent_fine_job_action_logs(
    query: str = Query(default="", max_length=120),
    level: str | None = Query(default=None, max_length=20),
    action_type: str | None = Query(default=None, max_length=100),
    category: str | None = Query(default=None, max_length=40),
    outcome: str | None = Query(default=None, max_length=40),
    created_from: str | None = Query(default=None, max_length=40),
    created_to: str | None = Query(default=None, max_length=40),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    db: Database = Depends(get_database),
) -> FineJobActionLogListEnvelope:
    return FineJobActionLogListEnvelope(**query_action_logs(
        db, query=query, level=level, action_type=action_type, category=category,
        outcome=outcome, created_from=created_from, created_to=created_to,
        page=page, page_size=page_size,
    ))


@router.post("/logs/cleanup", response_model=FineJobActionLogCleanupResponse)
def cleanup_fine_job_action_logs(
    payload: FineJobActionLogCleanupRequest,
    db: Database = Depends(get_database),
) -> FineJobActionLogCleanupResponse:
    deleted = cleanup_action_logs(db, before=payload.before)
    return FineJobActionLogCleanupResponse(deleted=deleted, before=payload.before)


@router.get("/operations/dashboard", response_model=FineJobOperationsDashboardResponse)
def get_fine_job_operations_dashboard(
    db: Database = Depends(get_database),
) -> FineJobOperationsDashboardResponse:
    return FineJobOperationsDashboardResponse(**get_operations_dashboard(db))
