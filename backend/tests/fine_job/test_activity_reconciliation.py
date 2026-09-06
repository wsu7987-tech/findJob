from __future__ import annotations

from types import SimpleNamespace

from backend.app.services.fine_job.execution_reconciliation import (
    normalize_message_text,
    observe_outbound_chat_message,
    record_execution_evidence,
)
from backend.app.services.fine_job.job_activity import (
    append_job_activity,
    reconcile_chat_session_activity,
    replay_job_pipeline,
)
from backend.app.services.fine_job import job_hunt_analysis


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


def test_rejection_observed_is_allowed_execution_evidence(test_db) -> None:
    with test_db.connect() as connection:
        connection.commit()
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.executescript(
            """
            DROP TABLE fj_execution_evidence;
            CREATE TABLE fj_execution_evidence (
              id TEXT PRIMARY KEY, action_ref_type TEXT NOT NULL,
              action_ref_id TEXT NOT NULL, evidence_type TEXT NOT NULL,
              source TEXT NOT NULL, source_ref_type TEXT NOT NULL,
              source_ref_id TEXT NOT NULL, observed_at TEXT NOT NULL,
              confidence REAL NOT NULL DEFAULT 1, evidence_level TEXT NOT NULL,
              payload_json TEXT NOT NULL DEFAULT '{}', dedupe_key TEXT NOT NULL UNIQUE,
              created_at TEXT NOT NULL,
              CHECK (action_ref_type IN ('automation_action', 'chat_send_action')),
              CHECK (evidence_type IN (
                'outbound_message_observed', 'inbound_reply_observed',
                'conversation_created', 'greeting_state_changed',
                'page_state_confirmed', 'protocol_acknowledged'
              )),
              CHECK (confidence >= 0 AND confidence <= 1),
              CHECK (evidence_level IN ('direct', 'strong_inferred', 'weak_inferred'))
            );
            """
        )
        connection.commit()
        connection.execute("PRAGMA foreign_keys = ON")
    test_db.initialize()
    with test_db.connect() as connection:
        connection.execute(
            """
            INSERT INTO fj_execution_evidence (
              id, action_ref_type, action_ref_id, evidence_type, source,
              source_ref_type, source_ref_id, observed_at, confidence,
              evidence_level, payload_json, dedupe_key, created_at
            ) VALUES (
              'rejection-evidence', 'automation_action', 'action-pending',
              'rejection_observed', 'analysis', 'chat_message', 'message-rejected',
              ?, 1, 'strong_inferred', '{}', 'rejection-evidence-dedupe', ?
            )
            """,
            (NOW, NOW),
        )
        stored = connection.execute(
            "SELECT evidence_type FROM fj_execution_evidence WHERE id = 'rejection-evidence'"
        ).fetchone()[0]
    assert stored == "rejection_observed"


def test_database_upgrade_adds_missing_conversation_analysis_activity(test_db) -> None:
    _create_job(test_db, "partial-activity-upgrade")
    job_id = _job_id(test_db, "partial-activity-upgrade")
    with test_db.connect() as connection:
        connection.commit()
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.executescript(
            """
            DROP TABLE fj_job_activity_events;
            CREATE TABLE fj_job_activity_events (
              id TEXT PRIMARY KEY, job_id TEXT NOT NULL, company_id TEXT,
              chat_session_id TEXT, event_type TEXT NOT NULL, occurred_at TEXT NOT NULL,
              source TEXT NOT NULL, source_ref_type TEXT NOT NULL,
              source_ref_id TEXT NOT NULL, confidence REAL NOT NULL DEFAULT 1,
              evidence_level TEXT NOT NULL DEFAULT 'direct',
              payload_json TEXT NOT NULL DEFAULT '{}', dedupe_key TEXT NOT NULL UNIQUE,
              created_at TEXT NOT NULL,
              CHECK (event_type IN (
                'job_discovered', 'job_shortlisted',
                'candidate_initiated_contact', 'recruiter_initiated_contact',
                'greeting_requested', 'greeting_sent', 'greeting_failed',
                'recruiter_replied', 'candidate_replied',
                'resume_requested', 'resume_submitted', 'resume_accepted', 'resume_viewed',
                'under_review', 'interview_intent_detected', 'interview_invited',
                'interview_scheduled', 'rejected', 'job_closed',
                'followup_recommended', 'no_response_detected', 'offer_received',
                'conversation_closed', 'manual_stage_changed'
              )),
              CHECK (confidence >= 0 AND confidence <= 1),
              CHECK (evidence_level IN ('direct', 'strong_inferred', 'weak_inferred'))
            );
            """
        )
        connection.execute(
            """
            INSERT INTO fj_job_activity_events (
              id, job_id, event_type, occurred_at, source, source_ref_type,
              source_ref_id, confidence, evidence_level, payload_json,
              dedupe_key, created_at
            ) VALUES (
              'partial-existing-event', ?, 'job_discovered', ?, 'migration',
              'boss_job', ?, 1, 'direct', '{}', 'partial-existing-event', ?
            )
            """,
            (job_id, NOW, job_id, NOW),
        )
        connection.commit()
        connection.execute("PRAGMA foreign_keys = ON")

    test_db.initialize()
    append_job_activity(
        test_db,
        job_id=job_id,
        event_type="conversation_state_analyzed",
        occurred_at=NOW,
        source="analysis",
        source_ref_type="chat_message",
        source_ref_id="partial-analysis-message",
        confidence=0.9,
        evidence_level="strong_inferred",
        payload={"waiting_on": "recruiter"},
        dedupe_key="partial-analysis-event",
    )
    with test_db.connect() as connection:
        stored_types = {
            row["event_type"]
            for row in connection.execute(
                "SELECT event_type FROM fj_job_activity_events WHERE job_id = ?",
                (job_id,),
            ).fetchall()
        }
    assert stored_types == {"job_discovered", "conversation_state_analyzed"}


def test_database_upgrade_preserves_legacy_terminal_application_statuses(test_db) -> None:
    for source_job_id in ("upgrade-offer", "upgrade-rejected", "upgrade-closed"):
        _create_job(test_db, source_job_id)
    rows = [
        ("upgrade-app-offer", _job_id(test_db, "upgrade-offer"), "offer"),
        ("upgrade-app-rejected", _job_id(test_db, "upgrade-rejected"), "rejected"),
        ("upgrade-app-closed", _job_id(test_db, "upgrade-closed"), "closed"),
    ]
    with test_db.connect() as connection:
        connection.commit()
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.executescript(
            """
            DROP TABLE fj_job_applications;
            CREATE TABLE fj_job_applications (
              id TEXT PRIMARY KEY, job_id TEXT NOT NULL UNIQUE, company_id TEXT,
              status TEXT, source TEXT NOT NULL DEFAULT 'manual', source_action_id TEXT,
              evidence_level TEXT NOT NULL DEFAULT 'confirmed', applied_at TEXT NOT NULL,
              note TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
              CHECK (status IS NULL OR status IN (
                'applied', 'cleared', 'offer', 'rejected', 'closed'
              )),
              CHECK (source IN ('boss_action', 'manual', 'mcp', 'migration')),
              CHECK (evidence_level IN ('confirmed', 'inferred'))
            );
            """
        )
        connection.executemany(
            """
            INSERT INTO fj_job_applications (
              id, job_id, status, source, evidence_level, applied_at,
              note, created_at, updated_at
            ) VALUES (?, ?, ?, 'migration', 'confirmed', ?, '', ?, ?)
            """,
            [(application_id, job_id, status, NOW, NOW, NOW) for application_id, job_id, status in rows],
        )
        connection.commit()
        connection.execute("PRAGMA foreign_keys = ON")

    test_db.initialize()
    with test_db.connect() as connection:
        stored = {
            row["status"]: connection.execute(
                "SELECT stage FROM fj_job_pipeline_snapshots WHERE job_id = ?",
                (row["job_id"],),
            ).fetchone()[0]
            for row in connection.execute(
                "SELECT job_id, status FROM fj_job_applications"
            ).fetchall()
        }
        event_count = connection.execute(
            """
            SELECT COUNT(*) FROM fj_job_activity_events
            WHERE source = 'migration' AND event_type IN ('offer_received', 'rejected', 'job_closed')
            """
        ).fetchone()[0]
    assert stored == {"offer": "offer", "rejected": "rejected", "closed": "closed"}
    assert event_count == 3


def test_legacy_migration_is_conservative_and_idempotent(test_db) -> None:
    statuses = [
        "pending_greeting", "pending_application", "communicating",
        "rejected", "offer", "closed",
    ]
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
        terminal_stages = {
            row["status"]: connection.execute(
                "SELECT stage FROM fj_job_pipeline_snapshots WHERE job_id = ?",
                (row["job_id"],),
            ).fetchone()[0]
            for row in connection.execute(
                "SELECT job_id, status FROM fj_job_applications WHERE status IN ('rejected', 'offer', 'closed')"
            ).fetchall()
        }
    assert after == before
    assert after_job_count == before_job_count
    assert after_company_count == before_company_count
    assert after_jd == before_jd
    assert resume_count == 0
    assert {"job_discovered", "greeting_sent", "recruiter_replied"}.issubset(migrated_types)
    assert second_event_count == first_event_count
    assert terminal_stages == {"rejected": "rejected", "offer": "offer", "closed": "closed"}


def _create_progress_session(
    db,
    *,
    source_job_id: str,
    session_id: str,
    content: str,
    direction: str = "inbound",
    message_type: str = "text",
    source: str = "websocket",
    client_mid: str = "",
    history_has_more: int = 0,
) -> tuple[str, str]:
    _create_job(db, source_job_id)
    job_id = _job_id(db, source_job_id)
    message_id = f"message-{session_id}"
    with db.connect() as connection:
        connection.execute(
            """
            INSERT INTO fj_chat_sessions (
              id, account_uid, peer_uid, job_id, status, history_has_more,
              session_version, latest_message_id, latest_inbound_message_id,
              last_message_at, created_at, updated_at
            ) VALUES (?, 'candidate-1', ?, ?, 'active', ?, 1, ?, ?, ?, ?, ?)
            """,
            (
                session_id, f"boss-{session_id}", job_id, history_has_more,
                message_id, message_id if direction == "inbound" else None,
                NOW, NOW, NOW,
            ),
        )
        connection.execute(
            """
            INSERT INTO fj_chat_messages (
              id, session_id, platform_message_id, direction, message_type, content,
              sender_uid, receiver_uid, client_mid, source, sent_at, observed_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                message_id, session_id, f"platform-{message_id}", direction, message_type, content,
                f"boss-{session_id}" if direction == "inbound" else "candidate-1",
                "candidate-1" if direction == "inbound" else f"boss-{session_id}",
                client_mid, source,
                NOW, NOW, NOW,
            ),
        )
    return job_id, message_id


def test_single_analysis_rejection_is_idempotent_and_survives_auxiliary_failure(
    configured_client,
    monkeypatch,
) -> None:
    db = configured_client.app.state.db
    job_id, message_id = _create_progress_session(
        db,
        source_job_id="progress-rejected",
        session_id="progress-rejected-session",
        content="你的经验和岗位不太匹配，这次先不考虑了。",
    )
    _create_automation_action(
        db,
        action_id="progress-running-action",
        job_id=job_id,
        status="running",
        canonical_status="unknown",
    )

    def fail_auxiliary_evidence(*args, **kwargs):
        raise RuntimeError("auxiliary evidence failed")

    monkeypatch.setattr(
        job_hunt_analysis,
        "record_execution_evidence_with_connection",
        fail_auxiliary_evidence,
    )
    first = configured_client.post(
        "/api/fine-job/boss-chat/sessions/progress-rejected-session/analyze-progress"
    )
    second = configured_client.post(
        "/api/fine-job/boss-chat/sessions/progress-rejected-session/analyze-progress"
    )
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["auxiliary_warning"]
    assert second.json()["insight"]["id"] == first.json()["insight"]["id"]
    with db.connect() as connection:
        snapshot = connection.execute(
            "SELECT * FROM fj_job_pipeline_snapshots WHERE job_id = ?", (job_id,)
        ).fetchone()
        application = connection.execute(
            "SELECT status FROM fj_job_applications WHERE job_id = ?", (job_id,)
        ).fetchone()
        activity_count = connection.execute(
            """
            SELECT COUNT(*) FROM fj_job_activity_events
            WHERE job_id = ? AND event_type = 'rejected' AND source_ref_id = ?
            """,
            (job_id, message_id),
        ).fetchone()[0]
        insight_count = connection.execute(
            "SELECT COUNT(*) FROM fj_conversation_insights WHERE session_id = ? AND run_id IS NULL",
            ("progress-rejected-session",),
        ).fetchone()[0]
    assert snapshot["stage"] == "rejected"
    assert snapshot["waiting_on"] == "none"
    assert snapshot["rejection_reason_source"] == "recruiter_explicit"
    assert snapshot["rejection_reason_category"] == "experience"
    assert application["status"] == "rejected"
    assert activity_count == 1
    assert insight_count == 1


def test_position_filled_and_job_closed_have_distinct_pipeline_outcomes(configured_client) -> None:
    db = configured_client.app.state.db
    filled_job_id, _ = _create_progress_session(
        db,
        source_job_id="progress-filled",
        session_id="progress-filled-session",
        content="这个岗位已经招到合适候选人了。",
    )
    closed_job_id, _ = _create_progress_session(
        db,
        source_job_id="progress-closed",
        session_id="progress-closed-session",
        content="这个岗位的 HC 已关闭，停止招聘。",
    )
    for session_id in ("progress-filled-session", "progress-closed-session"):
        response = configured_client.post(
            f"/api/fine-job/boss-chat/sessions/{session_id}/analyze-progress"
        )
        assert response.status_code == 200
    with db.connect() as connection:
        filled = connection.execute(
            "SELECT * FROM fj_job_pipeline_snapshots WHERE job_id = ?", (filled_job_id,)
        ).fetchone()
        closed = connection.execute(
            "SELECT * FROM fj_job_pipeline_snapshots WHERE job_id = ?", (closed_job_id,)
        ).fetchone()
    assert filled["stage"] == "rejected"
    assert filled["rejection_reason_category"] == "position_filled"
    assert closed["stage"] == "closed"
    assert closed["rejection_reason_category"] == "headcount_closed"


def test_contact_origin_requires_complete_history_and_action_evidence(test_db) -> None:
    recruiter_job, _ = _create_progress_session(
        test_db,
        source_job_id="origin-recruiter",
        session_id="origin-recruiter-session",
        content="你好，想和你聊聊这个岗位。",
    )
    external_job, _ = _create_progress_session(
        test_db,
        source_job_id="origin-external",
        session_id="origin-external-session",
        content="你好，我对这个岗位感兴趣。",
        direction="outbound",
    )
    unknown_job, _ = _create_progress_session(
        test_db,
        source_job_id="origin-unknown",
        session_id="origin-unknown-session",
        content="你好，我对这个岗位感兴趣。",
        direction="outbound",
        history_has_more=1,
    )
    manual_job, manual_message = _create_progress_session(
        test_db,
        source_job_id="origin-finejob-manual",
        session_id="origin-finejob-manual-session",
        content="你好，我想了解这个岗位。",
        direction="outbound",
        source="assistant",
        client_mid="finejob-manual-client",
    )
    auto_job, _ = _create_progress_session(
        test_db,
        source_job_id="origin-finejob-auto",
        session_id="origin-finejob-auto-session",
        content="你好，我对这个岗位感兴趣。",
        direction="outbound",
    )
    with test_db.connect() as connection:
        connection.execute(
            """
            INSERT INTO fj_chat_reply_tasks (
              id, session_id, trigger_source, status, based_on_message_id,
              based_on_session_version, created_at, updated_at
            ) VALUES (
              'origin-manual-reply', 'origin-finejob-manual-session', 'manual',
              'confirmed', ?, 1, ?, ?
            )
            """,
            (manual_message, NOW, NOW),
        )
        connection.execute(
            """
            INSERT INTO fj_chat_send_actions (
              id, reply_task_id, session_id, status, text, client_mid,
              execution_epoch, attempt_count, completed_at, created_at, updated_at,
              canonical_status, canonical_updated_at
            ) VALUES (
              'origin-manual-send', 'origin-manual-reply',
              'origin-finejob-manual-session', 'accepted', '你好，我想了解这个岗位。',
              'finejob-manual-client', 1, 1, ?, ?, ?, 'succeeded', ?
            )
            """,
            (NOW, NOW, NOW, NOW),
        )
    _create_automation_action(
        test_db,
        action_id="origin-auto-action",
        job_id=auto_job,
        status="succeeded",
        canonical_status="succeeded",
    )
    for session_id in (
        "origin-recruiter-session", "origin-external-session", "origin-unknown-session",
        "origin-finejob-manual-session", "origin-finejob-auto-session",
    ):
        reconcile_chat_session_activity(test_db, session_id)
    with test_db.connect() as connection:
        origins = {
            job_id: connection.execute(
                "SELECT contact_origin FROM fj_job_pipeline_snapshots WHERE job_id = ?",
                (job_id,),
            ).fetchone()[0]
            for job_id in (recruiter_job, external_job, unknown_job, manual_job, auto_job)
        }
    assert origins[recruiter_job] == "recruiter_initiated"
    assert origins[external_job] == "external_candidate_initiated"
    assert origins[unknown_job] == "unknown"
    assert origins[manual_job] == "candidate_initiated"
    assert origins[auto_job] == "finejob_auto"


def test_analyze_progress_handles_outbound_first_message(
    configured_client,
    monkeypatch,
) -> None:
    db = configured_client.app.state.db
    config = configured_client.app.state.config
    config.reasoning_executor = "codex-cli"
    config.codex_model = "test-model"

    def assert_strict_objects(node) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object":
                assert node.get("additionalProperties") is False
                assert set(node.get("required") or []) == set((node.get("properties") or {}).keys())
            for value in node.values():
                assert_strict_objects(value)
        elif isinstance(node, list):
            for value in node:
                assert_strict_objects(value)

    def fake_run_codex_exec(**kwargs):
        # 单会话分析必须向 Codex 提交可严格校验的完整输出契约。
        assert_strict_objects(kwargs["output_schema"])
        return SimpleNamespace(output=job_hunt_analysis._stub_single_analysis({"messages": []}))

    monkeypatch.setattr(job_hunt_analysis, "run_codex_exec", fake_run_codex_exec)
    job_id, _ = _create_progress_session(
        db,
        source_job_id="origin-analysis-outbound",
        session_id="origin-analysis-outbound-session",
        content="你好，我想了解这个岗位。",
        direction="outbound",
        client_mid="",
    )

    response = configured_client.post(
        "/api/fine-job/boss-chat/sessions/origin-analysis-outbound-session/analyze-progress"
    )

    assert response.status_code == 200
    with db.connect() as connection:
        snapshot = connection.execute(
            "SELECT contact_origin FROM fj_job_pipeline_snapshots WHERE job_id = ?",
            (job_id,),
        ).fetchone()
    assert snapshot["contact_origin"] == "external_candidate_initiated"


def test_analyze_progress_creates_review_draft_when_reply_is_needed(
    configured_client,
) -> None:
    db = configured_client.app.state.db
    _create_progress_session(
        db,
        source_job_id="analysis-draft-needed",
        session_id="analysis-draft-needed-session",
        content="方便介绍一下最近的项目吗？",
    )

    response = configured_client.post(
        "/api/fine-job/boss-chat/sessions/analysis-draft-needed-session/analyze-progress"
    )

    assert response.status_code == 200
    assert response.json()["reply_task_created"] is True
    with db.connect() as connection:
        task = connection.execute(
            """
            SELECT status, action_kind, draft_text
            FROM fj_chat_reply_tasks
            WHERE session_id = 'analysis-draft-needed-session'
            """
        ).fetchone()
    assert task["status"] == "awaiting_review"
    assert task["action_kind"] == "reply"
    assert task["draft_text"]


def test_generic_fit_rejection_creates_reason_question_draft(
    configured_client,
) -> None:
    db = configured_client.app.state.db
    _create_progress_session(
        db,
        source_job_id="generic-fit-rejection",
        session_id="generic-fit-rejection-session",
        content="这个岗位暂时不考虑了。",
    )

    response = configured_client.post(
        "/api/fine-job/boss-chat/sessions/generic-fit-rejection-session/analyze-progress"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["progress"]["stage"] == "rejected"
    assert payload["progress"]["primary_action"]["type"] == "ask_rejection_reason"
    assert payload["reply_task_created"] is True
    with db.connect() as connection:
        task = connection.execute(
            """
            SELECT action_kind, draft_text FROM fj_chat_reply_tasks
            WHERE session_id = 'generic-fit-rejection-session'
            """
        ).fetchone()
    assert task["action_kind"] == "ask_rejection_reason"
    assert "原因" in task["draft_text"]


def test_manual_message_generation_allows_human_takeover_session(
    configured_client,
) -> None:
    db = configured_client.app.state.db
    _create_progress_session(
        db,
        source_job_id="manual-draft-human-takeover",
        session_id="manual-draft-human-takeover-session",
        content="你好，我想进一步了解这个岗位。",
        direction="outbound",
    )
    with db.connect() as connection:
        connection.execute(
            """
            UPDATE fj_chat_sessions SET status = 'human_takeover'
            WHERE id = 'manual-draft-human-takeover-session'
            """
        )

    response = configured_client.post(
        "/api/fine-job/boss-chat/sessions/manual-draft-human-takeover-session/generate",
        json={"action_kind": "followup", "instruction": "突出项目经验"},
    )

    assert response.status_code == 200
    task = response.json()["reply_task"]
    assert task["status"] == "awaiting_review"
    assert task["action_kind"] == "followup"
    assert task["context"]["job_progress"]["waiting_duration_days"] >= 0


def test_progress_acceptance_resume_review_reply_unknown_reason_and_soft_rejection(
    configured_client,
) -> None:
    db = configured_client.app.state.db
    resume_job, _ = _create_progress_session(
        db,
        source_job_id="accept-resume-viewed",
        session_id="accept-resume-viewed-session",
        content="对方已查看了您的附件简历",
        message_type="system",
    )
    review_job, _ = _create_progress_session(
        db,
        source_job_id="accept-under-review",
        session_id="accept-under-review-session",
        content="我把你的简历发给用人部门看看。",
    )
    reply_job, _ = _create_progress_session(
        db,
        source_job_id="accept-needs-reply",
        session_id="accept-needs-reply-session",
        content="方便介绍一下最近的项目吗？",
    )
    unknown_reason_job, unknown_message = _create_progress_session(
        db,
        source_job_id="accept-unknown-reason",
        session_id="accept-unknown-reason-session",
        content="感谢沟通。",
    )
    append_job_activity(
        db,
        job_id=unknown_reason_job,
        chat_session_id="accept-unknown-reason-session",
        event_type="rejected",
        occurred_at=NOW,
        source="manual",
        source_ref_type="chat_message",
        source_ref_id=unknown_message,
        evidence_level="direct",
        payload={"rejection_reason_source": "unknown", "rejection_reason_category": "unknown"},
        dedupe_key="accept:unknown-reason:rejected",
    )
    soft_job, _ = _create_progress_session(
        db,
        source_job_id="accept-soft-rejection",
        session_id="accept-soft-rejection-session",
        content="有消息再联系你。",
    )

    results = {}
    for session_id in (
        "accept-resume-viewed-session", "accept-under-review-session",
        "accept-needs-reply-session", "accept-unknown-reason-session",
        "accept-soft-rejection-session",
    ):
        response = configured_client.post(
            f"/api/fine-job/boss-chat/sessions/{session_id}/analyze-progress"
        )
        assert response.status_code == 200
        results[session_id] = response.json()["progress"]

    assert results["accept-resume-viewed-session"]["stage"] == "resume_viewed"
    assert results["accept-resume-viewed-session"]["waiting_on"] == "recruiter"
    assert results["accept-under-review-session"]["stage"] == "under_review"
    assert results["accept-under-review-session"]["waiting_on"] == "recruiter"
    assert results["accept-needs-reply-session"]["waiting_on"] == "candidate"
    assert results["accept-needs-reply-session"]["primary_action"]["type"] == "reply"
    assert results["accept-unknown-reason-session"]["primary_action"]["type"] == "ask_rejection_reason"
    assert results["accept-soft-rejection-session"]["stage"] != "rejected"
    progress_response = configured_client.get(
        f"/api/fine-job/jobs/{review_job}/progress",
        params={"session_id": "accept-under-review-session"},
    )
    assert progress_response.status_code == 200
    assert progress_response.json()["stage"] == "under_review"
    with db.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM fj_job_activity_events WHERE job_id = ? AND event_type = 'rejected'",
            (soft_job,),
        ).fetchone()[0] == 0


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
