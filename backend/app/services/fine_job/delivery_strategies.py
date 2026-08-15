from __future__ import annotations

from backend.app.db import Database
from backend.app.schemas.fine_job.delivery_strategies import FineJobDeliveryStrategyPayload
from backend.app.utils import utc_now


DEFAULT_STRATEGY_ID = "default"


def get_delivery_strategy(db: Database) -> dict[str, object] | None:
    with db.connect() as connection:
        row = connection.execute(
            """
            SELECT id, automation_level, auto_greeting_enabled, daily_greeting_limit,
                   hourly_greeting_limit, min_match_score, resume_submit_mode,
                   contact_share_mode, interview_accept_mode, only_online_interview,
                   pause_on_risk, notes, confirmed_at, created_at, updated_at
            FROM fj_delivery_strategies
            WHERE id = ?
            """,
            (DEFAULT_STRATEGY_ID,),
        ).fetchone()
    if row is None:
        return None
    return _serialize_strategy(row)


def save_delivery_strategy(
    db: Database,
    payload: FineJobDeliveryStrategyPayload,
) -> dict[str, object]:
    now = utc_now()
    existing = get_delivery_strategy(db)
    created_at = str(existing["created_at"]) if existing else now
    with db.connect() as connection:
        connection.execute(
            """
            INSERT INTO fj_delivery_strategies (
              id, automation_level, auto_greeting_enabled, daily_greeting_limit,
              hourly_greeting_limit, min_match_score, resume_submit_mode,
              contact_share_mode, interview_accept_mode, only_online_interview,
              pause_on_risk, notes, confirmed_at, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              automation_level = excluded.automation_level,
              auto_greeting_enabled = excluded.auto_greeting_enabled,
              daily_greeting_limit = excluded.daily_greeting_limit,
              hourly_greeting_limit = excluded.hourly_greeting_limit,
              min_match_score = excluded.min_match_score,
              resume_submit_mode = excluded.resume_submit_mode,
              contact_share_mode = excluded.contact_share_mode,
              interview_accept_mode = excluded.interview_accept_mode,
              only_online_interview = excluded.only_online_interview,
              pause_on_risk = excluded.pause_on_risk,
              notes = excluded.notes,
              confirmed_at = excluded.confirmed_at,
              updated_at = excluded.updated_at
            """,
            (
                DEFAULT_STRATEGY_ID,
                payload.automation_level,
                1 if payload.auto_greeting_enabled else 0,
                payload.daily_greeting_limit,
                payload.hourly_greeting_limit,
                payload.min_match_score,
                payload.resume_submit_mode,
                payload.contact_share_mode,
                payload.interview_accept_mode,
                1 if payload.only_online_interview else 0,
                1 if payload.pause_on_risk else 0,
                payload.notes.strip(),
                now,
                created_at,
                now,
            ),
        )
    strategy = get_delivery_strategy(db)
    assert strategy is not None
    return strategy


def _serialize_strategy(row) -> dict[str, object]:
    confirmed_at = row["confirmed_at"]
    return {
        "id": row["id"],
        "automation_level": row["automation_level"],
        "auto_greeting_enabled": bool(row["auto_greeting_enabled"]),
        "daily_greeting_limit": row["daily_greeting_limit"],
        "hourly_greeting_limit": row["hourly_greeting_limit"],
        "min_match_score": row["min_match_score"],
        "resume_submit_mode": row["resume_submit_mode"],
        "contact_share_mode": row["contact_share_mode"],
        "interview_accept_mode": row["interview_accept_mode"],
        "only_online_interview": bool(row["only_online_interview"]),
        "pause_on_risk": bool(row["pause_on_risk"]),
        "notes": row["notes"],
        "ready": bool(confirmed_at),
        "confirmed_at": confirmed_at,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
