from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


PlatformName = Literal["boss"]
PlatformSessionStatus = Literal["ready", "needs_login", "invalid"]


class FineJobPlatformSessionPayload(BaseModel):
    platform: PlatformName = "boss"
    display_name: str = "BOSS 直聘"
    login_url: str = "https://www.zhipin.com/"
    browser_profile: str = ""
    browser_channel: str = "chrome"
    status: PlatformSessionStatus = "needs_login"
    status_detail: str = ""


class FineJobPlatformSessionResponse(FineJobPlatformSessionPayload):
    ready: bool
    last_checked_at: str | None = None
    created_at: str
    updated_at: str


class FineJobPlatformSessionEnvelope(BaseModel):
    session: FineJobPlatformSessionResponse | None


class FineJobPlatformSessionListEnvelope(BaseModel):
    sessions: list[FineJobPlatformSessionResponse]


class FineJobPlatformLoginActionEnvelope(BaseModel):
    session: FineJobPlatformSessionResponse
    detail: str
