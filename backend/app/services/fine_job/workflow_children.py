from __future__ import annotations

import json
import sqlite3
from typing import Any

from backend.app.db import Database
from backend.app.utils import new_id, utc_now


SMART_CAPTURE_CHILD_TYPE = "smart_capture"


def create_child_relation_in_connection(
    connection: sqlite3.Connection,
    *,
    workflow_run_id: str,
    child_ref: str,
    status: str,
    capabilities: dict[str, bool],
    waiting_reason: str = "",
    control_cause: str = "",
    result_summary: dict[str, object] | None = None,
    child_state_version: int = 1,
    created_at: str | None = None,
) -> str:
    """在父子身份事务内创建唯一的 Smart Capture child relation。"""
    now = created_at or utc_now()
    relation_id = new_id()
    connection.execute(
        """
        INSERT INTO fj_workflow_children (
          id, workflow_run_id, child_type, child_ref, sequence, status,
          control_state, waiting_reason, control_cause, capabilities_json,
          result_summary_json, created_at, updated_at, state_version,
          child_state_version
        ) VALUES (?, ?, ?, ?, 1, ?, 'active', ?, ?, ?, ?, ?, ?, 1, ?)
        """,
        (
            relation_id,
            workflow_run_id,
            SMART_CAPTURE_CHILD_TYPE,
            child_ref,
            status,
            waiting_reason,
            control_cause,
            _dump(capabilities),
            _dump(result_summary or {}),
            now,
            now,
            max(1, int(child_state_version)),
        ),
    )
    return relation_id


def project_smart_capture_in_connection(
    connection: sqlite3.Connection,
    capture_row: sqlite3.Row | dict[str, object],
    *,
    capabilities: dict[str, bool],
    now: str | None = None,
) -> None:
    """把 child 当前快照投影到 relation，relation 自身版本独立递增。"""
    workflow_run_id = str(_row_value(capture_row, "workflow_run_id") or "")
    child_ref = str(_row_value(capture_row, "id") or "")
    if not workflow_run_id or not child_ref:
        return
    relation = connection.execute(
        """
        SELECT * FROM fj_workflow_children
        WHERE workflow_run_id = ? AND child_type = ? AND child_ref = ?
        """,
        (workflow_run_id, SMART_CAPTURE_CHILD_TYPE, child_ref),
    ).fetchone()
    if relation is None:
        return

    child_version = max(1, int(_row_value(capture_row, "state_version") or 1))
    if child_version <= int(relation["child_state_version"] or 0):
        return
    status = str(_row_value(capture_row, "status") or "pending")
    control_cause = str(_row_value(capture_row, "control_cause") or "")
    control_state = _control_state_for_child(status, control_cause)
    waiting_reason = str(_row_value(capture_row, "waiting_reason") or "")
    result_summary = _load_json(str(_row_value(capture_row, "result_summary_json") or "{}"))
    timestamp = now or utc_now()
    started_at = relation["started_at"]
    if started_at is None and status in {
        "running",
        "pausing",
        "paused",
        "waiting_next_batch",
        "waiting_for_user",
        "interrupted",
        "completed",
        "stopped",
        "failed",
    }:
        started_at = timestamp
    completed_at = _row_value(capture_row, "completed_at")
    observable = (
        status,
        control_state,
        waiting_reason,
        control_cause,
        _dump(capabilities),
        _dump(result_summary),
        started_at,
        completed_at,
    )
    previous = (
        str(relation["status"]),
        str(relation["control_state"]),
        str(relation["waiting_reason"] or ""),
        str(relation["control_cause"] or ""),
        str(relation["capabilities_json"] or "{}"),
        str(relation["result_summary_json"] or "{}"),
        relation["started_at"],
        relation["completed_at"],
    )
    if observable == previous and child_version == int(relation["child_state_version"] or 0):
        return
    relation_version = max(1, int(relation["state_version"] or 1)) + 1
    connection.execute(
        """
        UPDATE fj_workflow_children
        SET status = ?, control_state = ?, waiting_reason = ?, control_cause = ?,
            capabilities_json = ?, result_summary_json = ?, started_at = ?,
            completed_at = ?, updated_at = ?, state_version = ?, child_state_version = ?
        WHERE id = ?
        """,
        (
            status,
            control_state,
            waiting_reason,
            control_cause,
            observable[4],
            observable[5],
            started_at,
            completed_at,
            timestamp,
            relation_version,
            child_version,
            str(relation["id"]),
        ),
    )


def list_workflow_children(db: Database, workflow_run_id: str) -> list[dict[str, object]]:
    """返回父层稳定读取的 child relation 快照。"""
    with db.connect() as connection:
        rows = connection.execute(
            """
            SELECT * FROM fj_workflow_children
            WHERE workflow_run_id = ?
            ORDER BY sequence, created_at, id
            """,
            (workflow_run_id,),
        ).fetchall()
    return [_serialize(row) for row in rows]


def get_workflow_child(db: Database, workflow_run_id: str, child_relation_id: str) -> dict[str, object] | None:
    """按父 Run 和 relation identity 查询单个 child，避免 child_ref 跨父任务误匹配。"""
    with db.connect() as connection:
        row = connection.execute(
            """
            SELECT * FROM fj_workflow_children
            WHERE workflow_run_id = ? AND id = ?
            """,
            (workflow_run_id, child_relation_id),
        ).fetchone()
    return _serialize(row) if row is not None else None


def _serialize(row: sqlite3.Row) -> dict[str, object]:
    child_ref = str(row["child_ref"])
    return {
        "child_relation_id": str(row["id"]),
        "workflow_run_id": str(row["workflow_run_id"]),
        "child_type": str(row["child_type"]),
        "child_ref": child_ref,
        "smart_capture_id": child_ref if str(row["child_type"]) == SMART_CAPTURE_CHILD_TYPE else None,
        "sequence": int(row["sequence"]),
        "status": str(row["status"]),
        "control_state": str(row["control_state"]),
        "waiting_reason": str(row["waiting_reason"] or ""),
        "control_cause": str(row["control_cause"] or ""),
        "capabilities": _load_json(str(row["capabilities_json"] or "{}")),
        "result_summary": _load_json(str(row["result_summary_json"] or "{}")),
        "started_at": row["started_at"],
        "completed_at": row["completed_at"],
        "created_at": str(row["created_at"]),
        "updated_at": str(row["updated_at"]),
        "state_version": int(row["state_version"]),
        "child_state_version": int(row["child_state_version"]),
    }


def _control_state_for_child(status: str, control_cause: str) -> str:
    if status == "failed":
        return "child_failed_waiting_decision"
    if status == "stopped":
        return "child_cancelled_waiting_decision"
    if status == "interrupted":
        return "waiting_child_interrupted"
    if status in {"pausing", "paused"}:
        return "paused" if control_cause == "parent_pause" else "waiting_child_paused"
    return "active"


def _dump(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _load_json(value: str) -> dict[str, Any]:
    try:
        loaded = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _row_value(row: sqlite3.Row | dict[str, object], key: str) -> object:
    if isinstance(row, dict):
        return row.get(key)
    return row[key]
