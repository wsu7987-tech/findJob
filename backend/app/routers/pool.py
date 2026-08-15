from __future__ import annotations

from fastapi import APIRouter, Depends, status

from backend.app.config import AppConfig
from backend.app.db import Database
from backend.app.dependencies import get_config, get_database
from backend.app.schemas.pool import (
    DeletePoolItemResponse,
    PoolCreateResponse,
    PoolItemCreateRequest,
    PoolMetadataSuggestionRequest,
    PoolMetadataSuggestionResponse,
    PoolListResponse,
    ResummarizePoolItemResponse,
)
from backend.app.services.ai import suggest_metadata
from backend.app.services.pool import (
    create_pool_item,
    delete_pool_item,
    list_pool_items,
    reingest_pool_item,
    resummarize_pool_item,
)


router = APIRouter(prefix="/pool", tags=["pool"])


@router.get("/items", response_model=PoolListResponse)
def get_pool_items(db: Database = Depends(get_database)) -> PoolListResponse:
    items = list_pool_items(db)
    return PoolListResponse(items=items, total=len(items))


@router.post(
    "/items",
    response_model=PoolCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_pool_items(
    payload: PoolItemCreateRequest,
    db: Database = Depends(get_database),
    config: AppConfig = Depends(get_config),
) -> PoolCreateResponse:
    return PoolCreateResponse(item=create_pool_item(db, config, payload))


@router.post(
    "/metadata-suggestions",
    response_model=PoolMetadataSuggestionResponse,
)
def suggest_pool_item_metadata(
    payload: PoolMetadataSuggestionRequest,
) -> PoolMetadataSuggestionResponse:
    suggestion = suggest_metadata(
        title=payload.title,
        raw_content=payload.raw_text,
        source_type=payload.source_type,
        source_value=payload.source_value,
    )
    return PoolMetadataSuggestionResponse(
        category=suggestion.category,
        tags=suggestion.tags,
        strategy=suggestion.strategy,
    )


@router.delete("/items/{item_id}", response_model=DeletePoolItemResponse)
def remove_pool_item(
    item_id: str,
    db: Database = Depends(get_database),
) -> DeletePoolItemResponse:
    delete_pool_item(db, item_id)
    return DeletePoolItemResponse(deleted=True)


@router.post(
    "/items/{item_id}/reingest",
    response_model=ResummarizePoolItemResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def reingest_pool_entry(
    item_id: str,
    db: Database = Depends(get_database),
    config: AppConfig = Depends(get_config),
) -> ResummarizePoolItemResponse:
    reingest_pool_item(db, config, item_id)
    return ResummarizePoolItemResponse(accepted=True)


@router.post(
    "/items/{item_id}/resummarize",
    response_model=ResummarizePoolItemResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def rerun_pool_item(
    item_id: str,
    db: Database = Depends(get_database),
) -> ResummarizePoolItemResponse:
    resummarize_pool_item(db, item_id)
    return ResummarizePoolItemResponse(accepted=True)
