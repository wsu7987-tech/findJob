from __future__ import annotations

from pydantic import BaseModel


class RunResponse(BaseModel):
    run_id: str
    task_type: str
    status: str
    stage: str
    total_items: int
    succeeded_items: int
    failed_items: int
    skipped_items: int
    current_item_id: str | None
    current_item_label: str | None
    error_category: str | None
    error_message: str | None
    updated_at: str
    finished_at: str | None = None
    report_week_key: str | None = None
    linked_report_version_id: str | None = None


class RunResultSnapshotResponse(BaseModel):
    snapshot_id: str
    knowledge_item_id: str
    title: str
    final_category: str | None = None
    created_at: str
    markdown_path: str | None = None
    markdown_filename: str | None = None


class RunDetailResponse(RunResponse):
    result_snapshots: list[RunResultSnapshotResponse] = []


class RunListResponse(BaseModel):
    items: list[RunResponse]
    total: int


class SummaryRunCreateResponse(BaseModel):
    run_id: str
    status: str
    stage: str
