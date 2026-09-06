from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

from backend.app.services.fine_job.boss_capture_history import (
    create_capture_batch,
    record_capture_jobs,
)
from backend.app.services.fine_job.job_activity import append_job_activity
from backend.app.services.fine_job import boss_chat, job_action_center


EVENT_TIME = "2026-09-05T11:00:00Z"


def _seed_reply_action(configured_client, suffix: str = "api") -> tuple[str, str, str, str]:
    db = configured_client.app.state.db
    capture_id = f"capture-job-action-{suffix}"
    source_job_id = f"source-job-action-{suffix}"
    create_capture_batch(
        db,
        capture_id=capture_id,
        keyword="Python",
        city="广州",
        pages=1,
        auto_details=False,
        created_at="2026-09-05T10:00:00Z",
    )
    record_capture_jobs(
        db,
        capture_id=capture_id,
        jobs=[{
            "job_id": source_job_id,
            "encrypt_job_id": f"encrypt-{suffix}",
            "title": "后端工程师",
            "boss_name": "示例公司",
            "job_link": f"https://www.zhipin.com/job_detail/{suffix}.html",
        }],
        collected_at="2026-09-05T10:00:00Z",
    )
    session_id = f"session-job-action-{suffix}"
    message_id = f"message-job-action-{suffix}"
    with db.connect() as connection:
        job = connection.execute(
            "SELECT id FROM fj_boss_jobs WHERE source_job_id = ?",
            (source_job_id,),
        ).fetchone()
        assert job is not None
        job_id = str(job["id"])
        connection.execute(
            """
            INSERT INTO fj_chat_sessions (
              id, account_uid, peer_uid, job_id, status, session_version,
              latest_message_id, latest_inbound_message_id, last_message_at,
              created_at, updated_at
            ) VALUES (?, 'candidate-1', ?, ?, 'active', 1,
                      ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                f"boss-{suffix}",
                job_id,
                message_id,
                message_id,
                EVENT_TIME,
                EVENT_TIME,
                EVENT_TIME,
            ),
        )
        connection.execute(
            """
            INSERT INTO fj_chat_messages (
              id, session_id, platform_message_id, direction, message_type,
              content, sender_uid, receiver_uid, source,
              sent_at, observed_at, created_at
            ) VALUES (?, ?, ?, 'inbound', 'text', '请介绍一下自己',
                      ?, 'candidate-1', 'websocket', ?, ?, ?)
            """,
            (
                message_id,
                session_id,
                f"platform-{message_id}",
                f"boss-{suffix}",
                EVENT_TIME,
                EVENT_TIME,
                EVENT_TIME,
            ),
        )
    append_job_activity(
        db,
        job_id=job_id,
        chat_session_id=session_id,
        event_type="recruiter_replied",
        occurred_at=EVENT_TIME,
        source="chat",
        source_ref_type="chat_message",
        source_ref_id=message_id,
        dedupe_key=f"job-action-api:{suffix}",
    )
    response = configured_client.get("/api/fine-job/job-actions")
    assert response.status_code == 200
    action_key = response.json()["items"][0]["action_key"]
    return job_id, session_id, message_id, action_key


def test_job_action_state_lifecycle_and_expired_snooze(configured_client) -> None:
    _, _, _, action_key = _seed_reply_action(configured_client, "state")
    encoded_key = action_key.replace(":", "%3A")

    snoozed = configured_client.post(
        f"/api/fine-job/job-actions/{encoded_key}/snooze",
        json={"snoozed_until": "2099-09-09T10:00:00Z"},
    )
    assert snoozed.status_code == 200
    assert snoozed.json()["item"]["state"] == "snoozed"
    assert configured_client.get("/api/fine-job/job-actions").json()["items"] == []
    snoozed_list = configured_client.get(
        "/api/fine-job/job-actions", params={"status": "snoozed"}
    ).json()
    assert len(snoozed_list["items"]) == 1
    assert snoozed_list["summary"]["snoozed"] == 1

    with configured_client.app.state.db.connect() as connection:
        connection.execute(
            """
            UPDATE fj_job_action_item_states
            SET snoozed_until = '2000-01-01T00:00:00Z'
            WHERE action_key = ?
            """,
            (action_key,),
        )
    active = configured_client.get("/api/fine-job/job-actions").json()
    assert len(active["items"]) == 1
    assert active["items"][0]["state"] == "active"
    assert active["summary"]["snoozed"] == 0
    assert configured_client.get(
        "/api/fine-job/job-actions", params={"status": "snoozed"}
    ).json()["items"] == []

    dismissed = configured_client.post(
        f"/api/fine-job/job-actions/{encoded_key}/dismiss"
    )
    assert dismissed.status_code == 200
    assert dismissed.json()["state"] == "dismissed"
    assert len(configured_client.get(
        "/api/fine-job/job-actions", params={"status": "dismissed"}
    ).json()["items"]) == 1

    restored = configured_client.post(
        f"/api/fine-job/job-actions/{encoded_key}/restore"
    )
    assert restored.status_code == 200
    assert restored.json()["state"] == "active"

    completed = configured_client.post(
        f"/api/fine-job/job-actions/{encoded_key}/complete"
    )
    assert completed.status_code == 200
    assert completed.json()["state"] == "completed"
    assert len(configured_client.get(
        "/api/fine-job/job-actions", params={"status": "completed"}
    ).json()["items"]) == 1


def test_state_write_rejects_unknown_and_expired_action_keys(configured_client) -> None:
    db = configured_client.app.state.db
    unknown = configured_client.post(
        "/api/fine-job/job-actions/arbitrary-value/dismiss"
    )
    assert unknown.status_code == 404
    with db.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM fj_job_action_item_states"
        ).fetchone()[0] == 0

    job_id, session_id, _, action_key = _seed_reply_action(configured_client, "expired")
    encoded_key = action_key.replace(":", "%3A")
    assert configured_client.post(
        f"/api/fine-job/job-actions/{encoded_key}/dismiss"
    ).status_code == 200

    outbound_id = "message-job-action-outbound"
    with db.connect() as connection:
        connection.execute(
            """
            INSERT INTO fj_chat_messages (
              id, session_id, platform_message_id, direction, message_type,
              content, sender_uid, receiver_uid, source,
              sent_at, observed_at, created_at
            ) VALUES (?, ?, ?, 'outbound', 'text', '已经回复',
                      'candidate-1', 'boss-1', 'websocket',
                      '2026-09-05T12:00:00Z', '2026-09-05T12:00:00Z',
                      '2026-09-05T12:00:00Z')
            """,
            (outbound_id, session_id, f"platform-{outbound_id}"),
        )
        connection.execute(
            """
            UPDATE fj_chat_sessions
            SET session_version = 2, latest_message_id = ?,
                last_message_at = '2026-09-05T12:00:00Z',
                updated_at = '2026-09-05T12:00:00Z'
            WHERE id = ?
            """,
            (outbound_id, session_id),
        )
    append_job_activity(
        db,
        job_id=job_id,
        chat_session_id=session_id,
        event_type="candidate_replied",
        occurred_at="2026-09-05T12:00:00Z",
        source="chat",
        source_ref_type="chat_message",
        source_ref_id=outbound_id,
        dedupe_key="job-action-api:expired:outbound",
    )

    expired = configured_client.post(
        f"/api/fine-job/job-actions/{encoded_key}/complete"
    )
    assert expired.status_code == 409
    assert expired.json()["error_category"] == "JOB_ACTION_EXPIRED"

    # restore 只依赖已有用户状态，当前业务触发失效后仍可清理该状态。
    restored = configured_client.post(
        f"/api/fine-job/job-actions/{encoded_key}/restore"
    )
    assert restored.status_code == 200
    assert restored.json()["state"] is None
    assert restored.json()["item"] is None


def test_job_action_filters_validate_enums(configured_client) -> None:
    invalid = configured_client.get(
        "/api/fine-job/job-actions", params={"priority": "critical"}
    )
    assert invalid.status_code == 422


def test_batch_generate_is_idempotent_and_never_creates_send_action(configured_client) -> None:
    _, _, _, action_key = _seed_reply_action(configured_client, "batch-idempotent")

    first = configured_client.post(
        "/api/fine-job/job-actions/generate-drafts",
        json={"action_keys": [action_key]},
    )
    second = configured_client.post(
        "/api/fine-job/job-actions/generate-drafts",
        json={"action_keys": [action_key]},
    )

    assert first.status_code == 200
    assert first.json()["results"][0]["status"] == "created"
    assert second.status_code == 200
    assert second.json()["results"][0]["status"] == "already_exists"
    assert second.json()["results"][0]["reply_task_id"] == first.json()["results"][0]["reply_task_id"]
    with configured_client.app.state.db.connect() as connection:
        tasks = connection.execute(
            "SELECT id, status, job_action_key FROM fj_chat_reply_tasks"
        ).fetchall()
        send_count = connection.execute("SELECT COUNT(*) FROM fj_chat_send_actions").fetchone()[0]
    assert len(tasks) == 1
    assert tasks[0]["status"] == "awaiting_review"
    assert tasks[0]["job_action_key"] == action_key
    assert send_count == 0


@pytest.mark.parametrize("task_status", ["pending_generation", "generating"])
def test_batch_reports_generation_in_progress_as_already_exists(
    configured_client,
    task_status: str,
) -> None:
    _, session_id, message_id, action_key = _seed_reply_action(
        configured_client,
        f"batch-{task_status}",
    )
    with configured_client.app.state.db.connect() as connection:
        connection.execute(
            """
            INSERT INTO fj_chat_reply_tasks (
              id, session_id, trigger_source, action_kind, status,
              based_on_message_id, based_on_session_version, created_at, updated_at
            ) VALUES (?, ?, 'manual', 'reply', ?, ?, 1, ?, ?)
            """,
            (
                f"task-{task_status}",
                session_id,
                task_status,
                message_id,
                EVENT_TIME,
                EVENT_TIME,
            ),
        )

    response = configured_client.post(
        "/api/fine-job/job-actions/generate-drafts",
        json={"action_keys": [action_key]},
    )

    result = response.json()["results"][0]
    assert result["status"] == "already_exists"
    assert result["reply_task_id"] == f"task-{task_status}"


def test_batch_revalidates_stale_action_and_skips_it(configured_client) -> None:
    job_id, session_id, _, action_key = _seed_reply_action(configured_client, "batch-stale")
    outbound_id = "message-job-action-batch-stale-outbound"
    with configured_client.app.state.db.connect() as connection:
        connection.execute(
            """
            INSERT INTO fj_chat_messages (
              id, session_id, platform_message_id, direction, message_type,
              content, sender_uid, receiver_uid, source, sent_at, observed_at, created_at
            ) VALUES (?, ?, ?, 'outbound', 'text', '已经处理',
                      'candidate-1', 'boss-1', 'websocket', ?, ?, ?)
            """,
            (
                outbound_id,
                session_id,
                f"platform-{outbound_id}",
                "2026-09-05T12:00:00Z",
                "2026-09-05T12:00:00Z",
                "2026-09-05T12:00:00Z",
            ),
        )
        connection.execute(
            """
            UPDATE fj_chat_sessions
            SET session_version = 2, latest_message_id = ?, last_message_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (outbound_id, "2026-09-05T12:00:00Z", "2026-09-05T12:00:00Z", session_id),
        )
    append_job_activity(
        configured_client.app.state.db,
        job_id=job_id,
        chat_session_id=session_id,
        event_type="candidate_replied",
        occurred_at="2026-09-05T12:00:00Z",
        source="chat",
        source_ref_type="chat_message",
        source_ref_id=outbound_id,
        dedupe_key="job-action-api:batch-stale:outbound",
    )

    response = configured_client.post(
        "/api/fine-job/job-actions/generate-drafts",
        json={"action_keys": [action_key]},
    )

    assert response.status_code == 200
    assert response.json()["results"][0]["status"] == "skipped"
    with configured_client.app.state.db.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM fj_chat_reply_tasks").fetchone()[0] == 0


def test_batch_item_failure_does_not_block_later_items(configured_client, monkeypatch) -> None:
    _, failed_session, _, failed_key = _seed_reply_action(configured_client, "batch-failed")
    _, _, _, created_key = _seed_reply_action(configured_client, "batch-created")
    original = boss_chat.generate_reply_for_action

    def selective_generate(db, config, session_id, *, action_kind, job_action_key):
        if session_id == failed_session:
            raise RuntimeError("单项生成失败")
        return original(
            db,
            config,
            session_id,
            action_kind=action_kind,
            job_action_key=job_action_key,
        )

    monkeypatch.setattr(boss_chat, "generate_reply_for_action", selective_generate)
    response = configured_client.post(
        "/api/fine-job/job-actions/generate-drafts",
        json={"action_keys": [failed_key, created_key]},
    )

    assert response.status_code == 200
    assert [item["status"] for item in response.json()["results"]] == ["failed", "created"]
    with configured_client.app.state.db.connect() as connection:
        created = connection.execute(
            "SELECT status FROM fj_chat_reply_tasks WHERE job_action_key = ?",
            (created_key,),
        ).fetchone()
        failed = connection.execute(
            "SELECT 1 FROM fj_chat_reply_tasks WHERE job_action_key = ?",
            (failed_key,),
        ).fetchone()
    assert created is not None and created["status"] == "awaiting_review"
    assert failed is None


def test_concurrent_batch_and_single_generation_share_one_reply_task(configured_client) -> None:
    _, session_id, _, action_key = _seed_reply_action(configured_client, "batch-race")
    db = configured_client.app.state.db
    config = configured_client.app.state.config

    def batch_generate():
        return job_action_center.generate_job_action_drafts(db, config, [action_key])

    def single_generate():
        return boss_chat.generate_reply(
            db,
            config,
            session_id,
            action_kind="reply",
            job_action_key=action_key,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        batch_future = executor.submit(batch_generate)
        single_future = executor.submit(single_generate)
        batch_result = batch_future.result()
        single_task = single_future.result()

    assert batch_result["results"][0]["status"] in {"created", "already_exists"}
    assert single_task["id"] == batch_result["results"][0]["reply_task_id"]
    with db.connect() as connection:
        task_count = connection.execute(
            """
            SELECT COUNT(*) FROM fj_chat_reply_tasks
            WHERE job_action_key = ?
              AND status IN ('pending_generation', 'generating', 'awaiting_review', 'confirmed')
            """,
            (action_key,),
        ).fetchone()[0]
        send_count = connection.execute("SELECT COUNT(*) FROM fj_chat_send_actions").fetchone()[0]
    assert task_count == 1
    assert send_count == 0


def test_concurrent_batch_requests_share_one_reply_task(configured_client) -> None:
    _, _, _, action_key = _seed_reply_action(configured_client, "batch-concurrent")
    db = configured_client.app.state.db
    config = configured_client.app.state.config

    def generate():
        return job_action_center.generate_job_action_drafts(db, config, [action_key])

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = [future.result() for future in [executor.submit(generate), executor.submit(generate)]]

    statuses = sorted(result["results"][0]["status"] for result in results)
    assert statuses == ["already_exists", "created"]
    with db.connect() as connection:
        assert connection.execute(
            """
            SELECT COUNT(*) FROM fj_chat_reply_tasks
            WHERE job_action_key = ?
              AND status IN ('pending_generation', 'generating', 'awaiting_review', 'confirmed')
            """,
            (action_key,),
        ).fetchone()[0] == 1
