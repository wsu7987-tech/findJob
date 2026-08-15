from __future__ import annotations

from fastapi import APIRouter, Depends, status

from backend.app.config import AppConfig
from backend.app.db import Database
from backend.app.dependencies import get_config, get_database
from backend.app.schemas.report import (
    ReportPrecheckResponse,
    ReportRunCreateResponse,
    ReportRunRequest,
    ReportVersionDetailResponse,
    ReportVersionListResponse,
)
from backend.app.services.report import (
    build_report_precheck,
    create_report_run,
    get_report_version,
    list_report_versions,
)


report_router = APIRouter(prefix="/report", tags=["report"])
reports_router = APIRouter(prefix="/reports", tags=["reports"])


@report_router.get("/precheck", response_model=ReportPrecheckResponse)
def read_report_precheck(
    db: Database = Depends(get_database),
    config: AppConfig = Depends(get_config),
) -> ReportPrecheckResponse:
    return ReportPrecheckResponse(**build_report_precheck(db, config))


@report_router.post(
    "/runs",
    response_model=ReportRunCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_report(
    payload: ReportRunRequest,
    db: Database = Depends(get_database),
    config: AppConfig = Depends(get_config),
) -> ReportRunCreateResponse:
    return ReportRunCreateResponse(**create_report_run(db, config, payload))


@reports_router.get("/{week_key}/versions", response_model=ReportVersionListResponse)
def read_report_versions(
    week_key: str,
    db: Database = Depends(get_database),
) -> ReportVersionListResponse:
    return ReportVersionListResponse(items=list_report_versions(db, week_key))


@reports_router.get(
    "/{week_key}/versions/{version}",
    response_model=ReportVersionDetailResponse,
)
def read_report_version(
    week_key: str,
    version: int,
    db: Database = Depends(get_database),
) -> ReportVersionDetailResponse:
    return ReportVersionDetailResponse(**get_report_version(db, week_key, version))
