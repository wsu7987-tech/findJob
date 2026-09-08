from __future__ import annotations

import json

import pytest

from backend.app.errors import AppError
from backend.app.services.fine_job import action_scheduler, boss_executor
from backend.app.services.fine_job.action_store import sync_legacy_action


NOW = "2026-09-08T00:00:00Z"


def _seed_bridge(test_db) -> tuple[str, str]:
    with test_db.connect() as connection:
        connection.execute("INSERT INTO fj_boss_capture_batches (id, keyword, city, created_at, updated_at) VALUES ('capture', 'Python', '广州', ?, ?)", (NOW, NOW))
        connection.execute("""INSERT INTO fj_boss_jobs (id, dedupe_key, encrypt_job_id, title, company_name, first_collected_at, last_collected_at, latest_batch_id)
                              VALUES ('job', 'job-key', 'encrypt-job', '后端工程师', '示例科技', ?, ?, 'capture')""", (NOW, NOW))
        connection.execute("INSERT INTO fj_job_evaluations (id, job_id, source, decision, created_at) VALUES ('evaluation', 'job', 'rules', 'review', ?)", (NOW,))
        connection.execute("INSERT INTO fj_review_items (id, job_id, evaluation_id, ai_decision, created_at, updated_at) VALUES ('review', 'job', 'evaluation', 'review', ?, ?)", (NOW, NOW))
        connection.execute("""INSERT INTO fj_automation_actions (id, job_id, evaluation_id, review_item_id, action_type, status, idempotency_key, payload_json, execution_state, created_at, updated_at)
                              VALUES ('legacy', 'job', 'evaluation', 'review', 'BOSS_DEFAULT_GREETING', 'queued', 'greeting-key', ?, 'queued', ?, ?)""", (json.dumps({"message": "您好"}), NOW, NOW))
        connection.execute("""INSERT INTO fj_chat_sessions (id, account_uid, peer_uid, encrypt_peer_uid, security_id, job_id, encrypt_job_id, status, created_at, updated_at)
                              VALUES ('session', 'account-1', 'peer', 'encrypt-peer', 'security', 'job', 'encrypt-job', 'active', ?, ?)""", (NOW, NOW))
        connection.execute("""INSERT INTO fj_boss_executor_instances (id, token_hash, protocol_version, plugin_version, queue_state, created_at, updated_at)
                              VALUES ('executor', 'token', '1.1', 'test', 'running', ?, ?)""", (NOW, NOW))
        unified_id = str(sync_legacy_action(connection, source_table="fj_automation_actions", source_id="legacy"))
    boss_chat_runtime = {"send_enabled": True}
    from backend.app.services.fine_job import boss_chat
    boss_chat.update_runtime(test_db, boss_chat_runtime)
    return "legacy", unified_id


def _identity() -> dict[str, object]:
    return {"job": {"encryptJobId": "encrypt-job", "securityId": "security", "contacted": False}}


def test_page_match_then_bridge_claim_preflight_dispatch_and_result_sync(test_db) -> None:
    legacy_id, unified_id = _seed_bridge(test_db)

    boss_executor.match_task(test_db, "executor", legacy_id, 0)
    with test_db.connect() as connection:
        before = connection.execute("SELECT status FROM fj_actions WHERE id=?", (unified_id,)).fetchone()
    assert before["status"] == "queued"

    bridge = boss_executor.prepare_greeting_unified_bridge(test_db, "executor", legacy_id, _identity())
    assert bridge == {"ok": True, "unified_action_id": unified_id, "unified_execution_epoch": 1}
    with test_db.connect() as connection:
        unified = connection.execute("SELECT status FROM fj_actions WHERE id=?", (unified_id,)).fetchone()
        legacy = connection.execute("SELECT status, dispatch_started_at FROM fj_automation_actions WHERE id=?", (legacy_id,)).fetchone()
    assert unified["status"] == "dispatching"
    assert legacy["status"] in {"running", "leased"} and legacy["dispatch_started_at"]

    boss_executor.complete_task(test_db, "executor", legacy_id, {
        "execution_epoch": 0,
        "outcome": "accepted",
        "status_code": "BOSS_REQUEST_ACCEPTED",
        "unified_action_id": unified_id,
        "unified_execution_epoch": 1,
    })
    with test_db.connect() as connection:
        unified = connection.execute("SELECT status, outcome FROM fj_actions WHERE id=?", (unified_id,)).fetchone()
        legacy = connection.execute("SELECT status FROM fj_automation_actions WHERE id=?", (legacy_id,)).fetchone()
    assert (unified["status"], unified["outcome"]) == ("accepted", "accepted")
    assert legacy["status"] == "unknown"


def test_network_unknown_result_keeps_unified_identity_for_completion(test_db) -> None:
    legacy_id, unified_id = _seed_bridge(test_db)
    boss_executor.match_task(test_db, "executor", legacy_id, 0)
    bridge = boss_executor.prepare_greeting_unified_bridge(test_db, "executor", legacy_id, _identity())

    boss_executor.complete_task(test_db, "executor", legacy_id, {
        "execution_epoch": 0,
        "outcome": "unknown",
        "status_code": "BOSS_REQUEST_NETWORK_UNKNOWN",
        "unified_action_id": bridge["unified_action_id"],
        "unified_execution_epoch": bridge["unified_execution_epoch"],
    })

    with test_db.connect() as connection:
        unified = connection.execute("SELECT status, outcome FROM fj_actions WHERE id=?", (unified_id,)).fetchone()
        legacy = connection.execute("SELECT status, execution_state FROM fj_automation_actions WHERE id=?", (legacy_id,)).fetchone()
    assert (unified["status"], unified["outcome"]) == ("unknown", "unknown")
    assert (legacy["status"], legacy["execution_state"]) == ("unknown", "unknown")


def test_mapped_greeting_rejects_result_without_unified_identity(test_db) -> None:
    legacy_id, _ = _seed_bridge(test_db)
    boss_executor.match_task(test_db, "executor", legacy_id, 0)

    with pytest.raises(AppError) as error:
        boss_executor.complete_task(test_db, "executor", legacy_id, {
            "execution_epoch": 0,
            "outcome": "unknown",
            "status_code": "BOSS_REQUEST_NETWORK_UNKNOWN",
        })

    assert error.value.error_category == "UNIFIED_EXECUTION_IDENTITY_REQUIRED"
    with test_db.connect() as connection:
        legacy = connection.execute("SELECT status, execution_state FROM fj_automation_actions WHERE id=?", (legacy_id,)).fetchone()
    assert (legacy["status"], legacy["execution_state"]) == ("leased", "running")


def test_missing_or_terminal_mapping_fails_closed_before_sender(test_db) -> None:
    legacy_id, unified_id = _seed_bridge(test_db)
    boss_executor.match_task(test_db, "executor", legacy_id, 0)
    with test_db.connect() as connection:
        connection.execute("DELETE FROM fj_actions WHERE id=?", (unified_id,))
    assert boss_executor.prepare_greeting_unified_bridge(test_db, "executor", legacy_id, _identity())["reason_code"] == "mapping_missing"
    with test_db.connect() as connection:
        legacy = connection.execute("SELECT status, execution_state FROM fj_automation_actions WHERE id=?", (legacy_id,)).fetchone()
    assert (legacy["status"], legacy["execution_state"]) == ("blocked", "blocked")


def test_terminal_unified_mapping_never_restarts_sender(test_db) -> None:
    legacy_id, unified_id = _seed_bridge(test_db)
    boss_executor.match_task(test_db, "executor", legacy_id, 0)
    with test_db.connect() as connection:
        connection.execute("UPDATE fj_actions SET status='unknown' WHERE id=?", (unified_id,))
    assert boss_executor.prepare_greeting_unified_bridge(test_db, "executor", legacy_id, _identity())["reason_code"] == "unified_action_terminal"
    with test_db.connect() as connection:
        legacy = connection.execute("SELECT status, execution_state FROM fj_automation_actions WHERE id=?", (legacy_id,)).fetchone()
    assert (legacy["status"], legacy["execution_state"]) == ("unknown", "unknown")


def test_ambiguous_mapping_fails_closed_and_calls_closure(monkeypatch) -> None:
    closed: list[tuple[str, str]] = []
    monkeypatch.setattr(
        boss_executor,
        "_close_greeting_bridge_rejection",
        lambda _db, task_id, reason_code, **_kwargs: closed.append((task_id, reason_code)),
    )

    class Cursor:
        def fetchall(self):
            return [{"id": "a"}, {"id": "b"}]

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, *_args):
            return Cursor()

    class Database:
        def connect(self):
            return Connection()

    result = boss_executor.prepare_greeting_unified_bridge(Database(), "executor", "legacy", _identity())

    assert result == {"ok": False, "reason_code": "mapping_ambiguous"}
    assert closed == [("legacy", "mapping_ambiguous")]


def test_claim_conflict_requeues_legacy_instead_of_leaving_running(test_db) -> None:
    legacy_id, unified_id = _seed_bridge(test_db)
    boss_executor.match_task(test_db, "executor", legacy_id, 0)
    with test_db.connect() as connection:
        connection.execute(
            "UPDATE fj_actions SET status='claimed', lease_owner='other', execution_epoch=1 WHERE id=?",
            (unified_id,),
        )

    result = boss_executor.prepare_greeting_unified_bridge(test_db, "executor", legacy_id, _identity())

    assert result == {"ok": False, "reason_code": "unified_claim_failed"}
    with test_db.connect() as connection:
        legacy = connection.execute("SELECT status, execution_state, executor_id FROM fj_automation_actions WHERE id=?", (legacy_id,)).fetchone()
    assert (legacy["status"], legacy["execution_state"], legacy["executor_id"]) == ("queued", "queued", None)
