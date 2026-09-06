from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from backend.app.config import AppConfig
from backend.app.db import Database
from backend.app.dependencies import get_config, get_database
from backend.app.schemas.fine_job.job_actions import (
    JobActionGenerateDraftsRequest,
    JobActionGenerateDraftsResponse,
    JobActionListResponse,
    JobActionMutationResponse,
    JobActionPriority,
    JobActionSnoozeRequest,
    JobActionState,
    JobActionType,
)
from backend.app.services.fine_job.job_action_center import (
    generate_job_action_drafts,
    list_job_actions,
    restore_action_state,
    set_action_state,
)


router = APIRouter(prefix="/fine-job/job-actions", tags=["fine-job-job-actions"])


@router.post("/generate-drafts", response_model=JobActionGenerateDraftsResponse)
def generate_drafts(
    payload: JobActionGenerateDraftsRequest,
    db: Database = Depends(get_database),
    config: AppConfig = Depends(get_config),
) -> JobActionGenerateDraftsResponse:
    return JobActionGenerateDraftsResponse(**generate_job_action_drafts(
        db,
        config,
        payload.action_keys,
    ))


@router.get("", response_model=JobActionListResponse)
def read_job_actions(
    status: JobActionState = Query(default="active"),
    priority: JobActionPriority | None = Query(default=None),
    action_type: JobActionType | None = Query(default=None),
    db: Database = Depends(get_database),
) -> JobActionListResponse:
    return JobActionListResponse(**list_job_actions(
        db,
        status=status,
        priority=priority,
        action_type=action_type,
    ))


@router.post("/{action_key}/snooze", response_model=JobActionMutationResponse)
def snooze_job_action(
    action_key: str,
    payload: JobActionSnoozeRequest,
    db: Database = Depends(get_database),
) -> JobActionMutationResponse:
    return JobActionMutationResponse(**set_action_state(
        db,
        action_key,
        "snoozed",
        snoozed_until=payload.snoozed_until,
    ))


@router.post("/{action_key}/dismiss", response_model=JobActionMutationResponse)
def dismiss_job_action(
    action_key: str,
    db: Database = Depends(get_database),
) -> JobActionMutationResponse:
    return JobActionMutationResponse(**set_action_state(db, action_key, "dismissed"))


@router.post("/{action_key}/complete", response_model=JobActionMutationResponse)
def complete_job_action(
    action_key: str,
    db: Database = Depends(get_database),
) -> JobActionMutationResponse:
    return JobActionMutationResponse(**set_action_state(db, action_key, "completed"))


@router.post("/{action_key}/restore", response_model=JobActionMutationResponse)
def restore_job_action(
    action_key: str,
    db: Database = Depends(get_database),
) -> JobActionMutationResponse:
    return JobActionMutationResponse(**restore_action_state(db, action_key))
