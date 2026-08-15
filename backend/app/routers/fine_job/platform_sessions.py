from __future__ import annotations

from fastapi import APIRouter, Depends

from backend.app.config import AppConfig
from backend.app.db import Database
from backend.app.dependencies import get_config, get_database
from backend.app.schemas.fine_job.platform_sessions import (
    FineJobPlatformLoginActionEnvelope,
    FineJobPlatformSessionEnvelope,
    FineJobPlatformSessionListEnvelope,
    FineJobPlatformSessionPayload,
    PlatformName,
)
from backend.app.services.fine_job.platform_sessions import (
    check_boss_login_status,
    get_platform_session,
    list_platform_sessions,
    open_boss_login_window,
    save_platform_session,
)


router = APIRouter(prefix="/fine-job/platform-sessions", tags=["fine-job-platform-sessions"])


@router.get("", response_model=FineJobPlatformSessionListEnvelope)
def list_fine_job_platform_sessions(
    db: Database = Depends(get_database),
) -> FineJobPlatformSessionListEnvelope:
    return FineJobPlatformSessionListEnvelope(sessions=list_platform_sessions(db))


@router.get("/{platform}", response_model=FineJobPlatformSessionEnvelope)
def get_fine_job_platform_session(
    platform: PlatformName,
    db: Database = Depends(get_database),
) -> FineJobPlatformSessionEnvelope:
    return FineJobPlatformSessionEnvelope(session=get_platform_session(db, platform))


@router.put("/{platform}", response_model=FineJobPlatformSessionEnvelope)
def save_fine_job_platform_session(
    platform: PlatformName,
    payload: FineJobPlatformSessionPayload,
    db: Database = Depends(get_database),
) -> FineJobPlatformSessionEnvelope:
    return FineJobPlatformSessionEnvelope(
        session=save_platform_session(db, payload.model_copy(update={"platform": platform}))
    )


@router.post("/boss/login-window", response_model=FineJobPlatformLoginActionEnvelope)
def open_fine_job_boss_login_window(
    db: Database = Depends(get_database),
    config: AppConfig = Depends(get_config),
) -> FineJobPlatformLoginActionEnvelope:
    session = open_boss_login_window(db=db, config=config)
    return FineJobPlatformLoginActionEnvelope(
        session=session,
        detail="BOSS 登录窗口已打开。",
    )


@router.post("/boss/check", response_model=FineJobPlatformLoginActionEnvelope)
def check_fine_job_boss_login_status(
    db: Database = Depends(get_database),
    config: AppConfig = Depends(get_config),
) -> FineJobPlatformLoginActionEnvelope:
    session = check_boss_login_status(db=db, config=config)
    return FineJobPlatformLoginActionEnvelope(
        session=session,
        detail=str(session["status_detail"]),
    )
