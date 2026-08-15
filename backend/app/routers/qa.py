from __future__ import annotations

from fastapi import APIRouter, Depends

from backend.app.config import AppConfig
from backend.app.db import Database
from backend.app.dependencies import get_config, get_database
from backend.app.schemas.qa import (
    QAAnswerRequest,
    QAAnswerResponse,
    QASessionDeleteResponse,
    QASessionDetailResponse,
    QASessionListResponse,
)
from backend.app.services.qa import (
    answer_question,
    delete_qa_session,
    get_qa_session_detail,
    list_qa_sessions,
)


router = APIRouter(prefix="/qa", tags=["qa"])


@router.post("/answer", response_model=QAAnswerResponse)
def qa_answer(
    payload: QAAnswerRequest,
    db: Database = Depends(get_database),
    config: AppConfig = Depends(get_config),
) -> QAAnswerResponse:
    return QAAnswerResponse(**answer_question(db=db, config=config, payload=payload))


@router.get("/sessions", response_model=QASessionListResponse)
def qa_sessions(
    db: Database = Depends(get_database),
) -> QASessionListResponse:
    return QASessionListResponse(items=list_qa_sessions(db=db))


@router.get("/sessions/{session_id}", response_model=QASessionDetailResponse)
def qa_session_detail(
    session_id: str,
    db: Database = Depends(get_database),
) -> QASessionDetailResponse:
    return QASessionDetailResponse(**get_qa_session_detail(db=db, session_id=session_id))


@router.delete("/sessions/{session_id}", response_model=QASessionDeleteResponse)
def qa_session_delete(
    session_id: str,
    db: Database = Depends(get_database),
) -> QASessionDeleteResponse:
    return QASessionDeleteResponse(deleted=delete_qa_session(db=db, session_id=session_id))
