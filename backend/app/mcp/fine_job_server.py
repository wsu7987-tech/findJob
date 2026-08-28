from __future__ import annotations

import os
import sys
from typing import Any

import httpx
from mcp.server.mcpserver import MCPServer


MCP_CONTRACT_VERSION = "v1"
INTERNAL_API_VERSION = "v1"
BACKEND_ORIGIN = os.environ.get("FINE_JOB_BACKEND_ORIGIN", "http://127.0.0.1:8000").rstrip("/")
RUN_TOKEN = os.environ.get("FINE_JOB_MCP_RUN_TOKEN", "")
_handshake_complete = False

server = MCPServer(
    name="finejob",
    title="FineJob 业务能力",
    version="1.0.0",
    instructions=(
        "先调用 finejob.get_capabilities 确认运行状态。岗位评估流程依次读取岗位上下文和简历事实，"
        "保存评估后再生成打招呼预览。发送打招呼或代聊回复前必须使用最新资源版本请求执行；"
        "返回 awaiting_confirmation 时等待用户在 FineJob 确认卡片处理，返回任务或动作资源时使用 "
        "finejob.get_operation_status 查询结果。不得绕过 FineJob 的确认、版本与队列状态。"
    ),
    log_level="ERROR",
)


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {RUN_TOKEN}",
        "X-FineJob-MCP-Contract-Version": MCP_CONTRACT_VERSION,
        "X-FineJob-Internal-API-Version": INTERNAL_API_VERSION,
    }


async def _invoke(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """统一通过本机内部 API 调用 FineJob 领域服务。"""
    if not RUN_TOKEN:
        return _error("CODEX_RUNTIME_UNAUTHORIZED", "缺少 FineJob MCP 运行凭证。")
    try:
        # MCP 只访问本机后端，绕过系统代理，避免代理将回环请求转成 502。
        async with httpx.AsyncClient(timeout=30.0, trust_env=False) as client:
            handshake_error = await _ensure_handshake(client)
            if handshake_error:
                return handshake_error
            response = await client.post(
                f"{BACKEND_ORIGIN}/api/internal/codex/v1/tools/{tool_name}",
                headers=_headers(),
                json={"arguments": arguments},
            )
        if response.is_success:
            return response.json()
        return _error_from_response(
            response,
            "FINEJOB_API_ERROR",
            "FineJob 内部 API 调用失败。",
        )
    except (httpx.HTTPError, ValueError) as exc:
        print(f"FineJob MCP 调用失败：{exc}", file=sys.stderr)
        return _error("FINEJOB_BACKEND_UNAVAILABLE", f"FineJob 后端当前不可用：{exc}")


async def _ensure_handshake(client: httpx.AsyncClient) -> dict[str, Any] | None:
    """首个工具调用前确认本次运行身份和协议版本。"""
    global _handshake_complete
    if _handshake_complete:
        return None
    response = await client.post(
        f"{BACKEND_ORIGIN}/api/internal/codex/v1/handshake",
        headers=_headers(),
    )
    if not response.is_success:
        return _error_from_response(
            response,
            "CODEX_HANDSHAKE_FAILED",
            "FineJob MCP 握手失败，请重新启动 Codex 会话。",
        )
    payload = response.json()
    if (
        payload.get("mcp_contract_version") != MCP_CONTRACT_VERSION
        or payload.get("finejob_internal_api_version") != INTERNAL_API_VERSION
    ):
        return _error("CODEX_CONTRACT_INCOMPATIBLE", "FineJob MCP 合同版本不兼容。")
    _handshake_complete = True
    return None


def _error_from_response(
    response: httpx.Response,
    fallback_category: str,
    fallback_message: str,
) -> dict[str, Any]:
    """保留内部 API 返回的业务错误，便于会话直接显示可处理原因。"""
    try:
        payload = response.json()
    except ValueError:
        payload = {}

    detail = payload.get("detail", payload) if isinstance(payload, dict) else {}
    if not isinstance(detail, dict):
        detail = {}
    category = str(detail.get("error_category") or detail.get("category") or fallback_category)
    message = str(detail.get("error_message") or detail.get("message") or fallback_message)
    if message == fallback_message:
        message = f"{fallback_message}（HTTP {response.status_code}）"
    return _error(category, message, status_code=response.status_code)


def _error(category: str, message: str, *, status_code: int | None = None) -> dict[str, Any]:
    return {
        "ok": False,
        "result_type": "error",
        "resource": None,
        "status": "failed",
        "terminal": True,
        "requires_confirmation": False,
        "sensitive_operation": False,
        "authorization_mode": None,
        "message": message,
        "data": None,
        "error": {"category": category, "message": message, "status_code": status_code},
    }


@server.tool(name="finejob.get_capabilities", structured_output=True)
async def get_capabilities() -> dict[str, Any]:
    """读取 FineJob 可用能力、业务运行状态和后续预留能力。"""
    return await _invoke("get_capabilities", {})


@server.tool(name="finejob.search_jobs", structured_output=True)
async def search_jobs(
    query: str = "", city: str = "", status: str = "", page: int = 1, page_size: int = 20
) -> dict[str, Any]:
    """检索 FineJob 已采集岗位，可按关键词、城市和详情状态筛选。"""
    return await _invoke("search_jobs", locals())


@server.tool(name="finejob.get_job_context", structured_output=True)
async def get_job_context(job_id: str, resume_id: str = "") -> dict[str, Any]:
    """读取岗位详情、版本、可用简历、既有评估和执行记录。"""
    return await _invoke("get_job_context", locals())


@server.tool(name="finejob.collect_job_detail", structured_output=True)
async def collect_job_detail(job_id: str) -> dict[str, Any]:
    """为已采集岗位创建详情采集任务。"""
    return await _invoke("collect_job_detail", locals())


@server.tool(name="finejob.list_resumes", structured_output=True)
async def list_resumes() -> dict[str, Any]:
    """列出 FineJob 简历及其事实版本。"""
    return await _invoke("list_resumes", {})


@server.tool(name="finejob.get_resume_facts", structured_output=True)
async def get_resume_facts(resume_id: str) -> dict[str, Any]:
    """读取指定简历的结构化事实与当前版本。"""
    return await _invoke("get_resume_facts", locals())


@server.tool(name="finejob.list_chat_sessions", structured_output=True)
async def list_chat_sessions(status: str = "", page: int = 1, page_size: int = 20) -> dict[str, Any]:
    """列出 BOSS 代聊会话，可按状态分页。"""
    return await _invoke("list_chat_sessions", locals())


@server.tool(name="finejob.get_chat_context", structured_output=True)
async def get_chat_context(session_id: str) -> dict[str, Any]:
    """读取会话消息、最新入站消息、会话版本和当前回复草稿。"""
    return await _invoke("get_chat_context", locals())


@server.tool(name="finejob.save_job_evaluation", structured_output=True)
async def save_job_evaluation(
    job_id: str,
    resume_id: str,
    job_detail_version: int,
    resume_facts_version: int,
    conclusion: str,
    confidence: float = 0,
    reasons: list[str] | None = None,
    risks: list[str] | None = None,
    matches: list[str] | None = None,
    gaps: list[str] | None = None,
    suggestion: str = "",
) -> dict[str, Any]:
    """基于明确的岗位和简历版本保存结构化评估。"""
    return await _invoke("save_job_evaluation", locals())


@server.tool(name="finejob.create_greeting_preview", structured_output=True)
async def create_greeting_preview(
    job_id: str, evaluation_id: str, job_detail_version: int, text: str
) -> dict[str, Any]:
    """基于最新岗位评估创建待确认的打招呼预览。"""
    return await _invoke("create_greeting_preview", locals())


@server.tool(name="finejob.save_chat_reply_draft", structured_output=True)
async def save_chat_reply_draft(
    session_id: str,
    session_version: int,
    latest_message_id: str,
    text: str,
    reply_task_id: str = "",
) -> dict[str, Any]:
    """基于最新会话版本创建或更新代聊回复草稿。"""
    return await _invoke("save_chat_reply_draft", locals())


@server.tool(name="finejob.request_greeting_execution", structured_output=True)
async def request_greeting_execution(preview_id: str, expected_version: int) -> dict[str, Any]:
    """请求执行打招呼预览；FineJob 依据敏感策略决定是否需要页面确认。"""
    return await _invoke("request_greeting_execution", locals())


@server.tool(name="finejob.request_chat_send", structured_output=True)
async def request_chat_send(
    reply_task_id: str,
    text_version: int,
    session_version: int,
    latest_message_id: str,
) -> dict[str, Any]:
    """请求发送代聊回复；FineJob 会校验文本、会话和最新消息版本。"""
    return await _invoke("request_chat_send", locals())


@server.tool(name="finejob.get_operation_status", structured_output=True)
async def get_operation_status(resource_type: str, resource_id: str) -> dict[str, Any]:
    """查询 FineJob 任务、确认项、评估或发送动作的当前状态。"""
    return await _invoke("get_operation_status", locals())


def main() -> None:
    """以 stdio 方式启动，标准输出仅供 MCP 协议使用。"""
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
