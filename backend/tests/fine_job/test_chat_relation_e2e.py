from __future__ import annotations

import json

from backend.app.config import load_config
from backend.app.services.fine_job import action_scheduler, boss_chat
from backend.app.services.fine_job.action_store import link_action_messages


def _event(event_id: str, message_id: str, content: str, *, body_type: int = 1) -> dict:
    return {
        "event_id": event_id,
        "event_type": "message",
        "account_uid": "account-1",
        "leader_epoch": 1,
        "message": {
            "platform_message_id": message_id,
            "direction": "inbound",
            "message_type": "text",
            "content": content,
            "sender_uid": "peer-1",
            "receiver_uid": "account-1",
            "peer_uid": "peer-1",
            "encrypt_peer_uid": "encrypt-peer-1",
            "security_id": "security-1",
            "encrypt_job_id": "encrypt-job-1",
            "sent_at": "2026-01-01T00:00:00Z",
            "observed_at": "2026-01-01T00:00:00Z",
            "source": "websocket",
            "raw_body": {"type": body_type, "text": content},
            "raw_meta": {},
        },
    }


def _seed_pending_action(test_db) -> tuple[str, str, str]:
    with test_db.connect() as connection:
        connection.execute(
            """INSERT INTO fj_boss_executor_instances
               (id, token_hash, protocol_version, plugin_version, created_at, updated_at)
               VALUES ('executor', 'token', '1.1', 'test', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')"""
        )
    boss_chat.update_runtime(test_db, {"generation_enabled": False})
    boss_chat.ingest_events(test_db, "executor", [_event("event-a", "platform-a", "您好，我可以沟通。")])
    with test_db.connect() as connection:
        # 测试自动规划路径使用已进入自动会话状态的真实 raw ingest 记录。
        connection.execute("UPDATE fj_chat_sessions SET status='active' WHERE account_uid='account-1'")
        session = connection.execute("SELECT * FROM fj_chat_sessions WHERE account_uid='account-1'").fetchone()
        base_message = connection.execute(
            "SELECT id FROM fj_chat_messages WHERE session_id=? AND platform_message_id='platform-a'",
            (session["id"],),
        ).fetchone()
        action_id = action_scheduler.create_action(
            connection,
            action_type="chat_message",
            account_uid="account-1",
            session_id=str(session["id"]),
            text="我明天下午有时间。",
            base_conversation_revision=int(session["session_version"]),
        )
        link_action_messages(connection, action_id=action_id, message_ids=[str(base_message["id"])], role="trigger")
    boss_chat.update_runtime(test_db, {"send_enabled": True})
    return action_id, str(session["id"]), str(base_message["id"])


def _add_delta(test_db, *, body_type: int = 1, content: str = "另外请问需要带作品集吗？") -> str:
    boss_chat.ingest_events(test_db, "executor", [_event("event-b", "platform-b", content, body_type=body_type)])
    with test_db.connect() as connection:
        row = connection.execute("SELECT id FROM fj_chat_messages WHERE platform_message_id='platform-b'").fetchone()
    return str(row["id"])


def _relation(decision: str):
    def classify(_config, **kwargs):
        return {
            "decision": decision,
            "reason_code": "test_relation",
            "classifier_version": "mock-relation-v1",
            "provider_version": "mock-provider",
            "action_id": kwargs["action_id"],
            "session_id": kwargs["session_id"],
            "base_message_ids": kwargs["base_message_ids"],
            "message_ids": [item["id"] for item in kwargs["messages"]],
            "conversation_revision": kwargs["conversation_revision"],
        }
    return classify


def _claim_and_preflight(test_db, action_id: str, snapshot: dict) -> dict:
    action = action_scheduler.claim_unified_action(test_db, "executor", action_id=action_id, account_uid="account-1")
    assert action is not None
    return action_scheduler.preflight_action(test_db, "executor", action_id, action["execution_epoch"], snapshot)


def _mock_generation(monkeypatch, reply_text: str) -> None:
    monkeypatch.setattr(
        boss_chat,
        "_build_context",
        lambda _db, _connection, _session: {
            "candidate_profile_context": {"profile_id": "profile", "artifact_version": "test"},
        },
    )
    monkeypatch.setattr(
        boss_chat,
        "_chat_completion",
        lambda *_args, **_kwargs: (
            {
                "reply_text": reply_text,
                "warnings": [],
                "decision": "reply",
                "facts_used": [],
                "requires_user_input": False,
                "reason": "offline_test",
            },
            "offline-mock-model",
        ),
    )


def test_no_delta_raw_semantic_preflight_passes_without_relation_provider(test_db) -> None:
    action_id, _session_id, _message_id = _seed_pending_action(test_db)

    snapshot = boss_chat.prepare_unified_chat_preflight(test_db, load_config(), action_id, {})
    result = _claim_and_preflight(test_db, action_id, snapshot)

    assert result["decision"] == "dispatch"


def test_relevant_delta_generates_replacement_action_from_existing_reply_flow(test_db, monkeypatch) -> None:
    action_id, session_id, base_message_id = _seed_pending_action(test_db)
    delta_message_id = _add_delta(test_db)
    monkeypatch.setattr(boss_chat, "classify_pending_reply_delta", _relation("relevant_to_existing_reply"))

    snapshot = boss_chat.prepare_unified_chat_preflight(test_db, load_config(), action_id, {})
    waiting = _claim_and_preflight(test_db, action_id, snapshot)
    assert waiting["decision"] == "waiting"
    task_id = snapshot["relation"]["replacement_reply_task_id"]

    _mock_generation(monkeypatch, "结合您的新问题，我会携带作品集。")
    task = boss_chat.generate_reply(test_db, load_config(), session_id)
    assert task["id"] == task_id
    with test_db.connect() as connection:
        original = connection.execute("SELECT status, session_sequence FROM fj_actions WHERE id=?", (action_id,)).fetchone()
        replacement = connection.execute(
            "SELECT * FROM fj_actions WHERE supersedes_action_id=?", (action_id,)
        ).fetchone()
        coverage = connection.execute(
            "SELECT raw_message_id FROM fj_action_message_links WHERE action_id=?",
            (replacement["id"],),
        ).fetchall()
    assert original["status"] == "superseded"
    assert replacement["status"] == "awaiting_confirmation"
    assert replacement["session_sequence"] == original["session_sequence"]
    assert int(replacement["revision_no"]) == 2
    assert {row["raw_message_id"] for row in coverage} == {base_message_id, delta_message_id}

    confirmed = boss_chat.confirm_reply(test_db, task_id, {
        "final_text": "结合您的新问题，我会携带作品集。",
        "based_on_message_id": delta_message_id,
        "based_on_session_version": 2,
    })
    assert confirmed["status"] == "queued"


def test_independent_delta_waits_for_queued_b_action_then_releases_a(test_db, monkeypatch) -> None:
    action_id, session_id, _base_message_id = _seed_pending_action(test_db)
    delta_message_id = _add_delta(test_db)
    monkeypatch.setattr(boss_chat, "classify_pending_reply_delta", _relation("independent_reply_required"))

    snapshot = boss_chat.prepare_unified_chat_preflight(test_db, load_config(), action_id, {})
    waiting = _claim_and_preflight(test_db, action_id, snapshot)
    assert waiting["decision"] == "waiting"
    task_id = snapshot["relation"]["independent_reply_task_id"]
    _mock_generation(monkeypatch, "作品集可以在面试时带来。")
    task = boss_chat.generate_reply(test_db, load_config(), session_id)
    assert task["id"] == task_id and task["status"] == "awaiting_review"

    still_waiting_snapshot = boss_chat.prepare_unified_chat_preflight(test_db, load_config(), action_id, {})
    still_waiting = _claim_and_preflight(test_db, action_id, still_waiting_snapshot)
    assert still_waiting["decision"] == "waiting"
    followup = boss_chat.confirm_reply(test_db, task_id, {
        "final_text": "作品集可以在面试时带来。",
        "based_on_message_id": delta_message_id,
        "based_on_session_version": 2,
    })
    assert followup["status"] == "queued"

    ready_snapshot = boss_chat.prepare_unified_chat_preflight(test_db, load_config(), action_id, {})
    released = _claim_and_preflight(test_db, action_id, ready_snapshot)
    assert released["decision"] == "dispatch"
    with test_db.connect() as connection:
        original = connection.execute("SELECT session_sequence FROM fj_actions WHERE id=?", (action_id,)).fetchone()
        b_action = connection.execute("SELECT session_sequence, payload_json FROM fj_actions WHERE id=?", (followup["id"],)).fetchone()
    assert int(original["session_sequence"]) < int(b_action["session_sequence"])
    assert json.loads(b_action["payload_json"])["reply_task_id"] == task_id


def test_platform_event_does_not_call_relation_or_create_reply_task(test_db, monkeypatch) -> None:
    action_id, _session_id, _base_message_id = _seed_pending_action(test_db)
    _add_delta(test_db, body_type=8, content="你与该职位竞争者PK情况")

    snapshot = boss_chat.prepare_unified_chat_preflight(test_db, load_config(), action_id, {})
    result = _claim_and_preflight(test_db, action_id, snapshot)
    with test_db.connect() as connection:
        task_count = connection.execute("SELECT COUNT(*) FROM fj_chat_reply_tasks").fetchone()[0]
    assert result["decision"] == "dispatch"
    assert task_count == 0


def test_uncertain_delta_keeps_a_waiting_without_reply_task(test_db, monkeypatch) -> None:
    action_id, _session_id, _base_message_id = _seed_pending_action(test_db)
    _add_delta(test_db)
    monkeypatch.setattr(boss_chat, "classify_pending_reply_delta", _relation("classification_uncertain"))

    snapshot = boss_chat.prepare_unified_chat_preflight(test_db, load_config(), action_id, {})
    result = _claim_and_preflight(test_db, action_id, snapshot)
    with test_db.connect() as connection:
        action = connection.execute("SELECT status, waiting_reason_code FROM fj_actions WHERE id=?", (action_id,)).fetchone()
        task_count = connection.execute(
            "SELECT COUNT(*) FROM fj_chat_reply_tasks WHERE status IN ('pending_generation', 'generating', 'awaiting_review', 'confirmed')"
        ).fetchone()[0]
    assert result["decision"] == "waiting"
    assert (action["status"], action["waiting_reason_code"]) == ("queued", "classification_uncertain")
    assert task_count == 0
