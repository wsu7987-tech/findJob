from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class AppConfigResponse(BaseModel):
    # FineJob 保留的契约：桌面端设置使用这些字段配置 LLM/Embedding，
    # 并执行本地运行时就绪检查。
    app_data_dir: str
    sqlite_path: str
    qdrant_path: str
    output_root: str
    summary_output_dir: str
    report_output_dir: str
    llm_provider: str | None
    llm_model: str | None
    llm_base_url: str | None
    llm_api_key: str | None
    llm_configured: bool
    embedding_provider: str | None
    embedding_model: str | None
    embedding_base_url: str | None
    embedding_api_key: str | None
    embedding_configured: bool
    fetch_concurrency: int
    llm_concurrency: int
    embedding_concurrency: int
    fetch_timeout_seconds: int
    llm_timeout_seconds: int
    embedding_timeout_seconds: int
    fetch_user_agent: str
    quick_capture_hotkey: str | None
    quick_capture_screenshot_hotkey: str | None
    close_to_tray: bool
    quick_capture_always_on_top: bool
    reasoning_executor: Literal["llm", "codex-cli"]
    codex_cli_path: str
    codex_model: str | None
    codex_reasoning_effort: Literal["minimal", "low", "medium", "high", "xhigh"] | None
    codex_timeout_seconds: int


class AppConfigPatchRequest(BaseModel):
    # FineJob 保留的契约：PATCH /api/config 持久化模型服务商、密钥、
    # 端点和投递启动前使用的本地运行时设置。
    model_config = ConfigDict(extra="forbid")

    app_data_dir: str | None = None
    sqlite_path: str | None = None
    qdrant_path: str | None = None
    output_root: str | None = None
    summary_output_dir: str | None = None
    report_output_dir: str | None = None
    llm_provider: str | None = None
    llm_model: str | None = None
    llm_base_url: str | None = None
    llm_api_key: str | None = None
    embedding_provider: str | None = None
    embedding_model: str | None = None
    embedding_base_url: str | None = None
    embedding_api_key: str | None = None
    fetch_concurrency: int | None = Field(default=None, ge=1)
    llm_concurrency: int | None = Field(default=None, ge=1)
    embedding_concurrency: int | None = Field(default=None, ge=1)
    fetch_timeout_seconds: int | None = Field(default=None, ge=1)
    llm_timeout_seconds: int | None = Field(default=None, ge=1)
    embedding_timeout_seconds: int | None = Field(default=None, ge=1)
    fetch_user_agent: str | None = None
    quick_capture_hotkey: str | None = None
    quick_capture_screenshot_hotkey: str | None = None
    close_to_tray: bool | None = None
    quick_capture_always_on_top: bool | None = None
    reasoning_executor: Literal["llm", "codex-cli"] | None = None
    codex_cli_path: str | None = None
    codex_model: str | None = None
    codex_reasoning_effort: Literal["minimal", "low", "medium", "high", "xhigh"] | None = None
    codex_timeout_seconds: int | None = Field(default=None, ge=1, le=3600)


class ProviderConnectivityCheckResponse(BaseModel):
    # FineJob 保留的契约：供桌面端配置界面检查必需的 LLM 和可选的 Embedding 连通性。
    capability: Literal["llm", "embedding"]
    ok: bool
    status: Literal["ready", "failed", "invalid"]
    provider: str | None
    model: str | None
    base_url: str | None
    detail: str
    error_category: str | None = None
    checked_at: str


class CodexConnectivityCheckResponse(BaseModel):
    capability: Literal["codex-cli"]
    ok: bool
    status: Literal["ready", "failed", "invalid"]
    cli_path: str | None
    cli_version: str | None
    authenticated: bool
    model: str | None
    reasoning_effort: str | None
    detail: str
    error_category: str | None = None
    checked_at: str


class CodexModelListRequest(BaseModel):
    # 只使用本机 Codex CLI 配置和登录状态，不读取登录凭据。
    cli_path: str = "codex"


class CodexModelItem(BaseModel):
    id: str
    label: str | None = None
    reasoning_efforts: list[str] = Field(default_factory=list)


class CodexModelListResponse(BaseModel):
    capability: Literal["codex-models"]
    models: list[CodexModelItem]
    fetched_at: str
