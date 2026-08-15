from __future__ import annotations

import json
from pathlib import Path

from backend.app.graphs.report_state import ReportGraphState


def bootstrap_report_run(state: ReportGraphState) -> ReportGraphState:
    from backend.app.services.report import current_week_key
    from backend.app.utils import new_id, utc_now

    return {
        "run_id": str(state.get("run_id") or new_id()),
        "report_version_id": str(state.get("report_version_id") or new_id()),
        "week_key": str(state.get("week_key") or current_week_key()),
        "started_at": str(state.get("started_at") or utc_now()),
    }


def load_report_inputs(state: ReportGraphState, *, db) -> ReportGraphState:
    from backend.app.services.report import _list_existing_versions, _load_report_snapshot_rows

    week_key = str(state["week_key"])
    versions = _list_existing_versions(db, week_key)
    version = (max(versions) + 1) if versions else 1
    snapshot_rows = _load_report_snapshot_rows(db, week_key)
    return {
        "version": version,
        "snapshot_rows": [dict(row) for row in snapshot_rows],
    }


def build_report_artifacts(state: ReportGraphState) -> ReportGraphState:
    from backend.app.services.report import _build_markdown_content, _build_snapshot_payload

    snapshot_rows = list(state.get("snapshot_rows", []))
    snapshot_payload = _build_snapshot_payload(snapshot_rows)
    markdown_content = _build_markdown_content(
        week_key=str(state["week_key"]),
        version=int(state["version"]),
        snapshot_rows=snapshot_rows,
        snapshot_payload=snapshot_payload,
    )
    return {
        "snapshot_payload": snapshot_payload,
        "markdown_content": markdown_content,
    }


def persist_report_run(state: ReportGraphState, *, db) -> ReportGraphState:
    from backend.app.utils import utc_now

    config_snapshot = dict(state["config_snapshot"])
    report_output_dir = Path(str(config_snapshot["report_output_dir"]))
    week_key = str(state["week_key"])
    version = int(state["version"])
    markdown_content = str(state["markdown_content"])
    finished_at = utc_now()
    markdown_path = report_output_dir / f"{week_key}-v{version}.md"
    report_output_dir.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text(markdown_content, encoding="utf-8")

    with db.connect() as connection:
        connection.execute(
            """
            INSERT INTO run_records (
              id, task_type, status, stage, started_at, finished_at,
              total_items, succeeded_items, failed_items, skipped_items,
              report_week_key, linked_report_version_id
            )
            VALUES (?, 'report', 'completed', 'completed', ?, ?, ?, ?, 0, 0, ?, ?)
            """,
            (
                str(state["run_id"]),
                str(state["started_at"]),
                finished_at,
                len(state.get("snapshot_rows", [])),
                len(state.get("snapshot_rows", [])),
                week_key,
                str(state["report_version_id"]),
            ),
        )
        connection.execute(
            """
            INSERT INTO weekly_report_versions (
              id, week_key, version, report_run_id, markdown_content,
              snapshot_payload, markdown_path, item_count, generated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(state["report_version_id"]),
                week_key,
                version,
                str(state["run_id"]),
                markdown_content,
                json.dumps(state["snapshot_payload"], ensure_ascii=False),
                str(markdown_path),
                len(state.get("snapshot_rows", [])),
                finished_at,
            ),
        )

    return {
        "markdown_path": str(markdown_path),
        "generated_at": finished_at,
    }
