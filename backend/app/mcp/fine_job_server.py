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
        "先调用 finejob.get_capabilities 确认运行状态。候选人简历分析先取得 V2 分析计划，"
        "按操作顺序逐项读取输入并按返回 schema 保存结果；确定内容直接入库，疑点进入待处理清单。"
        "岗位采集与投递建议任务先读取现有策略，并在一次会话中只读取一次统一 V3 岗位评估上下文；"
        "首次采集使用一页，完成筛选后按结果决定是否调用 finejob.continue_job_capture；续采必须沿用同一采集任务和原搜索页，"
        "每次续采后重新筛选新增结果，达到任务目标或没有更多岗位时停止，用户要求停止时调用 finejob.stop_job_capture。"
        "每个岗位只读取当前 JD，保存投递建议时携带同一上下文修订、所用策略和最新 JD 版本。"
        "岗位采集和详情获取只依赖 FineJob 专用 Chrome；BOSS 执行器及其权限、队列状态只用于审批后的打招呼动作，"
        "不得用执行器离线或 risk_paused 阻断采集、筛选、JD 获取和建议生成。"
        "岗位评估也可继续使用原有岗位上下文和候选人上下文入口，"
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
    query: str = "",
    search_keyword: str = "",
    city: str = "",
    status: str = "",
    page: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    """检索 FineJob 已采集岗位，可按采集搜索词、城市和详情状态筛选。"""
    return await _invoke("search_jobs", locals())


@server.tool(name="finejob.list_companies", structured_output=True)
async def list_companies(
    query: str = "",
    company_type: str = "",
    blacklist_status: str = "all",
    page: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    """检索公司主档、外包标记、黑名单状态及近期岗位事件。"""
    return await _invoke("list_companies", locals())


@server.tool(name="finejob.set_company_type", structured_output=True)
async def set_company_type(
    company_name: str,
    company_type: str,
    notes: str = "",
    aliases: list[str] | None = None,
) -> dict[str, Any]:
    """按公司名称录入直招、外包或待确认标记，并可同时登记别名。"""
    return await _invoke("set_company_type", locals())


@server.tool(name="finejob.set_company_blacklist", structured_output=True)
async def set_company_blacklist(
    blacklisted: bool = True,
    company_id: str = "",
    company_name: str = "",
    reason: str = "",
) -> dict[str, Any]:
    """按公司编号或名称更新全局公司黑名单。"""
    return await _invoke("set_company_blacklist", locals())


@server.tool(name="finejob.record_job_application", structured_output=True)
async def record_job_application(
    job_id: str,
    applied: bool = True,
    applied_at: str = "",
    note: str = "",
) -> dict[str, Any]:
    """录入或撤销岗位已投递事实，并立即更新筛选冷却清单。"""
    return await _invoke("record_job_application", locals())


@server.tool(name="finejob.list_job_strategies", structured_output=True)
async def list_job_strategies(enabled_only: bool = True) -> dict[str, Any]:
    """列出已有岗位筛选策略、建议投递策略及其搜索词。"""
    return await _invoke("list_job_strategies", locals())


@server.tool(name="finejob.get_job_evaluation_context", structured_output=True)
async def get_job_evaluation_context(
    recommendation_strategy_id: str,
    filter_strategy_id: str = "",
    context_stale_action: str = "",
) -> dict[str, Any]:
    """按具体简历组装一次统一 V3 岗位评估上下文，并处理上下文过期选择。"""
    return await _invoke("get_job_evaluation_context", locals())


@server.tool(name="finejob.start_job_capture", structured_output=True)
async def start_job_capture(
    filter_strategy_id: str,
    keyword: str = "",
    city: str = "",
    pages: int = 1,
) -> dict[str, Any]:
    """使用岗位筛选策略中的搜索词和城市启动现有采集任务。"""
    return await _invoke("start_job_capture", locals())


@server.tool(name="finejob.continue_job_capture", structured_output=True)
async def continue_job_capture(
    capture_task_id: str,
    pages: int = 1,
) -> dict[str, Any]:
    """沿用同一采集任务和原 BOSS 搜索页，继续下滑指定逻辑页数并追加岗位。"""
    return await _invoke("continue_job_capture", locals())


@server.tool(name="finejob.stop_job_capture", structured_output=True)
async def stop_job_capture(capture_task_id: str) -> dict[str, Any]:
    """停止当前列表下滑，保留已采集岗位、专用浏览器和原搜索页。"""
    return await _invoke("stop_job_capture", locals())


@server.tool(name="finejob.apply_job_filter", structured_output=True)
async def apply_job_filter(
    capture_task_id: str,
    filter_strategy_id: str,
) -> dict[str, Any]:
    """复用现有筛选逻辑并把最新筛选结论写入历史岗位。"""
    return await _invoke("apply_job_filter", locals())


@server.tool(name="finejob.collect_job_details", structured_output=True)
async def collect_job_details(
    capture_task_id: str,
    job_ids: list[str],
    force: bool = False,
) -> dict[str, Any]:
    """为筛选后的岗位启动现有详情采集，并覆盖保存最新 JD。"""
    return await _invoke("collect_job_details", locals())


@server.tool(name="finejob.get_job_jd", structured_output=True)
async def get_job_jd(job_id: str) -> dict[str, Any]:
    """读取一个历史岗位的当前 JD、最新筛选结论和最新投递建议。"""
    return await _invoke("get_job_jd", locals())


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


@server.tool(name="finejob.list_profiles", structured_output=True)
async def list_profiles() -> dict[str, Any]:
    """列出候选人档案和各类上下文版本。"""
    return await _invoke("list_profiles", {})


@server.tool(name="finejob.get_profile_analysis_input", structured_output=True)
async def get_profile_analysis_input(
    profile_id: str = "default",
    source_ids: list[str] | None = None,
) -> dict[str, Any]:
    """识别选定资料并返回 AI 分析提示、正文、输入版本和严格输出契约。"""
    return await _invoke("get_profile_analysis_input", locals())


@server.tool(name="finejob.save_profile_analysis_draft", structured_output=True)
async def save_profile_analysis_draft(
    profile_id: str,
    source_ids: list[str],
    input_versions: dict[str, int],
    output: dict[str, Any],
) -> dict[str, Any]:
    """保存符合契约的候选人资料分析草稿，等待用户逐项确认。"""
    return await _invoke("save_profile_analysis_draft", locals())


@server.tool(name="finejob.list_resume_families", structured_output=True)
async def list_resume_families(profile_id: str = "default") -> dict[str, Any]:
    """列出候选人简历组；每组包含一份基础简历及其岗位定制派生版本。"""
    return await _invoke("list_resume_families", locals())


@server.tool(name="finejob.get_resume_analysis_plan", structured_output=True)
async def get_resume_analysis_plan(
    profile_id: str = "default",
    resume_family_id: str = "",
    source_ids: list[str] | None = None,
    operation_ids: list[str] | None = None,
    run_id: str = "",
) -> dict[str, Any]:
    """创建或读取 Codex 简历分析计划，并返回排好依赖顺序的分析操作。"""
    return await _invoke("get_resume_analysis_plan", locals())


@server.tool(name="finejob.get_resume_operation_input", structured_output=True)
async def get_resume_operation_input(run_id: str, operation_id: str) -> dict[str, Any]:
    """读取单项分析的最新上下文、执行说明和严格输出结构。"""
    return await _invoke("get_resume_operation_input", locals())


@server.tool(name="finejob.save_resume_operation_result", structured_output=True)
async def save_resume_operation_result(
    run_id: str,
    operation_id: str,
    output: dict[str, Any],
) -> dict[str, Any]:
    """保存一项 V2 分析结果，并更新正式资料、策略或待处理问题。"""
    return await _invoke("save_resume_operation_result", locals())


@server.tool(name="finejob.get_resume_analysis_run", structured_output=True)
async def get_resume_analysis_run(run_id: str) -> dict[str, Any]:
    """读取 V2 简历分析任务及所有单项节点状态。"""
    return await _invoke("get_resume_analysis_run", locals())


@server.tool(name="finejob.list_resume_family_strategies", structured_output=True)
async def list_resume_family_strategies(resume_family_id: str) -> dict[str, Any]:
    """读取简历组的岗位筛选策略、建议投递策略、搜索词和待处理问题。"""
    return await _invoke("list_resume_family_strategies", locals())


@server.tool(name="finejob.get_profile_context", structured_output=True)
async def get_profile_context(
    profile_id: str = "default",
    view: str = "full",
    job_id: str = "",
    role_family: str = "",
    resume_family_id: str = "",
) -> dict[str, Any]:
    """读取后端统一生成并按场景脱敏的候选人 Markdown 上下文。"""
    return await _invoke("get_profile_context", locals())


@server.tool(name="finejob.list_profile_questions", structured_output=True)
async def list_profile_questions(profile_id: str = "default") -> dict[str, Any]:
    """读取动态 QA、确认状态和通用/岗位族/岗位回答版本。"""
    return await _invoke("list_profile_questions", locals())


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
    job_detail_version: int,
    conclusion: str,
    profile_id: str = "",
    profile_context_version: int = 0,
    resume_version_id: str = "",
    resume_id: str = "",
    resume_facts_version: int = 0,
    confidence: float = 0,
    reasons: list[str] | None = None,
    risks: list[str] | None = None,
    matches: list[str] | None = None,
    gaps: list[Any] | None = None,
    suggestion: str = "",
    recommendation_strategy_id: str = "",
    filter_strategy_id: str = "",
    context_revision_id: str = "",
    summary: str = "",
    missing_fields: list[str] | None = None,
    missing_information: list[str] | None = None,
    hard_requirements: list[Any] | None = None,
    match_dimensions: dict[str, Any] | None = None,
    strengths: list[str] | None = None,
    resume_suggestions: list[Any] | None = None,
    greeting_draft: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """保存结构化岗位评估；传入建议投递策略时复用现有历史写入和审批路由。"""
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
    profile_id: str = "default",
    profile_context_version: int = 0,
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
