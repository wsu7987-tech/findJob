from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


WebSessionProfileMode = Literal["browser_profile", "app_session"]
WebSessionProfileStatus = Literal["ready", "needs_login", "invalid"]


class WebSessionProfileCreateRequest(BaseModel):
    name: str
    mode: WebSessionProfileMode
    browser_channel: str | None = None
    profile_path: str | None = None
    login_url: str | None = None


class WebSessionProfileUpdateRequest(BaseModel):
    name: str | None = None
    browser_channel: str | None = None
    profile_path: str | None = None
    login_url: str | None = None


class WebSessionProfileLoginRequest(BaseModel):
    login_url: str | None = None


class WebSessionProfileResponse(BaseModel):
    id: str
    name: str
    mode: WebSessionProfileMode
    browser_channel: str
    profile_path: str | None = None
    managed_profile_path: str | None = None
    login_url: str | None = None
    status: WebSessionProfileStatus
    status_detail: str
    created_at: str
    updated_at: str


class WebSessionProfileEnvelope(BaseModel):
    profile: WebSessionProfileResponse


class WebSessionProfileListEnvelope(BaseModel):
    profiles: list[WebSessionProfileResponse]


class WebSessionProfileDeleteResponse(BaseModel):
    deleted: bool
