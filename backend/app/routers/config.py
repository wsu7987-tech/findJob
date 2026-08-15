from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request

from backend.app.config import AppConfig
from backend.app.db import Database
from backend.app.dependencies import get_config
from backend.app.schemas.config import (
    AppConfigPatchRequest,
    AppConfigResponse,
    ProviderConnectivityCheckResponse,
)
from backend.app.services.config import (
    persist_config_updates,
    serialize_config,
    update_config,
)
from backend.app.services.ai import check_embedding_connection, check_llm_connection


router = APIRouter(prefix="/config", tags=["config"])

# FineJob-retained API surface.
# These endpoints are required by FineJob V1 for local LLM/Embedding setup,
# provider connectivity checks, and persisted runtime configuration. Do not
# remove them when cleaning legacy KnowledgeCurator routers.


@router.get("", response_model=AppConfigResponse)
def read_config(config: AppConfig = Depends(get_config)) -> AppConfigResponse:
    return AppConfigResponse(**serialize_config(config))


@router.patch("", response_model=AppConfigResponse)
def patch_config(
    payload: AppConfigPatchRequest,
    request: Request,
    config: AppConfig = Depends(get_config),
) -> AppConfigResponse:
    original_sqlite_path = config.sqlite_path
    updated_config = update_config(config, payload)
    persist_config_updates(updated_config, payload)
    request.app.state.config = updated_config

    if updated_config.sqlite_path != original_sqlite_path:
        db = Database(updated_config.sqlite_path)
        db.initialize()
        request.app.state.db = db

    return AppConfigResponse(**serialize_config(updated_config))


@router.post("/check-llm", response_model=ProviderConnectivityCheckResponse)
def check_config_llm_connectivity(
    config: AppConfig = Depends(get_config),
) -> ProviderConnectivityCheckResponse:
    result = check_llm_connection(config)
    return ProviderConnectivityCheckResponse(
        capability=result.capability,
        ok=result.ok,
        status=result.status,
        provider=result.provider,
        model=result.model,
        base_url=result.base_url,
        detail=result.detail,
        error_category=result.error_category,
        checked_at=datetime.now(UTC).isoformat(),
    )


@router.post("/check-embedding", response_model=ProviderConnectivityCheckResponse)
def check_config_embedding_connectivity(
    config: AppConfig = Depends(get_config),
) -> ProviderConnectivityCheckResponse:
    result = check_embedding_connection(config)
    return ProviderConnectivityCheckResponse(
        capability=result.capability,
        ok=result.ok,
        status=result.status,
        provider=result.provider,
        model=result.model,
        base_url=result.base_url,
        detail=result.detail,
        error_category=result.error_category,
        checked_at=datetime.now(UTC).isoformat(),
    )
