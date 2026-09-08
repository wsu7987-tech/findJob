from __future__ import annotations

import json

from backend.app.services.fine_job import boss_chat
from backend.app.services.fine_job import job_hunt_analysis
from backend.app.services.fine_job.action_store import (
    backfill_legacy_actions,
    bind_greeting_account,
    observe_action_outbound_result,
    reconcile_reply_task_coverage,
    sync_legacy_action,
    transfer_action_coverage,
)
from backend.app.services.fine_job.message_semantics import derive_message_semantics


NOW = "2026-09-07T00:00:00Z"


def _seed_legacy_actions(test_db) -> None:
    with test_db.connect() as connection:
        connection.execute(
            "INSERT INTO fj_boss_capture_batches (id, keyword, city, created_at, updated_at) VALUES ('capture', 'Python', '广州', ?, ?)",
            (NOW, NOW),
        )
        connection.execute(
            """INSERT INTO fj_boss_jobs (id, dedupe_key, encrypt_job_id, title, company_name, first_collected_at, last_collected_at, latest_batch_id)
               VALUES ('job', 'job-key', 'encrypt-job', '后端工程师', '示例科技', ?, ?, 'capture')""",
            (NOW, NOW),
        )
        connection.execute(
            "INSERT INTO fj_job_evaluations (id, job_id, source, decision, created_at) VALUES ('evaluation', 'job', 'rules', 'review', ?)",
            (NOW,),
        )
        connection.execute(
            """INSERT INTO fj_review_items (id, job_id, evaluation_id, ai_decision, created_at, updated_at)
               VALUES ('review', 'job', 'evaluation', 'review', ?, ?)""",
            (NOW, NOW),
        )
        connection.execute(
            """INSERT INTO fj_automation_actions (
              id, job_id, evaluation_id, review_item_id, action_type, status, idempotency_key,
              payload_json, created_at, updated_at
            ) VALUES ('legacy-greeting', 'job', 'evaluation', 'review', 'BOSS_DEFAULT_GREETING', 'queued', 'greeting-key', ?, ?, ?)""",
            (json.dumps({"message": "您好", "encrypt_job_id": "encrypt-job"}), NOW, NOW),
        )
        connection.execute(
            """INSERT INTO fj_chat_sessions (
              id, account_uid, peer_uid, encrypt_peer_uid, security_id, job_id, encrypt_job_id,
              status, created_at, updated_at
            ) VALUES ('session', 'account-1', 'peer-1', 'encrypt-peer', 'security', 'job', 'encrypt-job', 'active', ?, ?)""",
            (NOW, NOW),
        )
        for message_id, content in (("m-a", "第一条"), ("m-b", "第二条"), ("m-c", "第三条")):
            connection.execute(
                """INSERT INTO fj_chat_messages (
                  id, session_id, platform_message_id, direction, message_type, content, raw_content,
                  sender_uid, receiver_uid, source, sent_at, observed_at, created_at
                ) VALUES (?, 'session', ?, 'inbound', 'text', ?, ?, 'peer-1', 'account-1', 'websocket', ?, ?, ?)""",
                (message_id, message_id, content, content, NOW, NOW, NOW),
            )
            derive_message_semantics(connection, message_id)
        connection.execute(
            """INSERT INTO fj_chat_reply_tasks (
              id, session_id, trigger_source, status, based_on_message_id, based_on_session_version,
              input_message_ids_json, created_at, updated_at
            ) VALUES ('reply-text', 'session', 'manual', 'confirmed', 'm-c', 3, ?, ?, ?)""",
            (json.dumps(["m-a", "m-b", "m-c"]), NOW, NOW),
        )
        connection.execute(
            """INSERT INTO fj_chat_reply_tasks (
              id, session_id, trigger_source, status, based_on_message_id, based_on_session_version,
              input_message_ids_json, created_at, updated_at
            ) VALUES ('reply-resume', 'session', 'manual', 'cancelled', 'm-c', 3, '[]', ?, ?)""",
            (NOW, NOW),
        )
        connection.execute(
            """INSERT INTO fj_chat_send_actions (
              id, reply_task_id, session_id, operation_kind, status, text, created_at, updated_at
            ) VALUES ('legacy-text', 'reply-text', 'session', 'text', 'queued', '收到，我会尽快回复。', ?, ?)""",
            (NOW, NOW),
        )
        connection.execute(
            """INSERT INTO fj_chat_send_actions (
              id, reply_task_id, session_id, operation_kind, encrypt_resume_id, resume_filename,
              status, text, created_at, updated_at
            ) VALUES ('legacy-resume', 'reply-resume', 'session', 'resume', 'resume-id', '简历.pdf', 'queued', '', ?, ?)""",
            (NOW, NOW),
        )


def test_legacy_backfill_is_idempotent_and_keeps_identity(test_db) -> None:
    _seed_legacy_actions(test_db)
    with test_db.connect() as connection:
        backfill_legacy_actions(connection)
        backfill_legacy_actions(connection)
        actions = connection.execute(
            "SELECT action_type, account_uid, target_page_key, base_conversation_revision, source_table, source_id FROM fj_actions ORDER BY source_id"
        ).fetchall()
    assert [(row["action_type"], row["source_id"]) for row in actions] == [
        ("greeting", "legacy-greeting"),
        ("resume_send", "legacy-resume"),
        ("chat_message", "legacy-text"),
    ]
    greeting = next(row for row in actions if row["action_type"] == "greeting")
    assert greeting["account_uid"] == "account-1"
    assert greeting["target_page_key"] == "boss:account-1:job:encrypt-job"
    text_action = next(row for row in actions if row["source_id"] == "legacy-text")
    assert text_action["base_conversation_revision"] == 3


def test_lifecycle_sync_and_full_coverage_recovery(test_db) -> None:
    _seed_legacy_actions(test_db)
    with test_db.connect() as connection:
        backfill_legacy_actions(connection)
        action_id = str(connection.execute("SELECT id FROM fj_actions WHERE source_id='legacy-text'").fetchone()["id"])
        covered = connection.execute(
            "SELECT raw_message_id FROM fj_action_message_links WHERE action_id=? AND role='covered' ORDER BY ordinal",
            (action_id,),
        ).fetchall()
        assert [row["raw_message_id"] for row in covered] == ["m-a", "m-b", "m-c"]
        assert connection.execute("SELECT reply_state FROM fj_chat_message_states WHERE raw_message_id='m-a'").fetchone()["reply_state"] == "queued"
        connection.execute("UPDATE fj_chat_send_actions SET status='leased', lease_owner='executor', execution_epoch=1, updated_at=? WHERE id='legacy-text'", (NOW,))
        sync_legacy_action(connection, source_table="fj_chat_send_actions", source_id="legacy-text")
        assert connection.execute("SELECT status FROM fj_actions WHERE id=?", (action_id,)).fetchone()["status"] == "claimed"
        connection.execute("UPDATE fj_chat_send_actions SET status='dispatching', dispatched_at=?, updated_at=? WHERE id='legacy-text'", (NOW, NOW))
        sync_legacy_action(connection, source_table="fj_chat_send_actions", source_id="legacy-text")
        assert connection.execute("SELECT status FROM fj_actions WHERE id=?", (action_id,)).fetchone()["status"] == "dispatching"
        connection.execute("UPDATE fj_chat_send_actions SET status='accepted', outcome='accepted', updated_at=? WHERE id='legacy-text'", (NOW,))
        sync_legacy_action(connection, source_table="fj_chat_send_actions", source_id="legacy-text")
        assert connection.execute("SELECT status FROM fj_actions WHERE id=?", (action_id,)).fetchone()["status"] == "accepted"
        connection.execute(
            """INSERT INTO fj_chat_messages (
              id, session_id, platform_message_id, direction, message_type, content, raw_content,
              sender_uid, receiver_uid, client_mid, source, sent_at, observed_at, created_at
            ) VALUES ('outbound', 'session', 'outbound', 'outbound', 'text', '收到', '收到', 'account-1', 'peer-1', 'client', 'assistant', ?, ?, ?)""",
            (NOW, NOW, NOW),
        )
        derive_message_semantics(connection, "outbound")
        observe_action_outbound_result(
            connection,
            source_table="fj_chat_send_actions",
            source_id="legacy-text",
            raw_message_id="outbound",
        )
        assert connection.execute("SELECT status FROM fj_actions WHERE id=?", (action_id,)).fetchone()["status"] == "succeeded"
        connection.execute("UPDATE fj_chat_send_actions SET status='failed', outcome='failed', completed_at=?, updated_at=? WHERE id='legacy-text'", (NOW, NOW))
        sync_legacy_action(connection, source_table="fj_chat_send_actions", source_id="legacy-text")
        states = connection.execute("SELECT reply_state FROM fj_chat_message_states WHERE raw_message_id IN ('m-a','m-b','m-c') ORDER BY raw_message_id").fetchall()
        assert [row["reply_state"] for row in states] == ["replied", "replied", "replied"]
        assert connection.execute("SELECT status FROM fj_actions WHERE id=?", (action_id,)).fetchone()["status"] == "succeeded"
        connection.execute("UPDATE fj_chat_send_actions SET status='unknown', outcome='unknown', updated_at=? WHERE id='legacy-text'", (NOW,))
        sync_legacy_action(connection, source_table="fj_chat_send_actions", source_id="legacy-text")
        assert connection.execute("SELECT status FROM fj_actions WHERE id=?", (action_id,)).fetchone()["status"] == "succeeded"
        connection.execute("UPDATE fj_chat_send_actions SET status='cancelled', outcome=NULL, updated_at=? WHERE id='legacy-text'", (NOW,))
        sync_legacy_action(connection, source_table="fj_chat_send_actions", source_id="legacy-text")
        assert connection.execute("SELECT status FROM fj_actions WHERE id=?", (action_id,)).fetchone()["status"] == "succeeded"
        connection.execute("UPDATE fj_automation_actions SET status='running', dispatch_started_at=?, updated_at=? WHERE id='legacy-greeting'", (NOW, NOW))
        greeting_id = str(sync_legacy_action(connection, source_table="fj_automation_actions", source_id="legacy-greeting"))
        assert connection.execute("SELECT status FROM fj_actions WHERE id=?", (greeting_id,)).fetchone()["status"] == "dispatching"
        connection.execute("UPDATE fj_automation_actions SET status='unknown', completed_at=?, updated_at=? WHERE id='legacy-greeting'", (NOW, NOW))
        sync_legacy_action(connection, source_table="fj_automation_actions", source_id="legacy-greeting")
        assert connection.execute("SELECT status FROM fj_actions WHERE id=?", (greeting_id,)).fetchone()["status"] == "unknown"


def test_replacement_keeps_coverage_and_queued_resume_is_not_semantic_context(test_db) -> None:
    _seed_legacy_actions(test_db)
    with test_db.connect() as connection:
        backfill_legacy_actions(connection)
        previous = str(connection.execute("SELECT id FROM fj_actions WHERE source_id='legacy-text'").fetchone()["id"])
        connection.execute(
            """INSERT INTO fj_actions (
              id, action_type, account_uid, session_id, session_sequence, status, source_table, source_id, created_at, updated_at
            ) VALUES ('replacement', 'chat_message', 'account-1', 'session', 9, 'queued', 'test', 'replacement', ?, ?)""",
            (NOW, NOW),
        )
        transfer_action_coverage(connection, previous_action_id=previous, replacement_action_id="replacement")
        count = connection.execute("SELECT COUNT(*) FROM fj_action_message_links WHERE action_id='replacement' AND role='covered'").fetchone()[0]
        assert count == 3
        connection.execute(
            """INSERT INTO fj_chat_messages (
              id, session_id, platform_message_id, direction, message_type, content, raw_content, raw_body_json,
              sender_uid, receiver_uid, source, sent_at, observed_at, created_at
            ) VALUES ('receipt', 'session', 'receipt', 'inbound', 'system', '简历.pdf', '简历.pdf', '{"type":4}', 'peer-1', 'account-1', 'websocket', ?, ?, ?)""",
            (NOW, NOW, NOW),
        )
        derive_message_semantics(connection, "receipt")
        semantic = connection.execute("SELECT semantic_type FROM fj_chat_message_semantics WHERE raw_message_id='receipt'").fetchone()["semantic_type"]
        assert semantic == "platform_event_unknown"


def test_realtime_raw_body_and_platform_ad_do_not_plan_reply(test_db) -> None:
    with test_db.connect() as connection:
        connection.execute(
            """INSERT INTO fj_boss_executor_instances (id, token_hash, protocol_version, plugin_version, created_at, updated_at)
               VALUES ('executor', 'token', '1.1', 'test', ?, ?)""",
            (NOW, NOW),
        )
    boss_chat.update_runtime(test_db, {"listen_enabled": True, "generation_enabled": True, "trigger_mode": "immediate"})
    result = boss_chat.ingest_events(test_db, "executor", [{
        "event_id": "platform-ad", "event_type": "message", "account_uid": "account-1", "leader_epoch": 1,
        "message": {
            "platform_message_id": "platform-ad", "direction": "inbound", "message_type": "text",
            "content": "你与该职位竞争者PK情况", "sender_uid": "peer-1", "receiver_uid": "account-1",
            "peer_uid": "peer-1", "encrypt_peer_uid": "encrypt-peer", "security_id": "security", "encrypt_job_id": "encrypt-job",
            "sent_at": NOW, "observed_at": NOW, "source": "websocket", "raw_body": {"type": 8, "card": {"unknown": "保留"}},
            "raw_meta": {"message": {"unknown_top_level": "保留"}},
        },
    }])
    assert result["queued_task_ids"] == []
    with test_db.connect() as connection:
        message = connection.execute("SELECT raw_body_json, raw_meta_json FROM fj_chat_messages WHERE platform_message_id='platform-ad'").fetchone()
        semantic = connection.execute("SELECT semantic_type, reply_required FROM fj_chat_message_semantics WHERE raw_message_id=(SELECT id FROM fj_chat_messages WHERE platform_message_id='platform-ad')").fetchone()
    assert json.loads(message["raw_body_json"])["card"]["unknown"] == "保留"
    assert json.loads(message["raw_meta_json"])["message"]["unknown_top_level"] == "保留"
    assert (semantic["semantic_type"], semantic["reply_required"]) == ("platform_ad", 0)


def test_shadow_lifecycle_keeps_observed_success_and_authorization(test_db) -> None:
    _seed_legacy_actions(test_db)
    with test_db.connect() as connection:
        backfill_legacy_actions(connection)
        action_id = str(connection.execute("SELECT id FROM fj_actions WHERE source_id='legacy-text'").fetchone()["id"])
        connection.execute(
            "UPDATE fj_chat_send_actions SET authorization_mode='pre_authorized', status='accepted', updated_at=? WHERE id='legacy-text'",
            (NOW,),
        )
        sync_legacy_action(connection, source_table="fj_chat_send_actions", source_id="legacy-text")
        assert connection.execute("SELECT authorization_mode FROM fj_actions WHERE id=?", (action_id,)).fetchone()["authorization_mode"] == "preauthorized"
        observe_action_outbound_result(
            connection,
            source_table="fj_chat_send_actions",
            source_id="legacy-text",
            raw_message_id="m-c",
        )
        sync_legacy_action(connection, source_table="fj_chat_send_actions", source_id="legacy-text")
        assert connection.execute("SELECT status FROM fj_actions WHERE id=?", (action_id,)).fetchone()["status"] == "succeeded"
        connection.execute(
            "UPDATE fj_automation_actions SET status='succeeded', canonical_status='unknown', updated_at=? WHERE id='legacy-greeting'",
            (NOW,),
        )
        greeting_id = str(sync_legacy_action(connection, source_table="fj_automation_actions", source_id="legacy-greeting"))
        greeting = connection.execute("SELECT status, canonical_status FROM fj_actions WHERE id=?", (greeting_id,)).fetchone()
    assert (greeting["status"], greeting["canonical_status"]) == ("unknown", "unknown")


def test_direct_greeting_success_evidence_overrides_unknown_shadow_state(test_db) -> None:
    _seed_legacy_actions(test_db)
    with test_db.connect() as connection:
        backfill_legacy_actions(connection)
        connection.execute(
            "UPDATE fj_automation_actions SET status='unknown', canonical_status='unknown', updated_at=? WHERE id='legacy-greeting'",
            (NOW,),
        )
        greeting_id = str(sync_legacy_action(connection, source_table="fj_automation_actions", source_id="legacy-greeting"))
        session = connection.execute("SELECT * FROM fj_chat_sessions WHERE id='session'").fetchone()
        job_hunt_analysis._sync_tasks_from_facts(
            connection,
            session,
            "job",
            {"greeting_anchor": {"message_id": "m-a", "occurred_at": NOW}},
        )
        confirmed = connection.execute("SELECT status, canonical_status FROM fj_actions WHERE id=?", (greeting_id,)).fetchone()
        assert (confirmed["status"], confirmed["canonical_status"]) == ("succeeded", "succeeded")
        connection.execute(
            "UPDATE fj_automation_actions SET status='failed', canonical_status='failed', updated_at=? WHERE id='legacy-greeting'",
            (NOW,),
        )
        sync_legacy_action(connection, source_table="fj_automation_actions", source_id="legacy-greeting")
        retained = connection.execute("SELECT status, canonical_status FROM fj_actions WHERE id=?", (greeting_id,)).fetchone()
    assert (retained["status"], retained["canonical_status"]) == ("succeeded", "succeeded")


def test_greeting_identity_binding_and_unknown_resume_text(test_db) -> None:
    _seed_legacy_actions(test_db)
    with test_db.connect() as connection:
        connection.execute("DELETE FROM fj_chat_sessions WHERE id='session'")
        greeting_id = str(sync_legacy_action(connection, source_table="fj_automation_actions", source_id="legacy-greeting"))
        greeting = connection.execute("SELECT status, account_uid, target_page_key, target_context_key FROM fj_actions WHERE id=?", (greeting_id,)).fetchone()
        assert (greeting["status"], greeting["account_uid"], greeting["target_page_key"], greeting["target_context_key"]) == ("queued", "", "", "greeting_account_binding:job")
        bind_greeting_account(connection, job_id="job", account_uid="account-2")
        bound = connection.execute("SELECT status, account_uid, target_page_key FROM fj_actions WHERE id=?", (greeting_id,)).fetchone()
        assert (bound["status"], bound["account_uid"], bound["target_page_key"]) == ("queued", "account-2", "boss:account-2:job:encrypt-job")
        connection.execute(
            """INSERT INTO fj_chat_sessions (id, account_uid, peer_uid, encrypt_peer_uid, security_id, status, created_at, updated_at)
               VALUES ('weak-session', 'account-2', 'peer', 'encrypt-peer', 'security', 'active', ?, ?)""",
            (NOW, NOW),
        )
        connection.execute(
            """INSERT INTO fj_chat_messages (
              id, session_id, platform_message_id, direction, message_type, content, raw_content,
              sender_uid, receiver_uid, source, sent_at, observed_at, created_at
            ) VALUES ('weak-resume', 'weak-session', 'weak-resume', 'inbound', 'text', '候选人简历.pdf', '候选人简历.pdf', 'peer', 'account-2', 'websocket', ?, ?, ?)""",
            (NOW, NOW, NOW),
        )
        derive_message_semantics(connection, "weak-resume")
        semantic = connection.execute(
            "SELECT semantic_type, reply_required FROM fj_chat_message_semantics WHERE raw_message_id='weak-resume'"
        ).fetchone()
    assert (semantic["semantic_type"], semantic["reply_required"]) == ("platform_event_unknown", 0)


def test_reply_task_recovery_and_confirm_replacement_chain(test_db) -> None:
    _seed_legacy_actions(test_db)
    with test_db.connect() as connection:
        backfill_legacy_actions(connection)
        connection.execute("UPDATE fj_chat_sessions SET session_version=3, latest_message_id='m-c', latest_inbound_message_id='m-c' WHERE id='session'")
        connection.execute("UPDATE fj_chat_send_actions SET status='cancelled', updated_at=? WHERE id='legacy-text'", (NOW,))
        previous_id = str(sync_legacy_action(connection, source_table="fj_chat_send_actions", source_id="legacy-text"))
        connection.execute("UPDATE fj_chat_reply_tasks SET status='cancelled', cancelled_at=?, updated_at=? WHERE id='reply-text'", (NOW, NOW))
        reconcile_reply_task_coverage(connection, reply_task_id="reply-text")
        states = connection.execute("SELECT reply_state FROM fj_chat_message_states WHERE raw_message_id IN ('m-a','m-b','m-c') ORDER BY raw_message_id").fetchall()
        assert [row["reply_state"] for row in states] == ["unanswered", "unanswered", "unanswered"]
        for terminal_status in ("failed", "stale"):
            connection.execute(
                "UPDATE fj_chat_message_states SET reply_state='reply_planned', reply_planned_at=? WHERE raw_message_id IN ('m-a','m-b','m-c')",
                (NOW,),
            )
            connection.execute("UPDATE fj_chat_reply_tasks SET status=?, updated_at=? WHERE id='reply-text'", (terminal_status, NOW))
            reconcile_reply_task_coverage(connection, reply_task_id="reply-text")
            states = connection.execute("SELECT reply_state FROM fj_chat_message_states WHERE raw_message_id IN ('m-a','m-b','m-c') ORDER BY raw_message_id").fetchall()
            assert [row["reply_state"] for row in states] == ["unanswered", "unanswered", "unanswered"]
        connection.execute(
            """INSERT INTO fj_chat_reply_tasks (
              id, session_id, trigger_source, status, based_on_message_id, based_on_session_version,
              final_text, input_message_ids_json, context_json, created_at, updated_at
            ) VALUES ('replacement-task', 'session', 'manual', 'awaiting_review', 'm-c', 3, '新的回复', ?, ?, ?, ?)""",
            (json.dumps(["m-a", "m-b", "m-c"]), json.dumps({"replacement_source_action_ids": [previous_id]}), NOW, NOW),
        )
    action = boss_chat.confirm_reply(test_db, "replacement-task", {
        "final_text": "新的回复",
        "based_on_message_id": "m-c",
        "based_on_session_version": 3,
    })
    with test_db.connect() as connection:
        replacement = connection.execute("SELECT id, supersedes_action_id FROM fj_actions WHERE source_id=?", (action["id"],)).fetchone()
        count = connection.execute("SELECT COUNT(*) FROM fj_action_message_links WHERE action_id=? AND role='covered'", (replacement["id"],)).fetchone()[0]
        previous = connection.execute("SELECT status FROM fj_actions WHERE id=?", (previous_id,)).fetchone()
    assert replacement["supersedes_action_id"] == previous_id
    assert count == 3
    assert previous["status"] == "superseded"


def test_realtime_replacement_queues_inherited_coverage(test_db) -> None:
    _seed_legacy_actions(test_db)
    with test_db.connect() as connection:
        backfill_legacy_actions(connection)
        connection.execute(
            """INSERT INTO fj_chat_messages (
              id, session_id, platform_message_id, direction, message_type, content, raw_content,
              sender_uid, receiver_uid, source, sent_at, observed_at, created_at
            ) VALUES ('m-d', 'session', 'm-d', 'inbound', 'text', '新的问题', '新的问题', 'peer-1', 'account-1', 'websocket', ?, ?, ?)""",
            (NOW, NOW, NOW),
        )
        derive_message_semantics(connection, "m-d")
        connection.execute(
            "UPDATE fj_chat_sessions SET session_version=4, latest_message_id='m-d', latest_inbound_message_id='m-d' WHERE id='session'"
        )
        session = connection.execute("SELECT * FROM fj_chat_sessions WHERE id='session'").fetchone()
        task_id = str(boss_chat._queue_reply_task(connection, session, "m-d", "realtime"))
        connection.execute(
            "UPDATE fj_chat_reply_tasks SET status='awaiting_review', final_text='新的回复', updated_at=? WHERE id=?",
            (NOW, task_id),
        )
    boss_chat.confirm_reply(test_db, task_id, {
        "final_text": "新的回复",
        "based_on_message_id": "m-d",
        "based_on_session_version": 4,
    })
    with test_db.connect() as connection:
        states = connection.execute(
            "SELECT raw_message_id, reply_state FROM fj_chat_message_states WHERE raw_message_id IN ('m-a','m-b','m-c','m-d') ORDER BY raw_message_id"
        ).fetchall()
    assert [(row["raw_message_id"], row["reply_state"]) for row in states] == [
        ("m-a", "queued"),
        ("m-b", "queued"),
        ("m-c", "queued"),
        ("m-d", "queued"),
    ]
