from __future__ import annotations

from backend.app.config import AppConfig
from backend.app.db import Database
from backend.app.errors import AppError
from backend.app.services.fine_job import smart_captures


SMART_CAPTURE_CHILD_TYPE = "smart_capture"


def start(db: Database, config: AppConfig, child_type: str, child_ref: str) -> dict[str, object]:
    """父编排只通过通用 child 身份启动，不接触采集内部单元。"""
    if child_type != SMART_CAPTURE_CHILD_TYPE:
        raise AppError(422, "CHILD_TYPE_UNSUPPORTED", "当前子任务类型不支持启动。")
    return smart_captures.start_smart_capture(db, config, child_ref)
