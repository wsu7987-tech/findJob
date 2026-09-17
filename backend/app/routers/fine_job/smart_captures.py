from __future__ import annotations

from fastapi import APIRouter, Depends, status

from backend.app.config import AppConfig
from backend.app.db import Database
from backend.app.dependencies import get_config, get_database
from backend.app.schemas.fine_job.smart_captures import SmartCaptureControlRequest, SmartCaptureCreateRequest
from backend.app.services.fine_job import smart_captures


router = APIRouter(prefix="/fine-job/smart-captures", tags=["fine-job-smart-captures"])


@router.post("", status_code=status.HTTP_201_CREATED)
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


@router.get("/{smart_capture_id}")
def get(smart_capture_id: str, db: Database = Depends(get_database)):
    return smart_captures.get_smart_capture(db, smart_capture_id)


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
def start(
    smart_capture_id: str,
    config: AppConfig = Depends(get_config),
    db: Database = Depends(get_database),
):
    return smart_captures.start_smart_capture(db, config, smart_capture_id)


@router.post("/{smart_capture_id}/resume")
def resume(
    smart_capture_id: str,
    payload: SmartCaptureControlRequest | None = None,
    config: AppConfig = Depends(get_config),
    db: Database = Depends(get_database),
):
    return smart_captures.resume_smart_capture(
        db, config, smart_capture_id,
        transition_id=payload.transition_id if payload else None,
    )


@router.post("/{smart_capture_id}/retry")
def retry(
    smart_capture_id: str,
    payload: SmartCaptureControlRequest | None = None,
    config: AppConfig = Depends(get_config),
    db: Database = Depends(get_database),
):
    return smart_captures.retry_smart_capture(db, config, smart_capture_id)


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
