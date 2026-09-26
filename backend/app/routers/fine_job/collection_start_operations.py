from fastapi import APIRouter, Depends

from backend.app.db import Database
from backend.app.dependencies import get_database
from backend.app.services.fine_job.collection_start_operations import get_operation, resolve_operation


router = APIRouter(prefix="/fine-job/collection-start-operations", tags=["fine-job"])


@router.get("/{operation_id}")
def read_operation(operation_id: str, db: Database = Depends(get_database)) -> dict:
    return get_operation(db, operation_id)


@router.post("/{operation_id}/resolve")
def close_operation(operation_id: str, db: Database = Depends(get_database)) -> dict:
    return resolve_operation(db, operation_id)
