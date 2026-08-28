from __future__ import annotations

from fastapi import APIRouter, Depends

from backend.app.config import AppConfig
from backend.app.db import Database
from backend.app.dependencies import get_config, get_database
from backend.app.errors import AppError
from backend.app.schemas.config import AppConfigPatchRequest
from backend.app.schemas.fine_job.codex import (
    CodexPendingDecisionRequest,
    CodexPermissionsPatchRequest,
    CodexPermissionsResponse,
)
from backend.app.services.config import persist_config_updates
from backend.app.services.fine_job.codex_authorization import SENSITIVE_OPERATION_KEYS
from backend.app.services.fine_job.codex_tools import (
    approve_pending,
    list_pending_work,
    reject_pending,
)


router = APIRouter(prefix="/fine-job/codex", tags=["fine-job-codex"])


def _permissions_response(config: AppConfig) -> CodexPermissionsResponse:
    permissions = config.codex_sensitive_operation_permissions or {}
    supported = {key: key not in {"submit_application", "change_automation_policy"} for key in SENSITIVE_OPERATION_KEYS}
    return CodexPermissionsResponse(
        enabled=config.codex_sensitive_auto_authorization_enabled,
        permissions={key: bool(permissions.get(key, False)) for key in SENSITIVE_OPERATION_KEYS},
        supported=supported,
    )


@router.get("/permissions", response_model=CodexPermissionsResponse)
def get_permissions(config: AppConfig = Depends(get_config)) -> CodexPermissionsResponse:
    return _permissions_response(config)


@router.patch("/permissions", response_model=CodexPermissionsResponse)
def patch_permissions(
    payload: CodexPermissionsPatchRequest,
    config: AppConfig = Depends(get_config),
) -> CodexPermissionsResponse:
    unknown = set(payload.permissions) - set(SENSITIVE_OPERATION_KEYS)
    if unknown:
        raise AppError(status_code=422, error_category="SENSITIVE_OPERATION_UNKNOWN", error_message="包含未登记的敏感操作标识。")
    config.codex_sensitive_auto_authorization_enabled = payload.enabled
    config.codex_sensitive_operation_permissions = {
        key: bool(payload.permissions.get(key, False)) for key in SENSITIVE_OPERATION_KEYS
    }
    persist_config_updates(
        config,
        AppConfigPatchRequest(
            codex_sensitive_auto_authorization_enabled=payload.enabled,
            codex_sensitive_operation_permissions=config.codex_sensitive_operation_permissions,
        ),
    )
    return _permissions_response(config)


@router.get("/pending")
def pending(db: Database = Depends(get_database)):
    return list_pending_work(db)


@router.post("/pending/{resource_type}/{resource_id}/approve")
def approve(
    resource_type: str,
    resource_id: str,
    payload: CodexPendingDecisionRequest,
    db: Database = Depends(get_database),
):
    return approve_pending(
        db,
        resource_type=resource_type,
        resource_id=resource_id,
        expected_version=payload.expected_version,
        final_text=payload.final_text,
        allow_override=payload.allow_override,
    )


@router.post("/pending/{resource_type}/{resource_id}/reject")
def reject(
    resource_type: str,
    resource_id: str,
    payload: CodexPendingDecisionRequest,
    db: Database = Depends(get_database),
):
    return reject_pending(db, resource_type=resource_type, resource_id=resource_id, note=payload.note)

