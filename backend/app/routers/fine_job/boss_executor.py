from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Query, WebSocket, WebSocketDisconnect

from backend.app.db import Database
from backend.app.dependencies import get_database
from backend.app.errors import AppError
from backend.app.schemas.fine_job.boss_executor import (
    BossActionCompleteRequest,
    BossDispatchStartedRequest,
    BossExecutorControlRequest,
    BossExecutorHeartbeatRequest,
    BossExecutorPairRequest,
    BossExecutorPairResponse,
    BossManualVerifyUnknownRequest,
    BossNavigationOpenRequest,
    BossPageStatusRequest,
    BossPairingCodeResponse,
    BossReturnToReviewRequest,
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
def pair(payload: BossExecutorPairRequest, db: Database = Depends(get_database)):
    return boss_executor.pair_executor(db, **payload.model_dump())


@router.post("/boss-executor/heartbeat")
def heartbeat(
    payload: BossExecutorHeartbeatRequest,
    authorization: str = Header(default=""),
    db: Database = Depends(get_database),
):
    executor = _executor(db, authorization)
    return boss_executor.heartbeat(db, str(executor["id"]), payload.model_dump())


@router.websocket("/boss-executor/channel")
async def executor_channel(
    websocket: WebSocket,
    token: str = Query(default=""),
):
    db = websocket.app.state.db
    try:
        executor = boss_executor.authenticate_executor(db, token)
    except AppError:
        await websocket.close(code=1008)
        return
    await websocket.accept()
    executor_id = str(executor["id"])
    await boss_executor.register_executor_channel(executor_id, websocket)
    try:
        while True:
            message = await websocket.receive_json()
            await boss_executor.handle_executor_channel_message(executor_id, message)
    except WebSocketDisconnect:
        pass
    finally:
        await boss_executor.unregister_executor_channel(executor_id, websocket)


@router.get("/boss-executor/queue")
def queue(authorization: str = Header(default=""), db: Database = Depends(get_database)):
    _executor(db, authorization)
    return boss_executor.list_queue(db)


@router.post("/boss-executor/actions/claim")
def claim(authorization: str = Header(default=""), db: Database = Depends(get_database)):
    executor = _executor(db, authorization)
    return {"action": boss_executor.claim_next_action(db, str(executor["id"]))}


@router.post("/boss-executor/actions/{action_id}/page-status")
def page_status(
    action_id: str,
    payload: BossPageStatusRequest,
    authorization: str = Header(default=""),
    db: Database = Depends(get_database),
):
    executor = _executor(db, authorization)
    return {"action": boss_executor.report_page_status(db, str(executor["id"]), action_id, payload.model_dump())}


@router.post("/boss-executor/actions/{action_id}/dispatch-started")
def dispatch_started(
    action_id: str,
    payload: BossDispatchStartedRequest,
    authorization: str = Header(default=""),
    db: Database = Depends(get_database),
):
    executor = _executor(db, authorization)
    return {"action": boss_executor.mark_dispatch_started(db, str(executor["id"]), action_id, payload.execution_epoch)}


@router.post("/boss-executor/actions/{action_id}/complete")
def complete(
    action_id: str,
    payload: BossActionCompleteRequest,
    authorization: str = Header(default=""),
    db: Database = Depends(get_database),
):
    executor = _executor(db, authorization)
    return {"action": boss_executor.complete_executor_action(db, str(executor["id"]), action_id, payload.model_dump())}


@router.post("/boss-executor/actions/{action_id}/return-to-review")
def executor_return(
    action_id: str,
    payload: BossReturnToReviewRequest,
    authorization: str = Header(default=""),
    db: Database = Depends(get_database),
):
    executor = _executor(db, authorization)
    return {"action": boss_executor.return_to_review(db, action_id, reason=payload.reason, executor_id=str(executor["id"]))}


@router.post("/boss-executor/actions/{action_id}/retry-failed")
def retry_failed_action(
    action_id: str,
    authorization: str = Header(default=""),
    db: Database = Depends(get_database),
):
    _executor(db, authorization)
    return boss_executor.retry_failed_action(db, action_id)


@router.post("/boss-executor/actions/{action_id}/cancel-failed")
def cancel_failed_action(
    action_id: str,
    authorization: str = Header(default=""),
    db: Database = Depends(get_database),
):
    _executor(db, authorization)
    return boss_executor.cancel_failed_action(db, action_id)


@router.post("/boss-executor/failed-actions/retry-all")
def retry_all_failed_actions(
    authorization: str = Header(default=""),
    db: Database = Depends(get_database),
):
    _executor(db, authorization)
    return boss_executor.retry_all_failed_actions(db)


@router.post("/boss-executor/failed-actions/cancel-all")
def cancel_all_failed_actions(
    authorization: str = Header(default=""),
    db: Database = Depends(get_database),
):
    _executor(db, authorization)
    return boss_executor.cancel_all_failed_actions(db)


@router.post("/boss-executor/actions/{action_id}/manual-verify")
def executor_manual_verify(
    action_id: str,
    payload: BossManualVerifyUnknownRequest,
    authorization: str = Header(default=""),
    db: Database = Depends(get_database),
):
    _executor(db, authorization)
    return {"action": boss_executor.manual_verify_unknown_action(
        db, action_id, contacted=payload.contacted, note=payload.note,
    )}


@router.post("/boss-executor/control")
def control(
    payload: BossExecutorControlRequest,
    authorization: str = Header(default=""),
    db: Database = Depends(get_database),
):
    executor = _executor(db, authorization)
    return boss_executor.set_control(db, str(executor["id"]), payload.command)


@router.get("/boss-executor/status")
def status(db: Database = Depends(get_database)):
    snapshot = boss_executor.executor_snapshot(db)
    executor = snapshot.get("executor")
    if isinstance(executor, dict):
        boss_executor.sweep_page_timeout(db, str(executor["id"]))
        snapshot = boss_executor.executor_snapshot(db, str(executor["id"]))
    return snapshot


@router.post("/boss-executor/desktop-control")
def desktop_control(
    payload: BossExecutorControlRequest,
    db: Database = Depends(get_database),
):
    snapshot = boss_executor.executor_snapshot(db)
    executor = snapshot.get("executor")
    if not isinstance(executor, dict):
        raise AppError(409, "EXECUTOR_NOT_PAIRED", "尚未配对BOSS执行器。")
    return boss_executor.set_control(db, str(executor["id"]), payload.command)


@router.post("/boss-executor/desktop-heartbeat-test")
async def desktop_heartbeat_test(db: Database = Depends(get_database)):
    snapshot = boss_executor.executor_snapshot(db)
    executor = snapshot.get("executor")
    if not isinstance(executor, dict):
        raise AppError(409, "EXECUTOR_NOT_PAIRED", "尚未配对BOSS执行器。")
    return await boss_executor.request_heartbeat_test(db, str(executor["id"]))


@router.post("/boss-executor/desktop-disconnect")
async def desktop_disconnect(db: Database = Depends(get_database)):
    snapshot = boss_executor.executor_snapshot(db)
    executor = snapshot.get("executor")
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
    return {"navigation": boss_executor.open_navigation(db, job_identifier=payload.job_id, source_context=payload.source_context)}


@router.get("/boss-navigation/{task_id}")
def navigation(task_id: str, db: Database = Depends(get_database)):
    return {"navigation": boss_executor.get_navigation(db, task_id)}


@router.post("/automation-actions/{action_id}/return-to-review")
def desktop_return(
    action_id: str,
    payload: BossReturnToReviewRequest,
    db: Database = Depends(get_database),
):
    return {"action": boss_executor.return_to_review(db, action_id, reason=payload.reason)}


@router.post("/automation-actions/{action_id}/manual-verify")
def desktop_manual_verify(
    action_id: str,
    payload: BossManualVerifyUnknownRequest,
    db: Database = Depends(get_database),
):
    return {"action": boss_executor.manual_verify_unknown_action(
        db, action_id, contacted=payload.contacted, note=payload.note,
    )}
