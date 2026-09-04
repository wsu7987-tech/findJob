from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Request

from backend.app.config import AppConfig
from backend.app.db import Database
from backend.app.dependencies import get_config, get_database
from backend.app.errors import AppError
from backend.app.schemas.fine_job.codex import (
    CodexHandshakeResponse,
    CodexRuntimeCompleteRequest,
    CodexRuntimeResponse,
    CodexToolRequest,
)
from backend.app.services.fine_job.codex_runtime import (
    CAPABILITIES_VERSION,
    INTERNAL_API_VERSION,
    MCP_CONTRACT_VERSION,
    CodexRuntimeRegistry,
)
from backend.app.services.fine_job import boss_executor
from backend.app.services.fine_job.codex_tools import CodexToolService
from backend.app.utils import utc_now


router = APIRouter(prefix="/internal/codex/v1", tags=["internal-codex"])


def _registry(request: Request) -> CodexRuntimeRegistry:
    return request.app.state.codex_runtime_registry


def _require_loopback(request: Request) -> None:
    host = request.client.host if request.client else ""
    if host not in {"127.0.0.1", "::1", "localhost", "testclient"}:
        raise AppError(
            status_code=403,
            error_category="CODEX_NON_LOOPBACK_REQUEST",
            error_message="Codex 内部 API 只接受本机回环请求。",
        )


def _bearer(authorization: str) -> str:
    prefix = "Bearer "
    if not authorization.startswith(prefix):
        raise AppError(status_code=401, error_category="CODEX_RUNTIME_UNAUTHORIZED", error_message="缺少 Codex MCP 运行凭证。")
    return authorization[len(prefix):].strip()


def _require_versions(contract: str, api_version: str) -> None:
    if contract != MCP_CONTRACT_VERSION or api_version != INTERNAL_API_VERSION:
        raise AppError(
            status_code=409,
            error_category="CODEX_CONTRACT_INCOMPATIBLE",
            error_message="Codex MCP 合同或内部 API 版本不兼容。",
        )


def _authorize(
    request: Request,
    authorization: str,
    contract: str,
    api_version: str,
):
    _require_loopback(request)
    _require_versions(contract, api_version)
    return _registry(request).require(_bearer(authorization))


@router.post("/runtime", response_model=CodexRuntimeResponse)
def create_runtime(
    request: Request,
    db: Database = Depends(get_database),
) -> CodexRuntimeResponse:
    _require_loopback(request)
    runtime = _registry(request).create()
    now = utc_now()
    with db.connect() as connection:
        connection.execute(
            "INSERT INTO fj_codex_sessions (id, status, started_at, exited_at, exit_reason, created_at, updated_at) VALUES (?, 'running', ?, NULL, '', ?, ?)",
            (runtime.run_id, now, now, now),
        )
    return CodexRuntimeResponse(
        run_id=runtime.run_id,
        token=runtime.token,
        expires_at=runtime.expires_at.isoformat(),
    )


@router.post("/runtime/{run_id}/complete")
def complete_runtime(
    run_id: str,
    payload: CodexRuntimeCompleteRequest,
    request: Request,
    authorization: str = Header(default=""),
    contract: str = Header(default="", alias="X-FineJob-MCP-Contract-Version"),
    api_version: str = Header(default="", alias="X-FineJob-Internal-API-Version"),
    db: Database = Depends(get_database),
):
    runtime = _authorize(request, authorization, contract, api_version)
    if runtime.run_id != run_id:
        raise AppError(status_code=404, error_category="CODEX_RUNTIME_NOT_FOUND", error_message="Codex 运行不存在。")
    now = utc_now()
    with db.connect() as connection:
        connection.execute(
            "UPDATE fj_codex_sessions SET status = ?, exited_at = ?, exit_reason = ?, updated_at = ? WHERE id = ?",
            (payload.status, now, payload.reason, now, run_id),
        )
    _registry(request).revoke(run_id)
    return {"completed": True, "run_id": run_id, "status": payload.status}


@router.post("/handshake", response_model=CodexHandshakeResponse)
def handshake(
    request: Request,
    authorization: str = Header(default=""),
    contract: str = Header(default="", alias="X-FineJob-MCP-Contract-Version"),
    api_version: str = Header(default="", alias="X-FineJob-Internal-API-Version"),
) -> CodexHandshakeResponse:
    runtime = _authorize(request, authorization, contract, api_version)
    return CodexHandshakeResponse(run_id=runtime.run_id, sensitive_actions_allowed=True)


@router.get("/capabilities")
def capabilities(
    request: Request,
    authorization: str = Header(default=""),
    contract: str = Header(default="", alias="X-FineJob-MCP-Contract-Version"),
    api_version: str = Header(default="", alias="X-FineJob-Internal-API-Version"),
    db: Database = Depends(get_database),
    config: AppConfig = Depends(get_config),
):
    _authorize(request, authorization, contract, api_version)
    return CodexToolService(db, config).get_capabilities({})


@router.post("/tools/{tool_name}")
async def invoke_tool(
    tool_name: str,
    payload: CodexToolRequest,
    request: Request,
    authorization: str = Header(default=""),
    contract: str = Header(default="", alias="X-FineJob-MCP-Contract-Version"),
    api_version: str = Header(default="", alias="X-FineJob-Internal-API-Version"),
    db: Database = Depends(get_database),
    config: AppConfig = Depends(get_config),
):
    _authorize(request, authorization, contract, api_version)
    result = CodexToolService(db, config).call(f"finejob.{tool_name}", payload.arguments)
    if _result_changes_boss_queue(result):
        await boss_executor.notify_queue_changed(db)
    return result


def _result_changes_boss_queue(result: dict[str, object]) -> bool:
    resource = result.get("resource")
    if not isinstance(resource, dict):
        return False
    return result.get("result_type") == "action" and resource.get("type") == "automation_action"


@router.get("/operations/{resource_type}/{resource_id}")
def operation_status(
    resource_type: str,
    resource_id: str,
    request: Request,
    authorization: str = Header(default=""),
    contract: str = Header(default="", alias="X-FineJob-MCP-Contract-Version"),
    api_version: str = Header(default="", alias="X-FineJob-Internal-API-Version"),
    db: Database = Depends(get_database),
    config: AppConfig = Depends(get_config),
):
    _authorize(request, authorization, contract, api_version)
    return CodexToolService(db, config).get_operation_status(
        {"resource_type": resource_type, "resource_id": resource_id}
    )


@router.delete("/runtime/{run_id}")
def revoke_runtime(
    run_id: str,
    request: Request,
    authorization: str = Header(default=""),
    contract: str = Header(default="", alias="X-FineJob-MCP-Contract-Version"),
    api_version: str = Header(default="", alias="X-FineJob-Internal-API-Version"),
):
    runtime = _authorize(request, authorization, contract, api_version)
    if runtime.run_id != run_id:
        raise AppError(status_code=404, error_category="CODEX_RUNTIME_NOT_FOUND", error_message="Codex 运行不存在。")
    _registry(request).revoke(run_id)
    return {"revoked": True, "run_id": run_id}
