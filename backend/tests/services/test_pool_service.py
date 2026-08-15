from __future__ import annotations

from backend.app.config import load_config
from backend.app.schemas.pool import PoolItemCreateRequest
from backend.app.services.pool import create_pool_item, fetch_pool_entries_for_summary


def test_pool_service_keeps_capture_metadata_structured(test_db) -> None:
    config = load_config()
    item = create_pool_item(
        test_db,
        config,
        PoolItemCreateRequest(
            source_type="text",
            source_value="quick-capture-service",
            title="Inbox note",
            raw_text="Body text",
            capture_source="manual",
            captured_at="2026-04-21T10:30:00+08:00",
            category="Research",
            tags=["tag-a", "tag-b"],
        ),
    )

    with test_db.connect() as connection:
        summary_row = fetch_pool_entries_for_summary(connection, [item["id"]])[0]
        stored_row = connection.execute(
            """
            SELECT capture_source, captured_at, capture_category, capture_tags_json, raw_content
            FROM knowledge_items
            WHERE id = ?
            """,
            (item["knowledge_item_id"],),
        ).fetchone()

    assert summary_row["raw_content"] == "Body text"
    assert stored_row["capture_source"] == "manual"
    assert stored_row["captured_at"] == "2026-04-21T10:30:00+08:00"
    assert stored_row["capture_category"] == "Research"
    assert stored_row["capture_tags_json"] == '["tag-a", "tag-b"]'
    assert stored_row["raw_content"] == "Body text"
