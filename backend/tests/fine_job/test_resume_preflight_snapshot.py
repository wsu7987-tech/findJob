from __future__ import annotations

from datetime import datetime, timezone

import pytest

from backend.app.services.fine_job import action_scheduler, boss_chat


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _claimed_resume_action(test_db) -> dict[str, object]:
    now = _now()
    with test_db.connect() as connection:
        connection.execute(
            """INSERT INTO fj_chat_sessions (
                 id, account_uid, peer_uid, encrypt_peer_uid, security_id, encrypt_job_id,
                 status, latest_inbound_message_id, created_at, updated_at
               ) VALUES ('session-1', 'account-1', 'peer-1', 'encrypt-peer-1', 'security-1', 'job-1',
                 'active', 'inbound-1', ?, ?)""",
            (now, now),
        )
        connection.execute(
            """INSERT INTO fj_chat_messages (
                 id, session_id, platform_message_id, direction, message_type, content, raw_content,
                 raw_body_json, sender_uid, receiver_uid, client_mid, source, sent_at, observed_at,
                 raw_meta_json, created_at
               ) VALUES ('inbound-1', 'session-1', 'inbound-1', 'inbound', 'text', '请发简历', '请发简历', '{}',
                 'peer-1', 'account-1', '', 'websocket', ?, ?, '{}', ?)""",
            (now, now, now),
        )
        connection.execute(
            """INSERT INTO fj_chat_message_semantics (
                 raw_message_id, semantic_type, display_text, reply_required, classifier_version,
                 semantic_context_json, derived_at
               ) VALUES ('inbound-1', 'recruiter_text', '请发简历', 1, 'test', '{}', ?)""",
            (now,),
        )
        action_id = action_scheduler.create_action(
            connection,
            action_type="resume_send",
            account_uid="account-1",
            session_id="session-1",
            encrypt_resume_id="resume-fixed",
            resume_filename="已选简历.pdf",
        )
    boss_chat.update_runtime(test_db, {"send_enabled": True})
    claimed = action_scheduler.claim_unified_action(
        test_db, "executor-1", action_id=action_id, account_uid="account-1"
    )
    assert claimed is not None
    return claimed


@pytest.mark.parametrize(
    ("attachments", "expected_reason"),
    [
        ([{"encryptResumeId": "resume-fixed", "filename": "已选简历.pdf"}], "resume_fixed_attachment_valid"),
        ([{"encryptResumeId": "other", "filename": "另一份简历.pdf"}], "resume_attachment_invalid"),
        ([{"encryptResumeId": "resume-fixed", "filename": "同 ID 的另一份.pdf"}], "resume_attachment_invalid"),
        ([{"encryptResumeId": "other", "filename": "已选简历.pdf"}], "resume_attachment_invalid"),
    ],
)
def test_resume_preflight_requires_exact_original_attachment(test_db, attachments, expected_reason) -> None:
    claimed = _claimed_resume_action(test_db)

    decision = action_scheduler.preflight_action(
        test_db,
        "executor-1",
        str(claimed["id"]),
        int(claimed["execution_epoch"]),
        {"resume_list_snapshot": {"attachments": attachments}},
    )

    if expected_reason == "resume_fixed_attachment_valid":
        assert decision["decision"] == "dispatch"
        with test_db.connect() as connection:
            action = connection.execute(
                "SELECT preflight_reason_code FROM fj_actions WHERE id=?", (claimed["id"],)
            ).fetchone()
        assert action["preflight_reason_code"] == expected_reason
        dispatched = action_scheduler.mark_dispatch_started(
            test_db,
            "executor-1",
            str(claimed["id"]),
            int(claimed["execution_epoch"]),
            str(decision["dispatch_token"]),
        )
        assert dispatched["status"] == "dispatching"
    else:
        assert decision["decision"] == "blocked"
        assert decision["reason_code"] == expected_reason
        with test_db.connect() as connection:
            action = connection.execute("SELECT status FROM fj_actions WHERE id=?", (claimed["id"],)).fetchone()
        assert action["status"] == "stale"


def test_resume_snapshot_error_blocks_before_dispatch(test_db) -> None:
    claimed = _claimed_resume_action(test_db)

    decision = action_scheduler.preflight_action(
        test_db,
        "executor-1",
        str(claimed["id"]),
        int(claimed["execution_epoch"]),
        {"resume_list_snapshot": {"error": "resume_snapshot_timeout"}},
    )

    assert decision["decision"] == "blocked"
    assert decision["reason_code"] == "resume_snapshot_unavailable"
    with test_db.connect() as connection:
        action = connection.execute("SELECT status FROM fj_actions WHERE id=?", (claimed["id"],)).fetchone()
    assert action["status"] == "blocked"


def test_resume_transport_accepted_waits_for_trusted_outbound_observation(test_db) -> None:
    claimed = _claimed_resume_action(test_db)
    with test_db.connect() as connection:
        connection.execute("UPDATE fj_actions SET status='accepted' WHERE id=?", (claimed["id"],))
        action = connection.execute("SELECT status FROM fj_actions WHERE id=?", (claimed["id"],)).fetchone()

    assert action["status"] == "accepted"


def test_unknown_resume_action_is_not_claimed_again(test_db) -> None:
    claimed = _claimed_resume_action(test_db)
    with test_db.connect() as connection:
        connection.execute("UPDATE fj_actions SET status='unknown', lease_expires_at=NULL WHERE id=?", (claimed["id"],))

    assert action_scheduler.claim_unified_action(
        test_db, "executor-2", action_id=str(claimed["id"]), account_uid="account-1"
    ) is None
