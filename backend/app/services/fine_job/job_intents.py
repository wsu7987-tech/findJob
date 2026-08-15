from __future__ import annotations

import json

from backend.app.db import Database
from backend.app.schemas.fine_job.job_intents import FineJobIntentPayload
from backend.app.utils import utc_now


DEFAULT_INTENT_ID = "default"


def get_job_intent(db: Database) -> dict[str, object] | None:
    with db.connect() as connection:
        row = connection.execute(
            """
            SELECT id, target_title, cities_json, keywords_json, expanded_keywords_json,
                   excluded_keywords_json, salary_min, salary_max, work_mode, notes,
                   created_at, updated_at
            FROM fj_job_intents
            WHERE id = ?
            """,
            (DEFAULT_INTENT_ID,),
        ).fetchone()
    if row is None:
        return None
    return _serialize_intent(row)


def save_job_intent(db: Database, payload: FineJobIntentPayload) -> dict[str, object]:
    now = utc_now()
    existing = get_job_intent(db)
    created_at = str(existing["created_at"]) if existing else now
    with db.connect() as connection:
        connection.execute(
            """
            INSERT INTO fj_job_intents (
              id, target_title, cities_json, keywords_json, expanded_keywords_json,
              excluded_keywords_json, salary_min, salary_max, work_mode, notes,
              created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              target_title = excluded.target_title,
              cities_json = excluded.cities_json,
              keywords_json = excluded.keywords_json,
              expanded_keywords_json = excluded.expanded_keywords_json,
              excluded_keywords_json = excluded.excluded_keywords_json,
              salary_min = excluded.salary_min,
              salary_max = excluded.salary_max,
              work_mode = excluded.work_mode,
              notes = excluded.notes,
              updated_at = excluded.updated_at
            """,
            (
                DEFAULT_INTENT_ID,
                payload.target_title.strip(),
                _dump_string_list(payload.cities),
                _dump_string_list(payload.keywords),
                _dump_string_list(payload.expanded_keywords),
                _dump_string_list(payload.excluded_keywords),
                payload.salary_min,
                payload.salary_max,
                payload.work_mode,
                payload.notes.strip(),
                created_at,
                now,
            ),
        )
    intent = get_job_intent(db)
    assert intent is not None
    return intent


def _serialize_intent(row) -> dict[str, object]:
    intent = {
        "id": row["id"],
        "target_title": row["target_title"],
        "cities": _load_string_list(row["cities_json"]),
        "keywords": _load_string_list(row["keywords_json"]),
        "expanded_keywords": _load_string_list(row["expanded_keywords_json"]),
        "excluded_keywords": _load_string_list(row["excluded_keywords_json"]),
        "salary_min": row["salary_min"],
        "salary_max": row["salary_max"],
        "work_mode": row["work_mode"],
        "notes": row["notes"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
    return {**intent, "ready": _is_ready(intent)}


def _is_ready(intent: dict[str, object]) -> bool:
    return bool(
        str(intent.get("target_title") or "").strip()
        and intent.get("cities")
        and intent.get("keywords")
    )


def _dump_string_list(values: list[str]) -> str:
    cleaned = []
    seen = set()
    for value in values:
        text = str(value).strip()
        if not text or text in seen:
            continue
        cleaned.append(text)
        seen.add(text)
    return json.dumps(cleaned, ensure_ascii=False)


def _load_string_list(value: str | None) -> list[str]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed if str(item).strip()]
