from __future__ import annotations

from fastapi import APIRouter, Depends, status

from backend.app.config import AppConfig
from backend.app.db import Database
from backend.app.dependencies import get_config, get_database
from backend.app.schemas.pool import SummaryPrecheckResponse, SummaryRunRequest
from backend.app.schemas.runs import SummaryRunCreateResponse
from backend.app.services.runs import build_summary_precheck, create_summary_run


router = APIRouter(prefix="/summary", tags=["summary"])


@router.get("/precheck", response_model=SummaryPrecheckResponse)
def summary_precheck(
    db: Database = Depends(get_database),
    config: AppConfig = Depends(get_config),
) -> SummaryPrecheckResponse:
    return SummaryPrecheckResponse(**build_summary_precheck(db, config))


@router.post(
    "/runs",
    response_model=SummaryRunCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_summary(
    payload: SummaryRunRequest,
    db: Database = Depends(get_database),
    config: AppConfig = Depends(get_config),
) -> SummaryRunCreateResponse:
    run = create_summary_run(db, config, payload.pool_ids)
    return SummaryRunCreateResponse(
        run_id=run["run_id"],
        status=run["status"],
        stage=run["stage"],
    )
