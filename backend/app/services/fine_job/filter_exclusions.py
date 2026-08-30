from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

from backend.app.db import Database
from backend.app.errors import AppError
from backend.app.schemas.fine_job.strategies import FineJobCooldownRules
from backend.app.utils import new_id, utc_now


COMPANY_RULES = {"applied_company", "detailed_company", "evaluated_company"}
JOB_RULES = {"applied_job", "detailed_job", "evaluated_job"}
PERIOD_DAYS = {"days_3": 3, "days_7": 7, "days_30": 30}
EVENT_RULES = {
    "application": ("applied_company", "applied_job"),
    "detail": ("detailed_company", "detailed_job"),
    "evaluation": ("evaluated_company", "evaluated_job"),
}
RULE_LABELS = {
    "blacklist_company": "公司黑名单",
    "outsourcing_company": "外包公司",
    "applied_company": "已投递公司冷却",
    "detailed_company": "已获取详情公司冷却",
    "evaluated_company": "已获取投递建议公司冷却",
    "applied_job": "已投递岗位冷却",
    "detailed_job": "已获取详情岗位冷却",
    "evaluated_job": "已获取投递建议岗位冷却",
}


def normalize_cooldown_rules(value: object) -> dict[str, object]:
    if isinstance(value, str):
        try:
            value = json.loads(value or "{}")
        except json.JSONDecodeError:
            value = {}
    payload = value if isinstance(value, dict) else {}
    return FineJobCooldownRules(**payload).model_dump()


def mark_all_states_stale(db: Database) -> None:
    with db.connect() as connection:
        connection.execute(
            "UPDATE fj_filter_exclusion_states SET status = 'stale', updated_at = ?",
            (utc_now(),),
        )


def ensure_exclusion_state(
    db: Database,
    strategy: dict[str, object],
    *,
    force: bool = False,
) -> dict[str, object]:
    from backend.app.services.fine_job.job_applications import sync_succeeded_applications

    sync_succeeded_applications(db)
    strategy_id = str(strategy["id"])
    strategy_version = int(strategy.get("strategy_version") or 1)
    with db.connect() as connection:
        state = connection.execute(
            "SELECT * FROM fj_filter_exclusion_states WHERE strategy_id = ?",
            (strategy_id,),
        ).fetchone()
    refresh_due = force or state is None
    if state is not None:
        refresh_due = refresh_due or state["status"] == "stale"
        refresh_due = refresh_due or int(state["strategy_version"]) != strategy_version
        refreshed_at = _parse_time(state["last_full_refreshed_at"])
        refresh_due = refresh_due or refreshed_at is None
        refresh_due = refresh_due or datetime.now(timezone.utc) - refreshed_at >= timedelta(days=1)
    if refresh_due:
        rebuild_exclusion_state(db, strategy)
    return get_exclusion_state(db, strategy_id)


def rebuild_exclusion_state(
    db: Database,
    strategy: dict[str, object],
) -> dict[str, object]:
    strategy_id = str(strategy["id"])
    strategy_version = int(strategy.get("strategy_version") or 1)
    rules = normalize_cooldown_rules(strategy.get("cooldown_rules"))
    now = utc_now()
    with db.connect() as connection:
        connection.execute(
            "DELETE FROM fj_filter_exclusion_entries WHERE strategy_id = ?",
            (strategy_id,),
        )
        companies = connection.execute(
            "SELECT id, company_type, is_blacklisted, blacklist_reason FROM fj_companies"
        ).fetchall()
        for company in companies:
            if company["is_blacklisted"]:
                _upsert_entry(
                    connection, strategy_id, "company", str(company["id"]),
                    "blacklist_company", None, None,
                    str(company["blacklist_reason"] or RULE_LABELS["blacklist_company"]), now,
                )
            if rules["exclude_outsourcing_companies"] and company["company_type"] == "outsourcing":
                _upsert_entry(
                    connection, strategy_id, "company", str(company["id"]),
                    "outsourcing_company", None, None, RULE_LABELS["outsourcing_company"], now,
                )

        event_queries = {
            "applied_job": """
                SELECT a.job_id AS entity_id, a.applied_at AS event_at, c.company_type
                FROM fj_job_applications a
                LEFT JOIN fj_companies c ON c.id = a.company_id
                WHERE a.status = 'applied'
            """,
            "applied_company": """
                SELECT a.company_id AS entity_id, MAX(a.applied_at) AS event_at, c.company_type
                FROM fj_job_applications a
                LEFT JOIN fj_companies c ON c.id = a.company_id
                WHERE a.status = 'applied' AND a.company_id IS NOT NULL
                GROUP BY a.company_id
            """,
            "detailed_job": """
                SELECT j.id AS entity_id, j.detail_collected_at AS event_at, c.company_type
                FROM fj_boss_jobs j LEFT JOIN fj_companies c ON c.id = j.company_id
                WHERE j.detail_status = 'completed' AND j.detail_collected_at IS NOT NULL
            """,
            "detailed_company": """
                SELECT j.company_id AS entity_id, MAX(j.detail_collected_at) AS event_at, c.company_type
                FROM fj_boss_jobs j LEFT JOIN fj_companies c ON c.id = j.company_id
                WHERE j.detail_status = 'completed' AND j.detail_collected_at IS NOT NULL
                  AND j.company_id IS NOT NULL GROUP BY j.company_id
            """,
            "evaluated_job": """
                SELECT e.job_id AS entity_id, MAX(e.created_at) AS event_at, c.company_type
                FROM fj_job_evaluations e JOIN fj_boss_jobs j ON j.id = e.job_id
                LEFT JOIN fj_companies c ON c.id = j.company_id GROUP BY e.job_id
            """,
            "evaluated_company": """
                SELECT j.company_id AS entity_id, MAX(e.created_at) AS event_at, c.company_type
                FROM fj_job_evaluations e JOIN fj_boss_jobs j ON j.id = e.job_id
                LEFT JOIN fj_companies c ON c.id = j.company_id
                WHERE j.company_id IS NOT NULL GROUP BY j.company_id
            """,
        }
        for rule_type, query in event_queries.items():
            rule = dict(rules[rule_type])
            if rule["period"] == "disabled":
                continue
            for event in connection.execute(query).fetchall():
                if (
                    rule_type in COMPANY_RULES
                    and rule["exclude_outsourcing"]
                    and event["company_type"] == "outsourcing"
                ):
                    continue
                excluded_until = _excluded_until(str(event["event_at"]), str(rule["period"]))
                if excluded_until is not None and _parse_time(excluded_until) <= datetime.now(timezone.utc):
                    continue
                _upsert_entry(
                    connection,
                    strategy_id,
                    "company" if rule_type in COMPANY_RULES else "job",
                    str(event["entity_id"]),
                    rule_type,
                    str(event["event_at"]),
                    excluded_until,
                    RULE_LABELS[rule_type],
                    now,
                )
        connection.execute(
            """
            INSERT INTO fj_filter_exclusion_states (
              strategy_id, strategy_version, status, last_full_refreshed_at, updated_at
            ) VALUES (?, ?, 'ready', ?, ?)
            ON CONFLICT(strategy_id) DO UPDATE SET
              strategy_version = excluded.strategy_version, status = 'ready',
              last_full_refreshed_at = excluded.last_full_refreshed_at,
              updated_at = excluded.updated_at
            """,
            (strategy_id, strategy_version, now, now),
        )
    return get_exclusion_state(db, strategy_id)


def get_exclusion_state(db: Database, strategy_id: str) -> dict[str, object]:
    now = utc_now()
    with db.connect() as connection:
        state = connection.execute(
            "SELECT * FROM fj_filter_exclusion_states WHERE strategy_id = ?", (strategy_id,)
        ).fetchone()
        counts = connection.execute(
            """
            SELECT entity_type, COUNT(DISTINCT entity_id) AS amount
            FROM fj_filter_exclusion_entries
            WHERE strategy_id = ? AND (excluded_until IS NULL OR excluded_until > ?)
            GROUP BY entity_type
            """,
            (strategy_id, now),
        ).fetchall()
    if state is None:
        return {
            "strategy_id": strategy_id, "status": "stale", "strategy_version": 0,
            "last_full_refreshed_at": None, "updated_at": None,
            "company_count": 0, "job_count": 0,
        }
    totals = {str(row["entity_type"]): int(row["amount"]) for row in counts}
    return {
        "strategy_id": strategy_id,
        "status": state["status"],
        "strategy_version": int(state["strategy_version"]),
        "last_full_refreshed_at": state["last_full_refreshed_at"],
        "updated_at": state["updated_at"],
        "company_count": totals.get("company", 0),
        "job_count": totals.get("job", 0),
    }


def apply_filter_exclusions(
    db: Database,
    strategy: dict[str, object],
    jobs: list[dict[str, object]],
    results: list[dict[str, object]],
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    ensure_exclusion_state(db, strategy)
    job_ids = [str(job.get("history_record_id") or job.get("id") or "") for job in jobs]
    governance = _load_job_governance(db, [value for value in job_ids if value])
    company_ids = [
        str(governance.get(job_id, {}).get("company_id") or "") for job_id in job_ids
    ]
    active_reason_map = _load_active_reason_map(
        db,
        str(strategy["id"]),
        [value for value in job_ids if value],
        [value for value in company_ids if value],
    )
    result_by_id = {str(item.get("job_id") or ""): item for item in results}
    enriched_jobs: list[dict[str, object]] = []
    admitted_companies: set[str] = set()
    rules = normalize_cooldown_rules(strategy.get("cooldown_rules"))
    company_dedup_enabled = any(
        dict(rules[rule_type])["period"] != "disabled" for rule_type in COMPANY_RULES
    )
    for job, history_id, company_id in zip(jobs, job_ids, company_ids):
        context = governance.get(history_id, {})
        reason_rows = [
            *active_reason_map.get(("job", history_id), []),
            *active_reason_map.get(("company", company_id), []),
        ]
        reasons = [
            RULE_LABELS.get(str(row["rule_type"]), str(row["reason"]))
            for row in reason_rows
        ]
        task_job_id = str(job.get("job_id") or "")
        result = result_by_id.get(task_job_id)
        if (
            not reasons
            and result is not None
            and result.get("status") in {"pass", "review"}
            and company_dedup_enabled
            and company_id
            and context.get("company_type") != "outsourcing"
        ):
            if company_id in admitted_companies:
                reasons.append("同批次同公司仅保留一个候选岗位")
            else:
                admitted_companies.add(company_id)
        enriched = {
            **job,
            **context,
            "cooldown_excluded": bool(reasons),
            "cooldown_reasons": reasons,
        }
        enriched_jobs.append(enriched)
        if result is not None and reasons:
            result["status"] = "exclude"
            result["reasons"] = [*list(result.get("reasons") or []), *reasons]
            result["cooldown_excluded"] = True
            result["cooldown_reasons"] = reasons
        if result is not None:
            result.update(context)
    return enriched_jobs, results


def assert_job_action_allowed(
    db: Database,
    job_id: str,
    *,
    strategy: dict[str, object] | None,
    action: str,
) -> None:
    from backend.app.services.fine_job.job_applications import sync_succeeded_applications

    sync_succeeded_applications(db)
    governance = _load_job_governance(db, [job_id]).get(job_id)
    if not governance:
        return
    if governance.get("is_blacklisted"):
        raise AppError(409, "JOB_EXCLUDED", "该岗位公司已加入黑名单。")
    if action == "application" and governance.get("application_status") == "applied":
        raise AppError(409, "JOB_ALREADY_APPLIED", "该岗位已经投递。")
    if strategy is None:
        return
    ensure_exclusion_state(db, strategy)
    reasons = _active_reason_rows(
        db, str(strategy["id"]), job_id, str(governance.get("company_id") or "")
    )
    if action == "evaluation":
        # 同一任务在详情完成后可以继续生成投递建议。
        reasons = [row for row in reasons if row["rule_type"] not in {"detailed_job", "detailed_company"}]
    if action == "application":
        reasons = [
            row for row in reasons
            if row["rule_type"] in {"blacklist_company", "applied_job", "applied_company"}
        ]
    if reasons:
        raise AppError(
            409,
            "JOB_EXCLUDED",
            "当前策略已排除该岗位：" + "；".join(RULE_LABELS.get(str(row["rule_type"]), str(row["reason"])) for row in reasons),
        )


def record_job_event(db: Database, event_type: str, job_id: str, event_at: str | None = None) -> None:
    rule_types = EVENT_RULES.get(event_type)
    if not rule_types:
        return
    event_time = event_at or utc_now()
    with db.connect() as connection:
        job = connection.execute(
            """
            SELECT j.id, j.company_id, c.company_type
            FROM fj_boss_jobs j LEFT JOIN fj_companies c ON c.id = j.company_id
            WHERE j.id = ?
            """,
            (job_id,),
        ).fetchone()
        if job is None:
            return
        strategies = connection.execute(
            "SELECT id, cooldown_rules_json FROM fj_job_filter_strategies WHERE enabled = 1"
        ).fetchall()
        now = utc_now()
        for strategy in strategies:
            rules = normalize_cooldown_rules(strategy["cooldown_rules_json"])
            for rule_type in rule_types:
                rule = dict(rules[rule_type])
                if rule["period"] == "disabled":
                    continue
                if (
                    rule_type in COMPANY_RULES
                    and rule["exclude_outsourcing"]
                    and job["company_type"] == "outsourcing"
                ):
                    continue
                entity_id = job["company_id"] if rule_type in COMPANY_RULES else job["id"]
                if not entity_id:
                    continue
                _upsert_entry(
                    connection, str(strategy["id"]),
                    "company" if rule_type in COMPANY_RULES else "job",
                    str(entity_id), rule_type, event_time,
                    _excluded_until(event_time, str(rule["period"])),
                    RULE_LABELS[rule_type], now,
                )
            connection.execute(
                "UPDATE fj_filter_exclusion_states SET updated_at = ? WHERE strategy_id = ?",
                (now, strategy["id"]),
            )


def _load_job_governance(db: Database, job_ids: list[str]) -> dict[str, dict[str, object]]:
    if not job_ids:
        return {}
    placeholders = ", ".join("?" for _ in job_ids)
    with db.connect() as connection:
        rows = connection.execute(
            f"""
            SELECT j.id, j.company_id, c.company_type, c.is_blacklisted,
                   a.status AS application_status, a.applied_at
            FROM fj_boss_jobs j
            LEFT JOIN fj_companies c ON c.id = j.company_id
            LEFT JOIN fj_job_applications a ON a.job_id = j.id
            WHERE j.id IN ({placeholders})
            """,
            job_ids,
        ).fetchall()
    return {
        str(row["id"]): {
            "company_id": row["company_id"],
            "company_type": row["company_type"] or "unknown",
            "is_outsourcing_company": row["company_type"] == "outsourcing",
            "is_blacklisted": bool(row["is_blacklisted"]),
            "application_status": row["application_status"],
            "applied_at": row["applied_at"],
        }
        for row in rows
    }


def _active_reasons(db: Database, strategy_id: str, job_id: str, company_id: str) -> list[str]:
    return [
        RULE_LABELS.get(str(row["rule_type"]), str(row["reason"]))
        for row in _active_reason_rows(db, strategy_id, job_id, company_id)
    ]


def _load_active_reason_map(
    db: Database,
    strategy_id: str,
    job_ids: list[str],
    company_ids: list[str],
) -> dict[tuple[str, str], list[sqlite3.Row]]:
    if not job_ids and not company_ids:
        return {}
    conditions: list[str] = []
    values: list[object] = [strategy_id, utc_now()]
    if job_ids:
        conditions.append(
            f"(entity_type = 'job' AND entity_id IN ({', '.join('?' for _ in job_ids)}))"
        )
        values.extend(job_ids)
    if company_ids:
        conditions.append(
            f"(entity_type = 'company' AND entity_id IN ({', '.join('?' for _ in company_ids)}))"
        )
        values.extend(company_ids)
    with db.connect() as connection:
        rows = connection.execute(
            f"""
            SELECT entity_type, entity_id, rule_type, reason
            FROM fj_filter_exclusion_entries
            WHERE strategy_id = ? AND (excluded_until IS NULL OR excluded_until > ?)
              AND ({' OR '.join(conditions)})
            ORDER BY CASE rule_type WHEN 'blacklist_company' THEN 0 WHEN 'outsourcing_company' THEN 1 ELSE 2 END,
                     rule_type
            """,
            values,
        ).fetchall()
    result: dict[tuple[str, str], list[sqlite3.Row]] = {}
    for row in rows:
        result.setdefault((str(row["entity_type"]), str(row["entity_id"])), []).append(row)
    return result


def _active_reason_rows(db: Database, strategy_id: str, job_id: str, company_id: str):
    now = utc_now()
    with db.connect() as connection:
        return connection.execute(
            """
            SELECT rule_type, reason FROM fj_filter_exclusion_entries
            WHERE strategy_id = ? AND (excluded_until IS NULL OR excluded_until > ?)
              AND ((entity_type = 'job' AND entity_id = ?)
                   OR (entity_type = 'company' AND entity_id = ?))
            ORDER BY CASE rule_type WHEN 'blacklist_company' THEN 0 WHEN 'outsourcing_company' THEN 1 ELSE 2 END,
                     rule_type
            """,
            (strategy_id, now, job_id, company_id),
        ).fetchall()


def _upsert_entry(
    connection,
    strategy_id: str,
    entity_type: str,
    entity_id: str,
    rule_type: str,
    source_event_at: str | None,
    excluded_until: str | None,
    reason: str,
    now: str,
) -> None:
    connection.execute(
        """
        INSERT INTO fj_filter_exclusion_entries (
          id, strategy_id, entity_type, entity_id, rule_type, source_event_at,
          excluded_until, reason, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(strategy_id, entity_type, entity_id, rule_type) DO UPDATE SET
          source_event_at = excluded.source_event_at,
          excluded_until = excluded.excluded_until,
          reason = excluded.reason,
          updated_at = excluded.updated_at
        """,
        (
            new_id(), strategy_id, entity_type, entity_id, rule_type,
            source_event_at, excluded_until, reason, now, now,
        ),
    )


def _excluded_until(event_at: str, period: str) -> str | None:
    if period == "permanent":
        return None
    parsed = _parse_time(event_at)
    days = PERIOD_DAYS.get(period)
    if parsed is None or days is None:
        return event_at
    return (parsed + timedelta(days=days)).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_time(value: object) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
