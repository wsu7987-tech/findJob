from __future__ import annotations

import pytest

from backend.app.errors import AppError
from backend.app.services.fine_job import action_scheduler


def _dispatching_action(test_db, action_type: str) -> tuple[str, str]:
    with test_db.connect() as connection:
        if action_type in {"chat_message", "resume_send"}:
            connection.execute(
                """INSERT INTO fj_chat_sessions (
                     id, account_uid, peer_uid, encrypt_peer_uid, security_id, encrypt_job_id,
                     status, created_at, updated_at
                   ) VALUES ('session-1', 'account-1', 'peer-1', 'encrypt-peer-1', 'security-1', 'job-1',
                     'active', '2026-09-08T00:00:00Z', '2026-09-08T00:00:00Z')"""
            )
            connection.execute(
                """INSERT INTO fj_chat_messages (
                     id, session_id, platform_message_id, direction, message_type, content, raw_content,
                     raw_body_json, sender_uid, receiver_uid, client_mid, source, sent_at, observed_at,
                     raw_meta_json, created_at
                   ) VALUES ('outbound-1', 'session-1', 'outbound-1', 'outbound', 'text', '您好', '您好', '{}',
                     'account-1', 'peer-1', '', 'assistant', '2026-09-08T00:00:00Z',
                     '2026-09-08T00:00:00Z', '{}', '2026-09-08T00:00:00Z')"""
            )
            connection.execute(
                """INSERT INTO fj_chat_messages (
                     id, session_id, platform_message_id, direction, message_type, content, raw_content,
                     raw_body_json, sender_uid, receiver_uid, client_mid, source, sent_at, observed_at,
                     raw_meta_json, created_at
                   ) VALUES ('outbound-duplicate', 'session-1', 'outbound-duplicate', 'outbound', 'text', '您好', '您好', '{}',
                     'account-1', 'peer-1', '', 'assistant', '2026-09-08T00:00:00Z',
                     '2026-09-08T00:00:00Z', '{}', '2026-09-08T00:00:00Z')"""
            )
        action_id = action_scheduler.create_action(
            connection,
            action_type=action_type,
            account_uid="account-1",
            session_id="session-1" if action_type in {"chat_message", "resume_send"} else None,
            text="您好" if action_type == "chat_message" else "",
            encrypt_resume_id="resume-1" if action_type == "resume_send" else "",
            resume_filename="候选人.pdf" if action_type == "resume_send" else "",
        )
        connection.execute(
            """UPDATE fj_actions
               SET status='dispatching', canonical_status='dispatching', lease_owner='executor-1',
                   execution_epoch=2 WHERE id=?""",
            (action_id,),
        )
        client_mid = action_scheduler._loads(
            connection.execute("SELECT payload_json FROM fj_actions WHERE id=?", (action_id,)).fetchone()["payload_json"],
            {},
        ).get("client_mid", "")
    return action_id, str(client_mid)


@pytest.mark.parametrize("action_type", ["chat_message", "resume_send", "greeting"])
@pytest.mark.parametrize("outcome", ["accepted", "unknown", "failed"])
def test_observed_succeeded_never_downgrades_from_later_completion(test_db, action_type: str, outcome: str) -> None:
    action_id, client_mid = _dispatching_action(test_db, action_type)
    with test_db.connect() as connection:
        if action_type in {"chat_message", "resume_send"}:
            action_scheduler.observe_outbound_result(
                connection,
                session_id="session-1",
                client_mid=client_mid,
                raw_message_id="outbound-1",
            )
        else:
            connection.execute(
                "UPDATE fj_actions SET status='succeeded', canonical_status='succeeded' WHERE id=?",
                (action_id,),
            )

    completed = action_scheduler.complete_action(
        test_db,
        "executor-1",
        action_id,
        {"execution_epoch": 2, "outcome": outcome, "status_code": f"late_{outcome}"},
    )

    assert completed["status"] == "succeeded"
    with test_db.connect() as connection:
        action = connection.execute("SELECT status, canonical_status FROM fj_actions WHERE id=?", (action_id,)).fetchone()
    assert (action["status"], action["canonical_status"]) == ("succeeded", "succeeded")


def test_stale_execution_epoch_completion_cannot_cover_current_action(test_db) -> None:
    action_id, _ = _dispatching_action(test_db, "chat_message")

    with pytest.raises(AppError) as error:
        action_scheduler.complete_action(
            test_db,
            "executor-1",
            action_id,
            {"execution_epoch": 1, "outcome": "unknown", "status_code": "late"},
        )

    assert error.value.error_category == "STALE_EXECUTION_EPOCH"
    with test_db.connect() as connection:
        action = connection.execute("SELECT status, execution_epoch FROM fj_actions WHERE id=?", (action_id,)).fetchone()
    assert (action["status"], action["execution_epoch"]) == ("dispatching", 2)


@pytest.mark.parametrize("action_type", ["chat_message", "resume_send"])
def test_duplicate_trusted_outbound_echo_is_idempotent(test_db, action_type: str) -> None:
    action_id, client_mid = _dispatching_action(test_db, action_type)
    with test_db.connect() as connection:
        for _ in range(2):
            action_scheduler.observe_outbound_result(
                connection,
                session_id="session-1",
                client_mid=client_mid,
                raw_message_id="outbound-duplicate",
            )
        links = connection.execute(
            "SELECT COUNT(*) FROM fj_action_message_links WHERE action_id=? AND role='outbound_result'",
            (action_id,),
        ).fetchone()[0]
        action = connection.execute("SELECT status, canonical_status FROM fj_actions WHERE id=?", (action_id,)).fetchone()

    assert links == 1
    assert (action["status"], action["canonical_status"]) == ("succeeded", "succeeded")
