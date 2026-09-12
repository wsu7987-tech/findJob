from __future__ import annotations

from fastapi import APIRouter, Depends, status

from backend.app.config import AppConfig
from backend.app.dependencies import get_config, get_database
from backend.app.db import Database
from backend.app.schemas.fine_job.workflow_runs import (
    WorkflowAnalysisSaveRequest,
    WorkflowAnalysisHandoffClaimRequest,
    WorkflowAnalysisHandoffRequest,
    WorkflowAnalysisHandoffStartAckRequest,
    WorkflowAnalysisFeedbackRequest,
    WorkflowAnalysisGuidanceUpdateRequest,
    WorkflowRunCodexSessionRequest,
    WorkflowRunCreateRequest,
)
from backend.app.services.fine_job import workflow_runs
from backend.app.services.fine_job.codex_tools import CodexToolService


router = APIRouter(prefix="/fine-job/workflow-runs", tags=["fine-job-workflow-runs"])


@router.post("", status_code=status.HTTP_201_CREATED)
def create(payload: WorkflowRunCreateRequest, config: AppConfig = Depends(get_config), db: Database = Depends(get_database)):
    return workflow_runs.create_deep_job_search_run(db, config, payload.deep_job_search.model_dump(), created_from=payload.created_from)


@router.get("/latest")
def latest(db: Database = Depends(get_database)):
    return {"workflow_run": workflow_runs.get_latest_active_workflow_run(db)}


@router.get("/{workflow_run_id}")
def get(workflow_run_id: str, db: Database = Depends(get_database)):
    return workflow_runs.get_workflow_run(db, workflow_run_id)


@router.post("/{workflow_run_id}/advance")
def advance(workflow_run_id: str, config: AppConfig = Depends(get_config), db: Database = Depends(get_database)):
    return workflow_runs.advance_deep_job_search(db, config, workflow_run_id)


@router.post("/{workflow_run_id}/resume")
def resume(workflow_run_id: str, db: Database = Depends(get_database)):
    return workflow_runs.resume_deep_job_search_run(db, workflow_run_id)


@router.post("/{workflow_run_id}/pause")
def pause(workflow_run_id: str, db: Database = Depends(get_database)):
    return workflow_runs.pause_deep_job_search_run(db, workflow_run_id)


@router.post("/{workflow_run_id}/cancel")
def cancel(workflow_run_id: str, db: Database = Depends(get_database)):
    return workflow_runs.cancel_deep_job_search_run(db, workflow_run_id)


@router.get("/{workflow_run_id}/context-snapshot")
def context_snapshot(workflow_run_id: str, channel: str = "deep_job_search", db: Database = Depends(get_database)):
    return workflow_runs.get_context_snapshot(db, workflow_run_id, channel)


@router.get("/{workflow_run_id}/analysis-items")
def list_analysis_items(workflow_run_id: str, db: Database = Depends(get_database)):
    return workflow_runs.list_workflow_analysis_items(db, workflow_run_id)


@router.get("/{workflow_run_id}/analysis-items/{workflow_task_id}/context")
def analysis_item_context(workflow_run_id: str, workflow_task_id: str, db: Database = Depends(get_database)):
    return workflow_runs.get_workflow_analysis_item_context(db, workflow_run_id, workflow_task_id)


@router.post("/{workflow_run_id}/analysis-items/{workflow_task_id}/feedback")
def save_analysis_feedback(
    workflow_run_id: str,
    workflow_task_id: str,
    payload: WorkflowAnalysisFeedbackRequest,
    db: Database = Depends(get_database),
):
    return workflow_runs.save_workflow_analysis_feedback(
        db, workflow_run_id, workflow_task_id, payload.model_dump()
    )


@router.post("/{workflow_run_id}/analysis-items/{workflow_task_id}/save")
def save_analysis_item(
    workflow_run_id: str,
    workflow_task_id: str,
    payload: WorkflowAnalysisSaveRequest,
    config: AppConfig = Depends(get_config),
    db: Database = Depends(get_database),
):
    result = CodexToolService(db, config).save_workflow_analysis_item(
        {"workflow_run_id": workflow_run_id, "workflow_task_id": workflow_task_id, **payload.model_dump()}
    )
    return result["data"]


@router.patch("/{workflow_run_id}/codex-session")
def attach_codex_session(workflow_run_id: str, payload: WorkflowRunCodexSessionRequest, db: Database = Depends(get_database)):
    return workflow_runs.attach_codex_session(db, workflow_run_id, payload.codex_session_ref, payload.codex_runtime_id)


@router.post("/{workflow_run_id}/analysis-handoff/claim")
def claim_analysis_handoff(
    workflow_run_id: str,
    payload: WorkflowAnalysisHandoffClaimRequest,
    db: Database = Depends(get_database),
):
    return workflow_runs.claim_workflow_analysis_handoff(
        db,
        workflow_run_id,
        codex_session_ref=payload.codex_session_ref,
        codex_runtime_id=payload.codex_runtime_id,
        handoff_kind=payload.handoff_kind,
        retry_handoff_attempt_id=payload.retry_handoff_attempt_id,
    )


@router.post("/{workflow_run_id}/analysis-handoff/prompt-written")
def mark_analysis_handoff_prompt_written(
    workflow_run_id: str,
    payload: WorkflowAnalysisHandoffRequest,
    db: Database = Depends(get_database),
):
    return workflow_runs.mark_workflow_analysis_handoff_prompt_written(
        db, workflow_run_id, payload.analysis_batch_id, payload.handoff_attempt_id, payload.codex_session_ref
    )


@router.post("/{workflow_run_id}/analysis-handoff/ack-started")
def ack_analysis_handoff_started(
    workflow_run_id: str,
    payload: WorkflowAnalysisHandoffStartAckRequest,
    db: Database = Depends(get_database),
):
    return workflow_runs.ack_workflow_analysis_batch_started(
        db, workflow_run_id, payload.analysis_batch_id, payload.handoff_attempt_id
    )


@router.post("/{workflow_run_id}/analysis-handoff/release")
def release_analysis_handoff(
    workflow_run_id: str,
    payload: WorkflowAnalysisHandoffRequest,
    db: Database = Depends(get_database),
):
    return workflow_runs.release_workflow_analysis_handoff(
        db,
        workflow_run_id,
        payload.analysis_batch_id,
        payload.handoff_attempt_id,
        payload.codex_session_ref,
        payload.release_reason,
    )


@router.patch("/{workflow_run_id}/analysis-guidance")
def update_analysis_guidance(
    workflow_run_id: str,
    payload: WorkflowAnalysisGuidanceUpdateRequest,
    db: Database = Depends(get_database),
):
    return workflow_runs.update_workflow_analysis_guidance(
        db, workflow_run_id, payload.analysis_guidance
    )
