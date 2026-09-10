from __future__ import annotations

from fastapi import APIRouter, Depends, status

from backend.app.config import AppConfig
from backend.app.dependencies import get_config, get_database
from backend.app.db import Database
from backend.app.schemas.fine_job.workflow_runs import WorkflowRunCodexSessionRequest, WorkflowRunCreateRequest
from backend.app.services.fine_job import workflow_runs


router = APIRouter(prefix="/fine-job/workflow-runs", tags=["fine-job-workflow-runs"])


@router.post("", status_code=status.HTTP_201_CREATED)
def create(payload: WorkflowRunCreateRequest, config: AppConfig = Depends(get_config), db: Database = Depends(get_database)):
    return workflow_runs.create_deep_job_search_run(db, config, payload.deep_job_search.model_dump(), created_from=payload.created_from)


@router.get("/{workflow_run_id}")
def get(workflow_run_id: str, db: Database = Depends(get_database)):
    return workflow_runs.get_workflow_run(db, workflow_run_id)


@router.post("/{workflow_run_id}/advance")
def advance(workflow_run_id: str, config: AppConfig = Depends(get_config), db: Database = Depends(get_database)):
    return workflow_runs.advance_deep_job_search(db, config, workflow_run_id)


@router.post("/{workflow_run_id}/resume")
def resume(workflow_run_id: str, db: Database = Depends(get_database)):
    return workflow_runs.resume_deep_job_search_run(db, workflow_run_id)


@router.get("/{workflow_run_id}/context-snapshot")
def context_snapshot(workflow_run_id: str, channel: str = "deep_job_search", db: Database = Depends(get_database)):
    return workflow_runs.get_context_snapshot(db, workflow_run_id, channel)


@router.patch("/{workflow_run_id}/codex-session")
def attach_codex_session(workflow_run_id: str, payload: WorkflowRunCodexSessionRequest, db: Database = Depends(get_database)):
    return workflow_runs.attach_codex_session(db, workflow_run_id, payload.codex_session_ref, payload.codex_runtime_id)
