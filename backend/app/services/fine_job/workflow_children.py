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
          child_state_version, transition_id
        ) VALUES (?, ?, ?, ?, 1, ?, 'active', ?, ?, ?, ?, ?, ?, 1, ?, ?)
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
            "",
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
    transition_id = str(_row_value(capture_row, "transition_id") or "")
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
        transition_id,
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
        str(relation["transition_id"] or ""),
    )
    if observable == previous and child_version == int(relation["child_state_version"] or 0):
        return
    relation_version = max(1, int(relation["state_version"] or 1)) + 1
    connection.execute(
        """
        UPDATE fj_workflow_children
        SET status = ?, control_state = ?, waiting_reason = ?, control_cause = ?,
            capabilities_json = ?, result_summary_json = ?, started_at = ?,
            completed_at = ?, updated_at = ?, state_version = ?, child_state_version = ?,
            transition_id = ?
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
            transition_id,
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
        "transition_id": str(row["transition_id"] or ""),
    }


def record_child_event_in_connection(
    connection: sqlite3.Connection,
    *,
    child_relation_id: str,
    child_type: str,
    child_ref: str,
    child_status: str,
    transition_id: str,
    state_version: int,
    waiting_reason: str = "",
    control_cause: str = "",
    result_summary: dict[str, object] | None = None,
    event_id: str | None = None,
    occurred_at: str | None = None,
) -> str:
    """持久化 linked child 事件，并在同一事务内幂等消费。"""
    relation = connection.execute(
        "SELECT id FROM fj_workflow_children WHERE id = ? AND child_type = ? AND child_ref = ?",
        (child_relation_id, child_type, child_ref),
    ).fetchone()
    if relation is None:
        return ""
    actual_event_id = event_id or new_id()
    occurred = occurred_at or utc_now()
    inserted = connection.execute(
        """
        INSERT OR IGNORE INTO fj_workflow_child_events (
          event_id, transition_id, child_relation_id, child_type, child_ref,
          child_status, waiting_reason, control_cause, result_summary_json,
          occurred_at, state_version
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            actual_event_id,
            transition_id,
            child_relation_id,
            child_type,
            child_ref,
            child_status,
            waiting_reason,
            control_cause,
            _dump(result_summary or {}),
            occurred,
            max(1, int(state_version)),
        ),
    )
    if inserted.rowcount:
        consume_child_event_in_connection(connection, actual_event_id, consumed_at=occurred)
    return actual_event_id


def consume_child_event_in_connection(
    connection: sqlite3.Connection,
    event_id: str,
    *,
    consumed_at: str | None = None,
) -> str:
    """按 relation identity 和 child lifecycle 版本消费事件。"""
    event = connection.execute(
        "SELECT * FROM fj_workflow_child_events WHERE event_id = ?",
        (event_id,),
    ).fetchone()
    if event is None:
        return "missing"
    relation = connection.execute(
        "SELECT * FROM fj_workflow_children WHERE id = ?",
        (str(event["child_relation_id"]),),
    ).fetchone()
    if relation is None:
        return "missing"
    event_version = max(1, int(event["state_version"] or 1))
    current_version = int(relation["child_state_version"] or 0)
    if event_version <= current_version:
        connection.execute(
            "UPDATE fj_workflow_child_events SET consumed_at = COALESCE(consumed_at, ?) WHERE event_id = ?",
            (consumed_at or utc_now(), event_id),
        )
        return "stale"

    event_status = str(event["child_status"])
    relation_status = str(relation["status"])
    if relation_status in {"completed", "stopped", "failed"} and relation_status != event_status:
        connection.execute(
            "UPDATE fj_workflow_child_events SET consumed_at = COALESCE(consumed_at, ?) WHERE event_id = ?",
            (consumed_at or utc_now(), event_id),
        )
        return "terminal_conflict"

    now = consumed_at or utc_now()
    control_cause = str(event["control_cause"] or "")
    control_state = _control_state_for_child(event_status, control_cause)
    relation_version = max(1, int(relation["state_version"] or 1)) + 1
    capabilities = _capabilities_for_event(event_status)
    connection.execute(
        """
        UPDATE fj_workflow_children
        SET status = ?, control_state = ?, waiting_reason = ?, control_cause = ?,
            capabilities_json = ?, result_summary_json = ?, completed_at = CASE
              WHEN ? IN ('completed', 'stopped', 'failed') THEN COALESCE(completed_at, ?)
              ELSE completed_at END,
            updated_at = ?, state_version = ?, child_state_version = ?, transition_id = ?
        WHERE id = ?
        """,
        (
            event_status,
            control_state,
            str(event["waiting_reason"] or ""),
            control_cause,
            _dump(capabilities),
            str(event["result_summary_json"] or "{}"),
            event_status,
            now,
            now,
            relation_version,
            event_version,
            str(event["transition_id"] or ""),
            str(relation["id"]),
        ),
    )

    parent = connection.execute(
        "SELECT * FROM fj_workflow_runs WHERE id = ?",
        (str(relation["workflow_run_id"]),),
    ).fetchone()
    if parent is not None and event_status in {"stopped", "failed", "interrupted"}:
        parent_status = str(parent["status"])
        parent_control_state = str(parent["control_state"] or "active")
        parent_control_cause = str(parent["control_cause"] or "")
        parent_has_higher_priority_control = (
            parent_control_state == "paused" and parent_control_cause == "parent_pause"
        )
        if (
            parent_status not in {"cancelled", "completed", "completed_with_errors", "failed"}
            and parent_control_state != "cancelled"
            and not parent_has_higher_priority_control
        ):
            if event_status == "stopped":
                next_state = "child_cancelled_waiting_decision"
                next_reason = "child_stopped"
                next_cause = control_cause or "child_user_stop"
            elif event_status == "failed":
                next_state = "child_failed_waiting_decision"
                next_reason = "child_failed"
                next_cause = control_cause or "child_failure"
            else:
                next_state = "waiting_child_interrupted"
                next_reason = str(event["waiting_reason"] or "capture_interrupted")
                next_cause = control_cause or "recovery"
            connection.execute(
                """
                UPDATE fj_workflow_runs
                SET status = 'waiting_for_user', current_step = 'waiting_for_user',
                    next_action = 'await_user_choice', next_action_reason = ?,
                    waiting_for_user = 1, paused = 0, control_state = ?,
                    waiting_reason = ?, control_cause = ?, stop_reason = ?, state_version = state_version + 1,
                    transition_id = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    "子任务已停止，请选择跳过该子任务继续或结束父任务。"
                    if event_status == "stopped"
                    else "子任务执行失败，请选择跳过该子任务继续或结束父任务。"
                    if event_status == "failed"
                    else "子任务执行已中断，请明确恢复后再继续。",
                    next_state,
                    next_reason,
                    next_cause,
                    "child_stopped" if event_status == "stopped" else "child_failed" if event_status == "failed" else next_reason,
                    str(event["transition_id"] or ""),
                    now,
                    str(parent["id"]),
                ),
            )
    connection.execute(
        "UPDATE fj_workflow_child_events SET consumed_at = COALESCE(consumed_at, ?) WHERE event_id = ?",
        (now, event_id),
    )
    return "applied"


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


def _capabilities_for_event(status: str) -> dict[str, bool]:
    """事件消费时同步固定五项 child capability，failed 不提供 retry。"""
    if status == "interrupted":
        return {"start": False, "pause": False, "resume": True, "retry": True, "stop": True}
    return {"start": False, "pause": False, "resume": False, "retry": False, "stop": False}


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
