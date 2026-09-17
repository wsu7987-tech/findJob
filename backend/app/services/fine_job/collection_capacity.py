from __future__ import annotations

from collections.abc import Callable
from typing import Any

from backend.app.db import Database
from backend.app.errors import AppError
from backend.app.services.fine_job.boss_capture_tasks import boss_capture_task_manager
from backend.app.utils import new_id, utc_now


SMART_CAPTURE_ACTIVE_STATUSES = frozenset(
    {
        "pending",
        "running",
        "pausing",
        "paused",
        "waiting_next_batch",
        "waiting_for_user",
        "interrupted",
    }
)
COLLECTION_EXECUTION_STATUSES = ("queued", "running")


def assert_collection_start_allowed(db: Database, *, requested_kind: str) -> None:
    """在数据库写锁内统一检查 Smart Capture 与 custom 的启动互斥。"""
    with db.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        _assert_collection_start_allowed(connection, db, requested_kind=requested_kind)


def assert_collection_start_allowed_in_connection(
    connection: Any,
    db: Database,
    *,
    requested_kind: str,
) -> None:
    """供创建 current Smart Capture 时复用同一事务中的容量检查。"""
    _assert_collection_start_allowed(connection, db, requested_kind=requested_kind)


def get_active_collection_task(db: Database) -> dict[str, object] | None:
    """读取统一的 active snapshot，供旧查询 API 继续提供兼容结果。"""
    with db.connect() as connection:
        smart = connection.execute(
            """
            SELECT capture.id, capture.status
            FROM fj_smart_capture_current current_task
            JOIN fj_smart_captures capture
              ON capture.id = current_task.smart_capture_id
            WHERE current_task.slot = 1
              AND capture.status IN (?, ?, ?, ?, ?, ?, ?)
            LIMIT 1
            """,
            tuple(sorted(SMART_CAPTURE_ACTIVE_STATUSES)),
        ).fetchone()
        custom = connection.execute(
            """
            SELECT id, status FROM fj_boss_capture_batches
            WHERE (capture_source = 'custom' OR smart_capture_id IS NULL)
              AND status IN (?, ?)
            LIMIT 1
            """,
            COLLECTION_EXECUTION_STATUSES,
        ).fetchone()
        reservation = connection.execute(
            """
            SELECT capture_ref FROM fj_collection_execution_capacity
            WHERE slot = 1 AND capture_source = 'custom'
            LIMIT 1
            """
        ).fetchone()
    memory_smart = boss_capture_task_manager.get_active_task(
        capture_source="smart", db=db
    )
    memory_custom = boss_capture_task_manager.get_active_task(
        capture_source="custom", db=db
    )
    if smart is not None or memory_smart is not None:
        smart_id = str(smart["id"]) if smart is not None else str(memory_smart["id"])
        smart_status = str(smart["status"]) if smart is not None else str(memory_smart["status"])
        return {
            "kind": "smart",
            "id": smart_id,
            "status": smart_status,
            "message": "智能采集尚未结束。",
        }
    if custom is not None or memory_custom is not None or reservation is not None:
        custom_id = (
            str(custom["id"])
            if custom is not None
            else str(memory_custom["id"])
            if memory_custom is not None
            else str(reservation["capture_ref"])
        )
        custom_status = (
            str(custom["status"])
            if custom is not None
            else str(memory_custom["status"])
            if memory_custom is not None
            else "queued"
        )
        return {
            "kind": "custom",
            "id": custom_id,
            "status": custom_status,
            "message": "自定义采集尚未结束。",
        }
    return None


def reserve_custom_collection(db: Database, reservation_ref: str | None = None) -> str:
    """先持久化 custom 执行占用，再启动进程内采集任务，避免入口竞态。"""
    reservation_ref = reservation_ref or f"custom-reservation:{new_id()}"
    now = utc_now()
    with db.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        _assert_collection_start_allowed(connection, db, requested_kind="custom")
        connection.execute(
            """
            INSERT INTO fj_collection_execution_capacity (
              slot, capture_source, capture_ref, created_at, updated_at
            ) VALUES (1, 'custom', ?, ?, ?)
            """,
            (reservation_ref, now, now),
        )
    return reservation_ref


def bind_custom_collection(db: Database, reservation_ref: str, capture_ref: str) -> None:
    """把启动前的临时占用绑定到真实 custom 采集任务。"""
    with db.connect() as connection:
        updated = connection.execute(
            """
            UPDATE fj_collection_execution_capacity
            SET capture_ref = ?, updated_at = ?
            WHERE slot = 1 AND capture_source = 'custom' AND capture_ref = ?
            """,
            (capture_ref, utc_now(), reservation_ref),
        ).rowcount
        if updated:
            batch = connection.execute(
                "SELECT status FROM fj_boss_capture_batches WHERE id = ?",
                (capture_ref,),
            ).fetchone()
            if batch is not None:
                task_status = str(batch["status"])
            else:
                try:
                    task = boss_capture_task_manager.get_task(capture_ref)
                    task_status = str(task.get("status") or "")
                except AppError:
                    task_status = ""
            if task_status not in COLLECTION_EXECUTION_STATUSES:
                # 独立历史详情没有 batch 行，绑定时也要处理已快速结束的任务。
                connection.execute(
                    "DELETE FROM fj_collection_execution_capacity WHERE slot = 1"
                )


def release_custom_collection(db: Database, capture_ref: str) -> None:
    """采集进入终态或安全暂停后释放实际执行容量。"""
    with db.connect() as connection:
        connection.execute(
            """
            DELETE FROM fj_collection_execution_capacity
            WHERE slot = 1 AND capture_source = 'custom' AND capture_ref = ?
            """,
            (capture_ref,),
        )


def start_custom_collection(
    db: Database,
    starter: Callable[[], dict[str, object]],
) -> dict[str, object]:
    """统一包裹 Boss API 与 Codex custom 启动，失败时回收预占容量。"""
    reservation_ref = reserve_custom_collection(db)
    try:
        task = starter()
        capture_ref = str(task.get("id") or "")
        if not capture_ref:
            raise AppError(500, "CAPTURE_START_FAILED", "自定义采集未返回任务标识。")
        if str(task.get("status") or "queued") in {"queued", "running"}:
            bind_custom_collection(db, reservation_ref, capture_ref)
        else:
            # 同步测试替身或极快任务可能已完成，避免完成后再留下孤立预占记录。
            release_custom_collection(db, reservation_ref)
        return task
    except Exception:
        release_custom_collection(db, reservation_ref)
        raise


def start_custom_collection_phase(
    db: Database,
    task_id: str,
    starter: Callable[[], dict[str, object]],
) -> dict[str, object]:
    """为已有采集任务的续采/详情阶段复用 custom 容量互斥。"""
    try:
        task = boss_capture_task_manager.get_task(task_id)
    except AppError:
        # 测试替身或未持久化的旧任务由原入口继续处理，避免改变其错误语义。
        return starter()
    if str(task.get("capture_source") or "") == "smart":
        return starter()
    if str(task.get("capture_source") or "") == "custom":
        return start_custom_collection(db, starter)
    with db.connect() as connection:
        batch = connection.execute(
            "SELECT capture_source, smart_capture_id FROM fj_boss_capture_batches WHERE id = ?",
            (task_id,),
        ).fetchone()
    if batch is not None and (
        str(batch["capture_source"] or "custom") == "custom"
        and batch["smart_capture_id"] is None
    ):
        return start_custom_collection(db, starter)
    return starter()


def recover_collection_capacity(db: Database) -> None:
    """启动时结束丢失进程的 custom 执行占用，避免重启留下幽灵容量。"""
    now = utc_now()
    with db.connect() as connection:
        connection.execute(
            """
            UPDATE fj_boss_capture_batches
            SET status = 'failed', control_status = 'interrupted',
                message = '应用重启后自定义采集执行已中断。',
                error_message = '应用重启后采集执行进程已退出。', updated_at = ?
            WHERE capture_source = 'custom' AND status IN ('queued', 'running')
            """,
            (now,),
        )
        connection.execute("DELETE FROM fj_collection_execution_capacity")


def _assert_collection_start_allowed(
    connection: Any,
    db: Database,
    *,
    requested_kind: str,
) -> None:
    if requested_kind not in {"smart", "custom"}:
        raise AppError(422, "VALIDATION_FAILED", "采集任务类型无效。")

    smart = connection.execute(
        """
        SELECT capture.id
        FROM fj_smart_capture_current current_task
        JOIN fj_smart_captures capture
          ON capture.id = current_task.smart_capture_id
        WHERE current_task.slot = 1
          AND capture.status IN (?, ?, ?, ?, ?, ?, ?)
        LIMIT 1
        """,
        tuple(sorted(SMART_CAPTURE_ACTIVE_STATUSES)),
    ).fetchone()
    custom_batch = connection.execute(
        """
        SELECT id FROM fj_boss_capture_batches
        WHERE (capture_source = 'custom' OR smart_capture_id IS NULL)
          AND status IN (?, ?)
        LIMIT 1
        """,
        COLLECTION_EXECUTION_STATUSES,
    ).fetchone()
    custom_reservation = connection.execute(
        """
        SELECT capture_ref FROM fj_collection_execution_capacity
        WHERE slot = 1 AND capture_source = 'custom'
        LIMIT 1
        """
    ).fetchone()
    memory_smart = boss_capture_task_manager.get_active_task(
        capture_source="smart", db=db
    )
    memory_custom = boss_capture_task_manager.get_active_task(
        capture_source="custom", db=db
    )

    if requested_kind == "custom" and (
        smart is not None or memory_smart is not None
    ):
        raise AppError(
            409,
            "COLLECTION_TASK_ACTIVE",
            "当前智能采集尚未结束，请先暂停后继续或停止当前任务。",
        )
    if requested_kind == "smart" and (
        custom_batch is not None
        or custom_reservation is not None
        or memory_custom is not None
    ):
        raise AppError(
            409,
            "COLLECTION_TASK_ACTIVE",
            "当前自定义采集尚未结束，请先停止后再开始智能采集。",
        )
