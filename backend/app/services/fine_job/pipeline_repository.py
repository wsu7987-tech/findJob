from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Iterable, Mapping

from backend.app.db import Database
from backend.app.errors import AppError
from backend.app.services.fine_job.pipeline_owner import (
    PipelineOwnerContext,
    get_pipeline_owner,
    resolve_history_read_context,
)
from backend.app.utils import new_id


_OWNER_TABLES = {
    "fj_workflow_tasks",
    "fj_workflow_job_discoveries",
    "fj_workflow_search_combinations",
    "fj_workflow_context_snapshots",
    "fj_workflow_analysis_handoffs",
    "fj_workflow_prefetch_batches",
    "fj_workflow_prefetch_items",
    "fj_workflow_candidate_reservations",
    "fj_workflow_evaluation_feedback",
    "fj_codex_sessions",
}

# handoff 表没有 created_at/id，使用其真实存在的声明时间和尝试标识排序。
_DEFAULT_ORDER_BY = {
    "fj_workflow_analysis_handoffs": "claimed_at, handoff_attempt_id",
}


@dataclass(frozen=True)
class PipelineQueryScope:
    """新业务按 Smart Capture 读取；历史适配器显式按 Workflow 读取。"""

    owner: PipelineOwnerContext
    history_only: bool = False

    @property
    def key(self) -> str:
        return self.owner.workflow_run_id if self.history_only else self.owner.smart_capture_id

    @property
    def column(self) -> str:
        return "workflow_run_id" if self.history_only else "smart_capture_id"


class PipelineRepository:
    """Pipeline owner-aware repository。

    新业务路径只接收 Smart Capture owner；历史读取通过显式 adapter 创建，避免
    缺少 Workflow ID 时偷偷回退旧查询。
    """

    def __init__(self, db: Database, scope: PipelineQueryScope):
        self.db = db
        self.scope = scope

    @classmethod
    def for_smart_capture(cls, db: Database, smart_capture_id: str) -> "PipelineRepository":
        return cls(db, PipelineQueryScope(get_pipeline_owner(db, smart_capture_id)))

    def _check_table(self, table: str) -> None:
        if table not in _OWNER_TABLES:
            raise AppError(500, "PIPELINE_TABLE_NOT_ALLOWED", "Pipeline repository 不支持该数据表。")

    def _where(self, alias: str = "") -> tuple[str, tuple[object, ...]]:
        prefix = f"{alias}." if alias else ""
        return f"{prefix}{self.scope.column} = ?", (self.scope.key,)

    def list_rows(
        self,
        table: str,
        *,
        where_sql: str = "",
        values: Iterable[object] = (),
        order_by: str | None = None,
    ) -> list[sqlite3.Row]:
        self._check_table(table)
        owner_sql, owner_values = self._where()
        extra = f" AND ({where_sql})" if where_sql else ""
        effective_order_by = order_by or _DEFAULT_ORDER_BY.get(table, "created_at, id")
        with self.db.connect() as connection:
            return connection.execute(
                f"SELECT * FROM {table} WHERE {owner_sql}{extra} ORDER BY {effective_order_by}",
                (*owner_values, *tuple(values)),
            ).fetchall()

    def get_row(self, table: str, row_id: str) -> sqlite3.Row | None:
        self._check_table(table)
        owner_sql, owner_values = self._where()
        identity_column = "analysis_batch_id" if table == "fj_workflow_analysis_handoffs" else "id"
        with self.db.connect() as connection:
            return connection.execute(
                f"SELECT * FROM {table} WHERE {identity_column} = ? AND {owner_sql}",
                (row_id, *owner_values),
            ).fetchone()

    def insert_owned(
        self,
        table: str,
        values: Mapping[str, object],
        *,
        workflow_run_id: str | None = None,
    ) -> str:
        """写入新 Pipeline 记录，Smart Capture owner 始终显式落库。"""
        self._check_table(table)
        if self.scope.history_only:
            raise AppError(409, "LEGACY_READ_ONLY", "历史 Workflow 数据只能通过兼容读取路径访问。")
        row = dict(values)
        if table != "fj_workflow_analysis_handoffs":
            row.setdefault("id", new_id())
        row["smart_capture_id"] = self.scope.owner.smart_capture_id
        requested_workflow_run_id = row.get("workflow_run_id", workflow_run_id)
        if (
            requested_workflow_run_id is not None
            and str(requested_workflow_run_id) != str(self.scope.owner.workflow_run_id or "")
        ):
            raise AppError(422, "WORKFLOW_PARENT_LINK_MISMATCH", "Pipeline 记录不能关联到其他 Workflow Run。")
        if "workflow_run_id" in row or workflow_run_id is not None:
            row["workflow_run_id"] = workflow_run_id or self.scope.owner.workflow_run_id
        columns = tuple(row)
        placeholders = ", ".join("?" for _ in columns)
        with self.db.connect() as connection:
            connection.execute(
                f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})",
                tuple(row[column] for column in columns),
            )
        return str(row.get("id") or row.get("analysis_batch_id"))

    def update_owned(self, table: str, row_id: str, values: Mapping[str, object]) -> None:
        self._check_table(table)
        if self.scope.history_only:
            raise AppError(409, "LEGACY_READ_ONLY", "历史 Workflow 数据只能通过兼容读取路径访问。")
        if not values:
            return
        owner_sql, owner_values = self._where()
        assignments = ", ".join(f"{column} = ?" for column in values)
        identity_column = "analysis_batch_id" if table == "fj_workflow_analysis_handoffs" else "id"
        with self.db.connect() as connection:
            result = connection.execute(
                f"UPDATE {table} SET {assignments} WHERE {identity_column} = ? AND {owner_sql}",
                (*tuple(values.values()), row_id, *owner_values),
            )
        if result.rowcount == 0:
            raise AppError(404, "PIPELINE_ROW_NOT_FOUND", "Pipeline 记录不存在或不属于当前 Smart Capture。")

    def list_tasks(self, task_type: str | None = None) -> list[sqlite3.Row]:
        where = "task_type = ?" if task_type else ""
        return self.list_rows("fj_workflow_tasks", where_sql=where, values=(task_type,) if task_type else ())

    def list_discoveries(self) -> list[sqlite3.Row]:
        return self.list_rows("fj_workflow_job_discoveries", order_by="discovered_at, id")

    def list_search_combinations(self) -> list[sqlite3.Row]:
        return self.list_rows("fj_workflow_search_combinations", order_by="sequence, id")

    def get_context_snapshot(self, channel: str) -> sqlite3.Row | None:
        rows = self.list_rows(
            "fj_workflow_context_snapshots",
            where_sql="channel = ?",
            values=(channel,),
            order_by="created_at DESC, id DESC",
        )
        return rows[0] if rows else None

    def get_handoff(self, analysis_batch_id: str) -> sqlite3.Row | None:
        rows = self.list_rows(
            "fj_workflow_analysis_handoffs",
            where_sql="analysis_batch_id = ?",
            values=(analysis_batch_id,),
            order_by="claimed_at DESC, handoff_attempt_id DESC",
        )
        return rows[0] if rows else None

    def get_prefetch_batch(self, source_analysis_batch_id: str) -> sqlite3.Row | None:
        rows = self.list_rows(
            "fj_workflow_prefetch_batches",
            where_sql="source_analysis_batch_id = ?",
            values=(source_analysis_batch_id,),
            order_by="created_at DESC, id DESC",
        )
        return rows[0] if rows else None

    def list_prefetch_items(self, prefetch_batch_id: str | None = None) -> list[sqlite3.Row]:
        where = "prefetch_batch_id = ?" if prefetch_batch_id else ""
        return self.list_rows(
            "fj_workflow_prefetch_items",
            where_sql=where,
            values=(prefetch_batch_id,) if prefetch_batch_id else (),
            order_by="created_at, id",
        )

    def list_reservations(self, *, job_id: str | None = None) -> list[sqlite3.Row]:
        where = "job_id = ?" if job_id else ""
        return self.list_rows(
            "fj_workflow_candidate_reservations",
            where_sql=where,
            values=(job_id,) if job_id else (),
        )

    def list_feedback(self, workflow_task_id: str | None = None) -> list[sqlite3.Row]:
        where = "workflow_task_id = ?" if workflow_task_id else ""
        return self.list_rows(
            "fj_workflow_evaluation_feedback",
            where_sql=where,
            values=(workflow_task_id,) if workflow_task_id else (),
            order_by="created_at DESC, id DESC",
        )

    def list_codex_sessions(self) -> list[sqlite3.Row]:
        return self.list_rows("fj_codex_sessions", order_by="updated_at DESC, id DESC")


class LegacyPipelineReadAdapter(PipelineRepository):
    """Workflow Run 历史查看适配器；只读且不参与新业务 owner 选择。"""

    @classmethod
    def for_workflow_run(cls, db: Database, workflow_run_id: str) -> "LegacyPipelineReadAdapter":
        owner = resolve_history_read_context(smart_capture_id=None, workflow_run_id=workflow_run_id)
        return cls(db, PipelineQueryScope(owner, history_only=True))


__all__ = [
    "LegacyPipelineReadAdapter",
    "PipelineQueryScope",
    "PipelineRepository",
]
