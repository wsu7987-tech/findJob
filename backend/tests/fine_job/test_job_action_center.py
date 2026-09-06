from __future__ import annotations

from datetime import datetime, timezone

import pytest

from backend.app.services.fine_job.boss_capture_history import (
    create_capture_batch,
    record_capture_jobs,
)
from backend.app.services.fine_job.job_action_center import list_job_actions
from backend.app.services.fine_job.job_activity import append_job_activity


NOW_TEXT = "2026-09-10T10:00:00Z"
NOW = datetime(2026, 9, 10, 10, tzinfo=timezone.utc)
EVENT_TIME = "2026-09-05T11:00:00Z"


def _create_job(db, suffix: str) -> str:
    capture_id = f"capture-action-{suffix}"
    create_capture_batch(
        db,
        capture_id=capture_id,
        keyword="Python",
        city="广州",
        pages=1,
        auto_details=False,
        created_at="2026-09-05T10:00:00Z",
    )
    record_capture_jobs(
        db,
        capture_id=capture_id,
        jobs=[{
            "job_id": f"source-{suffix}",
            "encrypt_job_id": f"encrypt-{suffix}",
            "title": f"后端工程师 {suffix}",
            "boss_name": f"示例公司 {suffix}",
            "job_link": f"https://www.zhipin.com/job_detail/{suffix}.html",
        }],
        collected_at="2026-09-05T10:00:00Z",
    )
    with db.connect() as connection:
        row = connection.execute(
            "SELECT id FROM fj_boss_jobs WHERE source_job_id = ?",
            (f"source-{suffix}",),
        ).fetchone()
    assert row is not None
    return str(row["id"])


def _create_session(
    db,
    *,
    job_id: str,
    session_id: str,
    message_id: str,
    direction: str,
    sent_at: str = EVENT_TIME,
    version: int = 1,
    created_at: str | None = None,
) -> None:
    created = created_at or sent_at
    with db.connect() as connection:
        connection.execute(
            """
            INSERT INTO fj_chat_sessions (
              id, account_uid, peer_uid, job_id, job_title, company_name,
              status, session_version, latest_message_id,
              latest_inbound_message_id, last_message_at, created_at, updated_at
            ) VALUES (?, 'candidate-1', ?, ?, '后端工程师', '示例公司',
                      'active', ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                f"boss-{session_id}",
                job_id,
                version,
                message_id,
                message_id if direction == "inbound" else None,
                sent_at,
                created,
                sent_at,
            ),
        )
        connection.execute(
            """
            INSERT INTO fj_chat_messages (
              id, session_id, platform_message_id, direction, message_type,
              content, sender_uid, receiver_uid, source,
              sent_at, observed_at, created_at
            ) VALUES (?, ?, ?, ?, 'text', '测试消息', ?, ?, 'websocket', ?, ?, ?)
            """,
            (
                message_id,
                session_id,
                f"platform-{message_id}",
                direction,
                f"boss-{session_id}" if direction == "inbound" else "candidate-1",
                "candidate-1" if direction == "inbound" else f"boss-{session_id}",
                sent_at,
                sent_at,
                sent_at,
            ),
        )


def _update_session_message(
    db,
    *,
    session_id: str,
    message_id: str,
    direction: str,
    sent_at: str,
    version: int,
) -> None:
    with db.connect() as connection:
        connection.execute(
            """
            INSERT INTO fj_chat_messages (
              id, session_id, platform_message_id, direction, message_type,
              content, sender_uid, receiver_uid, source,
              sent_at, observed_at, created_at
            ) VALUES (?, ?, ?, ?, 'text', '新消息', 'sender', 'receiver',
                      'websocket', ?, ?, ?)
            """,
            (message_id, session_id, f"platform-{message_id}", direction, sent_at, sent_at, sent_at),
        )
        connection.execute(
            """
            UPDATE fj_chat_sessions
            SET session_version = ?, latest_message_id = ?,
                latest_inbound_message_id = CASE WHEN ? = 'inbound' THEN ? ELSE latest_inbound_message_id END,
                last_message_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (version, message_id, direction, message_id, sent_at, sent_at, session_id),
        )


def _append_event(
    db,
    *,
    job_id: str,
    session_id: str,
    event_type: str,
    ref_id: str,
    occurred_at: str = EVENT_TIME,
) -> str:
    event, _ = append_job_activity(
        db,
        job_id=job_id,
        chat_session_id=session_id,
        event_type=event_type,
        occurred_at=occurred_at,
        source="chat",
        source_ref_type="chat_message",
        source_ref_id=ref_id,
        dedupe_key=f"job-action:{job_id}:{event_type}:{ref_id}",
    )
    return str(event["id"])


def _attention(
    db,
    *,
    job_id: str,
    session_id: str,
    status: str,
    action: str,
    decision: str,
    recommended_at: str | None = None,
) -> None:
    with db.connect() as connection:
        connection.execute(
            """
            INSERT INTO fj_chat_attention_states (
              session_id, job_id, attention_status, display_label,
              recommended_action, reason, decision, reason_code,
              recommended_at, priority, source, created_at, updated_at
            ) VALUES (?, ?, ?, '测试', ?, '测试行动原因', ?, 'test_reason',
                      ?, 50, 'analysis', ?, ?)
            """,
            (
                session_id,
                job_id,
                status,
                action,
                decision,
                recommended_at,
                EVENT_TIME,
                EVENT_TIME,
            ),
        )


def _reply_task(
    db,
    *,
    session_id: str,
    message_id: str,
    task_id: str,
    status: str,
    action_kind: str = "reply",
    version: int = 1,
    job_action_key: str | None = None,
) -> None:
    with db.connect() as connection:
        connection.execute(
            """
            INSERT INTO fj_chat_reply_tasks (
              id, session_id, trigger_source, job_action_key, action_kind, status,
              based_on_message_id, based_on_session_version,
              draft_text, final_text, generated_at, confirmed_at, created_at, updated_at
            ) VALUES (?, ?, 'manual', ?, ?, ?, ?, ?, '草稿', '草稿', ?, ?, ?, ?)
            """,
            (
                task_id,
                session_id,
                job_action_key,
                action_kind,
                status,
                message_id,
                version,
                EVENT_TIME,
                NOW_TEXT if status == "confirmed" else None,
                EVENT_TIME,
                EVENT_TIME,
            ),
        )


def _send_action(db, *, task_id: str, session_id: str, status: str = "queued") -> None:
    with db.connect() as connection:
        connection.execute(
            """
            INSERT INTO fj_chat_send_actions (
              id, reply_task_id, session_id, status, text,
              canonical_status, canonical_updated_at, canonical_reason,
              created_at, updated_at
            ) VALUES (?, ?, ?, ?, '草稿', 'pending', ?, '等待执行', ?, ?)
            """,
            (f"send-{task_id}", task_id, session_id, status, EVENT_TIME, EVENT_TIME, EVENT_TIME),
        )


def _candidate_job(db, suffix: str, event_type: str = "recruiter_replied") -> tuple[str, str, str]:
    job_id = _create_job(db, suffix)
    session_id = f"session-{suffix}"
    message_id = f"message-{suffix}"
    _create_session(
        db,
        job_id=job_id,
        session_id=session_id,
        message_id=message_id,
        direction="inbound",
    )
    _append_event(
        db,
        job_id=job_id,
        session_id=session_id,
        event_type=event_type,
        ref_id=message_id,
    )
    return job_id, session_id, message_id


def test_candidate_action_types_and_terminal_rules(test_db) -> None:
    interview_job, _, _ = _candidate_job(test_db, "interview", "interview_invited")
    resume_job, resume_session, resume_message = _candidate_job(test_db, "resume", "resume_requested")
    reply_job, _, _ = _candidate_job(test_db, "reply")
    rejected_job, rejected_session, rejected_message = _candidate_job(test_db, "rejected")
    _append_event(
        test_db,
        job_id=rejected_job,
        session_id=rejected_session,
        event_type="rejected",
        ref_id=rejected_message,
        occurred_at="2026-09-05T12:00:00Z",
    )
    _attention(
        test_db,
        job_id=rejected_job,
        session_id=rejected_session,
        status="needs_rejection_reason",
        action="ask_rejection_reason",
        decision="follow",
    )
    closed_job, closed_session, closed_message = _candidate_job(test_db, "closed")
    _append_event(
        test_db,
        job_id=closed_job,
        session_id=closed_session,
        event_type="job_closed",
        ref_id=closed_message,
        occurred_at="2026-09-05T12:00:00Z",
    )
    _reply_task(
        test_db,
        session_id=closed_session,
        message_id=closed_message,
        task_id="task-closed-old-draft",
        status="awaiting_review",
    )
    offer_job, offer_session, offer_message = _candidate_job(test_db, "offer")
    _append_event(
        test_db,
        job_id=offer_job,
        session_id=offer_session,
        event_type="offer_received",
        ref_id=offer_message,
        occurred_at="2026-09-05T12:00:00Z",
    )
    _reply_task(
        test_db,
        session_id=offer_session,
        message_id=offer_message,
        task_id="task-offer-old-draft",
        status="awaiting_review",
    )

    items = list_job_actions(test_db, now=NOW)["items"]
    by_job = {item["job_id"]: item for item in items}

    assert by_job[interview_job]["action_type"] == "respond_interview"
    assert by_job[interview_job]["priority_tier"] == "urgent"
    assert by_job[resume_job]["action_type"] == "send_resume"
    assert by_job[reply_job]["action_type"] == "reply_recruiter"
    assert by_job[rejected_job]["action_type"] == "ask_rejection_reason"
    assert closed_job not in by_job
    assert offer_job not in by_job

    _append_event(
        test_db,
        job_id=resume_job,
        session_id=resume_session,
        event_type="resume_submitted",
        ref_id=f"submitted-{resume_message}",
        occurred_at="2026-09-05T13:00:00Z",
    )
    assert resume_job not in {
        item["job_id"] for item in list_job_actions(test_db, now=NOW)["items"]
    }


def test_followup_and_recruiter_reply_invalidation(test_db) -> None:
    job_id = _create_job(test_db, "followup")
    session_id = "session-followup"
    message_id = "message-followup-outbound"
    _create_session(
        test_db,
        job_id=job_id,
        session_id=session_id,
        message_id=message_id,
        direction="outbound",
    )
    _append_event(
        test_db,
        job_id=job_id,
        session_id=session_id,
        event_type="candidate_replied",
        ref_id=message_id,
    )
    _attention(
        test_db,
        job_id=job_id,
        session_id=session_id,
        status="needs_followup",
        action="follow_up",
        decision="follow",
        recommended_at="2026-09-05T12:00:00Z",
    )

    first = list_job_actions(test_db, now=NOW)["items"]
    assert first[0]["action_type"] == "followup_recruiter"
    assert first[0]["priority_tier"] == "high"

    inbound_id = "message-followup-inbound"
    _update_session_message(
        test_db,
        session_id=session_id,
        message_id=inbound_id,
        direction="inbound",
        sent_at="2026-09-09T12:00:00Z",
        version=2,
    )
    _append_event(
        test_db,
        job_id=job_id,
        session_id=session_id,
        event_type="recruiter_replied",
        ref_id=inbound_id,
        occurred_at="2026-09-09T12:00:00Z",
    )
    refreshed = list_job_actions(test_db, now=NOW)["items"]
    assert all(item["action_type"] != "followup_recruiter" for item in refreshed)
    assert refreshed[0]["action_type"] == "reply_recruiter"


@pytest.mark.parametrize("task_status", ["pending_generation", "generating"])
def test_generation_in_progress_suppresses_same_trigger(test_db, task_status: str) -> None:
    _, session_id, message_id = _candidate_job(test_db, f"suppress-{task_status}")
    _reply_task(
        test_db,
        session_id=session_id,
        message_id=message_id,
        task_id=f"task-{task_status}",
        status=task_status,
    )

    assert list_job_actions(test_db, now=NOW)["items"] == []


def test_followup_generation_in_progress_suppresses_same_trigger(test_db) -> None:
    job_id = _create_job(test_db, "suppress-followup")
    session_id = "session-suppress-followup"
    message_id = "message-suppress-followup"
    _create_session(
        test_db,
        job_id=job_id,
        session_id=session_id,
        message_id=message_id,
        direction="outbound",
    )
    _append_event(
        test_db,
        job_id=job_id,
        session_id=session_id,
        event_type="candidate_replied",
        ref_id=message_id,
    )
    _attention(
        test_db,
        job_id=job_id,
        session_id=session_id,
        status="needs_followup",
        action="follow_up",
        decision="follow",
        recommended_at="2026-09-09T10:00:00Z",
    )
    _reply_task(
        test_db,
        session_id=session_id,
        message_id=message_id,
        task_id="task-suppress-followup",
        status="generating",
        action_kind="followup",
    )

    assert list_job_actions(test_db, now=NOW)["items"] == []


def test_awaiting_review_inherits_business_priority(test_db) -> None:
    _, interview_session, interview_message = _candidate_job(
        test_db, "review-interview", "interview_invited"
    )
    _reply_task(
        test_db,
        session_id=interview_session,
        message_id=interview_message,
        task_id="task-review-interview",
        status="awaiting_review",
    )

    follow_job = _create_job(test_db, "review-followup")
    follow_session = "session-review-followup"
    follow_message = "message-review-followup"
    _create_session(
        test_db,
        job_id=follow_job,
        session_id=follow_session,
        message_id=follow_message,
        direction="outbound",
    )
    _append_event(
        test_db,
        job_id=follow_job,
        session_id=follow_session,
        event_type="candidate_replied",
        ref_id=follow_message,
    )
    _attention(
        test_db,
        job_id=follow_job,
        session_id=follow_session,
        status="needs_followup",
        action="follow_up",
        decision="follow",
        recommended_at="2026-09-09T10:00:00Z",
    )
    _reply_task(
        test_db,
        session_id=follow_session,
        message_id=follow_message,
        task_id="task-review-followup",
        status="awaiting_review",
        action_kind="followup",
    )

    generic_job = _create_job(test_db, "review-generic")
    generic_session = "session-review-generic"
    generic_message = "message-review-generic"
    _create_session(
        test_db,
        job_id=generic_job,
        session_id=generic_session,
        message_id=generic_message,
        direction="inbound",
        sent_at="2026-09-10T09:00:00Z",
    )
    _append_event(
        test_db,
        job_id=generic_job,
        session_id=generic_session,
        event_type="recruiter_replied",
        ref_id=generic_message,
        occurred_at="2026-09-10T09:00:00Z",
    )
    _reply_task(
        test_db,
        session_id=generic_session,
        message_id=generic_message,
        task_id="task-review-generic",
        status="awaiting_review",
    )

    items = list_job_actions(test_db, now=NOW)["items"]
    by_task = {item["reply_task"]["id"]: item for item in items}
    assert by_task["task-review-interview"]["action_type"] == "review_draft"
    assert by_task["task-review-interview"]["priority_tier"] == "urgent"
    assert by_task["task-review-followup"]["priority_tier"] == "normal"
    assert by_task["task-review-followup"]["due_at"] == "2026-09-09T10:00:00Z"
    assert by_task["task-review-generic"]["priority_tier"] == "high"


def test_confirmed_send_window_suppresses_reply_until_activity_arrives(test_db) -> None:
    _, session_id, message_id = _candidate_job(test_db, "confirmed-window")
    _reply_task(
        test_db,
        session_id=session_id,
        message_id=message_id,
        task_id="task-confirmed-window",
        status="confirmed",
    )
    _send_action(test_db, task_id="task-confirmed-window", session_id=session_id)

    assert list_job_actions(test_db, now=NOW)["items"] == []


def test_rejected_suppresses_old_reply_draft_and_allows_reason_draft(test_db) -> None:
    job_id, session_id, message_id = _candidate_job(test_db, "rejected-draft")
    _reply_task(
        test_db,
        session_id=session_id,
        message_id=message_id,
        task_id="task-old-reply",
        status="awaiting_review",
    )
    _append_event(
        test_db,
        job_id=job_id,
        session_id=session_id,
        event_type="rejected",
        ref_id=message_id,
        occurred_at="2026-09-05T12:00:00Z",
    )
    _attention(
        test_db,
        job_id=job_id,
        session_id=session_id,
        status="needs_rejection_reason",
        action="ask_rejection_reason",
        decision="follow",
    )

    items = list_job_actions(test_db, now=NOW)["items"]
    assert len(items) == 1
    assert items[0]["action_type"] == "ask_rejection_reason"

    with test_db.connect() as connection:
        connection.execute(
            "UPDATE fj_chat_reply_tasks SET status = 'stale' WHERE id = 'task-old-reply'"
        )
    _reply_task(
        test_db,
        session_id=session_id,
        message_id=message_id,
        task_id="task-rejection-reason",
        status="awaiting_review",
        action_kind="ask_rejection_reason",
    )
    reviewed = list_job_actions(test_db, now=NOW)["items"]
    assert reviewed[0]["action_type"] == "review_draft"
    assert reviewed[0]["priority_tier"] == "low"
    assert reviewed[0]["reply_task"]["id"] == "task-rejection-reason"


def test_latest_session_key_stability_new_trigger_and_candidate_reply(test_db) -> None:
    job_id = _create_job(test_db, "latest")
    _create_session(
        test_db,
        job_id=job_id,
        session_id="session-old",
        message_id="message-old",
        direction="inbound",
        sent_at="2026-09-05T11:00:00Z",
    )
    _create_session(
        test_db,
        job_id=job_id,
        session_id="session-new",
        message_id="message-new",
        direction="inbound",
        sent_at="2026-09-05T12:00:00Z",
    )
    _append_event(
        test_db,
        job_id=job_id,
        session_id="session-new",
        event_type="recruiter_replied",
        ref_id="message-new",
        occurred_at="2026-09-05T12:00:00Z",
    )

    first = list_job_actions(test_db, now=NOW)["items"]
    second = list_job_actions(test_db, now=NOW)["items"]
    assert len(first) == 1
    assert first[0]["session_id"] == "session-new"
    assert first[0]["action_key"] == second[0]["action_key"]

    _update_session_message(
        test_db,
        session_id="session-new",
        message_id="message-newer",
        direction="inbound",
        sent_at="2026-09-05T13:00:00Z",
        version=2,
    )
    _append_event(
        test_db,
        job_id=job_id,
        session_id="session-new",
        event_type="recruiter_replied",
        ref_id="message-newer",
        occurred_at="2026-09-05T13:00:00Z",
    )
    newer = list_job_actions(test_db, now=NOW)["items"]
    assert newer[0]["action_key"] != first[0]["action_key"]

    _update_session_message(
        test_db,
        session_id="session-new",
        message_id="message-candidate",
        direction="outbound",
        sent_at="2026-09-05T14:00:00Z",
        version=3,
    )
    _append_event(
        test_db,
        job_id=job_id,
        session_id="session-new",
        event_type="candidate_replied",
        ref_id="message-candidate",
        occurred_at="2026-09-05T14:00:00Z",
    )
    assert list_job_actions(test_db, now=NOW)["items"] == []


def test_sorting_is_stable_and_listing_never_creates_send_action(test_db) -> None:
    _candidate_job(test_db, "sort-b")
    _candidate_job(test_db, "sort-a")
    first = list_job_actions(test_db, now=NOW)["items"]
    second = list_job_actions(test_db, now=NOW)["items"]

    assert [item["action_key"] for item in first] == [
        item["action_key"] for item in second
    ]
    with test_db.connect() as connection:
        send_count = connection.execute(
            "SELECT COUNT(*) FROM fj_chat_send_actions"
        ).fetchone()[0]
    assert send_count == 0


def test_followup_consumes_only_current_trigger_and_allows_new_trigger(test_db) -> None:
    job_id = _create_job(test_db, "followup-consumed")
    session_id = "session-followup-consumed"
    message_id = "message-followup-consumed"
    _create_session(
        test_db,
        job_id=job_id,
        session_id=session_id,
        message_id=message_id,
        direction="outbound",
    )
    _append_event(
        test_db,
        job_id=job_id,
        session_id=session_id,
        event_type="candidate_replied",
        ref_id=message_id,
    )
    _attention(
        test_db,
        job_id=job_id,
        session_id=session_id,
        status="needs_followup",
        action="follow_up",
        decision="follow",
        recommended_at="2026-09-06T10:00:00Z",
    )
    first = list_job_actions(test_db, now=NOW)["items"][0]
    _reply_task(
        test_db,
        session_id=session_id,
        message_id=message_id,
        task_id="task-followup-consumed",
        status="confirmed",
        action_kind="followup",
        job_action_key=first["action_key"],
    )
    _send_action(test_db, task_id="task-followup-consumed", session_id=session_id)

    # 用户确认发送后，即使平台回显尚未同步，同一跟进触发也不再出现。
    assert list_job_actions(test_db, now=NOW)["items"] == []
    echoed_id = "message-followup-echoed"
    _update_session_message(
        test_db,
        session_id=session_id,
        message_id=echoed_id,
        direction="outbound",
        sent_at="2026-09-06T11:00:00Z",
        version=2,
    )
    _append_event(
        test_db,
        job_id=job_id,
        session_id=session_id,
        event_type="candidate_replied",
        ref_id=echoed_id,
        occurred_at="2026-09-06T11:00:00Z",
    )
    assert list_job_actions(test_db, now=NOW)["items"] == []

    inbound_id = "message-followup-new-inbound"
    _update_session_message(
        test_db,
        session_id=session_id,
        message_id=inbound_id,
        direction="inbound",
        sent_at="2026-09-07T10:00:00Z",
        version=3,
    )
    _append_event(
        test_db,
        job_id=job_id,
        session_id=session_id,
        event_type="recruiter_replied",
        ref_id=inbound_id,
        occurred_at="2026-09-07T10:00:00Z",
    )
    assert list_job_actions(test_db, now=NOW)["items"][0]["action_type"] == "reply_recruiter"

    outbound_id = "message-followup-new-outbound"
    _update_session_message(
        test_db,
        session_id=session_id,
        message_id=outbound_id,
        direction="outbound",
        sent_at="2026-09-07T11:00:00Z",
        version=4,
    )
    _append_event(
        test_db,
        job_id=job_id,
        session_id=session_id,
        event_type="candidate_replied",
        ref_id=outbound_id,
        occurred_at="2026-09-07T11:00:00Z",
    )
    with test_db.connect() as connection:
        connection.execute(
            """
            UPDATE fj_chat_attention_states
            SET recommended_at = ?, updated_at = ?
            WHERE session_id = ?
            """,
            ("2026-09-08T10:00:00Z", "2026-09-08T10:00:00Z", session_id),
        )
    next_action = list_job_actions(test_db, now=NOW)["items"][0]
    assert next_action["action_type"] == "followup_recruiter"
    assert next_action["action_key"] != first["action_key"]


def test_rejection_reason_consumes_event_and_reopen_allows_new_rejection(test_db) -> None:
    job_id, session_id, message_id = _candidate_job(test_db, "rejection-consumed")
    _append_event(
        test_db,
        job_id=job_id,
        session_id=session_id,
        event_type="rejected",
        ref_id="rejected-event-one",
        occurred_at="2026-09-06T10:00:00Z",
    )
    _attention(
        test_db,
        job_id=job_id,
        session_id=session_id,
        status="needs_rejection_reason",
        action="ask_rejection_reason",
        decision="follow",
    )
    first = list_job_actions(test_db, now=NOW)["items"][0]
    _reply_task(
        test_db,
        session_id=session_id,
        message_id=message_id,
        task_id="task-rejection-consumed",
        status="confirmed",
        action_kind="ask_rejection_reason",
        job_action_key=first["action_key"],
    )
    _send_action(test_db, task_id="task-rejection-consumed", session_id=session_id)
    assert list_job_actions(test_db, now=NOW)["items"] == []

    append_job_activity(
        test_db,
        job_id=job_id,
        chat_session_id=session_id,
        event_type="manual_stage_changed",
        occurred_at="2026-09-07T10:00:00Z",
        source="manual",
        source_ref_type="user_operation",
        source_ref_id="reopen-rejection",
        evidence_level="direct",
        payload={"stage": "communicating", "waiting_on": "recruiter", "allow_reopen": True},
        dedupe_key="job-action:rejection-consumed:reopen",
    )
    _append_event(
        test_db,
        job_id=job_id,
        session_id=session_id,
        event_type="rejected",
        ref_id="rejected-event-two",
        occurred_at="2026-09-08T10:00:00Z",
    )
    reopened = list_job_actions(test_db, now=NOW)["items"][0]
    assert reopened["action_type"] == "ask_rejection_reason"
    assert reopened["action_key"] != first["action_key"]
