from __future__ import annotations

from typing import TypedDict


class ReportGraphState(TypedDict, total=False):
    run_id: str
    report_version_id: str
    week_key: str
    version: int
    started_at: str
    snapshot_rows: list[dict[str, object]]
    snapshot_payload: dict[str, object]
    markdown_content: str
    markdown_path: str
    generated_at: str
    config_snapshot: dict[str, object]
