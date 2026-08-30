from __future__ import annotations

from fastapi import APIRouter, Depends, Response, status

from backend.app.db import Database
from backend.app.dependencies import get_database
from backend.app.errors import AppError
from backend.app.schemas.fine_job.strategies import (
    FineJobFilterStrategyEnvelope,
    FineJobFilterStrategyListEnvelope,
    FineJobFilterStrategyPayload,
    FineJobRecommendationStrategyEnvelope,
    FineJobRecommendationStrategyListEnvelope,
    FineJobRecommendationStrategyPayload,
)
from backend.app.schemas.fine_job.profile_v3 import (
    SearchKeywordEnvelope,
    SearchKeywordListEnvelope,
    SearchKeywordOrderUpdate,
    SearchKeywordPayload,
    StrategyChangeSetApply,
    StrategyChangeSetEnvelope,
    StrategyChangeSetListEnvelope,
)
from backend.app.services.fine_job.strategies import (
    delete_filter_strategy,
    delete_recommendation_strategy,
    create_search_keyword,
    apply_strategy_change_set,
    delete_search_keyword,
    list_filter_strategies,
    list_recommendation_strategies,
    list_search_keywords,
    list_strategy_change_sets,
    reorder_search_keywords,
    save_filter_strategy,
    save_recommendation_strategy,
    update_search_keyword,
)
from backend.app.services.fine_job.filter_exclusions import (
    ensure_exclusion_state,
    rebuild_exclusion_state,
)
from backend.app.services.fine_job.strategies import get_filter_strategy


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


@router.get("/filters/{strategy_id}/exclusions")
def read_filter_exclusions(
    strategy_id: str,
    db: Database = Depends(get_database),
) -> dict[str, object]:
    strategy = get_filter_strategy(db, strategy_id)
    return ensure_exclusion_state(db, strategy)


@router.post("/filters/{strategy_id}/exclusions/refresh")
def refresh_filter_exclusions(
    strategy_id: str,
    db: Database = Depends(get_database),
) -> dict[str, object]:
    strategy = get_filter_strategy(db, strategy_id)
    return rebuild_exclusion_state(db, strategy)


@router.get(
    "/filters/{strategy_id}/search-keywords",
    response_model=SearchKeywordListEnvelope,
)
def read_filter_strategy_search_keywords(
    strategy_id: str,
    db: Database = Depends(get_database),
) -> SearchKeywordListEnvelope:
    return SearchKeywordListEnvelope(keywords=list_search_keywords(db, strategy_id))


@router.post(
    "/filters/{strategy_id}/search-keywords",
    response_model=SearchKeywordEnvelope,
    status_code=status.HTTP_201_CREATED,
)
def create_filter_strategy_search_keyword(
    strategy_id: str,
    payload: SearchKeywordPayload,
    db: Database = Depends(get_database),
) -> SearchKeywordEnvelope:
    return SearchKeywordEnvelope(
        keyword=create_search_keyword(db, strategy_id, payload)
    )


@router.patch(
    "/filters/{strategy_id}/search-keywords/{keyword_id}",
    response_model=SearchKeywordEnvelope,
)
def update_filter_strategy_search_keyword(
    strategy_id: str,
    keyword_id: str,
    payload: SearchKeywordPayload,
    db: Database = Depends(get_database),
) -> SearchKeywordEnvelope:
    return SearchKeywordEnvelope(
        keyword=update_search_keyword(db, strategy_id, keyword_id, payload)
    )


@router.delete(
    "/filters/{strategy_id}/search-keywords/{keyword_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def remove_filter_strategy_search_keyword(
    strategy_id: str,
    keyword_id: str,
    db: Database = Depends(get_database),
) -> Response:
    delete_search_keyword(db, strategy_id, keyword_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put(
    "/filters/{strategy_id}/search-keywords/order",
    response_model=SearchKeywordListEnvelope,
)
def order_filter_strategy_search_keywords(
    strategy_id: str,
    payload: SearchKeywordOrderUpdate,
    db: Database = Depends(get_database),
) -> SearchKeywordListEnvelope:
    return SearchKeywordListEnvelope(
        keywords=reorder_search_keywords(db, strategy_id, payload.keyword_ids)
    )


@router.get(
    "/filters/{strategy_id}/ai-change-sets",
    response_model=StrategyChangeSetListEnvelope,
)
def read_filter_strategy_ai_change_sets(
    strategy_id: str,
    db: Database = Depends(get_database),
) -> StrategyChangeSetListEnvelope:
    return StrategyChangeSetListEnvelope(
        change_sets=list_strategy_change_sets(db, strategy_id)
    )


@router.post(
    "/filters/{strategy_id}/ai-change-sets/{change_set_id}/apply",
    response_model=StrategyChangeSetEnvelope,
)
def apply_filter_strategy_ai_change_set(
    strategy_id: str,
    change_set_id: str,
    payload: StrategyChangeSetApply,
    db: Database = Depends(get_database),
) -> StrategyChangeSetEnvelope:
    change_set = next(
        (
            item
            for item in list_strategy_change_sets(db, strategy_id)
            if item["id"] == change_set_id
        ),
        None,
    )
    if change_set is None:
        raise AppError(404, "STRATEGY_CHANGE_SET_NOT_FOUND", "策略 AI 变更不存在。")
    return StrategyChangeSetEnvelope(
        change_set=apply_strategy_change_set(db, change_set_id, payload)
    )


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


@router.get(
    "/recommendations/{strategy_id}/ai-change-sets",
    response_model=StrategyChangeSetListEnvelope,
)
def read_recommendation_strategy_ai_change_sets(
    strategy_id: str,
    db: Database = Depends(get_database),
) -> StrategyChangeSetListEnvelope:
    return StrategyChangeSetListEnvelope(
        change_sets=list_strategy_change_sets(db, strategy_id)
    )


@router.post(
    "/recommendations/{strategy_id}/ai-change-sets/{change_set_id}/apply",
    response_model=StrategyChangeSetEnvelope,
)
def apply_recommendation_strategy_ai_change_set(
    strategy_id: str,
    change_set_id: str,
    payload: StrategyChangeSetApply,
    db: Database = Depends(get_database),
) -> StrategyChangeSetEnvelope:
    change_set = next(
        (
            item
            for item in list_strategy_change_sets(db, strategy_id)
            if item["id"] == change_set_id
        ),
        None,
    )
    if change_set is None:
        raise AppError(404, "STRATEGY_CHANGE_SET_NOT_FOUND", "策略 AI 变更不存在。")
    return StrategyChangeSetEnvelope(
        change_set=apply_strategy_change_set(db, change_set_id, payload)
    )
