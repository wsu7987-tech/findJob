from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from backend.app.config import AppConfig
from backend.app.db import Database
from backend.app.dependencies import get_config, get_database
from backend.app.schemas.fine_job.delivery_runs import (
    DeliveryRunCreateRequest,
    FineJobActionLogCleanupRequest,
    FineJobActionLogCleanupResponse,
    FineJobActionLogListEnvelope,
    FineJobDeliveryCandidateListEnvelope,
    FineJobDeliveryRunDeleteResponse,
    FineJobDeliveryRunEnvelope,
    FineJobDeliveryRunListEnvelope,
    FineJobOperationsDashboardResponse,
)
from backend.app.services.fine_job.delivery_runs import (
    cleanup_action_logs,
    create_delivery_run,
    delete_delivery_run,
    get_operations_dashboard,
    get_delivery_run,
    list_delivery_candidates,
    list_delivery_runs,
    query_action_logs,
)


router = APIRouter(prefix="/fine-job/delivery-runs", tags=["fine-job-delivery-runs"])


@router.get("", response_model=FineJobDeliveryRunListEnvelope)
def list_fine_job_delivery_runs(db: Database = Depends(get_database)) -> FineJobDeliveryRunListEnvelope:
    return FineJobDeliveryRunListEnvelope(runs=list_delivery_runs(db))


@router.post("", response_model=FineJobDeliveryRunEnvelope)
def create_fine_job_delivery_run(
    payload: DeliveryRunCreateRequest,
    db: Database = Depends(get_database),
    config: AppConfig = Depends(get_config),
) -> FineJobDeliveryRunEnvelope:
    return FineJobDeliveryRunEnvelope(
        run=create_delivery_run(
            db,
            config=config,
            mode=payload.mode,
            real_collect=payload.real_collect,
        )
    )


@router.get("/logs/recent", response_model=FineJobActionLogListEnvelope)
def list_recent_fine_job_action_logs(
    query: str = Query(default="", max_length=120),
    level: str | None = Query(default=None, max_length=20),
    action_type: str | None = Query(default=None, max_length=100),
    category: str | None = Query(default=None, max_length=40),
    outcome: str | None = Query(default=None, max_length=40),
    source: str | None = Query(default=None, max_length=40),
    created_from: str | None = Query(default=None, max_length=40),
    created_to: str | None = Query(default=None, max_length=40),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    db: Database = Depends(get_database),
) -> FineJobActionLogListEnvelope:
    return FineJobActionLogListEnvelope(**query_action_logs(
        db,
        query=query,
        level=level,
        action_type=action_type,
        category=category,
        outcome=outcome,
        source=source,
        created_from=created_from,
        created_to=created_to,
        page=page,
        page_size=page_size,
    ))


@router.post("/logs/cleanup", response_model=FineJobActionLogCleanupResponse)
def cleanup_fine_job_action_logs(
    payload: FineJobActionLogCleanupRequest,
    db: Database = Depends(get_database),
) -> FineJobActionLogCleanupResponse:
    deleted = cleanup_action_logs(db, before=payload.before, source=payload.source)
    return FineJobActionLogCleanupResponse(deleted=deleted, before=payload.before)


@router.get("/operations/dashboard", response_model=FineJobOperationsDashboardResponse)
def get_fine_job_operations_dashboard(
    db: Database = Depends(get_database),
) -> FineJobOperationsDashboardResponse:
    return FineJobOperationsDashboardResponse(**get_operations_dashboard(db))


@router.get("/{run_id}", response_model=FineJobDeliveryRunEnvelope)
def get_fine_job_delivery_run(
    run_id: str,
    db: Database = Depends(get_database),
) -> FineJobDeliveryRunEnvelope:
    return FineJobDeliveryRunEnvelope(run=get_delivery_run(db, run_id))


@router.get("/{run_id}/candidates", response_model=FineJobDeliveryCandidateListEnvelope)
def list_fine_job_delivery_candidates(
    run_id: str,
    db: Database = Depends(get_database),
) -> FineJobDeliveryCandidateListEnvelope:
    return FineJobDeliveryCandidateListEnvelope(candidates=list_delivery_candidates(db, run_id))


@router.get("/{run_id}/logs", response_model=FineJobActionLogListEnvelope)
def list_fine_job_delivery_run_logs(
    run_id: str,
    db: Database = Depends(get_database),
) -> FineJobActionLogListEnvelope:
    return FineJobActionLogListEnvelope(**query_action_logs(db, run_id=run_id, page_size=100))


@router.delete("/{run_id}", response_model=FineJobDeliveryRunDeleteResponse)
def delete_fine_job_delivery_run(
    run_id: str,
    db: Database = Depends(get_database),
) -> FineJobDeliveryRunDeleteResponse:
    return FineJobDeliveryRunDeleteResponse(**delete_delivery_run(db, run_id))
