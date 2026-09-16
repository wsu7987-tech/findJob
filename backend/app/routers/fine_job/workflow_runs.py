from __future__ import annotations

import json
from collections.abc import Iterator
from queue import Empty

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import StreamingResponse

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
    WorkflowManualAnalysisBatchRequest,
    WorkflowRunCodexSessionRequest,
    WorkflowRunCreateRequest,
)
from backend.app.services.fine_job import workflow_runs
from backend.app.services.fine_job.workflow_run_events import workflow_run_event_broker
from backend.app.services.fine_job.codex_tools import CodexToolService


router = APIRouter(prefix="/fine-job/workflow-runs", tags=["fine-job-workflow-runs"])


@router.post("", status_code=status.HTTP_201_CREATED)
def create(payload: WorkflowRunCreateRequest, config: AppConfig = Depends(get_config), db: Database = Depends(get_database)):
    workflow_runs.configure_realtime_runtime(db, config)
    return workflow_runs.create_deep_job_search_run(db, config, payload.deep_job_search.model_dump(), created_from=payload.created_from)


@router.get("/latest")
def latest(
    include_completed: bool = Query(default=False),
    created_from: str | None = Query(default=None, max_length=80),
    db: Database = Depends(get_database),
):
    return {
        "workflow_run": workflow_runs.get_latest_workflow_run(
            db,
            include_completed=include_completed,
            created_from=created_from,
        )
    }


@router.get("/collection-active")
def collection_active(db: Database = Depends(get_database)):
    """供两个采集入口在创建任务前读取统一互斥状态。"""
    return {"active_task": workflow_runs.get_active_collection_task(db)}


@router.get("/{workflow_run_id}")
def get(workflow_run_id: str, db: Database = Depends(get_database)):
    return workflow_runs.get_workflow_run(db, workflow_run_id)


@router.get("/{workflow_run_id}/events")
def events(workflow_run_id: str, db: Database = Depends(get_database)) -> StreamingResponse:
    initial_snapshot = workflow_runs.get_workflow_run(db, workflow_run_id)
    subscriber = workflow_run_event_broker.subscribe(workflow_run_id)

    def event_stream() -> Iterator[str]:
        try:
            yield f"data: {json.dumps(initial_snapshot, ensure_ascii=False)}\\n\\n"
            while True:
                try:
                    snapshot = subscriber.get(timeout=15)
                except Empty:
                    yield ": heartbeat\\n\\n"
                    continue
                yield f"data: {json.dumps(snapshot, ensure_ascii=False)}\\n\\n"
        finally:
            workflow_run_event_broker.unsubscribe(workflow_run_id, subscriber)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/{workflow_run_id}/capture-jobs")
def capture_jobs(workflow_run_id: str, db: Database = Depends(get_database)):
    return workflow_runs.list_workflow_capture_jobs(db, workflow_run_id)


@router.post("/{workflow_run_id}/advance")
def advance(workflow_run_id: str, config: AppConfig = Depends(get_config), db: Database = Depends(get_database)):
    workflow_runs.configure_realtime_runtime(db, config)
    return workflow_runs.advance_deep_job_search(db, config, workflow_run_id)


@router.post("/{workflow_run_id}/resume")
def resume(
    workflow_run_id: str,
    config: AppConfig = Depends(get_config),
    db: Database = Depends(get_database),
):
    workflow_runs.configure_realtime_runtime(db, config)
    return workflow_runs.resume_deep_job_search_run(db, config, workflow_run_id)


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


@router.post("/{workflow_run_id}/analysis-batches")
def create_manual_analysis_batch(
    workflow_run_id: str,
    payload: WorkflowManualAnalysisBatchRequest,
    config: AppConfig = Depends(get_config),
    db: Database = Depends(get_database),
):
    return workflow_runs.create_manual_analysis_batch(
        db,
        config,
        workflow_run_id,
        recommendation_strategy_id=payload.recommendation_strategy_id,
        job_ids=payload.job_ids,
        analysis_batch_size=payload.analysis_batch_size,
        codex_model=payload.codex_model,
        codex_reasoning_effort=payload.codex_reasoning_effort,
    )


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
    config: AppConfig = Depends(get_config),
    db: Database = Depends(get_database),
):
    return workflow_runs.ack_workflow_analysis_batch_started(
        db, workflow_run_id, payload.analysis_batch_id, payload.handoff_attempt_id, config
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
