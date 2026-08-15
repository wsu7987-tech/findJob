from __future__ import annotations

from fastapi import APIRouter, Depends

from backend.app.db import Database
from backend.app.dependencies import get_database
from backend.app.schemas.fine_job.delivery_strategies import (
    FineJobDeliveryStrategyEnvelope,
    FineJobDeliveryStrategyPayload,
)
from backend.app.services.fine_job.delivery_strategies import (
    get_delivery_strategy,
    save_delivery_strategy,
)


router = APIRouter(prefix="/fine-job/delivery-strategy", tags=["fine-job-delivery-strategy"])


@router.get("", response_model=FineJobDeliveryStrategyEnvelope)
def read_fine_job_delivery_strategy(
    db: Database = Depends(get_database),
) -> FineJobDeliveryStrategyEnvelope:
    return FineJobDeliveryStrategyEnvelope(strategy=get_delivery_strategy(db))


@router.put("", response_model=FineJobDeliveryStrategyEnvelope)
def save_fine_job_delivery_strategy(
    payload: FineJobDeliveryStrategyPayload,
    db: Database = Depends(get_database),
) -> FineJobDeliveryStrategyEnvelope:
    return FineJobDeliveryStrategyEnvelope(strategy=save_delivery_strategy(db, payload))
