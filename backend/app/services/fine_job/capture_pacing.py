from __future__ import annotations

from backend.app.schemas.fine_job.boss_capture import CapturePacing
from backend.app.utils import utc_now


def pacing_in_connection(connection) -> dict:
    row = connection.execute("SELECT * FROM fj_boss_capture_pacing WHERE id = 1").fetchone()
    return CapturePacing.model_validate(dict(row) if row else {}).model_dump()


def get_pacing(db) -> dict:
    with db.connect() as connection:
        return pacing_in_connection(connection)


def save_pacing(db, values: dict) -> dict:
    pacing = CapturePacing.model_validate(values).model_dump()
    with db.connect() as connection:
        connection.execute(
            """INSERT INTO fj_boss_capture_pacing
            (id, list_min_seconds, list_max_seconds, detail_min_seconds, detail_max_seconds, updated_at)
            VALUES (1, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET
            list_min_seconds=excluded.list_min_seconds, list_max_seconds=excluded.list_max_seconds,
            detail_min_seconds=excluded.detail_min_seconds, detail_max_seconds=excluded.detail_max_seconds,
            updated_at=excluded.updated_at""",
            (*pacing.values(), utc_now()),
        )
    return pacing


def default_pacing(values=None) -> dict:
    # 旧任务缺少快照时使用固定默认值，恢复不读取后来修改的应用设置。
    return CapturePacing.model_validate(values or {}).model_dump()
