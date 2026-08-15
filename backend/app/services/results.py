from __future__ import annotations

import json
from pathlib import Path

from backend.app.db import Database
from backend.app.errors import AppError
from backend.app.schemas.results import ResultPatchRequest
from backend.app.services.evidence_bundle import build_evidence_bundle
from backend.app.utils import new_id, utc_now


def get_result(db: Database, snapshot_id: str) -> dict[str, object]:
    with db.connect() as connection:
        row = connection.execute(
            """
            SELECT
              s.id,
              s.knowledge_item_id,
              s.summary_run_id,
              ki.title,
              ki.source_type,
              ki.source_value,
              s.generated_category,
              s.generated_tags,
              s.final_category,
              s.final_tags,
              s.summary_text,
              s.viewpoint_text,
              s.controversy_text,
              s.quality_meta,
              s.relation_meta,
              s.markdown_path,
              s.created_at,
              s.edited_at
            FROM item_result_snapshots AS s
            JOIN knowledge_items AS ki ON ki.id = s.knowledge_item_id
            WHERE s.id = ?
            """,
            (snapshot_id,),
        ).fetchone()

    if row is None:
        raise AppError(
            status_code=404,
            error_category="VALIDATION_FAILED",
            error_message="Result snapshot not found.",
        )

    return _serialize_result_row(row)


def update_result(
    db: Database,
    snapshot_id: str,
    payload: ResultPatchRequest,
) -> dict[str, object]:
    updates = payload.model_dump(exclude_unset=True)
    current = get_result(db, snapshot_id)

    final_category = (
        updates["final_category"] if "final_category" in updates else current["final_category"]
    )
    final_tags = updates["final_tags"] if "final_tags" in updates else current["final_tags"]
    edited_at = utc_now()

    with db.connect() as connection:
        cursor = connection.execute(
            """
            UPDATE item_result_snapshots
            SET final_category = ?, final_tags = ?, edited_at = ?
            WHERE id = ?
            """,
            (
                final_category,
                json.dumps(final_tags or [], ensure_ascii=False),
                edited_at,
                snapshot_id,
            ),
        )
        if cursor.rowcount == 0:
            raise AppError(
                status_code=404,
                error_category="VALIDATION_FAILED",
                error_message="Result snapshot not found.",
            )

    return get_result(db, snapshot_id)


def save_feedback(db: Database, snapshot_id: str, feedback_value: str) -> dict[str, object]:
    get_result(db, snapshot_id)
    now = utc_now()

    with db.connect() as connection:
        connection.execute(
            """
            INSERT INTO summary_feedback (
              id, result_snapshot_id, feedback_value, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(result_snapshot_id) DO UPDATE SET
              feedback_value = excluded.feedback_value,
              updated_at = excluded.updated_at
            """,
            (new_id(), snapshot_id, feedback_value, now, now),
        )

    return {"saved": True}


def _serialize_result_row(row) -> dict[str, object]:
    return {
        "id": row["id"],
        "knowledge_item_id": row["knowledge_item_id"],
        "summary_run_id": row["summary_run_id"],
        "title": row["title"],
        "source_type": row["source_type"],
        "source_value": row["source_value"],
        "generated_category": row["generated_category"],
        "generated_tags": _parse_string_list(row["generated_tags"]),
        "final_category": row["final_category"],
        "final_tags": _parse_string_list(row["final_tags"]),
        "summary_text": row["summary_text"],
        "viewpoint_text": row["viewpoint_text"],
        "controversy_text": row["controversy_text"],
        "evidence_bundle": build_evidence_bundle(row["relation_meta"]),
        "summary_meta": _build_summary_meta(row),
        "relation_meta": _parse_json_object(row["relation_meta"]),
        "markdown_path": row["markdown_path"],
        "markdown_filename": Path(str(row["markdown_path"])).name if row["markdown_path"] else None,
        "markdown_content": _read_markdown_content(row["markdown_path"]),
        "created_at": row["created_at"],
        "edited_at": row["edited_at"],
    }


def _parse_string_list(value: object) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, str):
        parsed = json.loads(value)
        if isinstance(parsed, list):
            return [str(item) for item in parsed]
    return []


def _read_markdown_content(path_value: object) -> str | None:
    if path_value in (None, ""):
        return None
    path = Path(str(path_value))
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


def _build_summary_meta(row) -> dict[str, object]:
    evidence_bundle = build_evidence_bundle(row["relation_meta"])
    quality_meta = _parse_json_object(row["quality_meta"]) or {}
    generated_tags = _parse_string_list(row["generated_tags"])
    final_tags = _parse_string_list(row["final_tags"])
    tags = final_tags or generated_tags
    article_keywords = quality_meta.get("keywords")
    if not isinstance(article_keywords, list) or not article_keywords:
        article_keywords = [
            {"keyword": tag, "weight": round(max(0.2, 1 - (index * 0.12)), 2)}
            for index, tag in enumerate(tags[:8])
        ]
    reading_focus = quality_meta.get("reading_focus")
    if not isinstance(reading_focus, list) or not reading_focus:
        reading_focus = [
            str(segment.get("text") or "").strip()
            for segment in evidence_bundle.get("summary_segments", [])[:3]
            if str(segment.get("text") or "").strip()
        ]
    key_points = quality_meta.get("key_points")
    if not isinstance(key_points, list) or not key_points:
        key_points = [
            str(claim.get("claim") or "").strip()
            for claim in evidence_bundle.get("grounded_claims", [])[:5]
            if str(claim.get("claim") or "").strip()
        ]
    reader_guide = quality_meta.get("reader_guide")
    if not isinstance(reader_guide, dict):
        reader_guide = {}
    reader_guide = {
        "what_it_is": str(reader_guide.get("what_it_is") or row["summary_text"] or "").strip(),
        "why_it_matters": str(reader_guide.get("why_it_matters") or row["viewpoint_text"] or row["summary_text"] or "").strip(),
        "how_to_apply": [
            str(item).strip()
            for item in (reader_guide.get("how_to_apply") or quality_meta.get("methods_or_process") or [])
            if str(item).strip()
        ],
        "core_concepts": [
            str(item).strip()
            for item in (reader_guide.get("core_concepts") or key_points[:4])
            if str(item).strip()
        ],
        "study_path": [
            str(item).strip()
            for item in (reader_guide.get("study_path") or reading_focus[:3])
            if str(item).strip()
        ],
    }
    return {
        "one_sentence_takeaway": quality_meta.get("one_sentence_takeaway"),
        "article_keywords": article_keywords,
        "reading_focus": reading_focus,
        "key_points": key_points,
        "reader_guide": reader_guide,
        "methods_or_process": quality_meta.get("methods_or_process") or [],
        "pitfalls_or_limits": quality_meta.get("pitfalls_or_limits") or [],
        "code_examples": quality_meta.get("code_examples") or [],
        "prompt_variant": quality_meta.get("prompt_variant"),
    }


def _parse_json_object(value: object) -> dict[str, object] | None:
    if value in (None, ""):
        return None
    if isinstance(value, str):
        parsed = json.loads(value)
        if isinstance(parsed, dict):
            return parsed
    return None
