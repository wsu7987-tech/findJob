from __future__ import annotations

from fastapi import APIRouter, Depends, status

from backend.app.config import AppConfig
from backend.app.db import Database
from backend.app.dependencies import get_config, get_database
from backend.app.schemas.fine_job.smart_captures import SmartCaptureCreateRequest
from backend.app.services.fine_job import smart_captures
from backend.app.services.fine_job.boss_capture_tasks import boss_capture_task_manager


router = APIRouter(prefix="/fine-job/smart-captures", tags=["fine-job-smart-captures"])


@router.post("", status_code=status.HTTP_201_CREATED)
def create(
    payload: SmartCaptureCreateRequest,
    config: AppConfig = Depends(get_config),
    db: Database = Depends(get_database),
):
    # 自定义采集仍使用既有执行入口；创建智能采集前继续保持采集器互斥。
    custom = boss_capture_task_manager.get_active_task(capture_source="custom")
    if custom is not None:
        from backend.app.errors import AppError

        raise AppError(409, "COLLECTION_TASK_ACTIVE", "当前自定义采集尚未结束，请先停止后再开始智能采集。")
    return smart_captures.start_independent_capture(db, config, payload.model_dump())


@router.get("/current")
def current(db: Database = Depends(get_database)):
    # 自定义采集占用执行器时由原采集快照负责页面状态，避免恢复上一条已结束智能任务。
    if boss_capture_task_manager.get_active_task(capture_source="custom") is not None:
        return {"smart_capture": None}
    return {"smart_capture": smart_captures.get_current_smart_capture(db)}


@router.get("/{smart_capture_id}")
def get(smart_capture_id: str, db: Database = Depends(get_database)):
    return smart_captures.get_smart_capture(db, smart_capture_id)


@router.post("/{smart_capture_id}/pause")
def pause(smart_capture_id: str, db: Database = Depends(get_database)):
    return smart_captures.pause_smart_capture(db, smart_capture_id)


@router.post("/{smart_capture_id}/resume")
def resume(
    smart_capture_id: str,
    config: AppConfig = Depends(get_config),
    db: Database = Depends(get_database),
):
    return smart_captures.resume_smart_capture(db, config, smart_capture_id)


@router.post("/{smart_capture_id}/stop")
def stop(smart_capture_id: str, db: Database = Depends(get_database)):
    return smart_captures.stop_smart_capture(db, smart_capture_id)
