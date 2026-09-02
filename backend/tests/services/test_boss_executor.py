from __future__ import annotations

from dataclasses import dataclass
import time

import pytest

from backend.app.errors import AppError
from backend.app.db import Database
from backend.app.services.fine_job import boss_executor
from backend.app.services.fine_job import workflow
from backend.app.services.fine_job.boss_capture_history import create_capture_batch, record_capture_jobs
from backend.app.utils import utc_now


def _seed_action(db, suffix: str = "1") -> tuple[str, str, str]:
    capture_id = f"capture-{suffix}"
    create_capture_batch(
        db, capture_id=capture_id, keyword="Python", city="上海", pages=1,
        auto_details=False, created_at=utc_now(),
    )
    job = record_capture_jobs(
        db,
        capture_id=capture_id,
        jobs=[{
            "job_id": f"source-{suffix}",
            "encrypt_job_id": f"encrypt-{suffix}",
            "title": f"Python工程师{suffix}",
            "boss_name": "示例科技",
            "job_link": f"https://www.zhipin.com/job_detail/encrypt-{suffix}.html",
        }],
    )[0]
    with db.connect() as connection:
        job_id = str(connection.execute(
            "SELECT id FROM fj_boss_jobs WHERE encrypt_job_id = ?", (f"encrypt-{suffix}",)
        ).fetchone()[0])
        evaluation_id = f"evaluation-{suffix}"
        review_id = f"review-{suffix}"
        action_id = f"action-{suffix}"
        now = utc_now()
        connection.execute(
            "INSERT INTO fj_job_evaluations (id, job_id, source, decision, confidence, evaluation_json, created_at) VALUES (?, ?, 'rules', 'recommend', 1, '{}', ?)",
            (evaluation_id, job_id, now),
        )
        connection.execute(
            "INSERT INTO fj_review_items (id, job_id, evaluation_id, status, ai_decision, created_at, updated_at) VALUES (?, ?, ?, 'approved', 'recommend', ?, ?)",
            (review_id, job_id, evaluation_id, now, now),
        )
        connection.execute(
            """
            INSERT INTO fj_automation_actions (
              id, job_id, evaluation_id, review_item_id, action_type, status,
              idempotency_key, payload_json, execution_state, queue_position,
              created_at, updated_at
            ) VALUES (?, ?, ?, ?, 'BOSS_DEFAULT_GREETING', 'queued', ?, '{}', 'queued', ?, ?, ?)
            """,
            (action_id, job_id, evaluation_id, review_id, f"boss:{job_id}:default:{suffix}", int(suffix), now, now),
        )
    assert job
    return job_id, review_id, action_id


def _paired_executor(db) -> tuple[str, str]:
    code = boss_executor.create_pairing_code(db)["code"]
    paired = boss_executor.pair_executor(
        db, code=code, label="测试插件", protocol_version="1.1",
        plugin_version="0.1.0", capabilities=["default_greeting"],
    )
    executor_id = paired["executor_id"]
    boss_executor.set_control(db, executor_id, "allow")
    boss_executor.heartbeat(db, executor_id, {
        "protocol_version": "1.1", "plugin_version": "0.1.0",
        "capabilities": ["default_greeting"], "browser_connected": True,
        "risk_state": "none",
    })
    return executor_id, paired["token"]


def _prepare_dispatch(db, *, suffix: str = "1") -> tuple[str, dict[str, object]]:
    _seed_action(db, suffix)
    executor_id, _token = _paired_executor(db)
    action = boss_executor.claim_next_action(db, executor_id, open_page=lambda _url: f"target-{suffix}")
    assert action
    boss_executor.report_page_status(db, executor_id, str(action["id"]), {
        "execution_epoch": action["execution_epoch"], "state": "ready", "logged_in": True,
        "page_kind": "detail", "encrypt_job_id": f"encrypt-{suffix}",
        "contacted": False, "reason": "ready", "observed_at": int(time.time() * 1000),
    })
    boss_executor.mark_dispatch_started(db, executor_id, str(action["id"]), int(action["execution_epoch"]))
    return executor_id, action


def _complete_unknown(db, executor_id: str, suffix: str) -> dict[str, object]:
    with db.connect() as connection:
        connection.execute(
            "UPDATE fj_boss_executor_instances SET next_eligible_at = '2000-01-01T00:00:00Z' WHERE id = ?",
            (executor_id,),
        )
    action = boss_executor.claim_next_action(db, executor_id, open_page=lambda _url: f"target-{suffix}")
    assert action
    boss_executor.report_page_status(db, executor_id, str(action["id"]), {
        "execution_epoch": action["execution_epoch"], "state": "ready", "logged_in": True,
        "page_kind": "detail", "encrypt_job_id": f"encrypt-{suffix}",
        "contacted": False, "reason": "ready",
    })
    boss_executor.mark_dispatch_started(db, executor_id, str(action["id"]), int(action["execution_epoch"]))
    return boss_executor.complete_executor_action(db, executor_id, str(action["id"]), {
        "execution_epoch": action["execution_epoch"], "outcome": "unknown", "contacted": None,
        "status_code": "NETWORK_UNKNOWN", "message": "结果未知", "evidence": {},
    })


def test_open_claim_verify_dispatch_and_complete_default_greeting(test_db) -> None:
    _job_id, _review_id, action_id = _seed_action(test_db)
    executor_id, _token = _paired_executor(test_db)

    action = boss_executor.claim_next_action(
        test_db, executor_id, open_page=lambda url: "target-1"
    )
    assert action is not None
    assert action["id"] == action_id
    assert action["execution_state"] == "waiting_page_ready"
    assert action["page_open_attempts"] == 1

    verified = boss_executor.report_page_status(test_db, executor_id, action_id, {
        "execution_epoch": action["execution_epoch"], "state": "ready",
        "logged_in": True, "page_kind": "detail", "encrypt_job_id": "encrypt-1",
        "contacted": False, "reason": "岗位已识别",
    })
    assert verified["execution_state"] == "ready_to_dispatch"

    started = boss_executor.mark_dispatch_started(
        test_db, executor_id, action_id, int(action["execution_epoch"])
    )
    assert started["execution_state"] == "dispatch_started"

    completed = boss_executor.complete_executor_action(test_db, executor_id, action_id, {
        "execution_epoch": action["execution_epoch"], "outcome": "succeeded",
        "contacted": True, "status_code": "BOSS_DEFAULT_GREETING_CONFIRMED",
        "message": "已确认", "evidence": {"responseCode": 0},
    })
    assert completed["execution_state"] == "succeeded"
    with test_db.connect() as connection:
        row = connection.execute(
            "SELECT cooldown_seconds, next_eligible_at FROM fj_automation_actions WHERE id = ?",
            (action_id,),
        ).fetchone()
    assert row["cooldown_seconds"] in {1, 2, 3}
    assert row["next_eligible_at"]


def test_stale_execution_epoch_is_rejected(test_db) -> None:
    _seed_action(test_db)
    executor_id, _token = _paired_executor(test_db)
    action = boss_executor.claim_next_action(test_db, executor_id, open_page=lambda _url: "target")
    assert action

    with pytest.raises(AppError) as error:
        boss_executor.report_page_status(test_db, executor_id, action["id"], {
            "execution_epoch": action["execution_epoch"] + 1, "state": "ready",
            "logged_in": True, "page_kind": "detail", "encrypt_job_id": "encrypt-1",
            "contacted": False, "reason": "迟到状态",
        })
    assert error.value.error_category == "STALE_EXECUTION_EPOCH"


@dataclass
class _BrowserStatus:
    running: bool
    current_url: str


def test_first_page_timeout_moves_to_tail_and_uses_fixed_random_wait(test_db) -> None:
    _seed_action(test_db, "1")
    _seed_action(test_db, "2")
    executor_id, _token = _paired_executor(test_db)
    action = boss_executor.claim_next_action(test_db, executor_id, open_page=lambda _url: "target")
    assert action
    with test_db.connect() as connection:
        connection.execute(
            "UPDATE fj_automation_actions SET page_deadline_at = '2000-01-01T00:00:00Z' WHERE id = ?",
            (action["id"],),
        )

    boss_executor.sweep_page_timeout(
        test_db, executor_id,
        browser_status_provider=lambda: _BrowserStatus(True, "https://www.zhipin.com/job_detail/encrypt-1.html"),
        random_seconds=lambda: 2,
    )
    with test_db.connect() as connection:
        moved = connection.execute(
            "SELECT execution_state, execution_epoch, queue_position, cooldown_seconds FROM fj_automation_actions WHERE id = ?",
            (action["id"],),
        ).fetchone()
        other = connection.execute("SELECT queue_position FROM fj_automation_actions WHERE id = 'action-2'").fetchone()
    assert moved["execution_state"] == "queued"
    assert moved["execution_epoch"] > action["execution_epoch"]
    assert moved["queue_position"] > other["queue_position"]
    assert moved["cooldown_seconds"] == 2


def test_second_page_timeout_blocks_job_but_keeps_queue_available(test_db) -> None:
    _seed_action(test_db)
    executor_id, _token = _paired_executor(test_db)
    action = boss_executor.claim_next_action(test_db, executor_id, open_page=lambda _url: "target")
    assert action
    with test_db.connect() as connection:
        connection.execute(
            "UPDATE fj_automation_actions SET page_open_attempts = 2, page_deadline_at = '2000-01-01T00:00:00Z' WHERE id = ?",
            (action["id"],),
        )
    boss_executor.sweep_page_timeout(
        test_db, executor_id,
        browser_status_provider=lambda: _BrowserStatus(True, "https://www.zhipin.com/job_detail/encrypt-1.html"),
        random_seconds=lambda: 1,
    )
    with test_db.connect() as connection:
        state = connection.execute("SELECT execution_state FROM fj_automation_actions WHERE id = ?", (action["id"],)).fetchone()[0]
        queue_state = connection.execute("SELECT queue_state FROM fj_boss_executor_instances WHERE id = ?", (executor_id,)).fetchone()[0]
    assert state == "blocked"
    assert queue_state == "running"


def test_unknown_result_is_recorded_but_hidden_from_both_queues(test_db) -> None:
    _seed_action(test_db, "1")
    _seed_action(test_db, "2")
    executor_id, _token = _paired_executor(test_db)
    result = _complete_unknown(test_db, executor_id, "1")
    assert result["execution_state"] == "unknown_after_dispatch"
    snapshot = boss_executor.executor_snapshot(test_db, executor_id)["executor"]
    assert snapshot["queue_state"] == "running"
    assert snapshot["risk_state"] == "none"
    assert snapshot["current_action_id"] is None
    queue_ids = [item["id"] for item in boss_executor.list_queue(test_db)["actions"]]
    assert queue_ids == ["action-2"]
    assert boss_executor.list_queue(test_db)["failed_actions"] == []
    # 兼容修补前已经写入全局未知锁的历史执行器状态。
    with test_db.connect() as connection:
        connection.execute(
            "UPDATE fj_boss_executor_instances SET permission_state = 'risk_paused', queue_state = 'risk_paused', risk_state = 'unknown_after_dispatch' WHERE id = ?",
            (executor_id,),
        )
    resumed = boss_executor.set_control(test_db, executor_id, "allow")["executor"]
    assert resumed["queue_state"] == "running"
    assert resumed["risk_state"] == "none"


def test_three_consecutive_unknown_jobs_pause_queue(test_db) -> None:
    for suffix in ("1", "2", "3"):
        _seed_action(test_db, suffix)
    executor_id, _token = _paired_executor(test_db)

    for suffix in ("1", "2", "3"):
        _complete_unknown(test_db, executor_id, suffix)

    snapshot = boss_executor.executor_snapshot(test_db, executor_id)["executor"]
    assert snapshot["queue_state"] == "risk_paused"
    assert snapshot["risk_state"] == "consecutive_unknown_after_dispatch"


def test_manual_unknown_verification_returns_uncontacted_job_for_reapproval(test_db) -> None:
    job_id, review_id, action_id = _seed_action(test_db)
    with test_db.connect() as connection:
        connection.execute(
            "UPDATE fj_automation_actions SET idempotency_key = ? WHERE id = ?",
            (f"boss:{job_id}:BOSS_DEFAULT_GREETING", action_id),
        )
    executor_id, _token = _paired_executor(test_db)
    _complete_unknown(test_db, executor_id, "1")

    verified = boss_executor.manual_verify_unknown_action(
        test_db, action_id, contacted=False, note="页面显示立即沟通",
    )
    assert verified["execution_state"] == "cancelled"
    assert verified["verification_state"] == "manual_confirmed"
    with test_db.connect() as connection:
        assert connection.execute(
            "SELECT status FROM fj_review_items WHERE id = ?", (review_id,)
        ).fetchone()[0] == "pending"

    _review, reapproved = workflow.approve_review_item(
        test_db, review_id, message="", allow_override=False,
    )
    assert reapproved["id"] == action_id
    assert reapproved["execution_state"] == "queued"
    assert reapproved["last_status_code"] == "REAPPROVED_AFTER_MANUAL_NOT_CONTACTED"


def test_code_zero_is_completed_without_waiting_for_verification(test_db) -> None:
    executor_id, action = _prepare_dispatch(test_db)

    result = boss_executor.complete_executor_action(test_db, executor_id, str(action["id"]), {
        "execution_epoch": action["execution_epoch"], "outcome": "accepted", "contacted": None,
        "status_code": "BOSS_REQUEST_ACCEPTED", "message": "平台已受理",
        "evidence": {"responseCode": 0, "token": "must-not-be-saved"},
    })

    assert result["execution_state"] == "succeeded"
    assert result["verification_state"] == "not_required"
    assert result["cooldown_seconds"] in {1, 2, 3}
    snapshot = boss_executor.executor_snapshot(test_db, executor_id)["executor"]
    assert snapshot["queue_state"] == "running"
    assert snapshot["risk_state"] == "none"
    assert snapshot["current_action_id"] is None
    with test_db.connect() as connection:
        stored = connection.execute(
            "SELECT result_json FROM fj_automation_actions WHERE id = ?", (action["id"],)
        ).fetchone()[0]
    assert "must-not-be-saved" not in stored


def test_failed_queue_supports_retry_and_cancel(test_db) -> None:
    executor_id, action = _prepare_dispatch(test_db)
    failed = boss_executor.complete_executor_action(test_db, executor_id, str(action["id"]), {
        "execution_epoch": action["execution_epoch"], "outcome": "failed", "contacted": False,
        "status_code": "BOSS_REQUEST_REJECTED", "message": "平台拒绝", "evidence": {},
    })
    assert failed["execution_state"] == "failed_after_dispatch"
    queue = boss_executor.list_queue(test_db)
    assert queue["actions"] == []
    assert [item["id"] for item in queue["failed_actions"]] == [action["id"]]

    retried = boss_executor.retry_failed_action(test_db, str(action["id"]))
    assert retried["action"]["execution_state"] == "queued"
    assert [item["id"] for item in retried["queue"]["actions"]] == [action["id"]]
    assert retried["queue"]["failed_actions"] == []

    executor_two, action_two = _prepare_dispatch(test_db, suffix="2")
    boss_executor.complete_executor_action(test_db, executor_two, str(action_two["id"]), {
        "execution_epoch": action_two["execution_epoch"], "outcome": "failed", "contacted": False,
        "status_code": "BOSS_REQUEST_REJECTED", "message": "平台拒绝", "evidence": {},
    })
    cancelled = boss_executor.cancel_failed_action(test_db, str(action_two["id"]))
    with pytest.raises(AppError) as error:
        boss_executor.cancel_failed_action(test_db, str(action_two["id"]))
    assert error.value.error_category == "CANCEL_NOT_ALLOWED"
    assert cancelled["queue"]["failed_actions"] == []


def test_dispatch_result_timeout_becomes_unknown_without_immediate_pause(test_db) -> None:
    _seed_action(test_db)
    executor_id, _token = _paired_executor(test_db)
    action = boss_executor.claim_next_action(test_db, executor_id, open_page=lambda _url: "target")
    assert action
    boss_executor.report_page_status(test_db, executor_id, action["id"], {
        "execution_epoch": action["execution_epoch"], "state": "ready", "logged_in": True,
        "page_kind": "detail", "encrypt_job_id": "encrypt-1", "contacted": False, "reason": "ready",
    })
    boss_executor.mark_dispatch_started(test_db, executor_id, action["id"], int(action["execution_epoch"]))
    with test_db.connect() as connection:
        connection.execute(
            "UPDATE fj_automation_actions SET dispatch_started_at = '2000-01-01T00:00:00Z' WHERE id = ?",
            (action["id"],),
        )

    boss_executor.sweep_page_timeout(test_db, executor_id)

    with test_db.connect() as connection:
        row = connection.execute(
            "SELECT execution_state FROM fj_automation_actions WHERE id = ?", (action["id"],)
        ).fetchone()
    assert row["execution_state"] == "unknown_after_dispatch"
    assert boss_executor.executor_snapshot(test_db, executor_id)["executor"]["queue_state"] == "running"


def test_unsent_action_can_return_to_review(test_db) -> None:
    _job_id, review_id, action_id = _seed_action(test_db)
    result = boss_executor.return_to_review(test_db, action_id, reason="用户撤回")
    assert result["execution_state"] == "cancelled"
    with test_db.connect() as connection:
        review_status = connection.execute("SELECT status FROM fj_review_items WHERE id = ?", (review_id,)).fetchone()[0]
    assert review_status == "pending"


def test_legacy_unsent_custom_action_migrates_back_to_review(configured_app_paths) -> None:
    db = Database(configured_app_paths["sqlite_path"])
    db.initialize()
    job_id, review_id, action_id = _seed_action(db, "9")
    with db.connect() as connection:
        action = connection.execute(
            "SELECT * FROM fj_automation_actions WHERE id = ?", (action_id,)
        ).fetchone()
        connection.execute("DROP TABLE fj_automation_actions")
        connection.execute(
            """
            CREATE TABLE fj_automation_actions (
              id TEXT PRIMARY KEY, job_id TEXT NOT NULL, evaluation_id TEXT NOT NULL,
              review_item_id TEXT NOT NULL, action_type TEXT NOT NULL DEFAULT 'start_conversation',
              status TEXT NOT NULL DEFAULT 'queued', idempotency_key TEXT NOT NULL UNIQUE,
              payload_json TEXT NOT NULL DEFAULT '{}', lease_owner TEXT, lease_expires_at TEXT,
              attempt_count INTEGER NOT NULL DEFAULT 0, last_error TEXT,
              created_at TEXT NOT NULL, updated_at TEXT NOT NULL, completed_at TEXT,
              CHECK (action_type IN ('start_conversation')),
              CHECK (status IN ('queued','leased','succeeded','failed','blocked','unknown','cancelled'))
            )
            """
        )
        connection.execute(
            """
            INSERT INTO fj_automation_actions (
              id, job_id, evaluation_id, review_item_id, action_type, status,
              idempotency_key, payload_json, attempt_count, created_at, updated_at
            ) VALUES (?, ?, ?, ?, 'start_conversation', 'queued', ?, ?, 0, ?, ?)
            """,
            (
                action["id"], job_id, action["evaluation_id"], review_id,
                f"boss:{job_id}:start_conversation", '{"message":"旧自定义文本"}',
                action["created_at"], action["updated_at"],
            ),
        )

    db.initialize()

    with db.connect() as connection:
        migrated = connection.execute(
            "SELECT action_type, status, execution_state, last_error FROM fj_automation_actions WHERE id = ?",
            (action_id,),
        ).fetchone()
        review = connection.execute("SELECT status FROM fj_review_items WHERE id = ?", (review_id,)).fetchone()
    assert migrated["action_type"] == "start_conversation"
    assert migrated["status"] == "cancelled"
    assert migrated["execution_state"] == "cancelled"
    assert "重新批准默认招呼动作" in migrated["last_error"]
    assert review["status"] == "pending"
