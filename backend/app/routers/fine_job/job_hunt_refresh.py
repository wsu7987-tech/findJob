from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status

from backend.app.db import Database
from backend.app.dependencies import get_database
from backend.app.schemas.fine_job.job_hunt_refresh import (
    JobHuntRefreshCodexSessionRequest,
    JobHuntRefreshScopeDiscoveryRequest,
    JobHuntRefreshRunCreateRequest,
)
from backend.app.services.fine_job import job_hunt_refresh


router = APIRouter(
    prefix="/fine-job/job-hunt-refresh",
    tags=["fine-job-job-hunt-refresh"],
)


@router.get("/context")
def context(db: Database = Depends(get_database)):
    return job_hunt_refresh.get_refresh_context(db)


@router.post("/scopes", status_code=status.HTTP_201_CREATED)
def discover_scope(
    payload: JobHuntRefreshScopeDiscoveryRequest,
    db: Database = Depends(get_database),
):
    return job_hunt_refresh.discover_scope(db, payload.selected_since_time)


@router.get("/scopes/{scope_id}")
def scope(scope_id: str, db: Database = Depends(get_database)):
    return job_hunt_refresh.get_scope(db, scope_id)


@router.post("/runs", status_code=status.HTTP_201_CREATED)
def create_run(
    payload: JobHuntRefreshRunCreateRequest,
    db: Database = Depends(get_database),
):
    return job_hunt_refresh.create_run(
        db,
        scope_id=payload.scope_id,
        workflow_options=payload.workflow_options.model_dump(),
        trigger_source=payload.trigger_source,
    )


@router.get("/runs")
def runs(
    limit: int = Query(default=10, ge=1, le=50),
    db: Database = Depends(get_database),
):
    return {"runs": job_hunt_refresh.list_runs(db, limit=limit)}


@router.get("/runs/{run_id}")
def run(run_id: str, db: Database = Depends(get_database)):
    return job_hunt_refresh.get_run(db, run_id)


@router.patch("/runs/{run_id}/codex-session")
def attach_codex_session(
    run_id: str,
    payload: JobHuntRefreshCodexSessionRequest,
    db: Database = Depends(get_database),
):
    return job_hunt_refresh.attach_codex_session(
        db,
        run_id,
        payload.codex_session_ref,
    )
