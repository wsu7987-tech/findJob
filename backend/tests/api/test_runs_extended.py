from __future__ import annotations

import time

from fastapi.testclient import TestClient


def _wait_for_completed_run(
    configured_client: TestClient,
    run_id: str,
    *,
    timeout_seconds: float = 5.0,
) -> dict[str, object]:
    deadline = time.time() + timeout_seconds
    last_payload: dict[str, object] | None = None
    while time.time() < deadline:
        response = configured_client.get(f"/api/runs/{run_id}")
        assert response.status_code == 200
        last_payload = response.json()
        if last_payload["status"] == "completed":
            return last_payload
        time.sleep(0.05)
    raise AssertionError(f"run {run_id} did not complete in time: {last_payload}")


def test_runs_list_returns_summary_runs(
    configured_client: TestClient,
) -> None:
    create_response = configured_client.post(
        "/api/pool/items",
        json={
            "source_type": "text",
            "source_value": "runs-list-item",
            "title": "Runs list item",
            "raw_text": "Used to verify /api/runs list output.",
        },
    )
    pool_item = create_response.json()["item"]

    start_response = configured_client.post(
        "/api/summary/runs",
        json={"pool_ids": [pool_item["id"]]},
    )
    run_id = start_response.json()["run_id"]

    _wait_for_completed_run(configured_client, run_id)

    response = configured_client.get("/api/runs?task_type=summary&status=completed")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert payload["items"][0]["run_id"] == run_id
    assert payload["items"][0]["task_type"] == "summary"
    assert payload["items"][0]["status"] == "completed"


def test_runs_list_orders_by_started_at_desc_then_id_desc_and_filters(
    configured_client: TestClient,
) -> None:
    with configured_client.app.state.db.connect() as connection:
        connection.execute(
            """
            INSERT INTO run_records (
              id, task_type, status, stage, started_at, finished_at, total_items,
              succeeded_items, failed_items, skipped_items
            )
            VALUES
              ('run-older', 'summary', 'completed', 'completed', '2026-04-14T00:00:00Z', '2026-04-14T00:05:00Z', 1, 1, 0, 0),
              ('run-2', 'report', 'completed', 'completed', '2026-04-15T00:00:00Z', '2026-04-15T00:02:00Z', 2, 2, 0, 0),
              ('run-1', 'summary', 'running', 'summarizing', '2026-04-15T00:00:00Z', NULL, 3, 1, 0, 0)
            """
        )

    response = configured_client.get("/api/runs")
    filtered_response = configured_client.get("/api/runs?task_type=report&status=completed")

    assert response.status_code == 200
    assert response.json()["total"] == 3
    assert [item["run_id"] for item in response.json()["items"]] == ["run-2", "run-1", "run-older"]

    assert filtered_response.status_code == 200
    assert filtered_response.json() == {
        "items": [
            {
                "run_id": "run-2",
                "task_type": "report",
                "status": "completed",
                "stage": "completed",
                "total_items": 2,
                "succeeded_items": 2,
                "failed_items": 0,
                "skipped_items": 0,
                "current_item_id": None,
                "current_item_label": None,
                "error_category": None,
                "error_message": None,
                "updated_at": "2026-04-15T00:02:00Z",
                "finished_at": "2026-04-15T00:02:00Z",
                "report_week_key": None,
                "linked_report_version_id": None,
            }
        ],
        "total": 1,
    }


def test_cancel_run_marks_running_run_cancelled(
    configured_client: TestClient,
    sqlite_connection,
) -> None:
    with configured_client.app.state.db.connect() as connection:
        connection.execute(
            """
            INSERT INTO run_records (
              id, task_type, status, stage, started_at, total_items,
              succeeded_items, failed_items, skipped_items
            )
            VALUES ('running-run-1', 'summary', 'running', 'summarizing', '2026-04-14T00:00:00Z', 3, 1, 0, 0)
            """
        )

    response = configured_client.post("/api/runs/running-run-1/cancel")

    assert response.status_code == 200
    payload = response.json()
    assert payload["run_id"] == "running-run-1"
    assert payload["status"] == "cancelled"
    assert payload["stage"] == "cancelled"
    assert payload["error_category"] == "CANCELLED"
    assert payload["error_message"] == "Run cancelled by user."
    assert payload["finished_at"]

    stored_row = sqlite_connection.execute(
        "SELECT status, stage, cancel_requested FROM run_records WHERE id = 'running-run-1'"
    ).fetchone()
    assert stored_row["status"] == "cancelled"
    assert stored_row["stage"] == "cancelled"
    assert stored_row["cancel_requested"] == 1


def test_cancel_run_returns_404_for_missing_run(
    configured_client: TestClient,
) -> None:
    response = configured_client.post("/api/runs/missing-run/cancel")

    assert response.status_code == 404
    assert response.json() == {
        "error_category": "VALIDATION_FAILED",
        "error_message": "Run not found.",
    }


def test_cancel_run_keeps_terminal_run_unchanged(
    configured_client: TestClient,
    sqlite_connection,
) -> None:
    with configured_client.app.state.db.connect() as connection:
        connection.execute(
            """
            INSERT INTO run_records (
              id, task_type, status, stage, started_at, finished_at, total_items,
              succeeded_items, failed_items, skipped_items, error_category, error_message
            )
            VALUES (
              'completed-run-1', 'summary', 'completed', 'completed',
              '2026-04-14T00:00:00Z', '2026-04-14T00:01:00Z', 1, 1, 0, 0, NULL, NULL
            )
            """
        )

    response = configured_client.post("/api/runs/completed-run-1/cancel")

    assert response.status_code == 200
    assert response.json()["run_id"] == "completed-run-1"
    assert response.json()["status"] == "completed"
    assert response.json()["stage"] == "completed"
    assert response.json()["finished_at"] == "2026-04-14T00:01:00Z"

    stored_row = sqlite_connection.execute(
        """
        SELECT status, stage, finished_at, cancel_requested
        FROM run_records
        WHERE id = 'completed-run-1'
        """
    ).fetchone()
    assert stored_row["status"] == "completed"
    assert stored_row["stage"] == "completed"
    assert stored_row["finished_at"] == "2026-04-14T00:01:00Z"
    assert stored_row["cancel_requested"] == 0
