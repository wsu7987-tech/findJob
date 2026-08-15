from __future__ import annotations

import sqlite3
import time

import pytest
from fastapi.testclient import TestClient


def _wait_for_snapshot_row(
    sqlite_connection: sqlite3.Connection,
    *,
    timeout_seconds: float = 5.0,
):
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        row = sqlite_connection.execute(
            """
            SELECT summary_text, generated_category, qdrant_point_id
            FROM item_result_snapshots
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """
        ).fetchone()
        if row is not None:
            return row
        time.sleep(0.05)
    raise AssertionError("summary snapshot was not created in time")


@pytest.mark.parametrize(
    ("source_type", "source_value", "title", "raw_text"),
    [
        ("url", "https://example.com/article", "Example article", "Fetched article body."),
        ("pdf", "D:/fixtures/example.pdf", "Example PDF", "Extracted pdf text."),
        ("markdown", "D:/fixtures/example.md", "Example Markdown", "# Example\n\nMarkdown body."),
        ("text", "manual-note", "Example Text", "Plain text body."),
    ],
)
def test_pool_accepts_all_declared_source_types(
    configured_client: TestClient,
    source_type: str,
    source_value: str,
    title: str,
    raw_text: str,
) -> None:
    response = configured_client.post(
        "/api/pool/items",
        json={
            "source_type": source_type,
            "source_value": source_value,
            "title": title,
            "raw_text": raw_text,
        },
    )

    assert response.status_code == 201
    payload = response.json()["item"]
    assert payload["source_type"] == source_type
    assert payload["source_value"] == source_value
    assert payload["title"] == title
    assert payload["current_status"] == "pending"


@pytest.mark.parametrize(
    ("source_type", "source_value", "raw_text"),
    [
        ("url", "https://example.com/summary", "Normalized URL content."),
        ("pdf", "D:/fixtures/summary.pdf", "Normalized PDF content."),
        ("markdown", "D:/fixtures/summary.md", "# Parsed Markdown"),
    ],
)
def test_summary_run_uses_normalized_raw_text_when_it_is_already_available(
    configured_client: TestClient,
    sqlite_connection: sqlite3.Connection,
    source_type: str,
    source_value: str,
    raw_text: str,
) -> None:
    create_response = configured_client.post(
        "/api/pool/items",
        json={
            "source_type": source_type,
            "source_value": source_value,
            "title": f"{source_type} source",
            "raw_text": raw_text,
        },
    )
    pool_item = create_response.json()["item"]

    run_response = configured_client.post(
        "/api/summary/runs",
        json={"pool_ids": [pool_item["id"]]},
    )

    assert run_response.status_code == 201
    assert run_response.json()["status"] == "pending"

    snapshot_row = _wait_for_snapshot_row(sqlite_connection)

    assert snapshot_row is not None
    assert snapshot_row["summary_text"] == raw_text
    assert snapshot_row["generated_category"] == "general"
    assert snapshot_row["qdrant_point_id"]


def test_pdf_markdown_and_text_items_complete_stub_delivery_chain(
    configured_client: TestClient,
    sqlite_connection: sqlite3.Connection,
) -> None:
    scenarios = [
        {
            "source_type": "pdf",
            "source_value": "D:/fixtures/delivery.pdf",
            "title": "Delivery PDF",
            "raw_text": "Normalized PDF content for acceptance delivery.",
        },
        {
            "source_type": "markdown",
            "source_value": "D:/fixtures/delivery.md",
            "title": "Delivery Markdown",
            "raw_text": "# Delivery Markdown\n\nNormalized markdown content.",
        },
        {
            "source_type": "text",
            "source_value": "delivery-text",
            "title": "Delivery Text",
            "raw_text": "Normalized text content for acceptance delivery.",
        },
    ]

    created_items = []
    for scenario in scenarios:
        response = configured_client.post("/api/pool/items", json=scenario)
        assert response.status_code == 201
        created_items.append(response.json()["item"])

    precheck_response = configured_client.get("/api/summary/precheck")
    assert precheck_response.status_code == 200
    precheck_ids = {item["id"] for item in precheck_response.json()["items"]}
    assert {item["id"] for item in created_items}.issubset(precheck_ids)

    run_response = configured_client.post(
        "/api/summary/runs",
        json={"pool_ids": [item["id"] for item in created_items]},
    )
    assert run_response.status_code == 201
    assert run_response.json()["status"] == "pending"

    deadline = time.time() + 5.0
    while time.time() < deadline:
        total = sqlite_connection.execute(
            "SELECT COUNT(*) FROM item_result_snapshots"
        ).fetchone()[0]
        if total >= len(created_items):
            break
        time.sleep(0.05)
    else:
        raise AssertionError("summary snapshots were not created in time")

    report_create = configured_client.post("/api/report/runs", json={})
    assert report_create.status_code == 201
    report_week_key = report_create.json()["week_key"]
    report_version = report_create.json()["version"]

    report_detail = configured_client.get(
        f"/api/reports/{report_week_key}/versions/{report_version}"
    )
    assert report_detail.status_code == 200
    payload = report_detail.json()

    assert payload["snapshot_payload"]["source_distribution"]["pdf"] >= 1
    assert payload["snapshot_payload"]["source_distribution"]["markdown"] >= 1
    assert payload["snapshot_payload"]["source_distribution"]["text"] >= 1

    report_items = {
        item["title"]: item["snapshot_id"] for item in payload["snapshot_payload"]["items"]
    }

    for scenario in scenarios:
        snapshot_id = report_items[scenario["title"]]
        result_detail = configured_client.get(f"/api/results/{snapshot_id}")

        assert result_detail.status_code == 200
        assert result_detail.json()["summary_text"] == scenario["raw_text"]

    feedback_target = report_items["Delivery Text"]
    patch_response = configured_client.patch(
        f"/api/results/{feedback_target}",
        json={"final_category": "acceptance", "final_tags": ["delivery", "stub"]},
    )
    feedback_response = configured_client.post(
        f"/api/results/{feedback_target}/feedback",
        json={"feedback_value": "useful"},
    )

    assert patch_response.status_code == 200
    assert patch_response.json()["final_category"] == "acceptance"
    assert patch_response.json()["final_tags"] == ["delivery", "stub"]
    assert feedback_response.status_code == 200
    assert feedback_response.json() == {"saved": True}

    stored_feedback = sqlite_connection.execute(
        """
        SELECT feedback_value
        FROM summary_feedback
        WHERE result_snapshot_id = ?
        """,
        (feedback_target,),
    ).fetchone()
    assert stored_feedback is not None
    assert stored_feedback["feedback_value"] == "useful"
