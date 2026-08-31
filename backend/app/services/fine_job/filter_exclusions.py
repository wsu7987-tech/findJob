from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

from backend.app.db import Database
from backend.app.errors import AppError
from backend.app.schemas.fine_job.strategies import (
    FineJobCooldownRules,
    normalize_cooldown_rules_payload,
)
from backend.app.utils import new_id, utc_now


COMPANY_RULES = {"applied_company", "detailed_and_evaluated_company"}
JOB_RULES = {"applied_job", "detailed_and_evaluated_job"}
PERIOD_DAYS = {"days_3": 3, "days_7": 7, "days_30": 30}
EVENT_RULES = {
    "application": ("applied_company", "applied_job"),
}
RULE_LABELS = {
    "blacklist_company": "公司黑名单",
    "outsourcing_company": "外包公司",
    "applied_company": "已投递公司冷却",
    "applied_job": "已投递岗位冷却",
    "detailed_and_evaluated_company": "已获取详情和投递建议岗位的公司冷却",
    "detailed_and_evaluated_job": "已获取详情和投递建议岗位冷却",
}


def normalize_cooldown_rules(value: object) -> dict[str, object]:
    return normalize_cooldown_rules_payload(value)


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
        legacy_entries = connection.execute(
            """
            SELECT 1
            FROM fj_filter_exclusion_entries
            WHERE strategy_id = ?
              AND rule_type IN (
                'outsourcing_company', 'detailed_company', 'evaluated_company',
                'detailed_job', 'evaluated_job'
              )
            LIMIT 1
            """,
            (strategy_id,),
        ).fetchone()
    refresh_due = force or state is None
    if state is not None:
        refresh_due = refresh_due or state["status"] == "stale"
        refresh_due = refresh_due or int(state["strategy_version"]) != strategy_version
        refreshed_at = _parse_time(state["last_full_refreshed_at"])
        refresh_due = refresh_due or refreshed_at is None
        refresh_due = refresh_due or datetime.now(timezone.utc) - refreshed_at >= timedelta(days=1)
    # 旧版本排除项必须先清理，避免旧的独立详情/建议冷却继续阻断新流程。
    refresh_due = refresh_due or legacy_entries is not None
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
            "detailed_and_evaluated_job": """
                SELECT j.id AS entity_id,
                       CASE
                         WHEN j.detail_collected_at >= evaluated.evaluated_at
                         THEN j.detail_collected_at
                         ELSE evaluated.evaluated_at
                       END AS event_at,
                       c.company_type
                FROM fj_boss_jobs j
                JOIN (
                  SELECT job_id, MAX(created_at) AS evaluated_at
                  FROM fj_job_evaluations
                  GROUP BY job_id
                ) evaluated ON evaluated.job_id = j.id
                LEFT JOIN fj_companies c ON c.id = j.company_id
                WHERE j.detail_status = 'completed'
                  AND j.detail_collected_at IS NOT NULL
            """,
            "detailed_and_evaluated_company": """
                SELECT j.company_id AS entity_id,
                       MAX(
                         CASE
                           WHEN j.detail_collected_at >= evaluated.evaluated_at
                           THEN j.detail_collected_at
                           ELSE evaluated.evaluated_at
                         END
                       ) AS event_at,
                       c.company_type
                FROM fj_boss_jobs j
                JOIN (
                  SELECT job_id, MAX(created_at) AS evaluated_at
                  FROM fj_job_evaluations
                  GROUP BY job_id
                ) evaluated ON evaluated.job_id = j.id
                LEFT JOIN fj_companies c ON c.id = j.company_id
                WHERE j.detail_status = 'completed'
                  AND j.detail_collected_at IS NOT NULL
                  AND j.company_id IS NOT NULL
                GROUP BY j.company_id
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
        if result is not None:
            # 保留岗位自身筛选结果，避免冷却排除后丢失原始通过状态。
            strategy_status = str(
                result.get("strategy_filter_status") or result.get("status") or "review"
            )
            result["strategy_filter_status"] = strategy_status
            result["final_filter_status"] = str(result.get("status") or strategy_status)
        enriched = {
            **job,
            **context,
            "cooldown_excluded": bool(reasons),
            "cooldown_reasons": reasons,
        }
        enriched_jobs.append(enriched)
        if result is not None and reasons:
            result["status"] = "exclude"
            result["final_filter_status"] = "exclude"
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
    allow_manual_override: bool = False,
) -> None:
    from backend.app.services.fine_job.job_applications import sync_succeeded_applications

    sync_succeeded_applications(db)
    governance = _load_job_governance(db, [job_id]).get(job_id)
    if not governance:
        return
    if governance.get("is_blacklisted") and not allow_manual_override:
        raise AppError(409, "JOB_EXCLUDED", "该岗位公司已加入黑名单。")
    if action == "application" and governance.get("application_status") == "applied":
        raise AppError(409, "JOB_ALREADY_APPLIED", "该岗位已经投递。")
    if strategy is None:
        return
    ensure_exclusion_state(db, strategy)
    reasons = _active_reason_rows(
        db, str(strategy["id"]), job_id, str(governance.get("company_id") or "")
    )
    if action == "application":
        reasons = [
            row for row in reasons
            if row["rule_type"] in {"blacklist_company", "applied_job", "applied_company"}
        ]
    # 自动流程遵循排除规则；用户明确发起详情或建议操作时允许人工覆盖。
    if reasons and not allow_manual_override:
        raise AppError(
            409,
            "JOB_EXCLUDED",
            "当前策略已排除该岗位：" + "；".join(RULE_LABELS.get(str(row["rule_type"]), str(row["reason"])) for row in reasons),
        )


def record_job_event(db: Database, event_type: str, job_id: str, event_at: str | None = None) -> None:
    if event_type in {"detail", "evaluation"}:
        _refresh_detail_and_evaluation_cooldowns(db, job_id)
        return
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


def _refresh_detail_and_evaluation_cooldowns(db: Database, job_id: str) -> None:
    """只有同一岗位同时完成详情和建议后，才更新岗位及公司的组合冷却。"""
    with db.connect() as connection:
        job = connection.execute(
            """
            SELECT j.id, j.company_id, c.company_type, j.detail_status,
                   j.detail_collected_at,
                   (SELECT MAX(created_at) FROM fj_job_evaluations e WHERE e.job_id = j.id)
                     AS evaluated_at
            FROM fj_boss_jobs j
            LEFT JOIN fj_companies c ON c.id = j.company_id
            WHERE j.id = ?
            """,
            (job_id,),
        ).fetchone()
        if job is None:
            return

        strategies = connection.execute(
            "SELECT id, cooldown_rules_json FROM fj_job_filter_strategies WHERE enabled = 1"
        ).fetchall()
        company_latest = None
        if job["company_id"]:
            company_latest = connection.execute(
                """
                SELECT MAX(
                         CASE
                           WHEN j.detail_collected_at >= evaluated.evaluated_at
                           THEN j.detail_collected_at
                           ELSE evaluated.evaluated_at
                         END
                       ) AS event_at
                FROM fj_boss_jobs j
                JOIN (
                  SELECT job_id, MAX(created_at) AS evaluated_at
                  FROM fj_job_evaluations
                  GROUP BY job_id
                ) evaluated ON evaluated.job_id = j.id
                WHERE j.company_id = ?
                  AND j.detail_status = 'completed'
                  AND j.detail_collected_at IS NOT NULL
                """,
                (job["company_id"],),
            ).fetchone()["event_at"]

        job_event_at = _latest_event_at(job["detail_collected_at"], job["evaluated_at"])
        now = utc_now()
        for strategy in strategies:
            strategy_id = str(strategy["id"])
            rules = normalize_cooldown_rules(strategy["cooldown_rules_json"])
            rule = dict(rules["detailed_and_evaluated_job"])
            if job_event_at and rule["period"] != "disabled":
                _upsert_entry(
                    connection,
                    strategy_id,
                    "job",
                    str(job["id"]),
                    "detailed_and_evaluated_job",
                    str(job_event_at),
                    _excluded_until(str(job_event_at), str(rule["period"])),
                    RULE_LABELS["detailed_and_evaluated_job"],
                    now,
                )
            else:
                _delete_entry(
                    connection, strategy_id, "job", str(job["id"]),
                    "detailed_and_evaluated_job",
                )

            company_rule = dict(rules["detailed_and_evaluated_company"])
            company_allowed = not (
                company_rule["exclude_outsourcing"]
                and job["company_type"] == "outsourcing"
            )
            if (
                company_latest
                and company_rule["period"] != "disabled"
                and company_allowed
                and job["company_id"]
            ):
                _upsert_entry(
                    connection,
                    strategy_id,
                    "company",
                    str(job["company_id"]),
                    "detailed_and_evaluated_company",
                    str(company_latest),
                    _excluded_until(str(company_latest), str(company_rule["period"])),
                    RULE_LABELS["detailed_and_evaluated_company"],
                    now,
                )
            elif job["company_id"]:
                _delete_entry(
                    connection, strategy_id, "company", str(job["company_id"]),
                    "detailed_and_evaluated_company",
                )
            connection.execute(
                "UPDATE fj_filter_exclusion_states SET updated_at = ? WHERE strategy_id = ?",
                (now, strategy_id),
            )


def _latest_event_at(detail_at: object, evaluated_at: object) -> str | None:
    """组合事件以第二个完成事实的时间作为冷却起点。"""
    detail_value = str(detail_at or "")
    evaluated_value = str(evaluated_at or "")
    if not detail_value or not evaluated_value:
        return None
    return max(detail_value, evaluated_value)


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


def _delete_entry(
    connection,
    strategy_id: str,
    entity_type: str,
    entity_id: str,
    rule_type: str,
) -> None:
    """删除不再满足组合条件的冷却项。"""
    connection.execute(
        """
        DELETE FROM fj_filter_exclusion_entries
        WHERE strategy_id = ? AND entity_type = ? AND entity_id = ? AND rule_type = ?
        """,
        (strategy_id, entity_type, entity_id, rule_type),
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
