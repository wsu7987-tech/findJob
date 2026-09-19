from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.tests.api.test_fine_job_workflow_runs_api import (
    _ack_started_batch,
    _create_run,
    _prepare_analysis_batch,
)
from backend.app.errors import AppError
from backend.app.services.fine_job import (
    cutover_guard,
    pipeline_owner,
    pipeline_repository,
    smart_captures,
)
from backend.app.services.fine_job import workflow_runs


@pytest.fixture(autouse=True)
def reset_runtime_cutover_guard():
    cutover_guard.reset_runtime_cutover_guard()
    yield
    cutover_guard.reset_runtime_cutover_guard()


def test_owner_context_supports_linked_and_independent_identity() -> None:
    linked = pipeline_owner.resolve_pipeline_owner(
        smart_capture_id="capture-linked",
        workflow_run_id="workflow-1",
        source="task_cockpit",
        config={"candidate_target_count": 4},
        contract={"delivery_target_enabled": False},
    )
    independent = pipeline_owner.resolve_pipeline_owner(
        smart_capture_id="capture-independent",
        workflow_run_id=None,
        source="boss_capture",
    )

    assert linked.identity == "capture-linked"
    assert linked.is_linked is True
    assert linked.is_independent is False
    assert independent.identity == "capture-independent"
    assert independent.is_independent is True
    assert independent.workflow_run_id is None


def test_linked_planner_decision_updates_smart_capture_owner(configured_client, test_db) -> None:
    run = _create_run(configured_client)
    with test_db.connect() as connection:
        combination_id = connection.execute(
            "SELECT id FROM fj_workflow_search_combinations WHERE workflow_run_id = ?",
            (run["workflow_run_id"],),
        ).fetchone()["id"]

    workflow_runs._save_planner_decision(
        test_db,
        run["workflow_run_id"],
        str(combination_id),
        SimpleNamespace(
            switch_reason="扩大经验范围",
            selected_axis="experience",
            evidence={"reason": "low_yield"},
        ),
    )

    with test_db.connect() as connection:
        row = connection.execute(
            "SELECT transition_reason, selected_axis, evidence_json FROM fj_workflow_search_combinations WHERE id = ?",
            (str(combination_id),),
        ).fetchone()
    assert row["transition_reason"] == "扩大经验范围"
    assert row["selected_axis"] == "experience"
    assert row["evidence_json"] == '{"reason":"low_yield"}'


def test_history_context_is_read_only_and_never_becomes_pipeline_owner() -> None:
    history = pipeline_owner.resolve_history_read_context(
        smart_capture_id="capture-a",
        workflow_run_id="workflow-history-b",
    )

    assert history.is_history_only is True
    assert history.identity == "capture-a"
    assert history.contract == {"read_only": True}


def test_smart_capture_database_row_resolves_execution_owner(test_db) -> None:
    capture = smart_captures.create_smart_capture(
        test_db,
        source="boss_capture",
        workflow_run_id=None,
        search_config={"allowed_search_keywords": ["Python"], "allowed_cities": ["上海"]},
        execution_config={"candidate_target_count": 10, "delivery_target_enabled": False},
    )

    owner = pipeline_owner.get_pipeline_owner(test_db, str(capture["smart_capture_id"]))

    assert owner.identity == capture["smart_capture_id"]
    assert owner.is_independent is True
    assert owner.config["candidate_target_count"] == 10
    assert owner.contract["allowed_search_keywords"] == ["Python"]

    snapshot = smart_captures.get_smart_capture(
        test_db, str(capture["smart_capture_id"])
    )
    assert pipeline_owner.validate_smart_capture_snapshot_contract(snapshot) == snapshot


def test_independent_pipeline_repository_crud_uses_smart_capture_owner(test_db) -> None:
    capture = smart_captures.create_smart_capture(
        test_db,
        source="boss_capture",
        workflow_run_id=None,
        search_config={"allowed_search_keywords": ["Python"], "allowed_cities": ["上海"]},
        execution_config={"candidate_target_count": 2},
    )
    capture_id = str(capture["smart_capture_id"])
    repository = pipeline_repository.PipelineRepository.for_smart_capture(test_db, capture_id)

    task_id = repository.insert_owned(
        "fj_workflow_tasks",
        {
            "task_type": "deep_job_search_analysis",
            "status": "pending",
            "payload_json": '{"job_id":"job-independent"}',
            "result_json": "{}",
            "created_at": "2026-09-19T00:00:00Z",
            "updated_at": "2026-09-19T00:00:00Z",
        },
    )
    repository.update_owned(
        "fj_workflow_tasks",
        task_id,
        {"status": "succeeded", "result_json": '{"decision":"review"}'},
    )

    row = repository.get_row("fj_workflow_tasks", task_id)
    assert row is not None
    assert row["smart_capture_id"] == capture_id
    assert row["workflow_run_id"] is None
    assert repository.list_tasks("deep_job_search_analysis")[0]["id"] == task_id

    batch_id = "batch-independent"
    repository.insert_owned(
        "fj_workflow_analysis_handoffs",
        {
            "analysis_batch_id": batch_id,
            "status": "claimed",
            "attempt_status": "claimed",
            "codex_session_ref": "session-independent",
            "claimed_at": "2026-09-19T00:00:00Z",
        },
    )
    handoffs = repository.list_rows("fj_workflow_analysis_handoffs")
    assert [row["analysis_batch_id"] for row in handoffs] == [batch_id]
    repository.update_owned(
        "fj_workflow_analysis_handoffs",
        batch_id,
        {"attempt_status": "released"},
    )
    assert repository.get_row("fj_workflow_analysis_handoffs", batch_id)["smart_capture_id"] == capture_id

    with pytest.raises(AppError) as error:
        repository.insert_owned(
            "fj_workflow_tasks",
            {"task_type": "deep_job_search", "workflow_run_id": "other-workflow"},
        )
    assert error.value.error_category == "WORKFLOW_PARENT_LINK_MISMATCH"


def test_legacy_pipeline_read_adapter_is_explicit_and_read_only(test_db) -> None:
    adapter = pipeline_repository.LegacyPipelineReadAdapter.for_workflow_run(
        test_db, "legacy-workflow-run"
    )

    assert adapter.scope.history_only is True
    assert adapter.scope.column == "workflow_run_id"
    assert adapter.list_tasks() == []
    with pytest.raises(AppError) as error:
        adapter.insert_owned("fj_workflow_tasks", {"task_type": "legacy"})
    assert error.value.error_category == "LEGACY_READ_ONLY"


def test_snapshot_contract_rejects_workflow_only_identity() -> None:
    with pytest.raises(AppError) as error:
        pipeline_owner.validate_smart_capture_snapshot_contract(
            {
                "smart_capture_id": "",
                "source": "boss_capture",
                "workflow_run_id": "workflow-only",
                "status": "pending",
                "stage": "created",
                "waiting_reason": "",
                "control_cause": "",
                "state_version": 1,
                "capabilities": {
                    "start": True,
                    "pause": False,
                    "resume": False,
                    "retry": False,
                    "stop": True,
                },
                "progress": {},
                "result_summary": {},
                "updated_at": "2026-09-18T00:00:00Z",
            }
        )

    assert error.value.error_category == "SMART_CAPTURE_ID_REQUIRED"


def test_characterization_preserves_search_candidate_jd_analysis_and_context(
    configured_client, test_db
) -> None:
    run, jobs = _prepare_analysis_batch(
        configured_client, test_db, candidate_count=3, target_count=2
    )

    assert len(jobs) == 3
    assert run["status"] == "waiting_codex"
    assert any(
        task["task_type"] == "deep_job_search"
        and task["payload"].get("search_combination_id")
        for task in run["tasks"]
    )
    assert sum(task["task_type"] == "deep_job_search_jd" for task in run["tasks"]) == 2
    assert sum(
        task["task_type"] == "deep_job_search_analysis" for task in run["tasks"]
    ) == 2

    context = workflow_runs.get_context_snapshot(
        test_db, str(run["workflow_run_id"]), "candidate_analysis"
    )
    assert context["status"] == "ready"
    assert context["context_characters"] > 0


def test_characterization_handoff_ack_release_keeps_prefetch_parallelism(
    configured_client, test_db
) -> None:
    run, _jobs = _prepare_analysis_batch(
        configured_client, test_db, candidate_count=4, target_count=2
    )
    claimed = configured_client.post(
        f"/api/fine-job/workflow-runs/{run['workflow_run_id']}/analysis-handoff/claim",
        json={
            "codex_session_ref": "runtime:prefetch-test",
            "codex_runtime_id": "prefetch-test",
            "handoff_kind": "initial",
        },
    )
    assert claimed.status_code == 200
    released = configured_client.post(
        f"/api/fine-job/workflow-runs/{run['workflow_run_id']}/analysis-handoff/release",
        json={
            "analysis_batch_id": claimed.json()["analysis_handoff"]["analysis_batch_id"],
            "handoff_attempt_id": claimed.json()["analysis_handoff"]["handoff_attempt_id"],
            "codex_session_ref": "runtime:prefetch-test",
        },
    )
    assert released.status_code == 200
    assert released.json()["analysis_handoff"]["attempt_status"] == "released"

    started = _ack_started_batch(configured_client, run)

    assert started["analysis_handoff"]["attempt_status"] == "started"
    assert started["prefetch"]["status"] == "ready"
    assert started["prefetch"]["ready_count"] == 2


def test_characterization_reservation_keeps_competing_prefetch_unique(
    configured_client, test_db
) -> None:
    run, _jobs = _prepare_analysis_batch(
        configured_client, test_db, candidate_count=3, target_count=2
    )
    config = configured_client.app.state.config

    first = workflow_runs._ensure_prefetch_batch(
        test_db, config, str(run["workflow_run_id"]), "characterization-source-a"
    )
    second = workflow_runs._ensure_prefetch_batch(
        test_db, config, str(run["workflow_run_id"]), "characterization-source-b"
    )

    assert first
    assert second
    with test_db.connect() as connection:
        duplicate_reservations = connection.execute(
            """
            SELECT job_id, COUNT(*) AS count
            FROM fj_workflow_candidate_reservations
            WHERE workflow_run_id = ? AND status = 'reserved'
            GROUP BY job_id
            HAVING COUNT(*) > 1
            """,
            (run["workflow_run_id"],),
        ).fetchall()
    assert duplicate_reservations == []


def test_characterization_manual_analysis_keeps_existing_candidate_pipeline(
    configured_client, test_db
) -> None:
    run, jobs = _prepare_analysis_batch(
        configured_client, test_db, candidate_count=3, target_count=2
    )
    run_id = str(run["workflow_run_id"])
    contract = dict(run["completion_contract"])
    workflow_runs._freeze_candidate_pool(test_db, run_id, contract)
    existing_analysis_job_ids = {
        str(task["payload"].get("job_id") or "")
        for task in run["tasks"]
        if task["task_type"] == "deep_job_search_analysis"
    }
    manual_job = next(
        job
        for job in jobs
        if str(job["history_record_id"]) not in existing_analysis_job_ids
    )

    manual = workflow_runs.create_manual_analysis_batch(
        test_db,
        configured_client.app.state.config,
        run_id,
        recommendation_strategy_id=str(
            contract["selected_strategy_ids"]["recommendation_strategy_id"]
        ),
        job_ids=[str(manual_job["history_record_id"])],
        analysis_batch_size=1,
    )
    manual_items = [
        item
        for item in manual["tasks"]
        if item["task_type"] == "deep_job_search_analysis"
        and item["payload"].get("manual_batch")
    ]

    assert manual["status"] == "waiting_codex"
    assert len(manual_items) == 1


def test_cutover_guard_covers_resume_plus_advance_operation() -> None:
    guard = cutover_guard.CutoverGuard(phase=cutover_guard.CutoverPhase.POST_CUTOVER)

    with pytest.raises(AppError) as error:
        guard.assert_workflow_live_allowed(operation="resume + advance")

    assert error.value.error_category == "WORKFLOW_LIVE_EXECUTION_DISABLED"


def test_workflow_legacy_guard_ignores_custom_capture_callback(monkeypatch) -> None:
    monkeypatch.setattr(workflow_runs, "_realtime_runtime", None)
    cutover_guard.configure_runtime_cutover_phase(cutover_guard.CutoverPhase.POST_CUTOVER)

    workflow_runs._on_capture_task_updated(
        {"id": "custom-capture", "capture_source": "custom", "status": "completed"}
    )

    assert cutover_guard.get_runtime_cutover_guard().legacy_callback_invocations == 0


def test_workflow_legacy_callback_is_rejected_after_cutover(monkeypatch) -> None:
    monkeypatch.setattr(workflow_runs, "_realtime_runtime", None)
    cutover_guard.configure_runtime_cutover_phase(cutover_guard.CutoverPhase.POST_CUTOVER)

    with pytest.raises(AppError) as error:
        workflow_runs._on_capture_task_updated(
            {
                "id": "smart-capture-batch",
                "capture_source": "smart",
                "smart_capture_id": "smart-capture-1",
                "status": "completed",
            }
        )

    assert error.value.error_category == "WORKFLOW_LIVE_EXECUTION_DISABLED"
    assert cutover_guard.get_runtime_cutover_guard().legacy_callback_invocations == 1


def test_production_start_seam_rejects_second_start_for_one_independent_child(
    configured_client, test_db, monkeypatch
) -> None:
    capture = smart_captures.create_smart_capture(
        test_db,
        source="boss_capture",
        workflow_run_id=None,
        search_config={
            "allowed_search_keywords": ["Python"],
            "allowed_cities": ["上海"],
            "pages": 1,
        },
        target_count=15,
    )
    monkeypatch.setattr(
        smart_captures.boss_capture_task_manager,
        "start_capture",
        lambda *args, **kwargs: {"id": "seam-batch-1"},
    )
    payload = {
        "keyword": "Python",
        "city": "上海",
        "pages": 1,
        "filters": {},
    }
    capture_id = str(capture["smart_capture_id"])

    smart_captures._start_new_batch(
        test_db, configured_client.app.state.config, capture_id, payload
    )
    with pytest.raises(AppError) as error:
        smart_captures._start_new_batch(
            test_db, configured_client.app.state.config, capture_id, payload
        )

    assert error.value.error_category == "LIVE_CHILD_ALREADY_STARTED"


def test_start_seam_stops_executor_when_batch_binding_fails(
    configured_client, test_db, monkeypatch
) -> None:
    capture = smart_captures.create_smart_capture(
        test_db,
        source="boss_capture",
        workflow_run_id=None,
        search_config={
            "allowed_search_keywords": ["Python"],
            "allowed_cities": ["上海"],
            "pages": 1,
        },
        target_count=15,
    )
    monkeypatch.setattr(
        smart_captures.boss_capture_task_manager,
        "start_capture",
        lambda *args, **kwargs: {"id": "seam-bind-failure-batch"},
    )
    stopped: list[str] = []
    monkeypatch.setattr(
        smart_captures.boss_capture_task_manager,
        "stop_capture",
        lambda task_id: stopped.append(task_id) or {"id": task_id},
    )
    monkeypatch.setattr(
        smart_captures,
        "bind_batch",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("bind failed")),
    )
    payload = {"keyword": "Python", "city": "上海", "pages": 1, "filters": {}}
    capture_id = str(capture["smart_capture_id"])

    with pytest.raises(RuntimeError, match="bind failed"):
        smart_captures._start_new_batch(
            test_db, configured_client.app.state.config, capture_id, payload
        )

    assert stopped == ["seam-bind-failure-batch"]
    with pytest.raises(AppError) as error:
        smart_captures._start_new_batch(
            test_db, configured_client.app.state.config, capture_id, payload
        )
    assert error.value.error_category == "LIVE_CHILD_ALREADY_STARTED"


def test_cutover_guard_detects_legacy_callback_and_authority_conflict() -> None:
    guard = cutover_guard.CutoverGuard()
    guard.observe_legacy_capture_callback(capture_id="capture-1")
    assert guard.legacy_callback_invocations == 1

    guard.assert_single_live_authority(
        child_ref="capture-1",
        requested_authority=cutover_guard.ExecutionAuthority.WORKFLOW_PIPELINE,
        production_authority=cutover_guard.ExecutionAuthority.WORKFLOW_PIPELINE,
    )
    with pytest.raises(AppError) as error:
        guard.assert_single_live_authority(
            child_ref="capture-1",
            requested_authority=cutover_guard.ExecutionAuthority.SMART_CAPTURE,
            production_authority=cutover_guard.ExecutionAuthority.WORKFLOW_PIPELINE,
        )
    assert error.value.error_category == "LIVE_EXECUTION_AUTHORITY_CONFLICT"


def test_cutover_guard_rejects_second_start_for_same_live_child() -> None:
    guard = cutover_guard.CutoverGuard()
    guard.claim_live_start(
        child_ref="capture-1",
        requested_authority=cutover_guard.ExecutionAuthority.WORKFLOW_PIPELINE,
    )

    with pytest.raises(AppError) as error:
        guard.claim_live_start(
            child_ref="capture-1",
            requested_authority=cutover_guard.ExecutionAuthority.WORKFLOW_PIPELINE,
        )
    assert error.value.error_category == "LIVE_CHILD_ALREADY_STARTED"

    guard.release_live_start(child_ref="capture-1")
    guard.claim_live_start(
        child_ref="capture-1",
        requested_authority=cutover_guard.ExecutionAuthority.WORKFLOW_PIPELINE,
    )


def test_cutover_guard_rejects_legacy_live_callback_after_cutover() -> None:
    guard = cutover_guard.CutoverGuard(phase=cutover_guard.CutoverPhase.POST_CUTOVER)

    with pytest.raises(AppError) as error:
        guard.observe_legacy_capture_callback(capture_id="capture-after-cutover")

    assert error.value.error_category == "WORKFLOW_LIVE_EXECUTION_DISABLED"
    assert guard.legacy_callback_invocations == 1


def test_pre_cutover_smart_capture_seam_cannot_become_linked_live_authority() -> None:
    guard = cutover_guard.CutoverGuard()

    with pytest.raises(AppError) as error:
        guard.assert_single_live_authority(
            child_ref="capture-1",
            requested_authority=cutover_guard.ExecutionAuthority.SMART_CAPTURE,
            production_authority=cutover_guard.ExecutionAuthority.SMART_CAPTURE,
        )

    assert error.value.error_category == "SMART_CAPTURE_LIVE_EXECUTION_NOT_CUTOVER"
