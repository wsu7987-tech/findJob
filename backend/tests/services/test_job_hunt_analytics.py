from __future__ import annotations

from backend.app.services.fine_job.boss_capture_history import (
    create_capture_batch,
    record_capture_jobs,
)
from backend.app.services.fine_job import boss_chat
from backend.app.services.fine_job.job_activity import (
    append_job_activity,
    replay_job_pipeline,
)
from backend.app.services.fine_job.job_hunt_analytics import (
    OVERVIEW_METRICS,
    get_job_hunt_analytics,
    get_job_hunt_analytics_jobs,
)


BASE_TIME = "2026-09-01T00:00:00Z"


def _create_job(db, source_job_id: str) -> str:
    capture_id = f"capture-{source_job_id}"
    create_capture_batch(
        db,
        capture_id=capture_id,
        keyword="Python",
        city="广州",
        pages=1,
        auto_details=False,
        created_at=BASE_TIME,
    )
    record_capture_jobs(
        db,
        capture_id=capture_id,
        jobs=[{
            "job_id": source_job_id,
            "encrypt_job_id": f"encrypt-{source_job_id}",
            "title": f"岗位 {source_job_id}",
            "boss_name": "示例公司",
            "job_link": f"https://www.zhipin.com/job_detail/{source_job_id}.html",
        }],
        collected_at=BASE_TIME,
    )
    with db.connect() as connection:
        row = connection.execute(
            "SELECT id FROM fj_boss_jobs WHERE source_job_id = ?", (source_job_id,)
        ).fetchone()
    assert row is not None
    return str(row["id"])


def _append(
    db,
    job_id: str,
    event_type: str,
    occurred_at: str,
    *,
    suffix: str,
    payload: dict | None = None,
    source: str = "test",
    source_ref_type: str = "test",
    chat_session_id: str | None = None,
    created_at: str | None = None,
) -> str:
    event, _created = append_job_activity(
        db,
        job_id=job_id,
        chat_session_id=chat_session_id,
        event_type=event_type,
        occurred_at=occurred_at,
        source=source,
        source_ref_type=source_ref_type,
        source_ref_id=f"ref-{suffix}",
        payload=payload,
        dedupe_key=f"analytics-{job_id}-{suffix}",
    )
    if created_at is not None:
        with db.connect() as connection:
            connection.execute(
                "UPDATE fj_job_activity_events SET created_at = ? WHERE id = ?",
                (created_at, event["id"]),
            )
        replay_job_pipeline(db, job_id)
    return str(event["id"])


def _candidate_contact(db, job_id: str, occurred_at: str, *, suffix: str = "contact") -> str:
    return _append(
        db,
        job_id,
        "candidate_initiated_contact",
        occurred_at,
        suffix=suffix,
        payload={"contact_origin": "candidate_initiated"},
    )


def _analytics(db, from_date: str = "2026-09-01", to_date: str = "2026-09-07"):
    return get_job_hunt_analytics(
        db,
        from_value=from_date,
        to_value=to_date,
        timezone_name="Asia/Shanghai",
        granularity="day",
    )


def _insert_session(
    db,
    job_id: str,
    session_id: str,
    *,
    last_message_at: str,
    created_at: str,
) -> None:
    with db.connect() as connection:
        connection.execute(
            """
            INSERT INTO fj_chat_sessions (
              id, account_uid, peer_uid, job_id, last_message_at,
              status, created_at, updated_at
            ) VALUES (?, 'candidate', ?, ?, ?, 'active', ?, ?)
            """,
            (
                session_id,
                f"peer-{session_id}",
                job_id,
                last_message_at,
                created_at,
                last_message_at,
            ),
        )


def _insert_attention(db, job_id: str, session_id: str, status: str, updated_at: str) -> None:
    action = "follow_up" if status == "needs_followup" else "no_further_action"
    decision = "follow" if status == "needs_followup" else "wait"
    with db.connect() as connection:
        connection.execute(
            """
            INSERT INTO fj_chat_attention_states (
              session_id, job_id, attention_status, recommended_action,
              decision, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (session_id, job_id, status, action, decision, updated_at, updated_at),
        )


def test_distinct_events_resume_semantics_and_zero_denominator(test_db) -> None:
    job_id = _create_job(test_db, "distinct-events")
    _candidate_contact(test_db, job_id, "2026-09-01T01:00:00Z")
    for index in range(3):
        _append(
            test_db,
            job_id,
            "recruiter_replied",
            f"2026-09-01T02:00:0{index}Z",
            suffix=f"reply-{index}",
        )
    _append(
        test_db,
        job_id,
        "resume_submitted",
        "2026-09-01T03:00:00Z",
        suffix="resume-submitted",
    )
    _append(
        test_db,
        job_id,
        "resume_accepted",
        "2026-09-01T03:01:00Z",
        suffix="resume-accepted",
    )

    result = _analytics(test_db)

    assert result["overview"]["candidate_contacts"] == 1
    assert result["overview"]["candidate_contact_replies"] == 1
    assert result["overview"]["candidate_reply_rate"] == 1
    assert result["overview"]["resume_submitted"] == 1

    empty_result = _analytics(test_db, "2026-08-01", "2026-08-02")
    assert empty_result["overview"]["candidate_reply_rate"] is None
    assert empty_result["funnel"]["stages"][0]["total_rate"] is None


def test_asia_shanghai_natural_day_boundaries(test_db) -> None:
    times = (
        ("before", "2026-09-01T15:59:59Z"),
        ("start", "2026-09-01T16:00:00Z"),
        ("end-inside", "2026-09-02T15:59:59Z"),
        ("after", "2026-09-02T16:00:00Z"),
    )
    for suffix, occurred_at in times:
        job_id = _create_job(test_db, f"boundary-{suffix}")
        _append(
            test_db,
            job_id,
            "resume_submitted",
            occurred_at,
            suffix=suffix,
        )

    result = _analytics(test_db, "2026-09-02", "2026-09-02")

    assert result["overview"]["resume_submitted"] == 2
    assert result["trend"] == [{
        "period_start": "2026-09-02",
        "candidate_contacts": 0,
        "resume_submitted": 2,
        "interview_scheduled": 0,
        "rejected": 0,
    }]


def test_strict_cohort_funnel_and_cutoff(test_db) -> None:
    job_id = _create_job(test_db, "funnel")
    _candidate_contact(test_db, job_id, "2026-09-01T01:00:00Z")
    _append(test_db, job_id, "recruiter_replied", "2026-09-01T02:00:00Z", suffix="reply")
    _append(test_db, job_id, "resume_submitted", "2026-09-02T02:00:00Z", suffix="resume")
    _append(test_db, job_id, "resume_viewed", "2026-09-03T02:00:00Z", suffix="view")
    _append(
        test_db,
        job_id,
        "interview_scheduled",
        "2026-09-10T02:00:00Z",
        suffix="interview",
    )

    recruiter_job = _create_job(test_db, "recruiter-origin")
    _append(
        test_db,
        recruiter_job,
        "recruiter_initiated_contact",
        "2026-09-01T01:00:00Z",
        suffix="recruiter-contact",
        payload={"contact_origin": "recruiter_initiated"},
    )
    _append(
        test_db,
        recruiter_job,
        "recruiter_replied",
        "2026-09-01T01:01:00Z",
        suffix="recruiter-message",
    )

    result = _analytics(test_db)

    assert [stage["count"] for stage in result["funnel"]["stages"]] == [1, 1, 1, 1, 0, 0]
    assert result["overview"]["candidate_contact_replies"] == 1
    recruiter_source = next(
        row
        for row in result["source_performance"]
        if row["contact_origin"] == "recruiter_initiated"
    )
    assert recruiter_source["job_count"] == 1
    assert recruiter_source["candidate_reply_rate"] is None


def test_recruiter_contact_anchor_is_unique_across_sessions(test_db) -> None:
    job_id = _create_job(test_db, "multi-session-recruiter")
    _insert_session(
        test_db,
        job_id,
        "recruiter-session-old",
        last_message_at="2026-08-20T02:00:00Z",
        created_at="2026-08-20T01:00:00Z",
    )
    _insert_session(
        test_db,
        job_id,
        "recruiter-session-new",
        last_message_at="2026-09-02T02:00:00Z",
        created_at="2026-09-02T01:00:00Z",
    )
    for session_id, occurred_at, suffix in (
        ("recruiter-session-old", "2026-08-20T01:00:00Z", "old"),
        ("recruiter-session-new", "2026-09-02T01:00:00Z", "new"),
    ):
        _append(
            test_db,
            job_id,
            "recruiter_initiated_contact",
            occurred_at,
            suffix=f"contact-{suffix}",
            payload={"contact_origin": "recruiter_initiated"},
            chat_session_id=session_id,
        )

    august = _analytics(test_db, "2026-08-20", "2026-08-20")
    september = _analytics(test_db, "2026-09-02", "2026-09-02")

    assert august["overview"]["recruiter_contacts"] == 1
    assert september["overview"]["recruiter_contacts"] == 0


def test_current_followup_uses_latest_job_session_and_ignores_date_range(test_db) -> None:
    job_id = _create_job(test_db, "latest-session")
    _candidate_contact(test_db, job_id, "2026-06-01T01:00:00Z")
    _insert_session(
        test_db,
        job_id,
        "session-old",
        last_message_at="2026-08-01T00:00:00Z",
        created_at="2026-08-01T00:00:00Z",
    )
    _insert_session(
        test_db,
        job_id,
        "session-new",
        last_message_at="2026-09-01T00:00:00Z",
        created_at="2026-09-01T00:00:00Z",
    )
    _insert_attention(test_db, job_id, "session-old", "needs_followup", "2026-08-01T01:00:00Z")
    _insert_attention(test_db, job_id, "session-new", "no_action", "2026-09-01T01:00:00Z")

    result = _analytics(test_db, "2026-09-05", "2026-09-05")

    assert result["current_state"]["followup_recommended"] == 0
    assert result["current_state"]["waiting_recruiter"] == 1


def test_canonical_rejection_episodes_and_reason_sources(test_db) -> None:
    job_id = _create_job(test_db, "rejection-episodes")
    _candidate_contact(test_db, job_id, "2026-09-01T00:10:00Z")
    _append(
        test_db,
        job_id,
        "rejected",
        "2026-09-01T01:00:00Z",
        suffix="rejected-first",
        payload={
            "rejection_reason_source": "recruiter_explicit",
            "rejection_reason_category": "experience",
        },
    )
    _append(
        test_db,
        job_id,
        "rejected",
        "2026-09-01T02:00:00Z",
        suffix="rejected-duplicate",
        payload={
            "rejection_reason_source": "ai_inferred",
            "rejection_reason_category": "skills",
        },
    )
    _append(
        test_db,
        job_id,
        "manual_stage_changed",
        "2026-09-01T03:00:00Z",
        suffix="reopen",
        payload={"stage": "communicating", "allow_reopen": True},
    )
    _append(
        test_db,
        job_id,
        "rejected",
        "2026-09-01T04:00:00Z",
        suffix="rejected-second-episode",
        payload={
            "rejection_analysis": {
                "reason_source": "ai_inferred",
                "reason_type": "salary",
                "reason_text": "薪资不匹配",
            }
        },
    )

    closed_job = _create_job(test_db, "closed-separate")
    _append(
        test_db,
        closed_job,
        "job_closed",
        "2026-09-01T04:00:00Z",
        suffix="closed",
        payload={
            "rejection_reason_source": "recruiter_explicit",
            "rejection_reason_category": "headcount_closed",
        },
    )

    result = _analytics(test_db)

    assert result["overview"]["rejected"] == 1
    assert result["overview"]["job_closed"] == 1
    assert result["rejection_analysis"]["recruiter_explicit"] == []
    assert result["rejection_analysis"]["ai_inferred"] == [
        {"category": "salary", "job_count": 1}
    ]


def test_same_timestamp_contact_and_reply_use_stable_event_order(test_db) -> None:
    job_id = _create_job(test_db, "same-second")
    _append(
        test_db,
        job_id,
        "candidate_initiated_contact",
        "2026-09-01T01:00:00Z",
        suffix="same-contact",
        payload={"contact_origin": "candidate_initiated"},
        created_at="2026-09-01T01:00:01Z",
    )
    _append(
        test_db,
        job_id,
        "recruiter_replied",
        "2026-09-01T01:00:00Z",
        suffix="same-reply",
        created_at="2026-09-01T01:00:02Z",
    )

    result = _analytics(test_db)

    assert result["overview"]["candidate_contact_replies"] == 1
    assert result["funnel"]["stages"][1]["count"] == 1


def test_unknown_real_chat_anchor_and_legacy_rejection(test_db) -> None:
    job_id = _create_job(test_db, "unknown-chat")
    _append(
        test_db,
        job_id,
        "candidate_replied",
        "2026-09-01T01:00:00Z",
        suffix="unknown-real-message",
        source="chat",
        source_ref_type="chat_message",
    )
    _append(
        test_db,
        job_id,
        "rejected",
        "2026-09-01T02:00:00Z",
        suffix="legacy-rejected",
        source="migration",
        payload={},
    )

    result = _analytics(test_db)
    unknown_source = next(
        row
        for row in result["source_performance"]
        if row["contact_origin"] == "unknown"
    )

    assert unknown_source["job_count"] == 1
    assert unknown_source["candidate_reply_rate"] is None
    assert unknown_source["rejection_rate"] == 1
    assert result["rejection_analysis"]["unknown"] == [
        {"category": "unknown", "job_count": 1}
    ]


def test_contact_origin_filter_applies_to_source_performance(test_db) -> None:
    candidate_job = _create_job(test_db, "filtered-candidate")
    _candidate_contact(test_db, candidate_job, "2026-09-01T01:00:00Z")
    recruiter_job = _create_job(test_db, "filtered-recruiter")
    _append(
        test_db,
        recruiter_job,
        "recruiter_initiated_contact",
        "2026-09-01T01:00:00Z",
        suffix="filtered-recruiter-contact",
        payload={"contact_origin": "recruiter_initiated"},
    )

    result = get_job_hunt_analytics(
        test_db,
        from_value="2026-09-01",
        to_value="2026-09-07",
        contact_origin="candidate_initiated",
    )
    rows = {row["contact_origin"]: row for row in result["source_performance"]}

    assert rows["candidate_initiated"]["job_count"] == 1
    assert rows["recruiter_initiated"]["job_count"] == 0


def test_drill_down_reuses_every_overview_distinct_job_set(test_db) -> None:
    candidate_job = _create_job(test_db, "drilldown-candidate")
    _candidate_contact(test_db, candidate_job, "2026-09-01T01:00:00Z")
    _append(
        test_db,
        candidate_job,
        "recruiter_replied",
        "2026-09-01T02:00:00Z",
        suffix="drilldown-reply-1",
    )
    _append(
        test_db,
        candidate_job,
        "recruiter_replied",
        "2026-09-01T02:01:00Z",
        suffix="drilldown-reply-2",
    )
    for index, event_type in enumerate((
        "resume_submitted",
        "resume_viewed",
        "under_review",
        "interview_scheduled",
        "offer_received",
    ), start=3):
        _append(
            test_db,
            candidate_job,
            event_type,
            f"2026-09-01T0{index}:00:00Z",
            suffix=f"drilldown-{event_type}",
        )

    recruiter_job = _create_job(test_db, "drilldown-recruiter")
    _append(
        test_db,
        recruiter_job,
        "recruiter_initiated_contact",
        "2026-09-01T01:00:00Z",
        suffix="drilldown-recruiter-contact",
        payload={"contact_origin": "recruiter_initiated"},
    )
    rejected_job = _create_job(test_db, "drilldown-rejected")
    _candidate_contact(test_db, rejected_job, "2026-09-01T01:00:00Z")
    _append(
        test_db,
        rejected_job,
        "rejected",
        "2026-09-01T03:00:00Z",
        suffix="drilldown-rejected",
        payload={
            "rejection_reason_source": "recruiter_explicit",
            "rejection_reason_category": "experience",
            "rejection_reason_summary": "工作经验不足",
        },
    )
    closed_job = _create_job(test_db, "drilldown-closed")
    _append(
        test_db,
        closed_job,
        "job_closed",
        "2026-09-01T03:00:00Z",
        suffix="drilldown-closed",
    )

    overview = _analytics(test_db)["overview"]
    for metric in OVERVIEW_METRICS:
        details = get_job_hunt_analytics_jobs(
            test_db,
            metric=metric,
            from_value="2026-09-01",
            to_value="2026-09-07",
        )
        assert details["total"] == overview[metric]
        assert len(details["jobs"]) == overview[metric]
        assert len({item["job_id"] for item in details["jobs"]}) == overview[metric]

    rejection_details = get_job_hunt_analytics_jobs(
        test_db,
        metric="rejected",
        from_value="2026-09-01",
        to_value="2026-09-07",
        rejection_reason_source="recruiter_explicit",
        rejection_reason_category="experience",
    )
    assert rejection_details["total"] == 1
    assert rejection_details["jobs"][0]["rejection_reason_summary"] == "工作经验不足"


def test_drill_down_respects_cutoff_origin_and_current_filters(test_db) -> None:
    waiting_job_id = _create_job(test_db, "drilldown-waiting")
    _candidate_contact(test_db, waiting_job_id, "2026-09-01T01:00:00Z")

    future_job_id = _create_job(test_db, "drilldown-future")
    _candidate_contact(test_db, future_job_id, "2026-09-01T01:00:00Z")
    _append(
        test_db,
        future_job_id,
        "interview_scheduled",
        "2026-09-10T01:00:00Z",
        suffix="drilldown-future-interview",
    )

    contact_details = get_job_hunt_analytics_jobs(
        test_db,
        metric="candidate_contacts",
        from_value="2026-09-01",
        to_value="2026-09-07",
        contact_origin="candidate_initiated",
        waiting_on="recruiter",
    )
    future_details = get_job_hunt_analytics_jobs(
        test_db,
        metric="interview_scheduled",
        from_value="2026-09-01",
        to_value="2026-09-07",
    )

    assert contact_details["total"] == 1
    assert future_details["total"] == 0


def test_auto_chat_waiting_filter_uses_current_pipeline(test_db) -> None:
    waiting_recruiter_job = _create_job(test_db, "chat-waiting-recruiter")
    _candidate_contact(test_db, waiting_recruiter_job, "2026-09-01T01:00:00Z")
    _insert_session(
        test_db,
        waiting_recruiter_job,
        "chat-session-waiting-recruiter",
        last_message_at="2026-09-01T01:00:00Z",
        created_at="2026-09-01T01:00:00Z",
    )

    waiting_candidate_job = _create_job(test_db, "chat-waiting-candidate")
    _append(
        test_db,
        waiting_candidate_job,
        "recruiter_initiated_contact",
        "2026-09-01T01:00:00Z",
        suffix="chat-recruiter-contact",
        payload={"contact_origin": "recruiter_initiated"},
    )
    _insert_session(
        test_db,
        waiting_candidate_job,
        "chat-session-waiting-candidate",
        last_message_at="2026-09-01T01:00:00Z",
        created_at="2026-09-01T01:00:00Z",
    )

    recruiter_rows = boss_chat.list_sessions(test_db, waiting_on="recruiter")
    candidate_rows = boss_chat.list_sessions(test_db, waiting_on="candidate")

    assert {row["job_id"] for row in recruiter_rows} == {waiting_recruiter_job}
    assert {row["job_id"] for row in candidate_rows} == {waiting_candidate_job}
