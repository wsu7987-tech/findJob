from __future__ import annotations

from fastapi import APIRouter, Depends

from backend.app.config import AppConfig
from backend.app.db import Database
from backend.app.dependencies import get_config, get_database
from backend.app.schemas.fine_job.delivery_runs import (
    DeliveryRunCreateRequest,
    FineJobActionLogListEnvelope,
    FineJobDeliveryCandidateListEnvelope,
    FineJobDeliveryRunEnvelope,
    FineJobDeliveryRunListEnvelope,
)
from backend.app.services.fine_job.delivery_runs import (
    create_delivery_run,
    get_delivery_run,
    list_action_logs,
    list_delivery_candidates,
    list_delivery_runs,
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
    db: Database = Depends(get_database),
) -> FineJobActionLogListEnvelope:
    return FineJobActionLogListEnvelope(logs=list_action_logs(db))


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
    return FineJobActionLogListEnvelope(logs=list_action_logs(db, run_id=run_id))
