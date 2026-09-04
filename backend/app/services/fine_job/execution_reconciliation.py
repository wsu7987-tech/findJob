from __future__ import annotations

import json
import sqlite3
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Any

from backend.app.db import Database
from backend.app.utils import new_id, utc_now


ACTION_TABLES = {
    "automation_action": "fj_automation_actions",
    "chat_send_action": "fj_chat_send_actions",
}
EVIDENCE_LEVELS = {"direct", "strong_inferred", "weak_inferred"}
SUCCESS_EVIDENCE_TYPES = {
    "outbound_message_observed",
    "conversation_created",
    "greeting_state_changed",
    "page_state_confirmed",
    "protocol_acknowledged",
}
OBSERVATION_WINDOW_BEFORE_SECONDS = 30
OBSERVATION_WINDOW_AFTER_SECONDS = 300


class ExecutionReconciler:
    def __init__(self, db: Database) -> None:
        self.db = db

    def reconcile_evidence(self, evidence_id: str) -> dict[str, Any] | None:
        with self.db.connect() as connection:
            return reconcile_evidence_with_connection(connection, evidence_id)


def record_execution_evidence(
    db: Database,
    **kwargs: Any,
) -> tuple[dict[str, Any], bool, dict[str, Any] | None]:
    with db.connect() as connection:
        return record_execution_evidence_with_connection(connection, **kwargs)


def record_execution_evidence_with_connection(
    connection: sqlite3.Connection,
    *,
    action_ref_type: str,
    action_ref_id: str,
    evidence_type: str,
    source: str,
    source_ref_type: str,
    source_ref_id: str,
    observed_at: str,
    evidence_level: str,
    dedupe_key: str,
    confidence: float = 1.0,
    payload: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], bool, dict[str, Any] | None]:
    if action_ref_type not in ACTION_TABLES:
        raise ValueError(f"Unsupported action reference type: {action_ref_type}")
    if evidence_level not in EVIDENCE_LEVELS:
        raise ValueError(f"Unsupported evidence level: {evidence_level}")
    if not 0 <= confidence <= 1:
        raise ValueError("Evidence confidence must be between 0 and 1")

    existing = connection.execute(
        "SELECT * FROM fj_execution_evidence WHERE dedupe_key = ?",
        (dedupe_key,),
    ).fetchone()
    if existing is not None:
        reconciliation = reconcile_evidence_with_connection(connection, str(existing["id"]))
        return _evidence_row(existing), False, reconciliation

    evidence_id = new_id()
    created_at = utc_now()
    connection.execute(
        """
        INSERT INTO fj_execution_evidence (
          id, action_ref_type, action_ref_id, evidence_type, source,
          source_ref_type, source_ref_id, observed_at, confidence,
          evidence_level, payload_json, dedupe_key, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            evidence_id,
            action_ref_type,
            action_ref_id,
            evidence_type,
            source,
            source_ref_type,
            source_ref_id,
            observed_at,
            confidence,
            evidence_level,
            json.dumps(payload or {}, ensure_ascii=False),
            dedupe_key,
            created_at,
        ),
    )
    reconciliation = reconcile_evidence_with_connection(connection, evidence_id)
    row = connection.execute(
        "SELECT * FROM fj_execution_evidence WHERE id = ?",
        (evidence_id,),
    ).fetchone()
    return _evidence_row(row), True, reconciliation


def reconcile_evidence_with_connection(
    connection: sqlite3.Connection,
    evidence_id: str,
) -> dict[str, Any] | None:
    evidence = connection.execute(
        "SELECT * FROM fj_execution_evidence WHERE id = ?",
        (evidence_id,),
    ).fetchone()
    if evidence is None:
        return None
    action_ref_type = str(evidence["action_ref_type"])
    table = ACTION_TABLES.get(action_ref_type)
    if table is None:
        return None
    action = connection.execute(
        f"SELECT * FROM {table} WHERE id = ?",
        (evidence["action_ref_id"],),
    ).fetchone()
    if action is None:
        return None

    previous_status = str(action["canonical_status"] or _canonical_from_raw(str(action["status"])))
    if previous_status in {"succeeded", "failed", "blocked", "cancelled"}:
        return None
    if evidence["evidence_level"] != "direct":
        return None
    if evidence["evidence_type"] not in SUCCESS_EVIDENCE_TYPES:
        return None

    payload = _load_json(evidence["payload_json"])
    if payload.get("confirmed") is False:
        return None
    new_status = "succeeded"
    existing = connection.execute(
        """
        SELECT * FROM fj_execution_reconciliations
        WHERE action_ref_type = ? AND action_ref_id = ? AND evidence_id = ? AND new_status = ?
        """,
        (action_ref_type, action["id"], evidence_id, new_status),
    ).fetchone()
    if existing is not None:
        return dict(existing)

    reason = _reconciliation_reason(str(evidence["evidence_type"]), payload)
    reconciled_at = str(evidence["observed_at"])
    reconciliation_id = new_id()
    connection.execute(
        f"""
        UPDATE {table}
        SET canonical_status = ?, canonical_updated_at = ?, canonical_reason = ?
        WHERE id = ?
        """,
        (new_status, reconciled_at, reason, action["id"]),
    )
    connection.execute(
        """
        INSERT INTO fj_execution_reconciliations (
          id, action_ref_type, action_ref_id, previous_status, new_status,
          reconciled_at, reconciliation_reason, evidence_id, evidence_level, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            reconciliation_id,
            action_ref_type,
            action["id"],
            previous_status,
            new_status,
            reconciled_at,
            reason,
            evidence_id,
            evidence["evidence_level"],
            utc_now(),
        ),
    )
    row = connection.execute(
        "SELECT * FROM fj_execution_reconciliations WHERE id = ?",
        (reconciliation_id,),
    ).fetchone()
    return dict(row) if row else None


def observe_outbound_chat_message(
    connection: sqlite3.Connection,
    *,
    message_id: str,
    observed_account_uid: str | None = None,
) -> list[dict[str, Any]]:
    message = connection.execute(
        """
        SELECT m.*, s.account_uid, s.job_id
        FROM fj_chat_messages m
        JOIN fj_chat_sessions s ON s.id = m.session_id
        WHERE m.id = ?
        """,
        (message_id,),
    ).fetchone()
    if message is None or message["direction"] != "outbound" or message["source"] == "manual":
        return []
    if observed_account_uid and observed_account_uid != message["account_uid"]:
        return []

    observed_time = _parse_time(str(message["sent_at"]))
    if observed_time is None:
        return []
    rows = connection.execute(
        """
        SELECT a.*
        FROM fj_chat_send_actions a
        WHERE a.session_id = ?
          AND a.status IN ('dispatching', 'accepted', 'unknown')
          AND a.dispatched_at IS NOT NULL
        ORDER BY a.dispatched_at DESC, a.id DESC
        """,
        (message["session_id"],),
    ).fetchall()

    exact_mid = [
        row for row in rows
        if message["client_mid"] and row["client_mid"] and message["client_mid"] == row["client_mid"]
        and _within_window(row["dispatched_at"], observed_time)
    ]
    match_method = "client_mid"
    matches = exact_mid
    if not matches:
        normalized = normalize_message_text(str(message["content"] or ""))
        matches = [
            row for row in rows
            if normalized
            and normalize_message_text(str(row["text"] or "")) == normalized
            and _within_window(row["dispatched_at"], observed_time)
        ]
        match_method = "normalized_text"
    # 文本匹配必须唯一；多条候选动作时保留消息事实但不反向确认具体 action。
    if len(matches) != 1:
        return []

    action = matches[0]
    evidence, _, reconciliation = record_execution_evidence_with_connection(
        connection,
        action_ref_type="chat_send_action",
        action_ref_id=str(action["id"]),
        evidence_type="outbound_message_observed",
        source="chat",
        source_ref_type="chat_message",
        source_ref_id=str(message["id"]),
        observed_at=str(message["observed_at"] or message["sent_at"]),
        confidence=1.0,
        evidence_level="direct",
        payload={
            "confirmed": True,
            "match_method": match_method,
            "session_id": str(message["session_id"]),
            "platform_message_id": str(message["platform_message_id"]),
            "client_mid": str(message["client_mid"] or ""),
            "account_uid": str(message["account_uid"]),
        },
        dedupe_key=f"chat_message:{message['id']}:chat_send:{action['id']}:outbound_observed",
    )
    return [{"evidence": evidence, "reconciliation": reconciliation}]


def initialize_execution_observability(connection: sqlite3.Connection) -> None:
    for action_ref_type, table in ACTION_TABLES.items():
        rows = connection.execute(
            f"SELECT * FROM {table} WHERE canonical_status IS NULL"
        ).fetchall()
        for row in rows:
            canonical = _canonical_from_raw(str(row["status"]))
            if action_ref_type == "automation_action" and row["status"] == "succeeded":
                canonical = "unknown"
            connection.execute(
                f"""
                UPDATE {table}
                SET canonical_status = ?, canonical_updated_at = ?, canonical_reason = ?
                WHERE id = ? AND canonical_status IS NULL
                """,
                (canonical, utc_now(), "由兼容迁移保守映射 raw status", row["id"]),
            )

    for action in connection.execute(
        """
        SELECT id, execution_epoch, completed_at, updated_at, result_json
        FROM fj_automation_actions
        WHERE action_type = 'BOSS_DEFAULT_GREETING' AND status = 'succeeded'
        """
    ).fetchall():
        result = _load_json(action["result_json"])
        if result.get("contacted") is not True:
            continue
        record_execution_evidence_with_connection(
            connection,
            action_ref_type="automation_action",
            action_ref_id=str(action["id"]),
            evidence_type="protocol_acknowledged",
            source="executor",
            source_ref_type="automation_result",
            source_ref_id=f"{action['id']}:{action['execution_epoch']}",
            observed_at=str(action["completed_at"] or action["updated_at"]),
            confidence=1.0,
            evidence_level="direct",
            payload={
                "confirmed": True,
                "status_code": str(result.get("statusCode") or ""),
            },
            dedupe_key=(
                f"automation_action:{action['id']}:epoch:{action['execution_epoch']}:protocol_ack"
            ),
        )

    # 真实 outbound 消息可修正历史 unknown/accepted；assistant 占位消息不参与。
    for message in connection.execute(
        """
        SELECT id FROM fj_chat_messages
        WHERE direction = 'outbound'
          AND source != 'manual'
          AND NOT (source = 'assistant' AND platform_message_id LIKE 'assistant:%')
        ORDER BY sent_at ASC, id ASC
        """
    ).fetchall():
        observe_outbound_chat_message(connection, message_id=str(message["id"]))


def set_canonical_from_raw(
    connection: sqlite3.Connection,
    *,
    action_ref_type: str,
    action_ref_id: str,
    raw_status: str,
    updated_at: str,
    reason: str,
) -> None:
    table = ACTION_TABLES[action_ref_type]
    canonical = _canonical_from_raw(raw_status)
    current = connection.execute(
        f"SELECT canonical_status FROM {table} WHERE id = ?",
        (action_ref_id,),
    ).fetchone()
    if current is None or current["canonical_status"] == "succeeded":
        return
    connection.execute(
        f"""
        UPDATE {table}
        SET canonical_status = ?, canonical_updated_at = ?, canonical_reason = ?
        WHERE id = ?
        """,
        (canonical, updated_at, reason, action_ref_id),
    )


def normalize_message_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    return " ".join(normalized.split()).strip()


def _canonical_from_raw(raw_status: str) -> str:
    return {
        "queued": "pending",
        "leased": "pending",
        "running": "pending",
        "dispatching": "dispatching",
        "accepted": "unknown",
        "succeeded": "succeeded",
        "failed": "failed",
        "blocked": "blocked",
        "unknown": "unknown",
        "cancelled": "cancelled",
    }.get(raw_status, "unknown")


def _within_window(dispatched_at: object, observed_time: datetime) -> bool:
    dispatched = _parse_time(str(dispatched_at or ""))
    if dispatched is None:
        return False
    return (
        dispatched - timedelta(seconds=OBSERVATION_WINDOW_BEFORE_SECONDS)
        <= observed_time
        <= dispatched + timedelta(seconds=OBSERVATION_WINDOW_AFTER_SECONDS)
    )


def _parse_time(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _reconciliation_reason(evidence_type: str, payload: dict[str, Any]) -> str:
    if evidence_type == "outbound_message_observed":
        method = str(payload.get("match_method") or "action reference")
        return f"observed matching outbound message ({method})"
    return f"confirmed by {evidence_type}"


def _evidence_row(row: sqlite3.Row) -> dict[str, Any]:
    result = dict(row)
    result["payload"] = _load_json(result.pop("payload_json"))
    return result


def _load_json(value: object) -> dict[str, Any]:
    try:
        loaded = json.loads(str(value or "{}"))
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}
