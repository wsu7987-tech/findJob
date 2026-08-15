from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends

from backend.app.config import AppConfig
from backend.app.db import Database
from backend.app.dependencies import get_config, get_database
from backend.app.schemas.retrieval import (
    RetrievalFilterRequest,
    RetrievalIndexVersionCreateRequest,
    RetrievalIndexRebuildResponse,
    RetrievalIndexVersionListResponse,
    RetrievalIndexVersionResponse,
    RetrievalSearchRequest,
    RetrievalSearchResponse,
)
from backend.app.services.retrieval import build_retrieval_context
from backend.app.services.retrieval_index_versions import (
    activate_chunk_index_version,
    create_chunk_index_version,
    list_chunk_index_versions,
    rebuild_chunk_index_version,
)
from backend.app.services.retrieval_types import RetrievalFilters, RetrievalQuery


router = APIRouter(prefix="/retrieval", tags=["retrieval"])


@router.get("/index-versions", response_model=RetrievalIndexVersionListResponse)
def retrieval_index_versions_list(
    db: Database = Depends(get_database),
    config: AppConfig = Depends(get_config),
) -> RetrievalIndexVersionListResponse:
    provider_name = config.embedding_provider or "stub-embedding"
    model_name = config.embedding_model or "stub-embedding-model"
    with db.connect() as connection:
        items = list_chunk_index_versions(
            connection,
            provider_name=provider_name,
            model_name=model_name,
        )
    return RetrievalIndexVersionListResponse(
        items=[RetrievalIndexVersionResponse(**asdict(item)) for item in items]
    )


@router.post("/index-versions", response_model=RetrievalIndexVersionResponse)
def retrieval_index_versions_create(
    payload: RetrievalIndexVersionCreateRequest,
    db: Database = Depends(get_database),
    config: AppConfig = Depends(get_config),
) -> RetrievalIndexVersionResponse:
    provider_name = config.embedding_provider or "stub-embedding"
    model_name = config.embedding_model or "stub-embedding-model"
    with db.connect() as connection:
        item = create_chunk_index_version(
            connection,
            provider_name=provider_name,
            model_name=model_name,
            version_tag=payload.version_tag,
        )
    return RetrievalIndexVersionResponse(**asdict(item))


@router.post("/index-versions/{version_id}/activate", response_model=RetrievalIndexVersionResponse)
def retrieval_index_versions_activate(
    version_id: str,
    db: Database = Depends(get_database),
    config: AppConfig = Depends(get_config),
) -> RetrievalIndexVersionResponse:
    provider_name = config.embedding_provider or "stub-embedding"
    model_name = config.embedding_model or "stub-embedding-model"
    with db.connect() as connection:
        item = activate_chunk_index_version(
            connection,
            version_id=version_id,
            provider_name=provider_name,
            model_name=model_name,
    )
    return RetrievalIndexVersionResponse(**asdict(item))


@router.post("/index-versions/{version_id}/rebuild", response_model=RetrievalIndexRebuildResponse)
def retrieval_index_versions_rebuild(
    version_id: str,
    db: Database = Depends(get_database),
    config: AppConfig = Depends(get_config),
) -> RetrievalIndexRebuildResponse:
    with db.connect() as connection:
        report = rebuild_chunk_index_version(
            connection,
            config=config,
            version_id=version_id,
        )
    return RetrievalIndexRebuildResponse(**asdict(report))


@router.post("/search", response_model=RetrievalSearchResponse)
def retrieval_search(
    payload: RetrievalSearchRequest,
    db: Database = Depends(get_database),
    config: AppConfig = Depends(get_config),
) -> RetrievalSearchResponse:
    request_filters = payload.filters or RetrievalFilterRequest()
    filters = RetrievalFilters(
        source_types=request_filters.source_types,
        created_at_from=request_filters.created_at_from,
        created_at_to=request_filters.created_at_to,
        knowledge_item_ids=request_filters.knowledge_item_ids,
        keyword=request_filters.keyword,
        category=request_filters.category,
        user_tags=request_filters.user_tags,
        ai_tags=request_filters.ai_tags,
    )
    result = build_retrieval_context(
        db=db,
        config=config,
        query=RetrievalQuery(
            text=payload.query,
            filters=filters,
            limit=payload.limit,
        ),
    )
    return RetrievalSearchResponse(
        query=result.query_text,
        applied_filters=RetrievalFilterRequest(
            source_types=result.filters.source_types,
            created_at_from=result.filters.created_at_from,
            created_at_to=result.filters.created_at_to,
            knowledge_item_ids=result.filters.knowledge_item_ids,
            keyword=result.filters.keyword,
            category=result.filters.category,
            user_tags=result.filters.user_tags,
            ai_tags=result.filters.ai_tags,
        ),
        child_hits=[asdict(hit) for hit in result.child_hits],
        parent_contexts={
            parent_chunk_id: asdict(parent_context)
            for parent_chunk_id, parent_context in result.parent_contexts.items()
        },
        citations=[asdict(citation) for citation in result.citations],
    )
