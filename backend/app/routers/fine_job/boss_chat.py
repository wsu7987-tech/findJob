from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Query

from backend.app.config import AppConfig
from backend.app.db import Database
from backend.app.dependencies import get_config, get_database
from backend.app.routers.fine_job.boss_executor import _executor
from backend.app.schemas.fine_job.boss_chat import (
    BossChatActionCompleteRequest,
    BossChatClaimActionRequest,
    BossChatDispatchStartedRequest,
    BossChatEventBatchRequest,
    BossChatGenerateRequest,
    BossChatHeartbeatRequest,
    BossChatReasonRequest,
    BossChatReplyConfirmRequest,
    BossChatReplyEditRequest,
    BossChatRuntimeUpdateRequest,
)
from backend.app.services.fine_job import boss_chat


router = APIRouter(prefix="/fine-job/boss-chat", tags=["fine-job-boss-chat"])


@router.get("/runtime")
def runtime(db: Database = Depends(get_database)):
    return {"runtime": boss_chat.get_runtime(db)}


@router.patch("/runtime")
def update_runtime(payload: BossChatRuntimeUpdateRequest, db: Database = Depends(get_database)):
    return {"runtime": boss_chat.update_runtime(db, payload.model_dump(exclude_none=True))}


@router.post("/check")
def check_pending(
    db: Database = Depends(get_database),
    config: AppConfig = Depends(get_config),
):
    return {"generated": boss_chat.process_due_tasks(db, config, force=True)}


@router.post("/executor/heartbeat")
def executor_heartbeat(
    payload: BossChatHeartbeatRequest,
    authorization: str = Header(default=""),
    db: Database = Depends(get_database),
):
    executor = _executor(db, authorization)
    return boss_chat.report_heartbeat(db, str(executor["id"]), payload.model_dump())


@router.post("/executor/events/batch")
def executor_events(
    payload: BossChatEventBatchRequest,
    authorization: str = Header(default=""),
    db: Database = Depends(get_database),
    config: AppConfig = Depends(get_config),
):
    executor = _executor(db, authorization)
    result = boss_chat.ingest_events(
        db,
        str(executor["id"]),
        [event.model_dump() for event in payload.events],
    )
    # 立即模式沿用同一任务处理器；定时模式只在到期时生成。
    result["generated"] = boss_chat.process_due_tasks(db, config)
    return result


@router.post("/executor/actions/claim")
def claim_action(
    payload: BossChatClaimActionRequest,
    authorization: str = Header(default=""),
    db: Database = Depends(get_database),
):
    executor = _executor(db, authorization)
    return {"action": boss_chat.claim_send_action(
        db,
        str(executor["id"]),
        account_uid=payload.account_uid,
        tab_id=payload.tab_id,
        leader_epoch=payload.leader_epoch,
    )}


@router.post("/executor/actions/{action_id}/dispatch-started")
def dispatch_started(
    action_id: str,
    payload: BossChatDispatchStartedRequest,
    authorization: str = Header(default=""),
    db: Database = Depends(get_database),
):
    executor = _executor(db, authorization)
    return {"action": boss_chat.mark_dispatch_started(
        db, str(executor["id"]), action_id, payload.execution_epoch
    )}


@router.post("/executor/actions/{action_id}/complete")
def complete_action(
    action_id: str,
    payload: BossChatActionCompleteRequest,
    authorization: str = Header(default=""),
    db: Database = Depends(get_database),
):
    executor = _executor(db, authorization)
    return {"action": boss_chat.complete_send_action(
        db, str(executor["id"]), action_id, payload.model_dump()
    )}


@router.get("/sessions")
def sessions(
    status: str | None = Query(default=None),
    db: Database = Depends(get_database),
):
    return {"sessions": boss_chat.list_sessions(db, status=status)}


@router.get("/sessions/{session_id}")
def session(session_id: str, db: Database = Depends(get_database)):
    return boss_chat.get_session(db, session_id)


@router.post("/sessions/{session_id}/generate")
def generate(
    session_id: str,
    payload: BossChatGenerateRequest,
    db: Database = Depends(get_database),
    config: AppConfig = Depends(get_config),
):
    return {"reply_task": boss_chat.generate_reply(
        db, config, session_id, instruction=payload.instruction
    )}


@router.post("/sessions/{session_id}/regenerate")
def regenerate(
    session_id: str,
    payload: BossChatGenerateRequest,
    db: Database = Depends(get_database),
    config: AppConfig = Depends(get_config),
):
    return {"reply_task": boss_chat.generate_reply(
        db, config, session_id, instruction=payload.instruction, regenerate=True
    )}


@router.post("/sessions/{session_id}/take-over")
def take_over(
    session_id: str,
    _: BossChatReasonRequest,
    db: Database = Depends(get_database),
):
    return {"session": boss_chat.set_session_status(db, session_id, "human_takeover")}


@router.post("/sessions/{session_id}/resume")
def resume_session(
    session_id: str,
    _: BossChatReasonRequest,
    db: Database = Depends(get_database),
):
    return {"session": boss_chat.set_session_status(db, session_id, "active")}


@router.post("/sessions/{session_id}/pause")
def pause_session(
    session_id: str,
    _: BossChatReasonRequest,
    db: Database = Depends(get_database),
):
    return {"session": boss_chat.set_session_status(db, session_id, "paused")}


@router.patch("/reply-tasks/{task_id}")
def edit_reply(
    task_id: str,
    payload: BossChatReplyEditRequest,
    db: Database = Depends(get_database),
):
    return {"reply_task": boss_chat.edit_reply(db, task_id, payload.final_text)}


@router.post("/reply-tasks/{task_id}/confirm")
def confirm_reply(
    task_id: str,
    payload: BossChatReplyConfirmRequest,
    db: Database = Depends(get_database),
):
    return {"action": boss_chat.confirm_reply(db, task_id, payload.model_dump())}


@router.post("/reply-tasks/{task_id}/cancel")
def cancel_reply(
    task_id: str,
    _: BossChatReasonRequest,
    db: Database = Depends(get_database),
):
    return {"reply_task": boss_chat.cancel_reply(db, task_id)}
