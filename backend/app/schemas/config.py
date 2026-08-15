from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class AppConfigResponse(BaseModel):
    # FineJob-retained contract: desktop settings uses these fields for
    # LLM/Embedding configuration and local runtime readiness checks.
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


class AppConfigPatchRequest(BaseModel):
    # FineJob-retained contract: PATCH /api/config persists model provider,
    # key, endpoint, and local runtime settings used before delivery can start.
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


class ProviderConnectivityCheckResponse(BaseModel):
    # FineJob-retained contract: used by the desktop configuration UI to test
    # required LLM and optional Embedding connectivity.
    capability: Literal["llm", "embedding"]
    ok: bool
    status: Literal["ready", "failed", "invalid"]
    provider: str | None
    model: str | None
    base_url: str | None
    detail: str
    error_category: str | None = None
    checked_at: str
