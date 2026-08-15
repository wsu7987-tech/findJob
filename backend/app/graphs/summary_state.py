from __future__ import annotations

from typing import TypedDict


class SummaryGraphState(TypedDict, total=False):
    run_id: str
    started_at: str
    run_record_created: bool
    pool_ids: list[str]
    pending_pool_ids: list[str]
    items: list[dict[str, object]]
    current_pool_id: str | None
    current_item_id: str | None
    current_item_label: str | None
    current_item: dict[str, object] | None
    retrieval_query: str | None
    memory_related_items: list[dict[str, object]]
    prompt_related_items: list[dict[str, object]]
    evidence_citations: list[dict[str, object]]
    summary_payload: dict[str, object] | None
    summary_vector: list[float] | None
    related_items: list[dict[str, object]]
    succeeded_items: int
    failed_items: int
    skipped_items: int
    status: str
    stage: str
    error_category: str | None
    error_message: str | None
    config_snapshot: dict[str, object]
