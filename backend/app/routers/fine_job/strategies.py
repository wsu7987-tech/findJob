from __future__ import annotations

from fastapi import APIRouter, Depends, Response, status

from backend.app.db import Database
from backend.app.dependencies import get_database
from backend.app.schemas.fine_job.strategies import (
    FineJobFilterStrategyEnvelope,
    FineJobFilterStrategyListEnvelope,
    FineJobFilterStrategyPayload,
    FineJobRecommendationStrategyEnvelope,
    FineJobRecommendationStrategyListEnvelope,
    FineJobRecommendationStrategyPayload,
)
from backend.app.services.fine_job.strategies import (
    delete_filter_strategy,
    delete_recommendation_strategy,
    list_filter_strategies,
    list_recommendation_strategies,
    save_filter_strategy,
    save_recommendation_strategy,
)


router = APIRouter(prefix="/fine-job/strategies", tags=["fine-job-strategies"])


@router.get("/filters", response_model=FineJobFilterStrategyListEnvelope)
def read_filter_strategies(
    db: Database = Depends(get_database),
) -> FineJobFilterStrategyListEnvelope:
    return FineJobFilterStrategyListEnvelope(strategies=list_filter_strategies(db))


@router.post(
    "/filters",
    response_model=FineJobFilterStrategyEnvelope,
    status_code=status.HTTP_201_CREATED,
)
def create_filter_strategy(
    payload: FineJobFilterStrategyPayload,
    db: Database = Depends(get_database),
) -> FineJobFilterStrategyEnvelope:
    return FineJobFilterStrategyEnvelope(strategy=save_filter_strategy(db, payload))


@router.put("/filters/{strategy_id}", response_model=FineJobFilterStrategyEnvelope)
def update_filter_strategy(
    strategy_id: str,
    payload: FineJobFilterStrategyPayload,
    db: Database = Depends(get_database),
) -> FineJobFilterStrategyEnvelope:
    return FineJobFilterStrategyEnvelope(
        strategy=save_filter_strategy(db, payload, strategy_id=strategy_id)
    )


@router.delete("/filters/{strategy_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_filter_strategy(
    strategy_id: str,
    db: Database = Depends(get_database),
) -> Response:
    delete_filter_strategy(db, strategy_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/recommendations", response_model=FineJobRecommendationStrategyListEnvelope)
def read_recommendation_strategies(
    db: Database = Depends(get_database),
) -> FineJobRecommendationStrategyListEnvelope:
    return FineJobRecommendationStrategyListEnvelope(
        strategies=list_recommendation_strategies(db)
    )


@router.post(
    "/recommendations",
    response_model=FineJobRecommendationStrategyEnvelope,
    status_code=status.HTTP_201_CREATED,
)
def create_recommendation_strategy(
    payload: FineJobRecommendationStrategyPayload,
    db: Database = Depends(get_database),
) -> FineJobRecommendationStrategyEnvelope:
    return FineJobRecommendationStrategyEnvelope(
        strategy=save_recommendation_strategy(db, payload)
    )


@router.put(
    "/recommendations/{strategy_id}",
    response_model=FineJobRecommendationStrategyEnvelope,
)
def update_recommendation_strategy(
    strategy_id: str,
    payload: FineJobRecommendationStrategyPayload,
    db: Database = Depends(get_database),
) -> FineJobRecommendationStrategyEnvelope:
    return FineJobRecommendationStrategyEnvelope(
        strategy=save_recommendation_strategy(db, payload, strategy_id=strategy_id)
    )


@router.delete(
    "/recommendations/{strategy_id}", status_code=status.HTTP_204_NO_CONTENT
)
def remove_recommendation_strategy(
    strategy_id: str,
    db: Database = Depends(get_database),
) -> Response:
    delete_recommendation_strategy(db, strategy_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
