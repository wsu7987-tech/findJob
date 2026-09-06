from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, timezone
from typing import Any, Iterable

from backend.app.db import Database
from backend.app.errors import AppError
from backend.app.services.fine_job.job_activity import (
    PIPELINE_STAGES,
    normalize_rejection_fields,
)
from backend.app.utils import utc_now


ASIA_SHANGHAI = timezone(timedelta(hours=8), name="Asia/Shanghai")
CANDIDATE_CONTACT_ORIGINS = {
    "finejob_auto",
    "candidate_initiated",
    "external_candidate_initiated",
}
SOURCE_ORDER = (
    "finejob_auto",
    "candidate_initiated",
    "external_candidate_initiated",
    "recruiter_initiated",
    "unknown",
)
OVERVIEW_METRICS = (
    "candidate_contacts",
    "candidate_contact_replies",
    "recruiter_contacts",
    "resume_submitted",
    "resume_viewed",
    "under_review",
    "interview_scheduled",
    "rejected",
    "job_closed",
    "offer_received",
)
ACTIONABLE_ATTENTION_STATUSES = {
    "needs_reply",
    "needs_resume",
    "needs_followup",
    "needs_rejection_reason",
    "needs_interview_confirm",
    "needs_info",
}
ANALYTICS_EVENT_TYPES = {
    "candidate_initiated_contact",
    "recruiter_initiated_contact",
    "recruiter_replied",
    "candidate_replied",
    "resume_submitted",
    "resume_viewed",
    "under_review",
    "interview_scheduled",
    "rejected",
    "job_closed",
    "offer_received",
    "conversation_closed",
    "manual_stage_changed",
}
TERMINAL_EVENT_STAGES = {
    "rejected": "rejected",
    "job_closed": "closed",
    "conversation_closed": "closed",
    "offer_received": "offer",
}
TERMINAL_STAGES = {"offer", "rejected", "closed"}


@dataclass(frozen=True)
class AnalyticsRange:
    from_date: date
    to_date: date
    start_utc: datetime
    end_utc: datetime
    granularity: str
    contact_origin: str | None


@dataclass(frozen=True)
class ActivityRecord:
    id: str
    job_id: str
    event_type: str
    occurred_at: str
    created_at: str
    source_ref_type: str
    payload: dict[str, Any]

    @property
    def occurred_datetime(self) -> datetime:
        return _parse_activity_datetime(self.occurred_at)

    @property
    def created_datetime(self) -> datetime:
        return _parse_activity_datetime(self.created_at)


@dataclass(frozen=True)
class ContactAnchor:
    job_id: str
    contact_at: str
    contact_origin: str
    contact_event_id: str
    event: ActivityRecord


def get_job_hunt_analytics(
    db: Database,
    *,
    from_value: str,
    to_value: str,
    timezone_name: str = "Asia/Shanghai",
    granularity: str = "auto",
    contact_origin: str | None = None,
) -> dict[str, Any]:
    analytics_range = _resolve_range(
        from_value,
        to_value,
        timezone_name=timezone_name,
        granularity=granularity,
        contact_origin=contact_origin,
    )
    with db.connect() as connection:
        events = _load_analytics_events(connection)
        origin_by_job = _load_contact_origins(connection)
        anchors = build_contact_anchors(events, origin_by_job)
        effective_terminal_events = canonical_terminal_events(events)
        current_state = _aggregate_current_state(
            connection,
            contact_origin=analytics_range.contact_origin,
        )

    events_by_job_type = _index_events(events)
    terminal_by_job_type = _index_events(effective_terminal_events)
    candidate_cohort = _candidate_contact_cohort(anchors, analytics_range)
    overview_matches = _select_overview_matches(
        events,
        effective_terminal_events,
        anchors,
        candidate_cohort,
        analytics_range,
        origin_by_job,
        events_by_job_type,
    )
    overview = _aggregate_overview(overview_matches)
    return {
        "range": {
            "from_date": analytics_range.from_date.isoformat(),
            "to_date": analytics_range.to_date.isoformat(),
            "timezone": "Asia/Shanghai",
            "granularity": analytics_range.granularity,
            "contact_origin": analytics_range.contact_origin,
        },
        "overview": overview,
        "trend": _aggregate_trend(
            events,
            effective_terminal_events,
            anchors,
            analytics_range,
            origin_by_job,
        ),
        "funnel": _aggregate_funnel(
            candidate_cohort,
            analytics_range,
            events_by_job_type,
            terminal_by_job_type,
        ),
        "current_state": current_state,
        "rejection_analysis": _aggregate_rejections(
            overview_matches["rejected"],
        ),
        "source_performance": _aggregate_source_performance(
            anchors,
            analytics_range,
            events_by_job_type,
            terminal_by_job_type,
        ),
        "definitions": {
            "count_basis": "distinct_job",
            "funnel_basis": "candidate_contact_cohort",
            "historical_time_basis": "event.occurred_at",
            "contact_basis": "canonical_contact_anchor",
            "event_order_basis": "occurred_at_created_at_id",
            "current_state_is_snapshot": True,
            "current_state_ignores_date_range": True,
            "rate_scale": "fraction",
        },
        "generated_at": utc_now(),
    }


def get_job_hunt_analytics_jobs(
    db: Database,
    *,
    metric: str,
    from_value: str,
    to_value: str,
    timezone_name: str = "Asia/Shanghai",
    contact_origin: str | None = None,
    rejection_reason_source: str | None = None,
    rejection_reason_category: str | None = None,
    waiting_on: str | None = None,
    attention: str | None = None,
) -> dict[str, Any]:
    """返回与 Overview 完全共用岗位集合的轻量统计明细。"""
    if metric not in OVERVIEW_METRICS:
        raise AppError(422, "ANALYTICS_METRIC_INVALID", "统计指标无效。")
    if rejection_reason_source is not None and rejection_reason_source not in {
        "recruiter_explicit", "ai_inferred", "unknown"
    }:
        raise AppError(422, "ANALYTICS_REJECTION_SOURCE_INVALID", "拒绝原因来源无效。")
    if waiting_on is not None and waiting_on not in {
        "candidate", "recruiter", "none", "unknown"
    }:
        raise AppError(422, "ANALYTICS_WAITING_ON_INVALID", "等待对象无效。")

    analytics_range = _resolve_range(
        from_value,
        to_value,
        timezone_name=timezone_name,
        granularity="auto",
        contact_origin=contact_origin,
    )
    with db.connect() as connection:
        events = _load_analytics_events(connection)
        origin_by_job = _load_contact_origins(connection)
        anchors = build_contact_anchors(events, origin_by_job)
        terminal_events = canonical_terminal_events(events)
        events_by_job_type = _index_events(events)
        candidate_cohort = _candidate_contact_cohort(anchors, analytics_range)
        matches = _select_overview_matches(
            events,
            terminal_events,
            anchors,
            candidate_cohort,
            analytics_range,
            origin_by_job,
            events_by_job_type,
        )[metric]

        # 拒绝原因明细直接读取主统计选中的有效拒绝事件，保证分组数字可以逐项核对。
        if rejection_reason_source is not None or rejection_reason_category is not None:
            if metric != "rejected":
                raise AppError(
                    422,
                    "ANALYTICS_REJECTION_FILTER_NOT_APPLICABLE",
                    "拒绝原因筛选仅适用于被拒绝岗位。",
                )
            matches = {
                job_id: event
                for job_id, event in matches.items()
                if _rejection_matches_filter(
                    event,
                    rejection_reason_source=rejection_reason_source,
                    rejection_reason_category=rejection_reason_category,
                )
            }

        state_by_job = _load_job_current_states(connection)
        matches = {
            job_id: event
            for job_id, event in matches.items()
            if _current_state_matches_filter(
                state_by_job.get(job_id),
                waiting_on=waiting_on,
                attention=attention,
            )
        }
        jobs_by_id = _load_job_summaries(connection, set(matches))

    items: list[dict[str, Any]] = []
    for job_id, event in matches.items():
        job = jobs_by_id.get(job_id)
        if job is None:
            continue
        reason_source: str | None = None
        reason_category: str | None = None
        reason_summary: str | None = None
        if metric == "rejected":
            reason_source, reason_category, reason_summary = normalize_rejection_fields(
                event.payload
            )
        items.append({
            "job_id": job_id,
            "title": str(job.get("title") or ""),
            "company_name": str(job.get("company_name") or ""),
            "progress": str(job.get("stage") or ""),
            "matched_at": event.occurred_at,
            "metric": metric,
            "rejection_reason_source": reason_source,
            "rejection_reason_category": reason_category,
            "rejection_reason_summary": reason_summary,
        })
    items.sort(
        key=lambda item: (_parse_activity_datetime(str(item["matched_at"])), item["job_id"]),
        reverse=True,
    )
    return {"metric": metric, "total": len(items), "jobs": items}


def build_contact_anchors(
    events: Iterable[ActivityRecord],
    origin_by_job: dict[str, str],
) -> dict[str, ContactAnchor]:
    """按 Pipeline 的 canonical 来源，为每个岗位建立唯一联系锚点。"""
    events_by_job: dict[str, list[ActivityRecord]] = defaultdict(list)
    for event in events:
        events_by_job[event.job_id].append(event)

    anchors: dict[str, ContactAnchor] = {}
    for job_id, job_events in events_by_job.items():
        origin = origin_by_job.get(job_id, "unknown")
        if origin in CANDIDATE_CONTACT_ORIGINS:
            candidates = [
                event
                for event in job_events
                if event.event_type == "candidate_initiated_contact"
            ]
        elif origin == "recruiter_initiated":
            candidates = [
                event
                for event in job_events
                if event.event_type == "recruiter_initiated_contact"
            ]
        else:
            candidates = [event for event in job_events if _is_real_chat_activity(event)]
        if not candidates:
            continue
        contact_event = min(candidates, key=activity_order_key)
        anchors[job_id] = ContactAnchor(
            job_id=job_id,
            contact_at=contact_event.occurred_at,
            contact_origin=origin if origin in SOURCE_ORDER else "unknown",
            contact_event_id=contact_event.id,
            event=contact_event,
        )
    return anchors


def canonical_terminal_events(
    events: Iterable[ActivityRecord],
) -> list[ActivityRecord]:
    """重放 terminal absorption/reopen，保留每个 episode 首个有效终态事件。"""
    events_by_job: dict[str, list[ActivityRecord]] = defaultdict(list)
    for event in events:
        if event.event_type in TERMINAL_EVENT_STAGES or event.event_type == "manual_stage_changed":
            events_by_job[event.job_id].append(event)

    effective: list[ActivityRecord] = []
    for job_events in events_by_job.values():
        terminal_stage: str | None = None
        for event in sorted(job_events, key=pipeline_activity_order_key):
            if event.event_type == "manual_stage_changed":
                stage = str(event.payload.get("stage") or "")
                if stage not in PIPELINE_STAGES:
                    continue
                allow_reopen = bool(event.payload.get("allow_reopen"))
                if terminal_stage is not None and not allow_reopen:
                    continue
                terminal_stage = stage if stage in TERMINAL_STAGES else None
                continue
            if terminal_stage is not None:
                continue
            terminal_stage = TERMINAL_EVENT_STAGES[event.event_type]
            effective.append(event)
    return effective


def activity_order_key(event: ActivityRecord) -> tuple[datetime, datetime, str]:
    """为同秒 Activity 提供全模块共用的稳定先后顺序。"""
    return event.occurred_datetime, event.created_datetime, event.id


def pipeline_activity_order_key(
    event: ActivityRecord,
) -> tuple[datetime, int, datetime, str]:
    priority = 30
    if event.event_type in {"candidate_initiated_contact", "recruiter_initiated_contact"}:
        priority = 10
    elif event.event_type in {"recruiter_replied", "candidate_replied"}:
        priority = 20
    elif event.event_type == "conversation_state_analyzed":
        priority = 40
    elif event.event_type in {"rejected", "job_closed", "offer_received"}:
        priority = 50
    elif event.event_type == "manual_stage_changed":
        priority = 60
    return event.occurred_datetime, priority, event.created_datetime, event.id


def is_event_after_contact(event: ActivityRecord, anchor: ContactAnchor) -> bool:
    return activity_order_key(event) > activity_order_key(anchor.event)


def _resolve_range(
    from_value: str,
    to_value: str,
    *,
    timezone_name: str,
    granularity: str,
    contact_origin: str | None,
) -> AnalyticsRange:
    if timezone_name != "Asia/Shanghai":
        raise AppError(422, "ANALYTICS_TIMEZONE_UNSUPPORTED", "P0 仅支持 Asia/Shanghai 时区。")
    if granularity not in {"auto", "day", "week"}:
        raise AppError(422, "ANALYTICS_GRANULARITY_INVALID", "统计粒度无效。")
    if contact_origin is not None and contact_origin not in SOURCE_ORDER:
        raise AppError(422, "ANALYTICS_CONTACT_ORIGIN_INVALID", "沟通来源无效。")
    try:
        from_date = date.fromisoformat(from_value)
        to_date = date.fromisoformat(to_value)
    except (TypeError, ValueError) as exc:
        raise AppError(422, "ANALYTICS_DATE_INVALID", "统计日期必须使用 YYYY-MM-DD 格式。") from exc
    if from_date > to_date:
        raise AppError(422, "ANALYTICS_RANGE_INVALID", "统计开始日期不能晚于结束日期。")

    selected_granularity = granularity
    if selected_granularity == "auto":
        selected_granularity = "day" if (to_date - from_date).days + 1 <= 31 else "week"
    start_local = datetime.combine(from_date, time.min, tzinfo=ASIA_SHANGHAI)
    end_local = datetime.combine(to_date + timedelta(days=1), time.min, tzinfo=ASIA_SHANGHAI)
    return AnalyticsRange(
        from_date=from_date,
        to_date=to_date,
        start_utc=start_local.astimezone(UTC),
        end_utc=end_local.astimezone(UTC),
        granularity=selected_granularity,
        contact_origin=contact_origin,
    )


def _load_analytics_events(connection: sqlite3.Connection) -> list[ActivityRecord]:
    placeholders = ",".join("?" for _ in ANALYTICS_EVENT_TYPES)
    rows = connection.execute(
        f"""
        SELECT id, job_id, event_type, occurred_at, created_at,
               source_ref_type, payload_json
        FROM fj_job_activity_events
        WHERE event_type IN ({placeholders})
        """,
        tuple(sorted(ANALYTICS_EVENT_TYPES)),
    ).fetchall()
    return [
        ActivityRecord(
            id=str(row["id"]),
            job_id=str(row["job_id"]),
            event_type=str(row["event_type"]),
            occurred_at=str(row["occurred_at"]),
            created_at=str(row["created_at"]),
            source_ref_type=str(row["source_ref_type"]),
            payload=_load_json(row["payload_json"]),
        )
        for row in rows
    ]


def _load_contact_origins(connection: sqlite3.Connection) -> dict[str, str]:
    return {
        str(row["job_id"]): str(row["contact_origin"] or "unknown")
        for row in connection.execute(
            "SELECT job_id, contact_origin FROM fj_job_pipeline_snapshots"
        ).fetchall()
    }


def _select_overview_matches(
    events: list[ActivityRecord],
    terminal_events: list[ActivityRecord],
    anchors: dict[str, ContactAnchor],
    candidate_cohort: dict[str, ContactAnchor],
    analytics_range: AnalyticsRange,
    origin_by_job: dict[str, str],
    events_by_job_type: dict[str, dict[str, list[ActivityRecord]]],
) -> dict[str, dict[str, ActivityRecord]]:
    """集中选择 Overview 岗位，Drill-down 直接复用这里的结果。"""
    metric_types = {
        "resume_submitted",
        "resume_viewed",
        "under_review",
        "interview_scheduled",
    }
    metric_matches: dict[str, dict[str, ActivityRecord]] = {
        metric: {} for metric in metric_types
    }
    for event in events:
        if (
            event.event_type in metric_types
            and _event_in_range(event, analytics_range)
            and _job_matches_origin(event.job_id, origin_by_job, analytics_range.contact_origin)
        ):
            current = metric_matches[event.event_type].get(event.job_id)
            if current is None or activity_order_key(event) < activity_order_key(current):
                metric_matches[event.event_type][event.job_id] = event

    terminal_matches: dict[str, dict[str, ActivityRecord]] = {
        "rejected": {},
        "job_closed": {},
        "offer_received": {},
    }
    for event in terminal_events:
        if (
            event.event_type in terminal_matches
            and _event_in_range(event, analytics_range)
            and _job_matches_origin(event.job_id, origin_by_job, analytics_range.contact_origin)
        ):
            current = terminal_matches[event.event_type].get(event.job_id)
            if current is None or pipeline_activity_order_key(event) > pipeline_activity_order_key(current):
                terminal_matches[event.event_type][event.job_id] = event

    candidate_reply_matches = _events_after_anchor(
        candidate_cohort,
        "recruiter_replied",
        events_by_job_type,
        analytics_range.end_utc,
    )
    recruiter_contacts = {
        anchor.job_id: anchor.event
        for anchor in anchors.values()
        if anchor.contact_origin == "recruiter_initiated"
        and _anchor_in_range(anchor, analytics_range)
        and _anchor_matches_filter(anchor, analytics_range.contact_origin)
    }
    return {
        "candidate_contacts": {
            job_id: anchor.event for job_id, anchor in candidate_cohort.items()
        },
        "candidate_contact_replies": candidate_reply_matches,
        "recruiter_contacts": recruiter_contacts,
        **metric_matches,
        **terminal_matches,
    }


def _aggregate_overview(
    matches: dict[str, dict[str, ActivityRecord]],
) -> dict[str, Any]:
    candidate_count = len(matches["candidate_contacts"])
    reply_count = len(matches["candidate_contact_replies"])
    return {
        "candidate_contacts": candidate_count,
        "recruiter_contacts": len(matches["recruiter_contacts"]),
        "candidate_contact_replies": reply_count,
        "candidate_reply_rate": _rate(reply_count, candidate_count),
        "resume_submitted": len(matches["resume_submitted"]),
        "resume_viewed": len(matches["resume_viewed"]),
        "under_review": len(matches["under_review"]),
        "interview_scheduled": len(matches["interview_scheduled"]),
        "rejected": len(matches["rejected"]),
        "job_closed": len(matches["job_closed"]),
        "offer_received": len(matches["offer_received"]),
    }


def _aggregate_trend(
    events: list[ActivityRecord],
    terminal_events: list[ActivityRecord],
    anchors: dict[str, ContactAnchor],
    analytics_range: AnalyticsRange,
    origin_by_job: dict[str, str],
) -> list[dict[str, Any]]:
    points = {
        bucket: {
            "period_start": bucket.isoformat(),
            "candidate_contacts": 0,
            "resume_submitted": 0,
            "interview_scheduled": 0,
            "rejected": 0,
        }
        for bucket in _trend_buckets(analytics_range)
    }
    candidate_jobs_by_bucket: dict[date, set[str]] = defaultdict(set)
    for anchor in anchors.values():
        if (
            anchor.contact_origin in CANDIDATE_CONTACT_ORIGINS
            and _anchor_in_range(anchor, analytics_range)
            and _anchor_matches_filter(anchor, analytics_range.contact_origin)
        ):
            candidate_jobs_by_bucket[_bucket_date(anchor.event, analytics_range.granularity)].add(
                anchor.job_id
            )

    metric_jobs_by_bucket: dict[tuple[date, str], set[str]] = defaultdict(set)
    for event in events:
        if (
            event.event_type in {"resume_submitted", "interview_scheduled"}
            and _event_in_range(event, analytics_range)
            and _job_matches_origin(event.job_id, origin_by_job, analytics_range.contact_origin)
        ):
            metric_jobs_by_bucket[
                (_bucket_date(event, analytics_range.granularity), event.event_type)
            ].add(event.job_id)
    for event in terminal_events:
        if (
            event.event_type == "rejected"
            and _event_in_range(event, analytics_range)
            and _job_matches_origin(event.job_id, origin_by_job, analytics_range.contact_origin)
        ):
            metric_jobs_by_bucket[
                (_bucket_date(event, analytics_range.granularity), "rejected")
            ].add(event.job_id)

    for bucket, point in points.items():
        point["candidate_contacts"] = len(candidate_jobs_by_bucket[bucket])
        point["resume_submitted"] = len(metric_jobs_by_bucket[(bucket, "resume_submitted")])
        point["interview_scheduled"] = len(
            metric_jobs_by_bucket[(bucket, "interview_scheduled")]
        )
        point["rejected"] = len(metric_jobs_by_bucket[(bucket, "rejected")])
    return list(points.values())


def _aggregate_funnel(
    candidate_cohort: dict[str, ContactAnchor],
    analytics_range: AnalyticsRange,
    events_by_job_type: dict[str, dict[str, list[ActivityRecord]]],
    terminal_by_job_type: dict[str, dict[str, list[ActivityRecord]]],
) -> dict[str, Any]:
    if analytics_range.contact_origin == "recruiter_initiated":
        return {
            "available": False,
            "unavailable_reason": "candidate_contact_cohort_not_applicable",
            "stages": [],
        }

    stage_specs = (
        ("candidate_contacts", None, events_by_job_type),
        ("candidate_contact_replies", "recruiter_replied", events_by_job_type),
        ("resume_submitted", "resume_submitted", events_by_job_type),
        ("resume_viewed", "resume_viewed", events_by_job_type),
        ("interview_scheduled", "interview_scheduled", events_by_job_type),
        ("offer_received", "offer_received", terminal_by_job_type),
    )
    stage_jobs = set(candidate_cohort)
    base_count = len(stage_jobs)
    previous_count: int | None = None
    stages: list[dict[str, Any]] = []
    for key, event_type, index in stage_specs:
        if event_type is not None:
            matching_jobs = _jobs_with_event_after_anchor(
                candidate_cohort,
                event_type,
                index,
                analytics_range.end_utc,
            )
            stage_jobs &= matching_jobs
        count = len(stage_jobs)
        stages.append({
            "key": key,
            "count": count,
            "previous_rate": None if previous_count is None else _rate(count, previous_count),
            "total_rate": _rate(count, base_count),
        })
        previous_count = count
    return {"available": True, "unavailable_reason": None, "stages": stages}


def _aggregate_current_state(
    connection: sqlite3.Connection,
    *,
    contact_origin: str | None,
) -> dict[str, int]:
    rows = connection.execute(
        """
        WITH ranked_sessions AS (
          SELECT id, job_id,
                 ROW_NUMBER() OVER (
                   PARTITION BY job_id
                   ORDER BY COALESCE(
                     last_message_at, platform_latest_message_at, updated_at, created_at
                   ) DESC, id DESC
                 ) AS row_number
          FROM fj_chat_sessions
          WHERE job_id IS NOT NULL
        )
        SELECT p.job_id, p.stage, p.waiting_on, p.contact_origin,
               attention.attention_status
        FROM fj_job_pipeline_snapshots p
        LEFT JOIN ranked_sessions latest
          ON latest.job_id = p.job_id AND latest.row_number = 1
        LEFT JOIN fj_chat_attention_states attention
          ON attention.session_id = latest.id
        """
    ).fetchall()
    result = {
        "waiting_recruiter": 0,
        "waiting_candidate": 0,
        "followup_recommended": 0,
        "under_review": 0,
        "interview_scheduling": 0,
    }
    for row in rows:
        if contact_origin is not None and str(row["contact_origin"] or "unknown") != contact_origin:
            continue
        result["waiting_recruiter"] += int(row["waiting_on"] == "recruiter")
        result["waiting_candidate"] += int(row["waiting_on"] == "candidate")
        result["followup_recommended"] += int(row["attention_status"] == "needs_followup")
        result["under_review"] += int(row["stage"] == "under_review")
        result["interview_scheduling"] += int(row["stage"] == "interview_scheduling")
    return result


def _aggregate_rejections(
    rejected_matches: dict[str, ActivityRecord],
) -> dict[str, list[dict[str, Any]]]:
    counts: dict[str, dict[str, int]] = {
        "recruiter_explicit": defaultdict(int),
        "ai_inferred": defaultdict(int),
        "unknown": defaultdict(int),
    }
    for event in rejected_matches.values():
        reason_source, category, _summary = normalize_rejection_fields(event.payload)
        counts[reason_source][category] += 1
    return {
        reason_source: [
            {"category": category, "job_count": job_count}
            for category, job_count in sorted(
                categories.items(), key=lambda item: (-item[1], item[0])
            )
        ]
        for reason_source, categories in counts.items()
    }


def _aggregate_source_performance(
    anchors: dict[str, ContactAnchor],
    analytics_range: AnalyticsRange,
    events_by_job_type: dict[str, dict[str, list[ActivityRecord]]],
    terminal_by_job_type: dict[str, dict[str, list[ActivityRecord]]],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for origin in SOURCE_ORDER:
        cohort = {
            anchor.job_id: anchor
            for anchor in anchors.values()
            if anchor.contact_origin == origin
            and _anchor_in_range(anchor, analytics_range)
            and (
                analytics_range.contact_origin is None
                or origin == analytics_range.contact_origin
            )
        }
        job_count = len(cohort)
        replies = _jobs_with_event_after_anchor(
            cohort, "recruiter_replied", events_by_job_type, analytics_range.end_utc
        )
        resumes = _jobs_with_event_after_anchor(
            cohort, "resume_submitted", events_by_job_type, analytics_range.end_utc
        )
        interviews = _jobs_with_event_after_anchor(
            cohort, "interview_scheduled", events_by_job_type, analytics_range.end_utc
        )
        offers = _jobs_with_event_after_anchor(
            cohort, "offer_received", terminal_by_job_type, analytics_range.end_utc
        )
        rejections = _jobs_with_event_after_anchor(
            cohort, "rejected", terminal_by_job_type, analytics_range.end_utc
        )
        result.append({
            "contact_origin": origin,
            "job_count": job_count,
            "candidate_reply_rate": (
                _rate(len(replies), job_count)
                if origin in CANDIDATE_CONTACT_ORIGINS
                else None
            ),
            "resume_rate": _rate(len(resumes), job_count),
            "interview_rate": _rate(len(interviews), job_count),
            "offer_rate": _rate(len(offers), job_count),
            "rejection_rate": _rate(len(rejections), job_count),
        })
    return result


def _candidate_contact_cohort(
    anchors: dict[str, ContactAnchor],
    analytics_range: AnalyticsRange,
) -> dict[str, ContactAnchor]:
    return {
        anchor.job_id: anchor
        for anchor in anchors.values()
        if anchor.contact_origin in CANDIDATE_CONTACT_ORIGINS
        and _anchor_in_range(anchor, analytics_range)
        and _anchor_matches_filter(anchor, analytics_range.contact_origin)
    }


def _jobs_with_event_after_anchor(
    cohort: dict[str, ContactAnchor],
    event_type: str,
    event_index: dict[str, dict[str, list[ActivityRecord]]],
    cutoff: datetime,
) -> set[str]:
    return set(_events_after_anchor(cohort, event_type, event_index, cutoff))


def _events_after_anchor(
    cohort: dict[str, ContactAnchor],
    event_type: str,
    event_index: dict[str, dict[str, list[ActivityRecord]]],
    cutoff: datetime,
) -> dict[str, ActivityRecord]:
    result: dict[str, ActivityRecord] = {}
    for job_id, anchor in cohort.items():
        events = event_index.get(job_id, {}).get(event_type, [])
        matching = [
            event
            for event in events
            if event.occurred_datetime < cutoff and is_event_after_contact(event, anchor)
        ]
        if matching:
            result[job_id] = min(matching, key=activity_order_key)
    return result


def _rejection_matches_filter(
    event: ActivityRecord,
    *,
    rejection_reason_source: str | None,
    rejection_reason_category: str | None,
) -> bool:
    reason_source, category, _summary = normalize_rejection_fields(event.payload)
    return (
        rejection_reason_source is None or reason_source == rejection_reason_source
    ) and (
        rejection_reason_category is None or category == rejection_reason_category
    )


def _load_job_current_states(
    connection: sqlite3.Connection,
) -> dict[str, dict[str, Any]]:
    rows = connection.execute(
        """
        WITH ranked_sessions AS (
          SELECT id, job_id,
                 ROW_NUMBER() OVER (
                   PARTITION BY job_id
                   ORDER BY COALESCE(
                     last_message_at, platform_latest_message_at, updated_at, created_at
                   ) DESC, id DESC
                 ) AS row_number
          FROM fj_chat_sessions
          WHERE job_id IS NOT NULL
        )
        SELECT p.job_id, p.waiting_on, attention.attention_status
        FROM fj_job_pipeline_snapshots p
        LEFT JOIN ranked_sessions latest
          ON latest.job_id = p.job_id AND latest.row_number = 1
        LEFT JOIN fj_chat_attention_states attention
          ON attention.session_id = latest.id
        """
    ).fetchall()
    return {str(row["job_id"]): dict(row) for row in rows}


def _current_state_matches_filter(
    state: dict[str, Any] | None,
    *,
    waiting_on: str | None,
    attention: str | None,
) -> bool:
    if waiting_on is not None and str((state or {}).get("waiting_on") or "unknown") != waiting_on:
        return False
    if attention is None:
        return True
    attention_status = str((state or {}).get("attention_status") or "")
    if attention == "actionable":
        return attention_status in ACTIONABLE_ATTENTION_STATUSES
    return attention_status == attention


def _load_job_summaries(
    connection: sqlite3.Connection,
    job_ids: set[str],
) -> dict[str, dict[str, Any]]:
    if not job_ids:
        return {}
    result: dict[str, dict[str, Any]] = {}
    ordered_ids = sorted(job_ids)
    # SQLite 参数数量有限，分批读取可让较大的明细列表保持稳定。
    for start in range(0, len(ordered_ids), 500):
        chunk = ordered_ids[start:start + 500]
        placeholders = ",".join("?" for _ in chunk)
        rows = connection.execute(
            f"""
            SELECT j.id, j.title, j.company_name, p.stage
            FROM fj_boss_jobs j
            LEFT JOIN fj_job_pipeline_snapshots p ON p.job_id = j.id
            WHERE j.id IN ({placeholders})
            """,
            tuple(chunk),
        ).fetchall()
        result.update({str(row["id"]): dict(row) for row in rows})
    return result


def _index_events(
    events: Iterable[ActivityRecord],
) -> dict[str, dict[str, list[ActivityRecord]]]:
    index: dict[str, dict[str, list[ActivityRecord]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for event in events:
        index[event.job_id][event.event_type].append(event)
    return index


def _is_real_chat_activity(event: ActivityRecord) -> bool:
    return (
        event.source_ref_type == "chat_message"
        and event.event_type
        in {
            "candidate_initiated_contact",
            "recruiter_initiated_contact",
            "candidate_replied",
            "recruiter_replied",
        }
    )


def _anchor_in_range(anchor: ContactAnchor, analytics_range: AnalyticsRange) -> bool:
    occurred_at = anchor.event.occurred_datetime
    return analytics_range.start_utc <= occurred_at < analytics_range.end_utc


def _event_in_range(event: ActivityRecord, analytics_range: AnalyticsRange) -> bool:
    return analytics_range.start_utc <= event.occurred_datetime < analytics_range.end_utc


def _anchor_matches_filter(anchor: ContactAnchor, contact_origin: str | None) -> bool:
    return contact_origin is None or anchor.contact_origin == contact_origin


def _job_matches_origin(
    job_id: str,
    origin_by_job: dict[str, str],
    contact_origin: str | None,
) -> bool:
    return contact_origin is None or origin_by_job.get(job_id, "unknown") == contact_origin


def _trend_buckets(analytics_range: AnalyticsRange) -> list[date]:
    current = analytics_range.from_date
    step = timedelta(days=1)
    if analytics_range.granularity == "week":
        current -= timedelta(days=current.weekday())
        step = timedelta(days=7)
    buckets: list[date] = []
    while current <= analytics_range.to_date:
        buckets.append(current)
        current += step
    return buckets


def _bucket_date(event: ActivityRecord, granularity: str) -> date:
    local_date = event.occurred_datetime.astimezone(ASIA_SHANGHAI).date()
    if granularity == "week":
        return local_date - timedelta(days=local_date.weekday())
    return local_date


def _rate(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _parse_activity_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _load_json(value: object) -> dict[str, Any]:
    try:
        loaded = json.loads(str(value or "{}"))
    except (TypeError, json.JSONDecodeError):
        return {}
    return loaded if isinstance(loaded, dict) else {}
