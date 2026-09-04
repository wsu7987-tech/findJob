from __future__ import annotations

from backend.app.services.fine_job.execution_reconciliation import (
    normalize_message_text,
    observe_outbound_chat_message,
    record_execution_evidence,
)
from backend.app.services.fine_job.job_activity import (
    append_job_activity,
    replay_job_pipeline,
)


NOW = "2026-09-05T10:00:00Z"


def _create_job(db, job_id: str = "job-1") -> None:
    from backend.app.services.fine_job.boss_capture_history import create_capture_batch, record_capture_jobs

    capture_id = f"capture-{job_id}"
    create_capture_batch(
        db,
        capture_id=capture_id,
        keyword="Python",
        city="广州",
        pages=1,
        auto_details=False,
        created_at=NOW,
    )
    saved = record_capture_jobs(
        db,
        capture_id=capture_id,
        jobs=[{
            "job_id": job_id,
            "encrypt_job_id": f"enc-{job_id}",
            "title": f"岗位 {job_id}",
            "boss_name": "示例公司",
            "job_link": f"https://www.zhipin.com/job_detail/enc-{job_id}.html",
        }],
        collected_at=NOW,
    )
    assert saved[0]["history_record_id"] == job_id or saved[0]["history_record_id"]


def _job_id(db, source_job_id: str = "job-1") -> str:
    with db.connect() as connection:
        row = connection.execute(
            "SELECT id FROM fj_boss_jobs WHERE source_job_id = ?",
            (source_job_id,),
        ).fetchone()
    assert row is not None
    return str(row["id"])


def _create_chat_action(
    db,
    *,
    action_id: str,
    job_id: str,
    status: str = "unknown",
    canonical_status: str | None = "unknown",
    session_id: str = "session-1",
    text: str = "你好，我目前还在职",
    client_mid: str = "client-1",
    dispatched_at: str = NOW,
) -> None:
    peer_uid = f"boss-{session_id}"
    with db.connect() as connection:
        connection.execute(
            """
            INSERT OR IGNORE INTO fj_chat_sessions (
              id, account_uid, peer_uid, job_id, status, created_at, updated_at
            ) VALUES (?, 'candidate-1', ?, ?, 'active', ?, ?)
            """,
            (session_id, peer_uid, job_id, NOW, NOW),
        )
        message_id = f"trigger-{action_id}"
        connection.execute(
            """
            INSERT OR IGNORE INTO fj_chat_messages (
              id, session_id, platform_message_id, direction, message_type, content,
              sender_uid, receiver_uid, source, sent_at, observed_at, created_at
            ) VALUES (?, ?, ?, 'inbound', 'text', '请介绍一下',
                      ?, 'candidate-1', 'websocket', ?, ?, ?)
            """,
            (message_id, session_id, message_id, peer_uid, NOW, NOW, NOW),
        )
        reply_id = f"reply-{action_id}"
        connection.execute(
            """
            INSERT OR IGNORE INTO fj_chat_reply_tasks (
              id, session_id, trigger_source, status, based_on_message_id,
              based_on_session_version, created_at, updated_at
            ) VALUES (?, ?, 'manual', 'confirmed', ?, 0, ?, ?)
            """,
            (reply_id, session_id, message_id, NOW, NOW),
        )
        connection.execute(
            """
            INSERT INTO fj_chat_send_actions (
              id, reply_task_id, session_id, status, text, client_mid,
              execution_epoch, attempt_count, dispatched_at, created_at, updated_at,
              canonical_status, canonical_updated_at, canonical_reason
            ) VALUES (?, ?, ?, ?, ?, ?, 1, 1, ?, ?, ?, ?, ?, '测试初始状态')
            """,
            (
                action_id,
                reply_id,
                session_id,
                status,
                text,
                client_mid,
                dispatched_at,
                NOW,
                NOW,
                canonical_status,
                NOW,
            ),
        )


def _insert_outbound(
    db,
    *,
    message_id: str,
    session_id: str,
    content: str = "你好，我目前还在职",
    client_mid: str = "client-1",
    source: str = "assistant",
    sent_at: str = "2026-09-05T10:01:00Z",
) -> None:
    with db.connect() as connection:
        connection.execute(
            """
            INSERT INTO fj_chat_messages (
              id, session_id, platform_message_id, direction, message_type, content,
              sender_uid, receiver_uid, client_mid, source, sent_at, observed_at, created_at
            ) VALUES (?, ?, ?, 'outbound', 'text', ?, 'candidate-1', 'boss-1', ?, ?, ?, ?, ?)
            """,
            (message_id, session_id, f"platform-{message_id}", content, client_mid, source, sent_at, sent_at, sent_at),
        )


def _create_automation_action(
    db,
    *,
    action_id: str,
    job_id: str,
    status: str = "unknown",
    canonical_status: str | None = "unknown",
) -> None:
    with db.connect() as connection:
        evaluation_id = f"evaluation-{action_id}"
        review_id = f"review-{action_id}"
        connection.execute(
            """
            INSERT INTO fj_job_evaluations (
              id, job_id, source, decision, confidence, created_at
            ) VALUES (?, ?, 'rules', 'recommend', 1, ?)
            """,
            (evaluation_id, job_id, NOW),
        )
        connection.execute(
            """
            INSERT INTO fj_review_items (
              id, job_id, evaluation_id, status, ai_decision, created_at, updated_at
            ) VALUES (?, ?, ?, 'approved', 'recommend', ?, ?)
            """,
            (review_id, job_id, evaluation_id, NOW, NOW),
        )
        connection.execute(
            """
            INSERT INTO fj_automation_actions (
              id, job_id, evaluation_id, review_item_id, action_type, task_type,
              status, idempotency_key, execution_state, result_json,
              created_at, updated_at, completed_at, canonical_status,
              canonical_updated_at, canonical_reason
            ) VALUES (?, ?, ?, ?, 'BOSS_DEFAULT_GREETING', 'BOSS_DEFAULT_GREETING',
                      ?, ?, ?, '{}', ?, ?, ?, ?, ?, '测试初始状态')
            """,
            (
                action_id,
                job_id,
                evaluation_id,
                review_id,
                status,
                f"idempotency-{action_id}",
                status,
                NOW,
                NOW,
                NOW if status in {"succeeded", "failed", "unknown"} else None,
                canonical_status,
                NOW,
            ),
        )


def test_activity_dedupe_projection_replay_and_terminal_reopen(test_db) -> None:
    _create_job(test_db)
    job_id = _job_id(test_db)
    event, created = append_job_activity(
        test_db,
        job_id=job_id,
        event_type="greeting_sent",
        occurred_at="2026-09-05T10:02:00Z",
        source="executor",
        source_ref_type="automation_action",
        source_ref_id="action-1",
        evidence_level="direct",
        dedupe_key="event:greeting:1",
    )
    duplicate, duplicate_created = append_job_activity(
        test_db,
        job_id=job_id,
        event_type="greeting_sent",
        occurred_at="2026-09-05T10:02:00Z",
        source="executor",
        source_ref_type="automation_action",
        source_ref_id="action-1",
        evidence_level="direct",
        dedupe_key="event:greeting:1",
    )
    assert created is True
    assert duplicate_created is False
    assert duplicate["id"] == event["id"]
    assert replay_job_pipeline(test_db, job_id)["stage"] == "greeted"

    append_job_activity(
        test_db,
        job_id=job_id,
        event_type="rejected",
        occurred_at="2026-09-05T10:03:00Z",
        source="manual",
        source_ref_type="user_operation",
        source_ref_id="reject-1",
        evidence_level="direct",
        dedupe_key="event:reject:1",
    )
    append_job_activity(
        test_db,
        job_id=job_id,
        event_type="resume_submitted",
        occurred_at="2026-09-05T10:04:00Z",
        source="workflow",
        source_ref_type="test",
        source_ref_id="resume-1",
        evidence_level="direct",
        dedupe_key="event:resume:1",
    )
    assert replay_job_pipeline(test_db, job_id)["stage"] == "rejected"
    reopened, _ = append_job_activity(
        test_db,
        job_id=job_id,
        event_type="manual_stage_changed",
        occurred_at="2026-09-05T10:05:00Z",
        source="manual",
        source_ref_type="user_operation",
        source_ref_id="reopen-1",
        evidence_level="direct",
        payload={"stage": "communicating", "allow_reopen": True},
        dedupe_key="event:reopen:1",
    )
    assert reopened["source_ref_id"] == "reopen-1"
    assert replay_job_pipeline(test_db, job_id)["stage"] == "communicating"


def test_greeting_sent_does_not_mean_resume_submitted(test_db) -> None:
    _create_job(test_db)
    job_id = _job_id(test_db)
    append_job_activity(
        test_db,
        job_id=job_id,
        event_type="greeting_sent",
        occurred_at=NOW,
        source="executor",
        source_ref_type="automation_action",
        source_ref_id="action-1",
        evidence_level="direct",
        dedupe_key="event:greeting-only",
    )
    assert replay_job_pipeline(test_db, job_id)["stage"] == "greeted"
    with test_db.connect() as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM fj_job_activity_events WHERE event_type = 'resume_submitted'"
        ).fetchone()[0]
    assert count == 0


def test_direct_outbound_evidence_reconciles_unknown_once(test_db) -> None:
    _create_job(test_db)
    job_id = _job_id(test_db)
    _create_chat_action(test_db, action_id="send-1", job_id=job_id)
    _insert_outbound(test_db, message_id="out-1", session_id="session-1")
    with test_db.connect() as connection:
        first = observe_outbound_chat_message(
            connection,
            message_id="out-1",
            observed_account_uid="candidate-1",
        )
        second = observe_outbound_chat_message(connection, message_id="out-1")
        action = connection.execute(
            "SELECT status, canonical_status, attempt_count FROM fj_chat_send_actions WHERE id = 'send-1'"
        ).fetchone()
        evidence_count = connection.execute(
            "SELECT COUNT(*) FROM fj_execution_evidence WHERE action_ref_type = 'chat_send_action' AND action_ref_id = 'send-1'"
        ).fetchone()[0]
        reconciliation_count = connection.execute(
            "SELECT COUNT(*) FROM fj_execution_reconciliations WHERE action_ref_type = 'chat_send_action' AND action_ref_id = 'send-1'"
        ).fetchone()[0]
    assert first[0]["evidence"]["evidence_level"] == "direct"
    assert second[0]["reconciliation"] is None
    assert action["status"] == "unknown"
    assert action["canonical_status"] == "succeeded"
    assert action["attempt_count"] == 1
    assert evidence_count == 1
    assert reconciliation_count == 1


def test_weak_evidence_and_terminal_actions_do_not_change_canonical_status(test_db) -> None:
    _create_job(test_db)
    job_id = _job_id(test_db)
    _create_chat_action(test_db, action_id="send-weak", job_id=job_id)
    evidence, created, reconciliation = record_execution_evidence(
        test_db,
        action_ref_type="chat_send_action",
        action_ref_id="send-weak",
        evidence_type="outbound_message_observed",
        source="manual",
        source_ref_type="manual_observation",
        source_ref_id="weak-1",
        observed_at=NOW,
        evidence_level="weak_inferred",
        confidence=0.4,
        payload={"confirmed": True},
        dedupe_key="weak-evidence-1",
    )
    assert created is True
    assert evidence["evidence_level"] == "weak_inferred"
    assert reconciliation is None
    with test_db.connect() as connection:
        assert connection.execute(
            "SELECT canonical_status FROM fj_chat_send_actions WHERE id = 'send-weak'"
        ).fetchone()[0] == "unknown"
        connection.execute(
            "UPDATE fj_chat_send_actions SET canonical_status = 'failed', status = 'failed' WHERE id = 'send-weak'"
        )
    _, _, terminal_reconciliation = record_execution_evidence(
        test_db,
        action_ref_type="chat_send_action",
        action_ref_id="send-weak",
        evidence_type="page_state_confirmed",
        source="executor",
        source_ref_type="page",
        source_ref_id="page-1",
        observed_at=NOW,
        evidence_level="direct",
        payload={"confirmed": True},
        dedupe_key="direct-after-failed",
    )
    assert terminal_reconciliation is None


def test_same_action_id_in_different_tables_does_not_cross_associate(test_db) -> None:
    _create_job(test_db)
    job_id = _job_id(test_db)
    _create_chat_action(test_db, action_id="shared-id", job_id=job_id)
    _create_automation_action(test_db, action_id="shared-id", job_id=job_id)
    evidence, created, reconciliation = record_execution_evidence(
        test_db,
        action_ref_type="chat_send_action",
        action_ref_id="shared-id",
        evidence_type="page_state_confirmed",
        source="executor",
        source_ref_type="page",
        source_ref_id="page-shared",
        observed_at=NOW,
        evidence_level="direct",
        payload={"confirmed": True},
        dedupe_key="chat:shared-id:page",
    )
    assert created is True
    assert evidence["action_ref_type"] == "chat_send_action"
    assert reconciliation is not None
    with test_db.connect() as connection:
        chat_status = connection.execute(
            "SELECT canonical_status FROM fj_chat_send_actions WHERE id = 'shared-id'"
        ).fetchone()[0]
        automation_status = connection.execute(
            "SELECT canonical_status FROM fj_automation_actions WHERE id = 'shared-id'"
        ).fetchone()[0]
    assert chat_status == "succeeded"
    assert automation_status == "unknown"


def test_matching_rejects_wrong_account_session_time_and_manual_outbound(test_db) -> None:
    _create_job(test_db)
    job_id = _job_id(test_db)
    _create_chat_action(test_db, action_id="send-match", job_id=job_id)
    _insert_outbound(test_db, message_id="wrong-account", session_id="session-1")
    _insert_outbound(
        test_db,
        message_id="bad-time",
        session_id="session-1",
        sent_at="2026-09-06T10:00:00Z",
    )
    _insert_outbound(
        test_db,
        message_id="manual",
        session_id="session-1",
        source="manual",
    )
    _create_chat_action(
        test_db,
        action_id="send-other-session",
        job_id=job_id,
        session_id="session-2",
        client_mid="other",
    )
    _insert_outbound(
        test_db,
        message_id="wrong-session",
        session_id="session-2",
        client_mid="client-1",
    )
    with test_db.connect() as connection:
        assert observe_outbound_chat_message(
            connection,
            message_id="wrong-account",
            observed_account_uid="different-account",
        ) == []
        assert observe_outbound_chat_message(connection, message_id="bad-time") == []
        assert observe_outbound_chat_message(connection, message_id="manual") == []
        # session-2 的消息只能关联 session-2 动作，不会修改 session-1。
        observe_outbound_chat_message(connection, message_id="wrong-session")
        first = connection.execute(
            "SELECT canonical_status FROM fj_chat_send_actions WHERE id = 'send-match'"
        ).fetchone()[0]
    assert first == "unknown"
    assert normalize_message_text(" 你好，  我目前还在职\n") == "你好, 我目前还在职"


def test_accepted_raw_status_is_not_confirmed_success(test_db) -> None:
    _create_job(test_db)
    job_id = _job_id(test_db)
    _create_chat_action(
        test_db,
        action_id="send-accepted",
        job_id=job_id,
        status="accepted",
        canonical_status="unknown",
    )
    with test_db.connect() as connection:
        row = connection.execute(
            "SELECT status, canonical_status FROM fj_chat_send_actions WHERE id = 'send-accepted'"
        ).fetchone()
    assert dict(row) == {"status": "accepted", "canonical_status": "unknown"}


def test_legacy_migration_is_conservative_and_idempotent(test_db) -> None:
    statuses = ["pending_greeting", "pending_application", "communicating", "rejected"]
    for index, status in enumerate(statuses):
        source_id = f"legacy-{index}"
        _create_job(test_db, source_id)
        job_id = _job_id(test_db, source_id)
        with test_db.connect() as connection:
            connection.execute(
                """
                INSERT INTO fj_job_applications (
                  id, job_id, status, source, evidence_level, applied_at,
                  note, created_at, updated_at
                ) VALUES (?, ?, ?, 'migration', 'confirmed', ?, '', ?, ?)
                """,
                (f"application-{index}", job_id, status, NOW, NOW, NOW),
            )
    greeting_job_id = _job_id(test_db, "legacy-1")
    _create_automation_action(
        test_db,
        action_id="legacy-greeting",
        job_id=greeting_job_id,
        status="succeeded",
        canonical_status=None,
    )
    with test_db.connect() as connection:
        connection.execute(
            "UPDATE fj_boss_jobs SET detail_json = ?, detail_status = 'completed' WHERE id = ?",
            ('{"jd": "负责 Python 服务开发"}', greeting_job_id),
        )
        connection.execute(
            "UPDATE fj_automation_actions SET result_json = ? WHERE id = 'legacy-greeting'",
            ('{"contacted": true, "statusCode": "BOSS_REQUEST_ACCEPTED"}',),
        )
        connection.execute(
            """
            INSERT INTO fj_chat_sessions (
              id, account_uid, peer_uid, job_id, status, created_at, updated_at
            ) VALUES ('legacy-session', 'candidate-1', 'boss-1', ?, 'active', ?, ?)
            """,
            (greeting_job_id, NOW, NOW),
        )
        connection.execute(
            """
            INSERT INTO fj_chat_messages (
              id, session_id, platform_message_id, direction, message_type, content,
              sender_uid, receiver_uid, source, sent_at, observed_at, created_at
            ) VALUES ('legacy-inbound', 'legacy-session', 'platform-in', 'inbound', 'text',
                      '你好', 'boss-1', 'candidate-1', 'websocket', ?, ?, ?)
            """,
            (NOW, NOW, NOW),
        )
    with test_db.connect() as connection:
        connection.execute("DELETE FROM fj_job_pipeline_snapshots")
        connection.execute("DELETE FROM fj_job_activity_events")
        before = [
            tuple(row)
            for row in connection.execute(
                "SELECT id, job_id, status, note FROM fj_job_applications ORDER BY id"
            ).fetchall()
        ]
        before_job_count = connection.execute("SELECT COUNT(*) FROM fj_boss_jobs").fetchone()[0]
        before_company_count = connection.execute("SELECT COUNT(*) FROM fj_companies").fetchone()[0]
        before_jd = connection.execute(
            "SELECT detail_json FROM fj_boss_jobs WHERE id = ?", (greeting_job_id,)
        ).fetchone()[0]

    test_db.initialize()
    with test_db.connect() as connection:
        first_event_count = connection.execute(
            "SELECT COUNT(*) FROM fj_job_activity_events"
        ).fetchone()[0]
        after = [
            tuple(row)
            for row in connection.execute(
                "SELECT id, job_id, status, note FROM fj_job_applications ORDER BY id"
            ).fetchall()
        ]
        after_job_count = connection.execute("SELECT COUNT(*) FROM fj_boss_jobs").fetchone()[0]
        after_company_count = connection.execute("SELECT COUNT(*) FROM fj_companies").fetchone()[0]
        after_jd = connection.execute(
            "SELECT detail_json FROM fj_boss_jobs WHERE id = ?", (greeting_job_id,)
        ).fetchone()[0]
        resume_count = connection.execute(
            "SELECT COUNT(*) FROM fj_job_activity_events WHERE event_type = 'resume_submitted'"
        ).fetchone()[0]
        migrated_types = {
            row[0]
            for row in connection.execute(
                "SELECT event_type FROM fj_job_activity_events WHERE job_id = ?",
                (greeting_job_id,),
            ).fetchall()
        }
    test_db.initialize()
    with test_db.connect() as connection:
        second_event_count = connection.execute(
            "SELECT COUNT(*) FROM fj_job_activity_events"
        ).fetchone()[0]
    assert after == before
    assert after_job_count == before_job_count
    assert after_company_count == before_company_count
    assert after_jd == before_jd
    assert resume_count == 0
    assert {"job_discovered", "greeting_sent", "recruiter_replied"}.issubset(migrated_types)
    assert second_event_count == first_event_count


def test_journey_api_returns_empty_state_without_breaking_job_detail(configured_client) -> None:
    _create_job(configured_client.app.state.db)
    job_id = _job_id(configured_client.app.state.db)
    with configured_client.app.state.db.connect() as connection:
        connection.execute("DELETE FROM fj_job_pipeline_snapshots WHERE job_id = ?", (job_id,))
        connection.execute("DELETE FROM fj_job_activity_events WHERE job_id = ?", (job_id,))
    response = configured_client.get(f"/api/fine-job/jobs/{job_id}/journey")
    assert response.status_code == 200
    assert response.json()["pipeline"] is None
    assert response.json()["activities"] == []
    assert response.json()["executions"] == []
