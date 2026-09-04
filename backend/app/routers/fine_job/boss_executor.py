from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Query, WebSocket, WebSocketDisconnect

from backend.app.db import Database
from backend.app.dependencies import get_database
from backend.app.errors import AppError
from backend.app.schemas.fine_job.boss_executor import (
    BossExecutorControlRequest,
    BossExecutorHeartbeatRequest,
    BossExecutorPairRequest,
    BossExecutorPairResponse,
    BossExecutorSettingsRequest,
    BossNavigationOpenRequest,
    BossPairingCodeResponse,
    BossReturnToReviewRequest,
    BossTaskCompleteRequest,
    BossTaskMatchRequest,
    BossTestJobUpdateRequest,
    BossTestTaskCreateRequest,
)
from backend.app.services.fine_job import boss_executor


router = APIRouter(prefix="/fine-job", tags=["fine-job-boss-executor"])


def _token(authorization: str = Header(default="")) -> str:
    scheme, _, value = authorization.partition(" ")
    return value.strip() if scheme.lower() == "bearer" else ""


def _executor(db: Database, authorization: str) -> dict[str, object]:
    return boss_executor.authenticate_executor(db, _token(authorization))


@router.post("/boss-executor/pairing-code", response_model=BossPairingCodeResponse)
def create_pairing_code(db: Database = Depends(get_database)):
    return boss_executor.create_pairing_code(db)


@router.post("/boss-executor/pair", response_model=BossExecutorPairResponse)
async def pair(payload: BossExecutorPairRequest, db: Database = Depends(get_database)):
    result = boss_executor.pair_executor(db, **payload.model_dump())
    await boss_executor.broadcast_executor_state(db)
    return result


@router.post("/boss-executor/heartbeat")
async def heartbeat(
    payload: BossExecutorHeartbeatRequest,
    authorization: str = Header(default=""),
    db: Database = Depends(get_database),
):
    executor = _executor(db, authorization)
    result = boss_executor.heartbeat(db, str(executor["id"]), payload.model_dump())
    await boss_executor.broadcast_executor_state(db)
    return result


@router.websocket("/boss-executor/channel")
async def executor_channel(websocket: WebSocket, token: str = Query(default="")):
    db = websocket.app.state.db
    try:
        executor = boss_executor.authenticate_executor(db, token)
    except AppError:
        await websocket.close(code=1008)
        return
    await websocket.accept()
    executor_id = str(executor["id"])
    await boss_executor.register_executor_channel(db, executor_id, websocket)
    try:
        while True:
            message = await websocket.receive_json()
            await boss_executor.handle_executor_channel_message(db, executor_id, message)
    except WebSocketDisconnect:
        pass
    finally:
        await boss_executor.unregister_executor_channel(db, executor_id, websocket)


@router.websocket("/boss-executor/desktop-channel")
async def desktop_channel(websocket: WebSocket):
    db = websocket.app.state.db
    await websocket.accept()
    await boss_executor.register_desktop_channel(db, websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        await boss_executor.unregister_desktop_channel(websocket)


@router.get("/boss-executor/queue")
def queue(authorization: str = Header(default=""), db: Database = Depends(get_database)):
    _executor(db, authorization)
    return boss_executor.list_queue(db)


@router.post("/boss-executor/tasks/open-page")
def open_task_page(authorization: str = Header(default=""), db: Database = Depends(get_database)):
    executor = _executor(db, authorization)
    return boss_executor.open_task_page(db, str(executor["id"]))


@router.post("/boss-executor/tasks/{task_id}/matched")
async def matched(
    task_id: str,
    payload: BossTaskMatchRequest,
    authorization: str = Header(default=""),
    db: Database = Depends(get_database),
):
    executor = _executor(db, authorization)
    result = boss_executor.match_task(db, str(executor["id"]), task_id, payload.execution_epoch)
    await boss_executor.notify_queue_changed(db)
    return result


@router.post("/boss-executor/tasks/{task_id}/complete")
async def complete(
    task_id: str,
    payload: BossTaskCompleteRequest,
    authorization: str = Header(default=""),
    db: Database = Depends(get_database),
):
    executor = _executor(db, authorization)
    result = boss_executor.complete_task(db, str(executor["id"]), task_id, payload.model_dump())
    await boss_executor.notify_queue_changed(db)
    return result


@router.get("/boss-executor/test-jobs")
def test_jobs(db: Database = Depends(get_database)):
    return boss_executor.list_test_jobs(db)


@router.put("/boss-executor/test-jobs/{job_id}")
def update_test_job(
    job_id: str,
    payload: BossTestJobUpdateRequest,
    db: Database = Depends(get_database),
):
    return {"job": boss_executor.update_test_job(db, job_id, **payload.model_dump())}


@router.post("/boss-executor/test-tasks")
async def create_test_task(
    payload: BossTestTaskCreateRequest,
    db: Database = Depends(get_database),
):
    task = boss_executor.create_test_task(db, **payload.model_dump())
    await boss_executor.notify_queue_changed(db)
    return {"task": task}


@router.post("/boss-executor/actions/{action_id}/return-to-review")
async def executor_return(
    action_id: str,
    payload: BossReturnToReviewRequest,
    authorization: str = Header(default=""),
    db: Database = Depends(get_database),
):
    executor = _executor(db, authorization)
    action = boss_executor.return_to_review(db, action_id, reason=payload.reason, executor_id=str(executor["id"]))
    await boss_executor.notify_queue_changed(db)
    return {"action": action}


@router.post("/boss-executor/control")
async def control(
    payload: BossExecutorControlRequest,
    authorization: str = Header(default=""),
    db: Database = Depends(get_database),
):
    executor = _executor(db, authorization)
    return await boss_executor.set_plugin_control(db, str(executor["id"]), payload.command)


@router.get("/boss-executor/status")
def status(db: Database = Depends(get_database)):
    return boss_executor.executor_status(db)


@router.post("/boss-executor/desktop-control")
async def desktop_control(
    payload: BossExecutorControlRequest,
    db: Database = Depends(get_database),
):
    runtime = boss_executor.executor_status(db)
    executor = runtime.get("executor")
    if not isinstance(executor, dict):
        raise AppError(409, "EXECUTOR_NOT_PAIRED", "尚未配对BOSS执行器。")
    return await boss_executor.request_control(db, str(executor["id"]), payload.command)


@router.patch("/boss-executor/settings")
async def update_settings(
    payload: BossExecutorSettingsRequest,
    db: Database = Depends(get_database),
):
    result = boss_executor.update_executor_settings(db, payload.model_dump())
    await boss_executor.notify_queue_changed(db)
    return result


@router.post("/boss-executor/desktop-heartbeat-test")
async def desktop_heartbeat_test(db: Database = Depends(get_database)):
    runtime = boss_executor.executor_status(db)
    executor = runtime.get("executor")
    if not isinstance(executor, dict):
        raise AppError(409, "EXECUTOR_NOT_PAIRED", "尚未配对BOSS执行器。")
    return await boss_executor.request_heartbeat_test(db, str(executor["id"]))


@router.post("/boss-executor/desktop-disconnect")
async def desktop_disconnect(db: Database = Depends(get_database)):
    runtime = boss_executor.executor_status(db)
    executor = runtime.get("executor")
    if not isinstance(executor, dict):
        raise AppError(409, "EXECUTOR_NOT_PAIRED", "尚未配对BOSS执行器。")
    return await boss_executor.disconnect_executor(db, str(executor["id"]))


@router.post("/boss-executor/disconnect")
async def disconnect(
    authorization: str = Header(default=""),
    db: Database = Depends(get_database),
):
    executor = _executor(db, authorization)
    return await boss_executor.disconnect_executor(db, str(executor["id"]))


@router.post("/boss-navigation/open")
def open_job(payload: BossNavigationOpenRequest, db: Database = Depends(get_database)):
    return {"navigation": boss_executor.open_navigation(
        db, job_identifier=payload.job_id, source_context=payload.source_context
    )}


@router.get("/boss-navigation/{task_id}")
def navigation(task_id: str, db: Database = Depends(get_database)):
    return {"navigation": boss_executor.get_navigation(db, task_id)}


@router.post("/automation-actions/{action_id}/return-to-review")
async def desktop_return(
    action_id: str,
    payload: BossReturnToReviewRequest,
    db: Database = Depends(get_database),
):
    action = boss_executor.return_to_review(db, action_id, reason=payload.reason)
    await boss_executor.notify_queue_changed(db)
    return {"action": action}
