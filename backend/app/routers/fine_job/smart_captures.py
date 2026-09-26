from __future__ import annotations

import json
from collections.abc import Iterator
from queue import Empty

from backend.app.services.fine_job.collection_start_operations import start_http_response

from fastapi import APIRouter, Depends, status
from fastapi.responses import StreamingResponse

from backend.app.config import AppConfig
from backend.app.db import Database
from backend.app.dependencies import get_config, get_database
from backend.app.schemas.fine_job.smart_captures import (
    SmartCaptureAnalysisFeedbackRequest,
    SmartCaptureAnalysisGuidanceUpdateRequest,
    SmartCaptureAnalysisSaveRequest,
    SmartCaptureCodexSessionRequest,
    SmartCaptureControlRequest,
    SmartCaptureCreateRequest,
    SmartCaptureHandoffClaimRequest,
    SmartCaptureHandoffRequest,
    SmartCaptureHandoffStartAckRequest,
    SmartCaptureManualAnalysisBatchRequest,
)
from backend.app.services.fine_job import smart_captures
from backend.app.services.fine_job import smart_capture_domain
from backend.app.services.fine_job.smart_capture_events import smart_capture_event_broker


router = APIRouter(prefix="/fine-job/smart-captures", tags=["fine-job-smart-captures"])


@router.post("", status_code=status.HTTP_201_CREATED)
@start_http_response
def create(
    payload: SmartCaptureCreateRequest,
    config: AppConfig = Depends(get_config),
    db: Database = Depends(get_database),
):
    return smart_captures.start_independent_capture(db, config, payload.model_dump())


@router.get("/current")
def current(db: Database = Depends(get_database)):
    # current 只读取持久化 Smart Capture 指针，不能与 custom 执行容量混为一谈。
    return {"smart_capture": smart_captures.get_current_smart_capture(db)}


@router.get("/current/events")
def current_events(db: Database = Depends(get_database)) -> StreamingResponse:
    subscriber = smart_capture_event_broker.subscribe_current()
    try:
        initial_snapshot = smart_captures.get_current_smart_capture(db)
    except Exception:
        smart_capture_event_broker.unsubscribe_current(subscriber)
        raise

    def event_stream() -> Iterator[str]:
        try:
            yield f"data: {json.dumps(initial_snapshot, ensure_ascii=False)}\n\n"
            while True:
                try:
                    snapshot = subscriber.get(timeout=15)
                except Empty:
                    yield ": heartbeat\n\n"
                    continue
                yield f"data: {json.dumps(snapshot, ensure_ascii=False)}\n\n"
        finally:
            smart_capture_event_broker.unsubscribe_current(subscriber)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/{smart_capture_id}")
def get(smart_capture_id: str, db: Database = Depends(get_database)):
    return smart_captures.get_smart_capture(db, smart_capture_id)


@router.get("/{smart_capture_id}/events")
def events(smart_capture_id: str, db: Database = Depends(get_database)) -> StreamingResponse:
    subscriber = smart_capture_event_broker.subscribe(smart_capture_id)
    try:
        initial_snapshot = smart_captures.get_smart_capture(db, smart_capture_id)
    except Exception:
        smart_capture_event_broker.unsubscribe(smart_capture_id, subscriber)
        raise

    def event_stream() -> Iterator[str]:
        try:
            yield f"data: {json.dumps(initial_snapshot, ensure_ascii=False)}\n\n"
            while True:
                try:
                    snapshot = subscriber.get(timeout=15)
                except Empty:
                    yield ": heartbeat\n\n"
                    continue
                yield f"data: {json.dumps(snapshot, ensure_ascii=False)}\n\n"
        finally:
            smart_capture_event_broker.unsubscribe(smart_capture_id, subscriber)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/{smart_capture_id}/pause")
def pause(
    smart_capture_id: str,
    payload: SmartCaptureControlRequest | None = None,
    db: Database = Depends(get_database),
):
    return smart_captures.pause_smart_capture(
        db, smart_capture_id,
        transition_id=payload.transition_id if payload else None,
    )


@router.post("/{smart_capture_id}/start")
@start_http_response
def start(
    smart_capture_id: str,
    payload: SmartCaptureControlRequest | None = None,
    config: AppConfig = Depends(get_config),
    db: Database = Depends(get_database),
):
    return smart_captures.start_smart_capture(db, config, smart_capture_id, operation_id=payload.operation_id if payload else None)


@router.post("/{smart_capture_id}/resume")
@start_http_response
def resume(
    smart_capture_id: str,
    payload: SmartCaptureControlRequest | None = None,
    config: AppConfig = Depends(get_config),
    db: Database = Depends(get_database),
):
    return smart_captures.resume_smart_capture(
        db, config, smart_capture_id,
        transition_id=payload.transition_id if payload else None,
        operation_id=payload.operation_id if payload else None,
    )


@router.post("/{smart_capture_id}/retry")
@start_http_response
def retry(
    smart_capture_id: str,
    payload: SmartCaptureControlRequest | None = None,
    config: AppConfig = Depends(get_config),
    db: Database = Depends(get_database),
):
    return smart_captures.retry_smart_capture(db, config, smart_capture_id, operation_id=payload.operation_id if payload else None)


@router.post("/{smart_capture_id}/stop")
def stop(
    smart_capture_id: str,
    payload: SmartCaptureControlRequest | None = None,
    db: Database = Depends(get_database),
):
    return smart_captures.stop_smart_capture(
        db, smart_capture_id,
        transition_id=payload.transition_id if payload else None,
    )


@router.get("/{smart_capture_id}/context-snapshot")
def context_snapshot(
    smart_capture_id: str,
    channel: str = "deep_job_search",
    db: Database = Depends(get_database),
):
    return smart_capture_domain.get_context_snapshot(db, smart_capture_id, channel)


@router.get("/{smart_capture_id}/analysis-items")
def list_analysis_items(
    smart_capture_id: str,
    analysis_batch_id: str | None = None,
    db: Database = Depends(get_database),
):
    return smart_capture_domain.list_analysis_items(db, smart_capture_id, analysis_batch_id)


@router.post("/{smart_capture_id}/analysis-batches")
def create_analysis_batch(
    smart_capture_id: str,
    payload: SmartCaptureManualAnalysisBatchRequest,
    config: AppConfig = Depends(get_config),
    db: Database = Depends(get_database),
):
    return smart_capture_domain.create_manual_analysis_batch(
        db,
        config,
        smart_capture_id,
        job_ids=payload.job_ids,
        analysis_batch_size=payload.analysis_batch_size,
        recommendation_strategy_id=payload.recommendation_strategy_id,
        codex_model=payload.codex_model,
        codex_reasoning_effort=payload.codex_reasoning_effort,
    )


@router.get("/{smart_capture_id}/analysis-items/{workflow_task_id}/context")
def analysis_item_context(
    smart_capture_id: str,
    workflow_task_id: str,
    db: Database = Depends(get_database),
):
    return smart_capture_domain.get_analysis_item_context(db, smart_capture_id, workflow_task_id)


@router.post("/{smart_capture_id}/analysis-items/{workflow_task_id}/feedback")
def save_analysis_feedback(
    smart_capture_id: str,
    workflow_task_id: str,
    payload: SmartCaptureAnalysisFeedbackRequest,
    db: Database = Depends(get_database),
):
    return smart_capture_domain.save_feedback(
        db, smart_capture_id, workflow_task_id, payload.model_dump()
    )


@router.post("/{smart_capture_id}/analysis-items/{workflow_task_id}/save")
def save_analysis_item(
    smart_capture_id: str,
    workflow_task_id: str,
    payload: SmartCaptureAnalysisSaveRequest,
    config: AppConfig = Depends(get_config),
    db: Database = Depends(get_database),
):
    return smart_capture_domain.save_analysis_item(
        db, config, smart_capture_id, workflow_task_id, payload.model_dump()
    )


@router.patch("/{smart_capture_id}/codex-session")
def attach_codex_session(
    smart_capture_id: str,
    payload: SmartCaptureCodexSessionRequest,
    db: Database = Depends(get_database),
):
    return smart_capture_domain.attach_codex_session(
        db,
        smart_capture_id,
        payload.codex_session_ref,
        payload.codex_runtime_id,
        payload.analysis_batch_id,
    )


@router.post("/{smart_capture_id}/analysis-handoff/claim")
def claim_analysis_handoff(
    smart_capture_id: str,
    payload: SmartCaptureHandoffClaimRequest,
    db: Database = Depends(get_database),
):
    return smart_capture_domain.claim_handoff(
        db,
        smart_capture_id,
        codex_session_ref=payload.codex_session_ref,
        codex_runtime_id=payload.codex_runtime_id,
        handoff_kind=payload.handoff_kind,
        retry_handoff_attempt_id=payload.retry_handoff_attempt_id,
    )


@router.post("/{smart_capture_id}/analysis-handoff/prompt-written")
def mark_analysis_handoff_prompt_written(
    smart_capture_id: str,
    payload: SmartCaptureHandoffRequest,
    db: Database = Depends(get_database),
):
    return smart_capture_domain.prompt_written(
        db,
        smart_capture_id,
        payload.analysis_batch_id,
        payload.handoff_attempt_id,
        payload.codex_session_ref,
    )


@router.post("/{smart_capture_id}/analysis-handoff/ack-started")
def ack_analysis_handoff_started(
    smart_capture_id: str,
    payload: SmartCaptureHandoffStartAckRequest,
    config: AppConfig = Depends(get_config),
    db: Database = Depends(get_database),
):
    return smart_capture_domain.ack_started(
        db, config, smart_capture_id, payload.analysis_batch_id, payload.handoff_attempt_id
    )


@router.post("/{smart_capture_id}/analysis-handoff/release")
def release_analysis_handoff(
    smart_capture_id: str,
    payload: SmartCaptureHandoffRequest,
    db: Database = Depends(get_database),
):
    return smart_capture_domain.release_handoff(
        db,
        smart_capture_id,
        payload.analysis_batch_id,
        payload.handoff_attempt_id,
        payload.codex_session_ref,
        payload.release_reason,
    )


@router.post("/{smart_capture_id}/analysis-handoff/retry")
def retry_analysis_handoff(
    smart_capture_id: str,
    payload: SmartCaptureHandoffRequest,
    config: AppConfig = Depends(get_config),
    db: Database = Depends(get_database),
):
    return smart_capture_domain.retry_handoff(
        db,
        smart_capture_id,
        payload.analysis_batch_id,
        payload.handoff_attempt_id,
        payload.codex_session_ref,
        None,
    )


@router.patch("/{smart_capture_id}/analysis-guidance")
def update_analysis_guidance(
    smart_capture_id: str,
    payload: SmartCaptureAnalysisGuidanceUpdateRequest,
    db: Database = Depends(get_database),
):
    return smart_capture_domain.update_guidance(
        db, smart_capture_id, payload.analysis_guidance
    )
