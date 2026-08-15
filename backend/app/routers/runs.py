from __future__ import annotations

from collections.abc import Iterator

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse

from backend.app.db import Database
from backend.app.dependencies import get_database
from backend.app.schemas.runs import RunDetailResponse, RunListResponse, RunResponse
from backend.app.services.runs import cancel_run, get_run, list_runs, stream_run_event_payload


router = APIRouter(prefix="/runs", tags=["runs"])


@router.get("", response_model=RunListResponse)
def read_runs(
    task_type: str | None = Query(default=None),
    status: str | None = Query(default=None),
    db: Database = Depends(get_database),
) -> RunListResponse:
    items = list_runs(db, task_type=task_type, status=status)
    return RunListResponse(items=[RunResponse(**item) for item in items], total=len(items))


@router.get("/{run_id}", response_model=RunDetailResponse)
def read_run(run_id: str, db: Database = Depends(get_database)) -> RunDetailResponse:
    return RunDetailResponse(**get_run(db, run_id))


@router.post("/{run_id}/cancel", response_model=RunResponse)
def cancel_run_route(run_id: str, db: Database = Depends(get_database)) -> RunResponse:
    return RunResponse(**cancel_run(db, run_id))


@router.get("/{run_id}/events")
def read_run_events(run_id: str, db: Database = Depends(get_database)) -> StreamingResponse:
    run_payload = get_run(db, run_id)

    def event_stream() -> Iterator[str]:
        yield stream_run_event_payload(run_payload)

    return StreamingResponse(event_stream(), media_type="text/event-stream")
