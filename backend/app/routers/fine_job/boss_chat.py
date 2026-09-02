from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Query, status

from backend.app.config import AppConfig
from backend.app.db import Database
from backend.app.dependencies import get_config, get_database
from backend.app.errors import AppError
from backend.app.routers.fine_job.boss_executor import _executor
from backend.app.services.fine_job.boss_capture_tasks import boss_capture_task_manager
from backend.app.schemas.fine_job.boss_chat import (
    BossChatActionCompleteRequest,
    BossChatBatchStartRequest,
    BossChatBatchSummaryResponse,
    BossChatBatchTaskResponse,
    BossChatClaimActionRequest,
    BossChatDispatchStartedRequest,
    BossChatEventBatchRequest,
    BossChatFriendListRefreshResponse,
    BossChatHistoryRefreshResponse,
    BossChatJobUpdateResponse,
    BossChatGenerateRequest,
    BossChatHeartbeatRequest,
    BossChatReasonRequest,
    BossChatReplyConfirmRequest,
    BossChatReplyEditRequest,
    BossChatRuntimeUpdateRequest,
)
from backend.app.services.fine_job import boss_chat
from backend.app.services.fine_job.boss_scraper.service import boss_scraper_service


router = APIRouter(prefix="/fine-job/boss-chat", tags=["fine-job-boss-chat"])


@router.get("/runtime")
def runtime(db: Database = Depends(get_database)):
    return {"runtime": boss_chat.get_runtime(db)}


@router.patch("/runtime")
def update_runtime(
    payload: BossChatRuntimeUpdateRequest,
    config: AppConfig = Depends(get_config),
    db: Database = Depends(get_database),
):
    runtime = boss_chat.update_runtime(db, payload.model_dump(exclude_none=True))
    boss_chat.schedule_pending_generation(db, config)
    return {"runtime": runtime}


@router.get("/batch/summary", response_model=BossChatBatchSummaryResponse)
def batch_summary(db: Database = Depends(get_database)) -> BossChatBatchSummaryResponse:
    return BossChatBatchSummaryResponse(**boss_chat.get_batch_summary(db))


@router.post("/batch", response_model=BossChatBatchTaskResponse, status_code=status.HTTP_202_ACCEPTED)
def start_batch(
    payload: BossChatBatchStartRequest,
    config: AppConfig = Depends(get_config),
    db: Database = Depends(get_database),
) -> BossChatBatchTaskResponse:
    return BossChatBatchTaskResponse(**boss_chat.boss_chat_batch_manager.start(
        db, config, batch_size=payload.batch_size,
    ))


@router.get("/batch/{task_id}", response_model=BossChatBatchTaskResponse)
def batch_status(task_id: str) -> BossChatBatchTaskResponse:
    return BossChatBatchTaskResponse(**boss_chat.boss_chat_batch_manager.get(task_id))


@router.post("/check")
def check_pending(
    db: Database = Depends(get_database),
    config: AppConfig = Depends(get_config),
):
    return {"generated": boss_chat.process_due_tasks(db, config, force=True)}


@router.post("/friend-list/refresh", response_model=BossChatFriendListRefreshResponse)
def refresh_friend_list(
    db: Database = Depends(get_database),
) -> BossChatFriendListRefreshResponse:
    """打开 BOSS 聊天页，监听页面自身的联系人列表请求并保存结果。"""
    try:
        captured = boss_scraper_service.capture_chat_friend_list()
        result = boss_chat.sync_friend_list(
            db,
            account_uid=str(captured["account_uid"]),
            response=captured["response"],
            source_url=str(captured["url"]),
        )
    except ValueError as exc:
        raise AppError(
            status_code=400,
            error_category="BOSS_CHAT_LIST_CAPTURE_INVALID",
            error_message=str(exc),
        ) from exc
    except RuntimeError as exc:
        raise AppError(
            status_code=409,
            error_category="BOSS_CHAT_LIST_CAPTURE_FAILED",
            error_message=str(exc),
        ) from exc
    return BossChatFriendListRefreshResponse(**result)


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
    config: AppConfig = Depends(get_config),
    db: Database = Depends(get_database),
):
    executor = _executor(db, authorization)
    result = boss_chat.ingest_events(
        db,
        str(executor["id"]),
        [event.model_dump() for event in payload.events],
    )
    # 事件入库后安排一次性生成回调，防抖到期时只处理对应的业务事件。
    boss_chat.schedule_pending_generation(db, config)
    result["generated"] = 0
    result["processing_deferred"] = bool(result["queued_task_ids"])
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
    account_uid: str | None = Query(default=None, max_length=80),
    q: str | None = Query(default=None, max_length=120),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Database = Depends(get_database),
):
    items = boss_chat.list_sessions(
        db,
        status=status,
        account_uid=account_uid,
        query=q,
        limit=limit,
        offset=offset,
    )
    return {
        "sessions": items,
        "next_offset": offset + len(items) if len(items) == limit else None,
    }


@router.get("/sessions/{session_id}")
def session(
    session_id: str,
    db: Database = Depends(get_database),
):
    return boss_chat.get_session(db, session_id)


@router.post("/sessions/{session_id}/history/refresh", response_model=BossChatHistoryRefreshResponse)
def refresh_history(
    session_id: str,
    db: Database = Depends(get_database),
) -> BossChatHistoryRefreshResponse:
    """使用会话保存的 encryptFriendId 和 securityId 获取历史消息。"""
    try:
        with db.connect() as connection:
            session = connection.execute(
                """
                SELECT encrypt_peer_uid, security_id
                FROM fj_chat_sessions WHERE id = ?
                """,
                (session_id,),
            ).fetchone()
        if session is None:
            raise AppError(
                status_code=404,
                error_category="CHAT_SESSION_NOT_FOUND",
                error_message="聊天会话不存在。",
            )
        captured = boss_scraper_service.capture_chat_history(
            boss_id=str(session["encrypt_peer_uid"] or ""),
            security_id=str(session["security_id"] or ""),
        )
        result = boss_chat.sync_history_messages(
            db,
            session_id=session_id,
            messages=list(captured.get("messages") or []),
            history_has_more=bool(captured.get("has_more")),
            history_next_cursor=str(captured.get("next_cursor") or ""),
        )
    except ValueError as exc:
        raise AppError(
            status_code=400,
            error_category="BOSS_CHAT_HISTORY_INVALID",
            error_message=str(exc),
        ) from exc
    except RuntimeError as exc:
        raise AppError(
            status_code=409,
            error_category="BOSS_CHAT_HISTORY_CAPTURE_FAILED",
            error_message=str(exc),
        ) from exc
    return BossChatHistoryRefreshResponse(**result)


@router.post(
    "/sessions/{session_id}/history/more",
    response_model=BossChatHistoryRefreshResponse,
)
def load_more_history(
    session_id: str,
    db: Database = Depends(get_database),
) -> BossChatHistoryRefreshResponse:
    """使用会话保存的分页游标补充更早的 20 条聊天记录。"""
    try:
        with db.connect() as connection:
            session = connection.execute(
                """
                SELECT encrypt_peer_uid, security_id, history_has_more, history_next_cursor
                FROM fj_chat_sessions WHERE id = ?
                """,
                (session_id,),
            ).fetchone()
        if session is None:
            raise AppError(
                status_code=404,
                error_category="CHAT_SESSION_NOT_FOUND",
                error_message="聊天会话不存在。",
            )
        if not bool(session["history_has_more"]):
            raise AppError(
                status_code=409,
                error_category="CHAT_HISTORY_COMPLETE",
                error_message="当前会话没有更多历史消息。",
            )
        cursor = str(session["history_next_cursor"] or "")
        if not cursor:
            raise AppError(
                status_code=409,
                error_category="CHAT_HISTORY_CURSOR_MISSING",
                error_message="当前会话缺少继续获取历史消息的位置。",
            )
        captured = boss_scraper_service.capture_chat_history(
            boss_id=str(session["encrypt_peer_uid"] or ""),
            security_id=str(session["security_id"] or ""),
            max_message_id=cursor,
        )
        result = boss_chat.sync_history_messages(
            db,
            session_id=session_id,
            messages=list(captured.get("messages") or []),
            history_has_more=bool(captured.get("has_more")),
            history_next_cursor=str(captured.get("next_cursor") or ""),
        )
    except ValueError as exc:
        raise AppError(
            status_code=400,
            error_category="BOSS_CHAT_HISTORY_INVALID",
            error_message=str(exc),
        ) from exc
    except RuntimeError as exc:
        raise AppError(
            status_code=409,
            error_category="BOSS_CHAT_HISTORY_CAPTURE_FAILED",
            error_message=str(exc),
        ) from exc
    return BossChatHistoryRefreshResponse(**result)


@router.post(
    "/sessions/{session_id}/job/update",
    response_model=BossChatJobUpdateResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def update_session_job(
    session_id: str,
    config: AppConfig = Depends(get_config),
    db: Database = Depends(get_database),
) -> BossChatJobUpdateResponse:
    """补录聊天岗位并复用历史岗位详情采集任务。"""
    result = boss_chat.prepare_chat_job(
        db,
        session_id,
        can_fetch_details=boss_scraper_service.get_browser_status().running,
    )
    if result["action"] == "update":
        result["task"] = boss_capture_task_manager.start_history_detail(
            result["job"],
            output_dir=config.output_root / "fine-job" / "boss-capture",
            db=db,
        )
    return BossChatJobUpdateResponse(**result)


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
