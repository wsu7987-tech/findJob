from __future__ import annotations

from datetime import datetime, timedelta, timezone

from backend.app.services.fine_job import action_scheduler
from backend.app.services.fine_job import boss_chat


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _seed_session(connection, session_id: str, account_uid: str = "account") -> None:
    now = _now()
    connection.execute(
        """INSERT INTO fj_chat_sessions (
             id, account_uid, peer_uid, encrypt_peer_uid, security_id, encrypt_job_id,
             status, created_at, updated_at
           ) VALUES (?, ?, ?, ?, 'security', ?, 'active', ?, ?)""",
        (session_id, account_uid, f"peer-{session_id}", f"encrypt-peer-{session_id}", f"encrypt-job-{session_id}", now, now),
    )


def _enable_send(db) -> None:
    boss_chat.update_runtime(db, {"send_enabled": True})


def test_scheduler_prioritizes_chat_then_resume_and_keeps_stable_order(test_db) -> None:
    with test_db.connect() as connection:
        _seed_session(connection, "s1")
        _seed_session(connection, "s2")
        first = action_scheduler.create_action(connection, action_type="chat_message", account_uid="account", session_id="s1", text="A")
        second = action_scheduler.create_action(connection, action_type="chat_message", account_uid="account", session_id="s2", text="B")
        resume = action_scheduler.create_action(connection, action_type="resume_send", account_uid="account", session_id="s1", encrypt_resume_id="resume", resume_filename="简历.pdf")
    _enable_send(test_db)
    ordered_chat = [first, second]
    claimed = action_scheduler.claim_unified_action(test_db, "executor", action_id=first, account_uid="account")
    assert claimed and claimed["id"] == first
    with test_db.connect() as connection:
        connection.execute("UPDATE fj_actions SET status='succeeded', completed_at=? WHERE id=?", (_now(), first))
    claimed = action_scheduler.claim_unified_action(test_db, "executor", action_id=second, account_uid="account")
    assert claimed and claimed["id"] == second
    with test_db.connect() as connection:
        connection.execute("UPDATE fj_actions SET status='succeeded', completed_at=? WHERE id=?", (_now(), second))
    claimed = action_scheduler.claim_unified_action(test_db, "executor", action_id=resume, account_uid="account")
    assert claimed and claimed["id"] == resume


def test_scheduler_session_unknown_blocks_successor_and_claim_is_atomic(test_db) -> None:
    with test_db.connect() as connection:
        _seed_session(connection, "s")
        first = action_scheduler.create_action(connection, action_type="chat_message", account_uid="account", session_id="s", text="A")
        second = action_scheduler.create_action(connection, action_type="chat_message", account_uid="account", session_id="s", text="B")
    _enable_send(test_db)
    first_claim = action_scheduler.claim_unified_action(test_db, "executor-a", action_id=first, account_uid="account")
    assert first_claim and first_claim["id"] == first
    assert action_scheduler.claim_unified_action(test_db, "executor-b", action_id=second, account_uid="account") is None
    with test_db.connect() as connection:
        connection.execute("UPDATE fj_actions SET status='unknown', completed_at=? WHERE id=?", (_now(), first))
    assert action_scheduler.claim_unified_action(test_db, "executor-b", action_id=second, account_uid="account") is None
    with test_db.connect() as connection:
        connection.execute("UPDATE fj_actions SET status='succeeded', completed_at=? WHERE id=?", (_now(), first))
    second_claim = action_scheduler.claim_unified_action(test_db, "executor-b", action_id=second, account_uid="account")
    assert second_claim and second_claim["id"] == second


def test_scheduler_session_checks_all_earlier_blocking_actions(test_db) -> None:
    with test_db.connect() as connection:
        _seed_session(connection, "unknown-gap")
        first = action_scheduler.create_action(connection, action_type="chat_message", account_uid="account", session_id="unknown-gap", text="A")
        second = action_scheduler.create_action(connection, action_type="chat_message", account_uid="account", session_id="unknown-gap", text="B")
        third = action_scheduler.create_action(connection, action_type="chat_message", account_uid="account", session_id="unknown-gap", text="C")
        connection.execute("UPDATE fj_actions SET status='unknown' WHERE id=?", (first,))
        connection.execute("UPDATE fj_actions SET status='cancelled' WHERE id=?", (second,))
    _enable_send(test_db)

    assert action_scheduler.claim_unified_action(test_db, "executor", action_id=third, account_uid="account") is None


def test_scheduler_session_accepted_before_succeeded_still_blocks_successor(test_db) -> None:
    with test_db.connect() as connection:
        _seed_session(connection, "accepted-gap")
        first = action_scheduler.create_action(connection, action_type="chat_message", account_uid="account", session_id="accepted-gap", text="A")
        second = action_scheduler.create_action(connection, action_type="chat_message", account_uid="account", session_id="accepted-gap", text="B")
        third = action_scheduler.create_action(connection, action_type="chat_message", account_uid="account", session_id="accepted-gap", text="C")
        connection.execute("UPDATE fj_actions SET status='accepted' WHERE id=?", (first,))
        connection.execute("UPDATE fj_actions SET status='succeeded' WHERE id=?", (second,))
    _enable_send(test_db)

    assert action_scheduler.claim_unified_action(test_db, "executor", action_id=third, account_uid="account") is None


def test_scheduler_session_all_terminal_predecessors_allow_successor(test_db) -> None:
    with test_db.connect() as connection:
        _seed_session(connection, "terminal-gap")
        first = action_scheduler.create_action(connection, action_type="chat_message", account_uid="account", session_id="terminal-gap", text="A")
        second = action_scheduler.create_action(connection, action_type="chat_message", account_uid="account", session_id="terminal-gap", text="B")
        third = action_scheduler.create_action(connection, action_type="chat_message", account_uid="account", session_id="terminal-gap", text="C")
        connection.execute("UPDATE fj_actions SET status='succeeded' WHERE id=?", (first,))
        connection.execute("UPDATE fj_actions SET status='cancelled' WHERE id=?", (second,))
    _enable_send(test_db)

    claimed = action_scheduler.claim_unified_action(test_db, "executor", action_id=third, account_uid="account")
    assert claimed and claimed["id"] == third


def test_send_disabled_keeps_action_queued_then_preflight_token_controls_dispatch(test_db) -> None:
    with test_db.connect() as connection:
        _seed_session(connection, "s")
        action_id = action_scheduler.create_action(connection, action_type="chat_message", account_uid="account", session_id="s", text="A")
    assert action_scheduler.claim_unified_action(test_db, "executor", action_id=action_id, account_uid="account") is None
    with test_db.connect() as connection:
        queued = connection.execute("SELECT status, waiting_reason_code FROM fj_actions WHERE id=?", (action_id,)).fetchone()
    assert (queued["status"], queued["waiting_reason_code"]) == ("queued", "send_disabled")
    _enable_send(test_db)
    action = action_scheduler.claim_unified_action(test_db, "executor", action_id=action_id, account_uid="account")
    assert action
    decision = action_scheduler.preflight_action(test_db, "executor", action_id, action["execution_epoch"], {"conversation_revision": 0})
    assert decision["decision"] == "dispatch"
    dispatched = action_scheduler.mark_dispatch_started(test_db, "executor", action_id, action["execution_epoch"], decision["dispatch_token"])
    assert dispatched["status"] == "dispatching"


def test_expired_lease_can_be_reclaimed_but_unknown_never_retries(test_db) -> None:
    with test_db.connect() as connection:
        _seed_session(connection, "s")
        action_id = action_scheduler.create_action(connection, action_type="chat_message", account_uid="account", session_id="s", text="A")
    _enable_send(test_db)
    claimed = action_scheduler.claim_unified_action(test_db, "executor-a", action_id=action_id, account_uid="account")
    assert claimed
    expired = (datetime.now(timezone.utc) - timedelta(minutes=1)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    with test_db.connect() as connection:
        connection.execute("UPDATE fj_actions SET lease_expires_at=? WHERE id=?", (expired, action_id))
    reclaimed = action_scheduler.claim_unified_action(test_db, "executor-b", action_id=action_id, account_uid="account")
    assert reclaimed and reclaimed["execution_epoch"] == 2
    with test_db.connect() as connection:
        connection.execute("UPDATE fj_actions SET status='unknown', lease_expires_at=NULL WHERE id=?", (action_id,))
    assert action_scheduler.claim_unified_action(test_db, "executor-c", action_id=action_id, account_uid="account") is None


def test_claim_requires_the_page_matched_action_id(test_db) -> None:
    with test_db.connect() as connection:
        _seed_session(connection, "s1")
        _seed_session(connection, "s2")
        requested = action_scheduler.create_action(connection, action_type="chat_message", account_uid="account", session_id="s1", text="A")
        other = action_scheduler.create_action(connection, action_type="chat_message", account_uid="account", session_id="s2", text="B")
    _enable_send(test_db)
    claimed = action_scheduler.claim_unified_action(test_db, "executor", action_id=requested, account_uid="account")
    assert claimed and claimed["id"] == requested
    with test_db.connect() as connection:
        queued = connection.execute("SELECT status FROM fj_actions WHERE id=?", (other,)).fetchone()
    assert queued["status"] == "queued"


def test_independent_reply_waits_until_followup_is_ready_then_releases_original(test_db) -> None:
    with test_db.connect() as connection:
        _seed_session(connection, "s")
        action_id = action_scheduler.create_action(
            connection,
            action_type="chat_message",
            account_uid="account",
            session_id="s",
            text="Reply-A",
            base_conversation_revision=0,
        )
        connection.execute(
            """INSERT INTO fj_chat_messages (
                 id, session_id, platform_message_id, direction, message_type, content, raw_content,
                 raw_body_json, sender_uid, receiver_uid, client_mid, source, sent_at, observed_at,
                 raw_meta_json, created_at
               ) VALUES ('m-b', 's', 'm-b', 'inbound', 'text', 'B', 'B', '{}', '', '', '',
                 'websocket', ?, ?, '{}', ?)""",
            (_now(), _now(), _now()),
        )
        connection.execute(
            """INSERT INTO fj_chat_message_semantics (
                 raw_message_id, semantic_type, display_text, reply_required, classifier_version,
                 semantic_context_json, derived_at
               ) VALUES ('m-b', 'recruiter_text', 'B', 1, 'test', '{}', ?)""",
            (_now(),),
        )
        connection.execute("UPDATE fj_chat_sessions SET session_version=1 WHERE id='s'")
    _enable_send(test_db)
    claimed = action_scheduler.claim_unified_action(test_db, "executor", action_id=action_id, account_uid="account")
    assert claimed
    waiting = action_scheduler.preflight_action(
        test_db,
        "executor",
        action_id,
        claimed["execution_epoch"],
        {
            "conversation_revision": 1,
            "message_decisions": {"m-b": "independent_reply_required"},
            "independent_text": "Reply-B",
        },
    )
    assert waiting["decision"] == "waiting"
    with test_db.connect() as connection:
        original = connection.execute("SELECT status, payload_json FROM fj_actions WHERE id=?", (action_id,)).fetchone()
        followup_id = action_scheduler._loads(original["payload_json"], {}).get("independent_followup_action_id")
        followup = connection.execute("SELECT status, session_sequence FROM fj_actions WHERE id=?", (followup_id,)).fetchone()
        assert original["status"] == "queued"
        assert followup["status"] == "awaiting_confirmation"
        connection.execute("UPDATE fj_actions SET status='queued' WHERE id=?", (followup_id,))
    reclaimed = action_scheduler.claim_unified_action(test_db, "executor", action_id=action_id, account_uid="account")
    assert reclaimed
    released = action_scheduler.preflight_action(
        test_db,
        "executor",
        action_id,
        reclaimed["execution_epoch"],
        {
            "conversation_revision": 1,
            "message_decisions": {"m-b": "independent_reply_required"},
        },
    )
    assert released["decision"] == "dispatch"


def test_resume_requires_trusted_outbound_observation_before_success(test_db) -> None:
    with test_db.connect() as connection:
        _seed_session(connection, "s")
        action_id = action_scheduler.create_action(
            connection,
            action_type="resume_send",
            account_uid="account",
            session_id="s",
            encrypt_resume_id="resume-1",
            resume_filename="候选人.pdf",
        )
        connection.execute("UPDATE fj_actions SET status='accepted' WHERE id=?", (action_id,))
        connection.execute(
            """INSERT INTO fj_chat_messages (
                 id, session_id, platform_message_id, direction, message_type, content, raw_content,
                 raw_body_json, sender_uid, receiver_uid, client_mid, source, sent_at, observed_at,
                 raw_meta_json, created_at
               ) VALUES ('outbound-resume', 's', 'outbound-resume', 'outbound', 'unknown', '', '', '{}',
                 '', '', ?, 'assistant', ?, ?, '{}', ?)""",
            (action_scheduler._loads(connection.execute("SELECT payload_json FROM fj_actions WHERE id=?", (action_id,)).fetchone()["payload_json"], {})["client_mid"], _now(), _now(), _now()),
        )
        client_mid = action_scheduler._loads(connection.execute("SELECT payload_json FROM fj_actions WHERE id=?", (action_id,)).fetchone()["payload_json"], {})["client_mid"]
        action_scheduler.observe_outbound_result(
            connection,
            session_id="s",
            client_mid=client_mid,
            raw_message_id="outbound-resume",
        )
        action = connection.execute("SELECT status FROM fj_actions WHERE id=?", (action_id,)).fetchone()
    assert action["status"] == "succeeded"
