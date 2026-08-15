from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app.services.report import current_week_key


def _wait_for_latest_snapshot_id(
    sqlite_connection: sqlite3.Connection,
    *,
    after_count: int = 0,
    timeout_seconds: float = 5.0,
) -> str:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        snapshot_row = sqlite_connection.execute(
            """
            SELECT
              (SELECT COUNT(*) FROM item_result_snapshots) AS total,
              (
                SELECT id
                FROM item_result_snapshots
                ORDER BY created_at DESC, id DESC
                LIMIT 1
              ) AS id
            """
        ).fetchone()
        if snapshot_row is not None and int(snapshot_row["total"]) > after_count:
            return str(snapshot_row["id"])
        time.sleep(0.05)
    raise AssertionError("summary snapshot was not created in time")


def _seed_summary_snapshot(configured_client: TestClient, sqlite_connection: sqlite3.Connection) -> str:
    return _seed_named_summary_snapshot(
        configured_client,
        sqlite_connection,
        source_value="report-source",
        title="Report seed item",
        raw_text="This seeded item powers report and result endpoint tests.",
    )


def _seed_named_summary_snapshot(
    configured_client: TestClient,
    sqlite_connection: sqlite3.Connection,
    *,
    source_value: str,
    title: str,
    raw_text: str,
) -> str:
    before_count = sqlite_connection.execute(
        "SELECT COUNT(*) FROM item_result_snapshots"
    ).fetchone()[0]
    create_response = configured_client.post(
        "/api/pool/items",
        json={
            "source_type": "text",
            "source_value": source_value,
            "title": title,
            "raw_text": raw_text,
        },
    )
    pool_item = create_response.json()["item"]

    start_response = configured_client.post(
        "/api/summary/runs",
        json={"pool_ids": [pool_item["id"]]},
    )
    assert start_response.status_code == 201

    return _wait_for_latest_snapshot_id(sqlite_connection, after_count=before_count)


def test_report_endpoints_create_and_read_versions(
    configured_client: TestClient,
    configured_app_paths: dict[str, Path],
    sqlite_connection: sqlite3.Connection,
) -> None:
    snapshot_id = _seed_summary_snapshot(configured_client, sqlite_connection)

    precheck_response = configured_client.get("/api/report/precheck")

    assert precheck_response.status_code == 200
    precheck_payload = precheck_response.json()
    assert precheck_payload["week_key"] == current_week_key()
    assert current_week_key() in precheck_payload["available_week_keys"]
    assert precheck_payload["existing_versions"] == []
    assert precheck_payload["next_version"] == 1

    create_response = configured_client.post("/api/report/runs", json={})

    assert create_response.status_code == 201
    create_payload = create_response.json()
    assert create_payload["week_key"] == current_week_key()
    assert create_payload["version"] == 1

    list_response = configured_client.get(f"/api/reports/{current_week_key()}/versions")

    assert list_response.status_code == 200
    list_payload = list_response.json()
    assert list_payload["items"] == [
        {
            "week_key": current_week_key(),
            "version": 1,
            "generated_at": list_payload["items"][0]["generated_at"],
        }
    ]

    detail_response = configured_client.get(f"/api/reports/{current_week_key()}/versions/1")

    assert detail_response.status_code == 200
    detail_payload = detail_response.json()
    assert detail_payload["week_key"] == current_week_key()
    assert detail_payload["version"] == 1
    assert "周报" in detail_payload["markdown_content"]
    assert "证据充分的条目" in detail_payload["markdown_content"]
    assert "可引用结论" in detail_payload["markdown_content"]
    assert detail_payload["snapshot_payload"]["category_stats"]["general"] == 1
    assert detail_payload["snapshot_payload"]["source_distribution"]["text"] == 1
    assert detail_payload["snapshot_payload"]["evidence_citation_total"] >= 1
    assert detail_payload["snapshot_payload"]["grounded_claim_total"] >= 1
    assert detail_payload["snapshot_payload"]["items"] == [
        {
            "snapshot_id": snapshot_id,
            "title": "Report seed item",
            "final_category": "general",
            "created_at": detail_payload["snapshot_payload"]["items"][0]["created_at"],
            "evidence_citation_count": detail_payload["snapshot_payload"]["items"][0][
                "evidence_citation_count"
            ],
            "memory_context_count": detail_payload["snapshot_payload"]["items"][0][
                "memory_context_count"
            ],
            "grounded_claim_count": detail_payload["snapshot_payload"]["items"][0][
                "grounded_claim_count"
            ],
            "top_evidence_titles": detail_payload["snapshot_payload"]["items"][0][
                "top_evidence_titles"
            ],
            "top_grounded_claims": detail_payload["snapshot_payload"]["items"][0][
                "top_grounded_claims"
            ],
            "evidence_bundle": detail_payload["snapshot_payload"]["items"][0][
                "evidence_bundle"
            ],
        }
    ]
    assert detail_payload["snapshot_payload"]["items"][0]["evidence_citation_count"] >= 1
    assert detail_payload["snapshot_payload"]["items"][0]["memory_context_count"] >= 0
    assert detail_payload["snapshot_payload"]["items"][0]["grounded_claim_count"] >= 1
    assert detail_payload["snapshot_payload"]["items"][0]["top_evidence_titles"]
    assert detail_payload["snapshot_payload"]["items"][0]["top_grounded_claims"]
    assert detail_payload["snapshot_payload"]["items"][0]["evidence_bundle"]["citations"]
    assert detail_payload["snapshot_payload"]["items"][0]["evidence_bundle"]["grounded_claims"]
    assert detail_payload["snapshot_payload"]["items"][0]["evidence_bundle"]["summary_segments"]
    assert detail_payload["snapshot_payload"]["grounded_items"] == [
        {
            "snapshot_id": snapshot_id,
            "title": "Report seed item",
            "final_category": "general",
            "claim": detail_payload["snapshot_payload"]["grounded_items"][0]["claim"],
            "citation_ids": detail_payload["snapshot_payload"]["grounded_items"][0]["citation_ids"],
            "evidence_titles": detail_payload["snapshot_payload"]["grounded_items"][0]["evidence_titles"],
        }
    ]
    assert detail_payload["snapshot_payload"]["grounded_items"][0]["citation_ids"]
    assert detail_payload["snapshot_payload"]["grounded_items"][0]["evidence_titles"]
    assert Path(detail_payload["markdown_path"]).exists()
    assert Path(detail_payload["markdown_path"]).parent == configured_app_paths["report_output_dir"]

    report_run_response = configured_client.get("/api/runs?task_type=report")
    assert report_run_response.status_code == 200
    report_run = report_run_response.json()["items"][0]
    assert report_run["task_type"] == "report"
    assert report_run["status"] == "completed"
    assert report_run["report_week_key"] == current_week_key()
    assert report_run["linked_report_version_id"] == detail_payload["id"]


def test_report_precheck_tracks_existing_versions_after_multiple_runs(
    configured_client: TestClient,
    sqlite_connection: sqlite3.Connection,
) -> None:
    _seed_summary_snapshot(configured_client, sqlite_connection)

    first_create_response = configured_client.post("/api/report/runs", json={})
    second_create_response = configured_client.post("/api/report/runs", json={})
    precheck_response = configured_client.get("/api/report/precheck")
    list_response = configured_client.get(f"/api/reports/{current_week_key()}/versions")

    assert first_create_response.status_code == 201
    assert first_create_response.json()["version"] == 1
    assert second_create_response.status_code == 201
    assert second_create_response.json()["version"] == 2

    assert precheck_response.status_code == 200
    assert precheck_response.json()["existing_versions"] == [1, 2]
    assert precheck_response.json()["next_version"] == 3

    assert list_response.status_code == 200
    assert [item["version"] for item in list_response.json()["items"]] == [2, 1]


def test_report_detail_does_not_drift_when_historical_payload_needs_normalization(
    configured_client: TestClient,
    sqlite_connection: sqlite3.Connection,
) -> None:
    snapshot_id = _seed_summary_snapshot(configured_client, sqlite_connection)
    create_response = configured_client.post("/api/report/runs", json={})
    assert create_response.status_code == 201

    report_row = sqlite_connection.execute(
        """
        SELECT id, snapshot_payload
        FROM weekly_report_versions
        WHERE week_key = ? AND version = 1
        """,
        (current_week_key(),),
    ).fetchone()
    assert report_row is not None

    historical_payload = json.loads(str(report_row["snapshot_payload"]))
    historical_payload.pop("grounded_items", None)
    historical_payload.pop("grounded_claim_total", None)
    historical_payload["items"] = [
        {
            key: value
            for key, value in historical_payload["items"][0].items()
            if key != "evidence_bundle"
        }
    ]
    with configured_client.app.state.db.connect() as connection:
        connection.execute(
            "UPDATE weekly_report_versions SET snapshot_payload = ? WHERE id = ?",
            (json.dumps(historical_payload, ensure_ascii=False), report_row["id"]),
        )

    later_create_response = configured_client.post(
        "/api/pool/items",
        json={
            "source_type": "text",
            "source_value": "later-report-source",
            "title": "Later report item",
            "raw_text": "This snapshot should not backfill into historical report versions.",
        },
    )
    later_pool_item = later_create_response.json()["item"]
    configured_client.post(
        "/api/summary/runs",
        json={"pool_ids": [later_pool_item["id"]]},
    )

    detail_response = configured_client.get(f"/api/reports/{current_week_key()}/versions/1")

    assert detail_response.status_code == 200
    detail_payload = detail_response.json()
    assert len(detail_payload["snapshot_payload"]["items"]) == 1
    assert detail_payload["snapshot_payload"]["items"][0]["snapshot_id"] == snapshot_id
    assert detail_payload["snapshot_payload"]["items"][0]["title"] == "Report seed item"
    assert detail_payload["snapshot_payload"]["grounded_items"] == []
    assert detail_payload["snapshot_payload"]["grounded_claim_total"] == 0


def test_report_run_only_uses_snapshots_inside_requested_week(
    configured_client: TestClient,
    sqlite_connection: sqlite3.Connection,
) -> None:
    old_snapshot_id = _seed_named_summary_snapshot(
        configured_client,
        sqlite_connection,
        source_value="report-source-old",
        title="Old report seed item",
        raw_text="This seeded item belongs to the previous week.",
    )
    previous_week_timestamp = (
        datetime.now(timezone.utc) - timedelta(days=7)
    ).replace(hour=0, minute=0, second=0, microsecond=0).isoformat().replace("+00:00", "Z")
    with configured_client.app.state.db.connect() as connection:
        connection.execute(
            """
            UPDATE item_result_snapshots
            SET created_at = ?, edited_at = ?
            WHERE id = ?
            """,
            (previous_week_timestamp, previous_week_timestamp, old_snapshot_id),
        )
    previous_week_key = current_week_key(
        datetime.fromisoformat(previous_week_timestamp.replace("Z", "+00:00"))
    )

    current_snapshot_id = _seed_named_summary_snapshot(
        configured_client,
        sqlite_connection,
        source_value="report-source-current",
        title="Current report seed item",
        raw_text="This seeded item belongs to the current week.",
    )

    precheck_response = configured_client.get("/api/report/precheck")
    assert precheck_response.status_code == 200
    assert previous_week_key in precheck_response.json()["available_week_keys"]

    create_response = configured_client.post("/api/report/runs", json={})
    assert create_response.status_code == 201

    detail_response = configured_client.get(f"/api/reports/{current_week_key()}/versions/1")

    assert detail_response.status_code == 200
    detail_payload = detail_response.json()
    assert detail_payload["snapshot_payload"]["category_stats"]["general"] == 1
    assert [item["snapshot_id"] for item in detail_payload["snapshot_payload"]["items"]] == [
        current_snapshot_id
    ]
    assert detail_payload["snapshot_payload"]["items"][0]["title"] == "Current report seed item"
    assert all(
        item["snapshot_id"] != old_snapshot_id
        for item in detail_payload["snapshot_payload"]["grounded_items"]
    )


def test_report_precheck_returns_current_week_without_existing_reports(
    configured_client: TestClient,
) -> None:
    response = configured_client.get("/api/report/precheck")

    assert response.status_code == 200
    assert response.json() == {
        "week_key": current_week_key(),
        "available_week_keys": [current_week_key()],
        "existing_versions": [],
        "next_version": 1,
    }


def test_report_detail_returns_404_for_missing_version(
    configured_client: TestClient,
) -> None:
    response = configured_client.get(f"/api/reports/{current_week_key()}/versions/99")

    assert response.status_code == 404
    assert response.json() == {
        "error_category": "VALIDATION_FAILED",
        "error_message": "Report version not found.",
    }


def test_result_endpoints_read_update_and_save_feedback(
    configured_client: TestClient,
    sqlite_connection: sqlite3.Connection,
) -> None:
    snapshot_id = _seed_summary_snapshot(configured_client, sqlite_connection)

    detail_response = configured_client.get(f"/api/results/{snapshot_id}")

    assert detail_response.status_code == 200
    detail_payload = detail_response.json()
    assert detail_payload["id"] == snapshot_id
    assert detail_payload["generated_category"] == "general"
    assert detail_payload["generated_tags"]
    assert detail_payload["title"] == "Report seed item"
    assert detail_payload["markdown_path"]
    assert detail_payload["markdown_filename"].endswith(".md")
    assert "Report seed item" in detail_payload["markdown_content"]
    assert detail_payload["evidence_bundle"]["citations"]
    assert detail_payload["evidence_bundle"]["grounded_claims"]
    assert detail_payload["evidence_bundle"]["summary_segments"]
    assert detail_payload["evidence_bundle"]["grounded_claims"][0]["citation_ids"]
    assert detail_payload["summary_meta"]["one_sentence_takeaway"]
    assert detail_payload["summary_meta"]["key_points"]
    assert detail_payload["summary_meta"]["article_keywords"]
    assert detail_payload["summary_meta"]["reader_guide"]
    assert detail_payload["summary_meta"]["reader_guide"]["what_it_is"]
    assert detail_payload["summary_meta"]["reader_guide"]["why_it_matters"]
    assert detail_payload["summary_meta"]["reader_guide"]["core_concepts"]
    assert detail_payload["summary_meta"]["reader_guide"]["study_path"]
    original_edited_at = detail_payload["edited_at"]

    patch_response = configured_client.patch(
        f"/api/results/{snapshot_id}",
        json={
            "final_category": "ops",
            "final_tags": ["agent", "workflow"],
        },
    )

    assert patch_response.status_code == 200
    patch_payload = patch_response.json()
    assert patch_payload["final_category"] == "ops"
    assert patch_payload["final_tags"] == ["agent", "workflow"]
    assert patch_payload["summary_text"] == detail_payload["summary_text"]
    assert patch_payload["edited_at"] >= original_edited_at

    feedback_response = configured_client.post(
        f"/api/results/{snapshot_id}/feedback",
        json={"feedback_value": "useful"},
    )

    assert feedback_response.status_code == 200
    assert feedback_response.json() == {"saved": True}

    feedback_row = sqlite_connection.execute(
        """
        SELECT feedback_value
        FROM summary_feedback
        WHERE result_snapshot_id = ?
        """,
        (snapshot_id,),
    ).fetchone()
    assert feedback_row is not None
    assert feedback_row["feedback_value"] == "useful"


def test_result_patch_preserves_unspecified_fields_and_feedback_is_idempotent(
    configured_client: TestClient,
    sqlite_connection: sqlite3.Connection,
) -> None:
    snapshot_id = _seed_summary_snapshot(configured_client, sqlite_connection)
    original_detail = configured_client.get(f"/api/results/{snapshot_id}").json()

    patch_response = configured_client.patch(
        f"/api/results/{snapshot_id}",
        json={"final_category": "research"},
    )
    first_feedback_response = configured_client.post(
        f"/api/results/{snapshot_id}/feedback",
        json={"feedback_value": "useful"},
    )
    second_feedback_response = configured_client.post(
        f"/api/results/{snapshot_id}/feedback",
        json={"feedback_value": "useless"},
    )

    assert patch_response.status_code == 200
    assert patch_response.json()["final_category"] == "research"
    assert patch_response.json()["final_tags"] == original_detail["final_tags"]
    assert patch_response.json()["summary_text"] == original_detail["summary_text"]
    assert patch_response.json()["generated_tags"] == original_detail["generated_tags"]

    assert first_feedback_response.status_code == 200
    assert second_feedback_response.status_code == 200
    assert first_feedback_response.json() == {"saved": True}
    assert second_feedback_response.json() == {"saved": True}

    feedback_row = sqlite_connection.execute(
        """
        SELECT feedback_value, COUNT(*) OVER () AS total_rows
        FROM summary_feedback
        WHERE result_snapshot_id = ?
        """,
        (snapshot_id,),
    ).fetchone()
    assert feedback_row is not None
    assert feedback_row["feedback_value"] == "useless"
    assert feedback_row["total_rows"] == 1


def test_result_detail_returns_404_for_missing_snapshot(
    configured_client: TestClient,
) -> None:
    response = configured_client.get("/api/results/missing-snapshot")

    assert response.status_code == 404
    assert response.json() == {
        "error_category": "VALIDATION_FAILED",
        "error_message": "Result snapshot not found.",
    }


def test_result_feedback_rejects_invalid_value(
    configured_client: TestClient,
    sqlite_connection: sqlite3.Connection,
) -> None:
    snapshot_id = _seed_summary_snapshot(configured_client, sqlite_connection)

    response = configured_client.post(
        f"/api/results/{snapshot_id}/feedback",
        json={"feedback_value": "maybe"},
    )

    assert response.status_code == 422


def test_report_run_uses_langgraph_execution_path(
    configured_client: TestClient,
    sqlite_connection: sqlite3.Connection,
    monkeypatch,
) -> None:
    from backend.app.services import report as report_service

    _seed_summary_snapshot(configured_client, sqlite_connection)
    captured_states: list[dict[str, object]] = []

    class DummyGraph:
        def invoke(self, state: dict[str, object]) -> dict[str, object]:
            captured_states.append(state)
            return {
                "run_id": "report-run-graph",
                "week_key": current_week_key(),
                "version": 7,
            }

    monkeypatch.setattr(report_service, "build_report_graph", lambda *_args, **_kwargs: DummyGraph())

    response = configured_client.post("/api/report/runs", json={})

    assert response.status_code == 201
    assert response.json() == {
        "run_id": "report-run-graph",
        "week_key": current_week_key(),
        "version": 7,
    }
    assert captured_states
    assert captured_states[0]["week_key"] == current_week_key()
    assert "config_snapshot" in captured_states[0]


def test_build_report_graph_returns_compiled_graph() -> None:
    from backend.app.graphs.report_graph import build_report_graph

    graph = build_report_graph(db=object())

    assert hasattr(graph, "invoke")
