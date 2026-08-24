from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request

from backend.app.config import AppConfig
from backend.app.db import Database
from backend.app.dependencies import get_config
from backend.app.schemas.config import (
    AppConfigPatchRequest,
    AppConfigResponse,
    CodexConnectivityCheckResponse,
    CodexModelListRequest,
    CodexModelListResponse,
    ProviderConnectivityCheckResponse,
)
from backend.app.services.config import (
    persist_config_updates,
    serialize_config,
    update_config,
)
from backend.app.services.ai import check_embedding_connection, check_llm_connection
from backend.app.services.reasoning.codex_exec import (
    check_codex_cli,
    validate_codex_options,
)
from backend.app.services.codex_models import list_codex_models
from backend.app.errors import AppError


router = APIRouter(prefix="/config", tags=["config"])

# FineJob 保留的 API 接口。
# FineJob V1 使用这些接口配置本地 LLM/Embedding、检查服务商连通性，
# 并持久化运行时配置；当前桌面端配置流程仍需要保留这些接口。


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


@router.post("/check-codex", response_model=CodexConnectivityCheckResponse)
def check_config_codex_connectivity(
    config: AppConfig = Depends(get_config),
) -> CodexConnectivityCheckResponse:
    try:
        validate_codex_options(
            model=config.codex_model,
            reasoning_effort=config.codex_reasoning_effort,
        )
    except AppError as exc:
        return CodexConnectivityCheckResponse(
            capability="codex-cli",
            ok=False,
            status="invalid",
            cli_path=None,
            cli_version=None,
            authenticated=False,
            model=config.codex_model,
            reasoning_effort=config.codex_reasoning_effort,
            detail=exc.error_message,
            error_category=exc.error_category,
            checked_at=datetime.now(UTC).isoformat(),
        )

    result = check_codex_cli(config.codex_cli_path)
    return CodexConnectivityCheckResponse(
        capability="codex-cli",
        ok=result.ok,
        status=result.status,
        cli_path=result.cli_path,
        cli_version=result.cli_version,
        authenticated=result.authenticated,
        model=config.codex_model,
        reasoning_effort=config.codex_reasoning_effort,
        detail=result.detail,
        error_category=result.error_category,
        checked_at=datetime.now(UTC).isoformat(),
    )


@router.post("/codex-models", response_model=CodexModelListResponse)
def list_config_codex_models(payload: CodexModelListRequest) -> CodexModelListResponse:
    result = list_codex_models(payload.cli_path)
    return CodexModelListResponse(**result)
