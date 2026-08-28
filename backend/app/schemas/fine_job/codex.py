from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class CodexPermissionsPatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool
    permissions: dict[str, bool]


class CodexPermissionsResponse(BaseModel):
    enabled: bool
    permissions: dict[str, bool]
    supported: dict[str, bool]


class CodexPendingDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)
    final_text: str = ""
    allow_override: bool = False
    note: str = ""


class CodexRuntimeResponse(BaseModel):
    run_id: str
    token: str
    expires_at: str


class CodexRuntimeCompleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["exited", "failed"]
    reason: str = Field(default="", max_length=500)


class CodexHandshakeResponse(BaseModel):
    run_id: str
    mcp_contract_version: Literal["v1"] = "v1"
    finejob_internal_api_version: Literal["v1"] = "v1"
    finejob_capabilities_version: Literal["v1"] = "v1"
    sensitive_actions_allowed: bool


class CodexToolRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    arguments: dict[str, Any] = Field(default_factory=dict)
