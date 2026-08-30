from __future__ import annotations

from fastapi import APIRouter, Depends, Header

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
