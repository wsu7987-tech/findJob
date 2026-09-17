from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

from backend.app.errors import AppError
from backend.app.services.fine_job import smart_captures
from backend.app.services.fine_job import workflow_children
from backend.app.services.fine_job import workflow_runs
from backend.app.services.fine_job.boss_capture_history import create_capture_batch
from backend.app.services.fine_job.boss_scraper.service import BossBrowserStatus


def _payload() -> dict[str, object]:
    return {
        "filter_strategy_id": "strategy-1",
        "allowed_search_keywords": ["Python"],
        "allowed_cities": ["上海"],
        "candidate_target_count": 15,
        "pages": 1,
    }


def _fake_smart_batch(batch_id: str = "smart-batch-1") -> dict[str, object]:
    return {"id": batch_id}


def _create_capture(test_db, *, status: str = "pending", capture_id: str | None = None):
    capture = smart_captures.create_smart_capture(
        test_db,
        source="boss_capture",
        workflow_run_id=None,
        search_config=_payload(),
        target_count=15,
    )
    if status != "pending":
        with test_db.connect() as connection:
            connection.execute(
                "UPDATE fj_smart_captures SET status = ?, stage = ? WHERE id = ?",
                (status, status, str(capture["smart_capture_id"])),
            )
    return capture


def test_smart_capture_router_is_registered(configured_client) -> None:
    response = configured_client.get("/api/fine-job/smart-captures/current")

    assert response.status_code == 200
    assert response.json() == {"smart_capture": None}


def test_create_smart_capture_occupies_current_and_exposes_snapshot_contract(
    configured_client,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "backend.app.services.fine_job.smart_captures.boss_scraper_service.get_browser_status",
        lambda: BossBrowserStatus(running=True, cdp_port=9222),
    )
    monkeypatch.setattr(
        "backend.app.services.fine_job.smart_captures.boss_capture_task_manager.start_capture",
        lambda *args, **kwargs: _fake_smart_batch(),
    )

    response = configured_client.post(
        "/api/fine-job/smart-captures",
        json=_payload(),
    )

    assert response.status_code == 201
    snapshot = response.json()
    assert snapshot["status"] == "running"
    assert snapshot["state_version"] >= 2
    assert snapshot["waiting_reason"] == ""
    assert snapshot["control_cause"] == ""
    assert set(snapshot["capabilities"]) == {
        "start",
        "pause",
        "resume",
        "retry",
        "stop",
    }
    current = configured_client.get(
        "/api/fine-job/smart-captures/current"
    ).json()["smart_capture"]
    assert current["smart_capture_id"] == snapshot["smart_capture_id"]


@pytest.mark.parametrize(
    "status",
    ["pending", "paused", "waiting_for_user", "interrupted"],
)
def test_all_non_terminal_smart_capture_statuses_block_second_create(
    test_db,
    status: str,
) -> None:
    _create_capture(test_db, status=status)

    with pytest.raises(AppError) as error:
        smart_captures.create_smart_capture(
            test_db,
            source="boss_capture",
            workflow_run_id=None,
            search_config=_payload(),
            target_count=15,
        )

    assert error.value.status_code == 409
    assert error.value.error_category == "COLLECTION_TASK_ACTIVE"


def test_terminal_smart_capture_allows_next_create_and_replaces_current(test_db) -> None:
    first = _create_capture(test_db, status="completed")

    second = smart_captures.create_smart_capture(
        test_db,
        source="boss_capture",
        workflow_run_id=None,
        search_config=_payload(),
        target_count=15,
    )

    current = smart_captures.get_current_smart_capture(test_db)
    assert current is not None
    assert current["smart_capture_id"] == second["smart_capture_id"]
    assert current["smart_capture_id"] != first["smart_capture_id"]


def test_independent_smart_capture_does_not_create_hidden_child_relation(test_db) -> None:
    capture = _create_capture(test_db)

    with test_db.connect() as connection:
        relation_count = connection.execute(
            "SELECT COUNT(*) AS count FROM fj_workflow_children WHERE child_ref = ?",
            (str(capture["smart_capture_id"]),),
        ).fetchone()["count"]

    assert relation_count == 0


def test_linked_parent_and_child_roll_back_together_when_relation_insert_fails(
    test_db, monkeypatch
) -> None:
    def fail_relation(*args, **kwargs):
        raise RuntimeError("relation insert failed")

    monkeypatch.setattr(
        workflow_children,
        "create_child_relation_in_connection",
        fail_relation,
    )
    workflow_run_id = "workflow-atomic-create"

    with pytest.raises(RuntimeError, match="relation insert failed"):
        with test_db.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            workflow_runs._create_workflow_identity_in_connection(
                connection,
                test_db,
                workflow_run_id=workflow_run_id,
                idempotency_key=None,
                contract={},
                recommend_target=1,
                requested_keywords=["Python"],
                requested_cities=["上海"],
                min_depth=1,
                max_depth=1,
                filter_strategy_id="strategy-1",
                candidate_target=15,
                review_target=None,
                payload=_payload(),
                now="2026-09-18T00:00:00Z",
            )

    with test_db.connect() as connection:
        assert connection.execute(
            "SELECT 1 FROM fj_workflow_runs WHERE id = ?", (workflow_run_id,)
        ).fetchone() is None
        assert connection.execute(
            "SELECT 1 FROM fj_smart_captures WHERE workflow_run_id = ?",
            (workflow_run_id,),
        ).fetchone() is None
        assert connection.execute(
            "SELECT 1 FROM fj_workflow_children WHERE workflow_run_id = ?",
            (workflow_run_id,),
        ).fetchone() is None
        assert connection.execute(
            "SELECT 1 FROM fj_workflow_tasks WHERE workflow_run_id = ?",
            (workflow_run_id,),
        ).fetchone() is None
        assert connection.execute(
            "SELECT 1 FROM fj_workflow_search_combinations WHERE workflow_run_id = ?",
            (workflow_run_id,),
        ).fetchone() is None
        assert connection.execute(
            "SELECT 1 FROM fj_smart_capture_current WHERE slot = 1"
        ).fetchone() is None


def test_orphan_pending_smart_capture_does_not_block_current_slot(test_db) -> None:
    current_capture = _create_capture(test_db, status="completed")
    with test_db.connect() as connection:
        connection.execute(
            """
            INSERT INTO fj_smart_captures (
              id, source, status, search_config_json, stage, message,
              created_at, updated_at
            ) VALUES ('orphan-pending', 'boss_capture', 'pending', '{}', 'created', '', ?, ?)
            """,
            ("2026-09-18T00:00:00Z", "2026-09-18T00:00:00Z"),
        )

    assert workflow_runs.get_active_collection_task(test_db) is None

    next_capture = smart_captures.create_smart_capture(
        test_db,
        source="boss_capture",
        workflow_run_id=None,
        search_config=_payload(),
        target_count=15,
    )

    current = smart_captures.get_current_smart_capture(test_db)
    assert current is not None
    assert current["smart_capture_id"] == next_capture["smart_capture_id"]
    assert current["smart_capture_id"] != current_capture["smart_capture_id"]


def test_custom_batch_blocks_smart_create(test_db) -> None:
    create_capture_batch(
        test_db,
        capture_id="custom-batch-1",
        keyword="Python",
        city="上海",
        pages=1,
        auto_details=False,
        created_at="2026-09-17T00:00:00Z",
        capture_source="custom",
    )

    with pytest.raises(AppError) as error:
        smart_captures.create_smart_capture(
            test_db,
            source="boss_capture",
            workflow_run_id=None,
            search_config=_payload(),
            target_count=15,
        )

    assert error.value.error_category == "COLLECTION_TASK_ACTIVE"


def test_non_terminal_smart_capture_blocks_custom_api(configured_client, monkeypatch) -> None:
    _create_capture(configured_client.app.state.db, status="paused")
    monkeypatch.setattr(
        "backend.app.routers.fine_job.boss_capture.boss_scraper_service.get_browser_status",
        lambda: BossBrowserStatus(running=True, cdp_port=9222),
    )

    response = configured_client.post(
        "/api/fine-job/boss-capture/capture",
        json={"keyword": "Python", "city": "上海", "pages": 1},
    )

    assert response.status_code == 409
    assert response.json()["error_category"] == "COLLECTION_TASK_ACTIVE"


def test_restart_keeps_persisted_current_and_does_not_choose_newer_history(
    test_db,
) -> None:
    first = _create_capture(test_db, status="pending")
    first_id = str(first["smart_capture_id"])
    with test_db.connect() as connection:
        connection.execute(
            "UPDATE fj_smart_captures SET status = 'running', updated_at = '2026-09-17T00:00:00Z' WHERE id = ?",
            (first_id,),
        )
        connection.execute(
            """
            INSERT INTO fj_smart_captures (
              id, source, status, search_config_json, stage, message,
              created_at, updated_at
            ) VALUES ('history-2', 'boss_capture', 'completed', '{}', 'completed', '', ?, ?)
            """,
            ("2026-09-17T00:01:00Z", "2026-09-17T23:00:00Z"),
        )

    smart_captures.recover_interrupted_smart_captures(test_db)

    current = smart_captures.get_current_smart_capture(test_db)
    assert current is not None
    assert current["smart_capture_id"] == first_id
    assert current["status"] == "interrupted"
    assert current["waiting_reason"] == "capture_interrupted"
    assert current["control_cause"] == "recovery"


def test_waiting_for_user_is_allowed_by_database_check_and_failed_is_not_retryable(
    test_db,
) -> None:
    capture = _create_capture(test_db, status="waiting_for_user")
    snapshot = smart_captures.get_smart_capture(
        test_db, str(capture["smart_capture_id"])
    )
    assert snapshot["status"] == "waiting_for_user"

    with test_db.connect() as connection:
        connection.execute(
            "UPDATE fj_smart_captures SET status = 'failed', stage = 'failed' WHERE id = ?",
            (str(capture["smart_capture_id"]),),
        )
    failed = smart_captures.get_smart_capture(
        test_db, str(capture["smart_capture_id"])
    )
    assert failed["capabilities"]["retry"] is False
    with pytest.raises(AppError) as error:
        smart_captures.retry_smart_capture(
            test_db,
            object(),  # type: ignore[arg-type]
            str(capture["smart_capture_id"]),
        )
    assert error.value.error_category == "SMART_CAPTURE_NOT_RETRYABLE"


def test_snapshot_state_version_increments_for_observable_change_only(test_db) -> None:
    capture = _create_capture(test_db)
    capture_id = str(capture["smart_capture_id"])
    task = {
        "smart_capture_id": capture_id,
        "status": "queued",
        "stage": "queued",
        "message": "等待执行",
        "progress_current": 0,
        "progress_total": 1,
        "jobs_collected": 0,
        "details_completed": 0,
        "details_failed": 0,
        "jobs": [],
        "has_more": True,
    }

    smart_captures.sync_capture_snapshot(test_db, task)
    first = smart_captures.get_smart_capture(test_db, capture_id)
    smart_captures.sync_capture_snapshot(test_db, task)
    second = smart_captures.get_smart_capture(test_db, capture_id)

    assert first["status"] == "running"
    assert second["state_version"] == first["state_version"]

    task.update(
        status="completed",
        stage="list_completed",
        message="采集完成",
        progress_current=1,
        jobs_collected=1,
        has_more=False,
    )
    smart_captures.sync_capture_snapshot(test_db, task)
    finished = smart_captures.get_smart_capture(test_db, capture_id)
    assert finished["status"] == "completed"
    assert finished["state_version"] > second["state_version"]


def test_concurrent_smart_capture_creation_allows_only_one(test_db) -> None:
    def create_one() -> str:
        try:
            capture = smart_captures.create_smart_capture(
                test_db,
                source="boss_capture",
                workflow_run_id=None,
                search_config=_payload(),
                target_count=15,
            )
        except AppError as error:
            return error.error_category
        return str(capture["smart_capture_id"])

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: create_one(), range(2)))

    assert sum(result == "COLLECTION_TASK_ACTIVE" for result in results) == 1
