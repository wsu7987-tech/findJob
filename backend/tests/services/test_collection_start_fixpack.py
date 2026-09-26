from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import uuid4
import json

import pytest

from backend.app.config import load_config
from backend.app.errors import AppError
from backend.app.services.fine_job import collection_start_operations as operations
from backend.app.services.fine_job import smart_captures, smart_capture_engine, workflow_runs


def test_same_intent_concurrency_conflict_and_resolve(test_db):
    entered, release = Event(), Event()
    calls = []

    @operations.collection_start("custom.capture", "custom")
    def start(db, payload):
        calls.append(1)
        entered.set()
        assert release.wait(3)
        operations.bind_existing(db, "custom", "real-task")
        return {"id": "real-task", "status": "completed"}

    payload = {"operation_id": str(uuid4()), "pages": 1}
    with ThreadPoolExecutor(max_workers=2) as executor:
        future = executor.submit(start, test_db, payload)
        assert entered.wait(3)
        try:
            assert start(test_db, payload)["status"] == "pending"
            assert operations.start_http_response(start)(test_db, payload).status_code == 202
            other = start(test_db, {"operation_id": str(uuid4()), "pages": 1})
            assert other["status"] == "rejected"
            assert other["error_category"] == "START_OPERATION_PENDING"
            assert operations.resolve_operation(test_db, payload["operation_id"])["status"] == "pending"
            with pytest.raises(AppError, match="不同参数"):
                start(test_db, {**payload, "pages": 2})
        finally:
            release.set()
        assert future.result()["id"] == "real-task"
    assert len(calls) == 1
    assert start(test_db, payload)["result_task_id"] == "real-task"
    closed = {"operation_id": str(uuid4()), "pages": 1}
    assert operations.resolve_operation(test_db, closed["operation_id"])["status"] == "rejected"
    assert start(test_db, closed)["closed_without_dispatch"] is True
    assert len(calls) == 1


def test_bound_dispatch_failure_is_unknown_and_restart_keeps_reference(test_db):
    @operations.collection_start("custom.capture", "custom")
    def start(db, payload):
        operations.bind_existing(db, "custom", "bound-task")
        raise RuntimeError("executor acknowledgement lost")

    payload = {"operation_id": str(uuid4())}
    with pytest.raises(RuntimeError):
        start(test_db, payload)
    receipt = operations.get_operation(test_db, payload["operation_id"])
    assert receipt["status"] == "unknown"
    assert receipt["result_task_id"] == "bound-task"
    assert operations.resolve_operation(test_db, payload["operation_id"])["status"] == "unknown"
    with test_db.connect() as connection:
        connection.execute("UPDATE fj_collection_start_operations SET status = 'pending', executor_epoch = 'old-process'")
    operations.recover_operations(test_db)
    assert operations.get_operation(test_db, payload["operation_id"])["status"] == "unknown"
    unbound_id = str(uuid4())
    operations.resolve_operation(test_db, unbound_id)
    with test_db.connect() as connection:
        connection.execute("UPDATE fj_collection_start_operations SET status = 'pending', executor_epoch = 'old-process' WHERE operation_id = ?", (unbound_id,))
    operations.recover_operations(test_db)
    assert operations.get_operation(test_db, unbound_id)["status"] == "rejected"
    assert operations.get_operation(test_db, unbound_id)["closed_without_dispatch"]


def test_receipt_incremental_migration_preserves_existing_data(test_db):
    capture = smart_captures.create_smart_capture(test_db, source="boss_capture", workflow_run_id=None, search_config={})
    with test_db.connect() as connection:
        connection.execute("DROP TABLE fj_collection_start_operations")
    test_db.initialize()
    operation_id = str(uuid4())
    operations.resolve_operation(test_db, operation_id)
    test_db.initialize()
    assert operations.get_operation(test_db, operation_id)["closed_without_dispatch"]
    assert smart_captures.get_current_smart_capture_id(test_db) == capture["smart_capture_id"]

    @operations.collection_start("custom.capture", "custom")
    def legacy_start(db, payload):
        operations.bind_existing(db, "custom", "legacy-result")
        return {"id": "legacy-result", "status": "completed"}
    result = legacy_start(test_db, {"pages": 1})
    assert operations.get_operation(test_db, result["operation_id"])["status"] == "started"


def test_parent_resume_preflight_failure_preserves_both_states(test_db, monkeypatch):
    now = "2026-09-26T00:00:00Z"
    with test_db.connect() as connection:
        workflow_runs._create_workflow_identity_in_connection(
            connection, test_db, workflow_run_id="parent", idempotency_key=None, contract={},
            recommend_target=1, requested_keywords=["Python"], requested_cities=["上海"],
            min_depth=1, max_depth=1, filter_strategy_id="strategy", candidate_target=15,
            review_target=None, payload={}, now=now,
        )
        connection.execute("UPDATE fj_workflow_runs SET control_state = 'paused', control_cause = 'parent_pause', paused = 1 WHERE id = 'parent'")
        connection.execute("UPDATE fj_smart_captures SET status = 'paused', control_cause = 'parent_pause' WHERE workflow_run_id = 'parent'")
        before_parent = dict(connection.execute("SELECT * FROM fj_workflow_runs WHERE id = 'parent'").fetchone())
        before_child = dict(connection.execute("SELECT * FROM fj_smart_captures WHERE workflow_run_id = 'parent'").fetchone())
    def reject(**kwargs):
        raise AppError(409, "LOGIN_UNAUTHENTICATED", "尚未登录")
    monkeypatch.setattr("backend.app.services.fine_job.collection_starts.ensure_ready", reject)
    with pytest.raises(AppError, match="尚未登录"):
        workflow_runs.resume_deep_job_search_run(test_db, load_config(), "parent", operation_id=str(uuid4()))
    with test_db.connect() as connection:
        assert dict(connection.execute("SELECT * FROM fj_workflow_runs WHERE id = 'parent'").fetchone()) == before_parent
        assert dict(connection.execute("SELECT * FROM fj_smart_captures WHERE workflow_run_id = 'parent'").fetchone()) == before_child

    def stop_during_check(**kwargs):
        with test_db.connect() as connection:
            connection.execute("UPDATE fj_smart_captures SET status = 'stopped', state_version = state_version + 1 WHERE workflow_run_id = 'parent'")
    monkeypatch.setattr("backend.app.services.fine_job.collection_starts.ensure_ready", stop_during_check)
    with pytest.raises(AppError, match="状态已变化"):
        workflow_runs.resume_deep_job_search_run(test_db, load_config(), "parent", operation_id=str(uuid4()))
    with test_db.connect() as connection:
        assert dict(connection.execute("SELECT * FROM fj_workflow_runs WHERE id = 'parent'").fetchone()) == before_parent
        assert connection.execute("SELECT status FROM fj_smart_captures WHERE workflow_run_id = 'parent'").fetchone()["status"] == "stopped"


def test_formal_progress_scope_and_committed_event_versions(test_db, monkeypatch):
    capture = smart_captures.create_smart_capture(test_db, source="boss_capture", workflow_run_id=None, search_config={})
    capture_id = capture["smart_capture_id"]
    with test_db.connect() as connection:
        for task_id, scope, status in [("old", "previous", "succeeded"), ("a", "current", "pending"), ("b", "current", "pending"), ("c", "current", "running")]:
            connection.execute("""INSERT INTO fj_workflow_tasks
                (id, smart_capture_id, workflow_run_id, task_type, status, payload_json, result_json, created_at, updated_at)
                VALUES (?, ?, NULL, 'deep_job_search_jd', ?, ?, '{}', ?, ?)""",
                (task_id, capture_id, status, json.dumps({"jd_batch_id": scope, "job_id": task_id}), task_id, task_id))
    events = []
    monkeypatch.setattr(smart_capture_engine, "publish_progress", lambda db, owner: events.append(smart_captures.get_smart_capture(db, owner)))
    for task_id, succeeded in [("a", True), ("b", False)]:
        with test_db.connect() as connection:
            unit = connection.execute("SELECT * FROM fj_workflow_tasks WHERE id = ?", (task_id,)).fetchone()
        smart_capture_engine._finish_pipeline_detail(test_db, capture_id, "formal_jd", unit, succeeded=succeeded)
    progress = events[-1]["collection_progress"]["formal_jd"]
    assert {key: progress[key] for key in ("scope_id", "processed", "succeeded", "failed", "running", "total")} == {
        "scope_id": "current", "processed": 2, "succeeded": 1, "failed": 1, "running": 1, "total": 3,
    }
    assert events[-1]["state_version"] > events[0]["state_version"]
    smart_capture_engine._finish_pipeline_detail(test_db, capture_id, "formal_jd", unit, succeeded=False)
    assert len(events) == 2
    with test_db.connect() as connection:
        connection.execute("UPDATE fj_workflow_tasks SET operation_ref_id = 'new-executor' WHERE id = 'c'")
    smart_capture_engine.process_detail_task_update(test_db, capture_id, {
        "id": "old-executor", "status": "completed", "stage": "details_completed",
        "pipeline_unit_type": "formal_jd", "pipeline_unit_id": "c",
    })
    assert len(events) == 2
    assert smart_captures.get_smart_capture(test_db, capture_id)["collection_progress"]["formal_jd"]["running"] == 1


def test_http_rejected_receipt_and_late_request_after_resolve(configured_client, monkeypatch):
    operation_id = str(uuid4())
    monkeypatch.setattr("backend.app.routers.fine_job.boss_capture.ensure_ready", lambda: (_ for _ in ()).throw(AppError(409, "LOGIN_EMPTY", "样本为空")))
    response = configured_client.post("/api/fine-job/boss-capture/capture", json={"operation_id": operation_id, "keyword": "Python", "city": "上海"})
    assert response.status_code == 409
    receipt = configured_client.get(f"/api/fine-job/collection-start-operations/{operation_id}").json()
    assert receipt["status"] == "rejected"
    assert receipt["error_category"] == "LOGIN_EMPTY"
    closed_id = str(uuid4())
    configured_client.post(f"/api/fine-job/collection-start-operations/{closed_id}/resolve")
    response = configured_client.post("/api/fine-job/boss-capture/capture", json={"operation_id": closed_id, "keyword": "Python", "city": "上海"})
    assert response.json()["closed_without_dispatch"] is True
