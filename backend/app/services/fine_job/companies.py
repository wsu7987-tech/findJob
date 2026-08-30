from __future__ import annotations

import re
import sqlite3
import unicodedata

from backend.app.db import Database
from backend.app.errors import AppError
from backend.app.utils import new_id, utc_now


def normalize_company_name(value: str) -> str:
    """统一全半角、空白与大小写，作为公司和别名的稳定匹配键。"""
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value.strip())).casefold()


def resolve_company(
    connection: sqlite3.Connection,
    company_name: str,
    *,
    source: str = "capture",
) -> sqlite3.Row | None:
    name = company_name.strip()
    if not name:
        return None
    normalized = normalize_company_name(name)
    row = _find_company_match(connection, normalized)
    if row is not None:
        return row
    return _insert_unknown_company(connection, name, normalized, source=source)


def reconcile_job_companies(connection: sqlite3.Connection, *, source: str = "capture") -> int:
    """按当前公司分类和别名规则重建历史岗位的公司关联。"""
    company_names = connection.execute(
        "SELECT DISTINCT company_name FROM fj_boss_jobs WHERE TRIM(company_name) <> ''"
    ).fetchall()
    updated_jobs = 0
    for item in company_names:
        company_name = str(item["company_name"]).strip()
        normalized = normalize_company_name(company_name)
        company = _find_company_match(connection, normalized)
        if company is None:
            company = _insert_unknown_company(
                connection,
                company_name,
                normalized,
                source=source,
            )
        cursor = connection.execute(
            "UPDATE fj_boss_jobs SET company_id = ? WHERE company_name = ? AND company_id IS NOT ?",
            (company["id"], company_name, company["id"]),
        )
        updated_jobs += max(cursor.rowcount, 0)

    # 投递事实中的公司 ID 跟随岗位关联，保证公司级去重使用最新识别结果。
    connection.execute(
        """
        UPDATE fj_job_applications
        SET company_id = (
          SELECT j.company_id FROM fj_boss_jobs j WHERE j.id = fj_job_applications.job_id
        ), updated_at = ?
        WHERE EXISTS (
          SELECT 1 FROM fj_boss_jobs j
          WHERE j.id = fj_job_applications.job_id
            AND j.company_id IS NOT fj_job_applications.company_id
        )
        """,
        (utc_now(),),
    )
    return updated_jobs


def _find_company_match(
    connection: sqlite3.Connection,
    normalized_name: str,
) -> sqlite3.Row | None:
    # 完整名称的明确分类或黑名单优先，保留用户对具体公司的人工判断。
    exact_priority = _find_exact_company(
        connection,
        normalized_name,
        priority_only=True,
    )
    if exact_priority is not None:
        return exact_priority

    # 外包标准名和别名均参与包含识别，多个命中时采用最长名称。
    outsourcing = connection.execute(
        """
        SELECT candidates.*
        FROM (
          SELECT c.*, c.normalized_name AS match_token, 0 AS match_source
          FROM fj_companies c
          WHERE c.company_type = 'outsourcing'
            AND c.normalized_name <> ''
            AND INSTR(?, c.normalized_name) > 0
          UNION ALL
          SELECT c.*, a.normalized_alias AS match_token, 1 AS match_source
          FROM fj_companies c
          JOIN fj_company_aliases a ON a.company_id = c.id
          WHERE c.company_type = 'outsourcing'
            AND a.normalized_alias <> ''
            AND INSTR(?, a.normalized_alias) > 0
        ) AS candidates
        ORDER BY LENGTH(match_token) DESC, match_source ASC, updated_at DESC, id
        LIMIT 1
        """,
        (normalized_name, normalized_name),
    ).fetchone()
    if outsourcing is not None:
        return outsourcing

    return _find_exact_company(connection, normalized_name)


def _find_exact_company(
    connection: sqlite3.Connection,
    normalized_name: str,
    *,
    priority_only: bool = False,
) -> sqlite3.Row | None:
    priority_condition = (
        "AND (c.company_type <> 'unknown' OR c.is_blacklisted = 1)"
        if priority_only
        else ""
    )
    return connection.execute(
        f"""
        SELECT c.* FROM fj_companies c
        LEFT JOIN fj_company_aliases a ON a.company_id = c.id
        WHERE (c.normalized_name = ? OR a.normalized_alias = ?)
          {priority_condition}
        ORDER BY CASE WHEN c.normalized_name = ? THEN 0 ELSE 1 END,
                 c.updated_at DESC, c.id
        LIMIT 1
        """,
        (normalized_name, normalized_name, normalized_name),
    ).fetchone()


def _insert_unknown_company(
    connection: sqlite3.Connection,
    name: str,
    normalized_name: str,
    *,
    source: str,
) -> sqlite3.Row:
    now = utc_now()
    company_id = new_id()
    connection.execute(
        """
        INSERT INTO fj_companies (
          id, canonical_name, normalized_name, company_type,
          classification_source, notes, is_blacklisted, blacklist_reason,
          version, created_at, updated_at
        ) VALUES (?, ?, ?, 'unknown', ?, '', 0, '', 1, ?, ?)
        """,
        (company_id, name, normalized_name, source, now, now),
    )
    return connection.execute(
        "SELECT * FROM fj_companies WHERE id = ?", (company_id,)
    ).fetchone()


def create_company(
    db: Database,
    *,
    name: str,
    company_type: str = "unknown",
    notes: str = "",
    source: str = "manual",
) -> dict[str, object]:
    with db.connect() as connection:
        clean_name = name.strip()
        normalized = normalize_company_name(clean_name)
        existing = _find_exact_company(connection, normalized)
        if existing is None and clean_name:
            existing = _insert_unknown_company(
                connection,
                clean_name,
                normalized,
                source=source,
            )
        if existing is None:
            raise AppError(422, "VALIDATION_FAILED", "公司名称不能为空。")
        company_id = str(existing["id"])
        connection.execute(
            """
            UPDATE fj_companies
            SET company_type = ?, classification_source = ?, notes = ?,
                version = version + 1, updated_at = ?
            WHERE id = ?
            """,
            (company_type, source, notes.strip(), utc_now(), company_id),
        )
        reconcile_job_companies(connection, source=source)
        _mark_exclusions_stale(connection)
    return get_company(db, company_id)


def list_companies(
    db: Database,
    *,
    query: str = "",
    company_type: str = "",
    blacklist_status: str = "all",
    page: int = 1,
    page_size: int = 20,
) -> dict[str, object]:
    from backend.app.services.fine_job.job_applications import sync_succeeded_applications

    sync_succeeded_applications(db)
    conditions: list[str] = []
    values: list[object] = []
    if query.strip():
        like = f"%{query.strip()}%"
        conditions.append(
            "(c.canonical_name LIKE ? OR EXISTS (SELECT 1 FROM fj_company_aliases qa WHERE qa.company_id = c.id AND qa.alias_name LIKE ?))"
        )
        values.extend([like, like])
    if company_type:
        conditions.append("c.company_type = ?")
        values.append(company_type)
    if blacklist_status in {"blacklisted", "normal"}:
        conditions.append("c.is_blacklisted = ?")
        values.append(1 if blacklist_status == "blacklisted" else 0)
    where_sql = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    offset = (page - 1) * page_size
    with db.connect() as connection:
        total = int(
            connection.execute(
                f"SELECT COUNT(*) FROM fj_companies c {where_sql}", values
            ).fetchone()[0]
        )
        rows = connection.execute(
            f"""
            SELECT c.* FROM fj_companies c
            {where_sql}
            ORDER BY c.is_blacklisted DESC,
                     CASE c.company_type WHEN 'outsourcing' THEN 0 WHEN 'direct' THEN 1 ELSE 2 END,
                     c.updated_at DESC, c.id
            LIMIT ? OFFSET ?
            """,
            [*values, page_size, offset],
        ).fetchall()
        items = [_serialize_company(connection, row) for row in rows]
    return {"items": items, "total": total, "page": page, "page_size": page_size}


def get_company(db: Database, company_id: str) -> dict[str, object]:
    with db.connect() as connection:
        row = connection.execute(
            "SELECT * FROM fj_companies WHERE id = ?", (company_id,)
        ).fetchone()
        if row is None:
            raise AppError(404, "NOT_FOUND", "公司不存在。")
        return _serialize_company(connection, row)


def update_company(
    db: Database,
    company_id: str,
    *,
    canonical_name: str | None = None,
    company_type: str | None = None,
    notes: str | None = None,
    source: str = "manual",
) -> dict[str, object]:
    with db.connect() as connection:
        row = _require_company(connection, company_id)
        name = canonical_name.strip() if canonical_name is not None else str(row["canonical_name"])
        normalized = normalize_company_name(name)
        conflict = connection.execute(
            "SELECT id FROM fj_companies WHERE normalized_name = ? AND id <> ?",
            (normalized, company_id),
        ).fetchone()
        if conflict is not None:
            raise AppError(409, "COMPANY_NAME_CONFLICT", "该公司名称已由其他公司使用。")
        connection.execute(
            """
            UPDATE fj_companies
            SET canonical_name = ?, normalized_name = ?, company_type = ?,
                classification_source = ?, notes = ?, version = version + 1,
                updated_at = ?
            WHERE id = ?
            """,
            (
                name,
                normalized,
                company_type or str(row["company_type"]),
                source,
                notes.strip() if notes is not None else str(row["notes"]),
                utc_now(),
                company_id,
            ),
        )
        reconcile_job_companies(connection, source=source)
        _mark_exclusions_stale(connection)
    return get_company(db, company_id)


def set_company_blacklist(
    db: Database,
    company_id: str,
    *,
    blacklisted: bool,
    reason: str = "",
) -> dict[str, object]:
    now = utc_now()
    with db.connect() as connection:
        _require_company(connection, company_id)
        connection.execute(
            """
            UPDATE fj_companies
            SET is_blacklisted = ?, blacklist_reason = ?, blacklisted_at = ?,
                version = version + 1, updated_at = ?
            WHERE id = ?
            """,
            (
                int(blacklisted),
                reason.strip() if blacklisted else "",
                now if blacklisted else None,
                now,
                company_id,
            ),
        )
        reconcile_job_companies(connection, source="manual")
        _mark_exclusions_stale(connection)
    return get_company(db, company_id)


def add_company_alias(
    db: Database,
    company_id: str,
    alias_name: str,
) -> dict[str, object]:
    alias = alias_name.strip()
    normalized = normalize_company_name(alias)
    with db.connect() as connection:
        company = _require_company(connection, company_id)
        if normalized == str(company["normalized_name"]):
            raise AppError(409, "COMPANY_ALIAS_CONFLICT", "别名与公司标准名相同。")
        occupied = connection.execute(
            """
            SELECT id FROM fj_companies WHERE normalized_name = ?
            UNION ALL SELECT company_id AS id FROM fj_company_aliases WHERE normalized_alias = ?
            LIMIT 1
            """,
            (normalized, normalized),
        ).fetchone()
        if occupied is not None:
            raise AppError(409, "COMPANY_ALIAS_CONFLICT", "该公司名称或别名已被使用。")
        alias_id = new_id()
        now = utc_now()
        connection.execute(
            """
            INSERT INTO fj_company_aliases (id, company_id, alias_name, normalized_alias, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (alias_id, company_id, alias, normalized, now),
        )
        connection.execute(
            "UPDATE fj_companies SET version = version + 1, updated_at = ? WHERE id = ?",
            (now, company_id),
        )
        reconcile_job_companies(connection, source="manual")
        _mark_exclusions_stale(connection)
    return get_company(db, company_id)


def delete_company_alias(db: Database, company_id: str, alias_id: str) -> None:
    with db.connect() as connection:
        cursor = connection.execute(
            "DELETE FROM fj_company_aliases WHERE id = ? AND company_id = ?",
            (alias_id, company_id),
        )
        if cursor.rowcount == 0:
            raise AppError(404, "NOT_FOUND", "公司别名不存在。")
        connection.execute(
            "UPDATE fj_companies SET version = version + 1, updated_at = ? WHERE id = ?",
            (utc_now(), company_id),
        )
        reconcile_job_companies(connection, source="manual")
        _mark_exclusions_stale(connection)


def _serialize_company(
    connection: sqlite3.Connection,
    row: sqlite3.Row,
) -> dict[str, object]:
    aliases = connection.execute(
        "SELECT id, alias_name FROM fj_company_aliases WHERE company_id = ? ORDER BY created_at, id",
        (row["id"],),
    ).fetchall()
    stats = connection.execute(
        """
        SELECT COUNT(DISTINCT j.id) AS job_count,
               COUNT(DISTINCT CASE WHEN a.status = 'applied' THEN j.id END) AS applied_job_count,
               MAX(j.detail_collected_at) AS last_detail_at,
               MAX(e.created_at) AS last_evaluated_at,
               MAX(CASE WHEN a.status = 'applied' THEN a.applied_at END) AS last_applied_at
        FROM fj_boss_jobs j
        LEFT JOIN fj_job_applications a ON a.job_id = j.id
        LEFT JOIN fj_job_evaluations e ON e.job_id = j.id
        WHERE j.company_id = ?
        """,
        (row["id"],),
    ).fetchone()
    return {
        "id": row["id"],
        "canonical_name": row["canonical_name"],
        "normalized_name": row["normalized_name"],
        "company_type": row["company_type"],
        "classification_source": row["classification_source"],
        "notes": row["notes"],
        "is_blacklisted": bool(row["is_blacklisted"]),
        "blacklist_reason": row["blacklist_reason"],
        "blacklisted_at": row["blacklisted_at"],
        "version": int(row["version"]),
        "aliases": [{"id": item["id"], "alias_name": item["alias_name"]} for item in aliases],
        "job_count": int(stats["job_count"] or 0),
        "applied_job_count": int(stats["applied_job_count"] or 0),
        "last_detail_at": stats["last_detail_at"],
        "last_evaluated_at": stats["last_evaluated_at"],
        "last_applied_at": stats["last_applied_at"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _require_company(connection: sqlite3.Connection, company_id: str) -> sqlite3.Row:
    row = connection.execute("SELECT * FROM fj_companies WHERE id = ?", (company_id,)).fetchone()
    if row is None:
        raise AppError(404, "NOT_FOUND", "公司不存在。")
    return row


def _mark_exclusions_stale(connection: sqlite3.Connection) -> None:
    connection.execute(
        "UPDATE fj_filter_exclusion_states SET status = 'stale', updated_at = ?",
        (utc_now(),),
    )
