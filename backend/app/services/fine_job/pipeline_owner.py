from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from typing import Any, Mapping

from backend.app.db import Database
from backend.app.errors import AppError


SMART_CAPTURE_SNAPSHOT_CAPABILITIES = frozenset(
    {"start", "pause", "resume", "retry", "stop"}
)
SMART_CAPTURE_SNAPSHOT_REQUIRED_FIELDS = frozenset(
    {
        "smart_capture_id",
        "source",
        "workflow_run_id",
        "status",
        "stage",
        "waiting_reason",
        "control_cause",
        "state_version",
        "capabilities",
        "progress",
        "result_summary",
        "updated_at",
    }
)


@dataclass(frozen=True)
class PipelineOwnerContext:
    """岗位采集 Pipeline 的 owner-neutral 身份上下文。"""

    smart_capture_id: str
    workflow_run_id: str | None
    source: str
    config: Mapping[str, Any] = field(default_factory=dict)
    contract: Mapping[str, Any] = field(default_factory=dict)

    @property
    def is_linked(self) -> bool:
        return self.workflow_run_id is not None

    @property
    def is_independent(self) -> bool:
        return self.workflow_run_id is None

    @property
    def is_history_only(self) -> bool:
        return self.source == "history"

    @property
    def identity(self) -> str:
        """新业务记录使用 Smart Capture 身份，Workflow ID 只保留父关联。"""
        return self.smart_capture_id


def validate_smart_capture_snapshot_contract(
    snapshot: Mapping[str, Any],
) -> dict[str, object]:
    """固定 Smart Capture 快照字段，避免 Workflow-only identity 混入新运行链。"""
    missing = SMART_CAPTURE_SNAPSHOT_REQUIRED_FIELDS.difference(snapshot)
    if missing:
        raise AppError(500, "SMART_CAPTURE_SNAPSHOT_INVALID", "Smart Capture 快照缺少必要字段。")
    smart_capture_id = str(snapshot.get("smart_capture_id") or "").strip()
    if not smart_capture_id:
        raise AppError(500, "SMART_CAPTURE_ID_REQUIRED", "Smart Capture 快照必须以 smart_capture_id 为身份。")
    if "revision" in snapshot:
        raise AppError(500, "SMART_CAPTURE_SNAPSHOT_INVALID", "Smart Capture 快照不能返回第二版本字段。")
    state_version = snapshot.get("state_version")
    if isinstance(state_version, bool) or not isinstance(state_version, int) or state_version < 1:
        raise AppError(500, "SMART_CAPTURE_SNAPSHOT_INVALID", "Smart Capture 快照 state_version 必须是正整数。")
    capabilities = snapshot.get("capabilities")
    if not isinstance(capabilities, Mapping) or set(capabilities) != set(
        SMART_CAPTURE_SNAPSHOT_CAPABILITIES
    ) or any(not isinstance(value, bool) for value in capabilities.values()):
        raise AppError(500, "SMART_CAPTURE_SNAPSHOT_INVALID", "Smart Capture 快照 capabilities 不符合固定契约。")
    return dict(snapshot)


def resolve_pipeline_owner(
    *,
    smart_capture_id: str,
    workflow_run_id: str | None,
    source: str,
    config: Mapping[str, Any] | None = None,
    contract: Mapping[str, Any] | None = None,
) -> PipelineOwnerContext:
    """构造 linked/independent 共用的 Pipeline owner 上下文。"""
    normalized_capture_id = str(smart_capture_id or "").strip()
    if not normalized_capture_id:
        raise AppError(422, "SMART_CAPTURE_ID_REQUIRED", "Pipeline owner 缺少 smart_capture_id。")
    normalized_source = str(source or "").strip()
    if normalized_source not in {"task_cockpit", "boss_capture"}:
        raise AppError(422, "VALIDATION_FAILED", "岗位采集任务来源无效。")
    normalized_workflow_id = str(workflow_run_id or "").strip() or None
    if normalized_source == "task_cockpit" and normalized_workflow_id is None:
        raise AppError(422, "WORKFLOW_RUN_ID_REQUIRED", "驾驶舱岗位采集必须关联 Workflow Run。")
    return PipelineOwnerContext(
        smart_capture_id=normalized_capture_id,
        workflow_run_id=normalized_workflow_id,
        source=normalized_source,
        config=dict(config or {}),
        contract=dict(contract or {}),
    )


def get_pipeline_owner(db: Database, smart_capture_id: str) -> PipelineOwnerContext:
    """按 Smart Capture 读取 owner；不会从 latest Workflow 推断身份。"""
    with db.connect() as connection:
        row = connection.execute(
            """
            SELECT id, workflow_run_id, source, search_config_json, execution_config_json
            FROM fj_smart_captures
            WHERE id = ?
            """,
            (smart_capture_id,),
        ).fetchone()
    if row is None:
        raise AppError(404, "SMART_CAPTURE_NOT_FOUND", "岗位采集任务不存在。")
    return _context_from_row(row)


def get_pipeline_owner_by_workflow_run(
    db: Database, workflow_run_id: str
) -> PipelineOwnerContext:
    """仅用于 linked 父关联读取，明确拒绝把历史 Run 当 current owner。"""
    with db.connect() as connection:
        row = connection.execute(
            """
            SELECT id, workflow_run_id, source, search_config_json, execution_config_json
            FROM fj_smart_captures
            WHERE workflow_run_id = ?
            """,
            (workflow_run_id,),
        ).fetchone()
    if row is None:
        raise AppError(404, "SMART_CAPTURE_NOT_FOUND", "当前 Workflow Run 没有关联岗位采集任务。")
    return _context_from_row(row)


def resolve_history_read_context(
    *, smart_capture_id: str | None, workflow_run_id: str
) -> PipelineOwnerContext:
    """构造只读历史上下文，防止历史 ID 回退成 live Pipeline identity。"""
    normalized_workflow_id = str(workflow_run_id or "").strip()
    if not normalized_workflow_id:
        raise AppError(422, "WORKFLOW_RUN_ID_REQUIRED", "历史查看缺少 Workflow Run ID。")
    return PipelineOwnerContext(
        smart_capture_id=str(smart_capture_id or "").strip(),
        workflow_run_id=normalized_workflow_id,
        source="history",
        config={},
        contract={"read_only": True},
    )


def _context_from_row(row: sqlite3.Row) -> PipelineOwnerContext:
    return resolve_pipeline_owner(
        smart_capture_id=str(row["id"]),
        workflow_run_id=row["workflow_run_id"],
        source=str(row["source"]),
        config=_load_json(row["execution_config_json"]),
        contract=_load_json(row["search_config_json"]),
    )


def _load_json(value: object) -> dict[str, Any]:
    try:
        loaded = json.loads(str(value or "{}"))
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}
