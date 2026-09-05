from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from backend.app.db import Database
from backend.app.services.fine_job import boss_chat, job_hunt_refresh
from backend.app.services.fine_job.boss_capture_history import (
    create_capture_batch,
    record_capture_jobs,
    update_capture_job_detail,
)
from backend.app.utils import new_id


def _insert_session(
    db: Database,
    *,
    session_id: str,
    peer_uid: str,
    changed_at: str,
    encrypt_job_id: str = "",
) -> None:
    with db.connect() as connection:
        connection.execute(
            """
            INSERT INTO fj_chat_sessions (
              id, account_uid, peer_uid, encrypt_peer_uid, security_id,
              encrypt_job_id, platform_latest_message_at, status, created_at, updated_at
            ) VALUES (?, 'candidate', ?, ?, ?, ?, ?, 'human_takeover', ?, ?)
            """,
            (
                session_id,
                peer_uid,
                f"encrypt-{peer_uid}",
                f"security-{peer_uid}",
                encrypt_job_id,
                changed_at,
                changed_at,
                changed_at,
            ),
        )


def _options(**changes: bool) -> dict[str, bool]:
    options = {
        "refresh_chat_list": False,
        "refresh_chat_messages": True,
        "refresh_related_jobs": True,
    }
    options.update(changes)
    return options


def _insert_message(
    db: Database,
    *,
    message_id: str,
    session_id: str,
    platform_message_id: str,
    sent_at: str,
    source: str = "websocket",
) -> None:
    with db.connect() as connection:
        connection.execute(
            """
            INSERT INTO fj_chat_messages (
              id, session_id, platform_message_id, direction, message_type,
              content, source, sent_at, observed_at, created_at
            ) VALUES (?, ?, ?, 'outbound', 'text', '消息', ?, ?, ?, ?)
            """,
            (
                message_id,
                session_id,
                platform_message_id,
                source,
                sent_at,
                sent_at,
                sent_at,
            ),
        )


def _insert_scope(
    db: Database,
    session_ids: list[str],
    *,
    include_job_items: bool = False,
) -> str:
    scope_id = new_id()
    now = "2026-09-05T03:00:00Z"
    with db.connect() as connection:
        relations = []
        for session_id in session_ids:
            session = dict(connection.execute(
                "SELECT * FROM fj_chat_sessions WHERE id = ?", (session_id,)
            ).fetchone())
            identity, job = job_hunt_refresh._related_job_identity(connection, session)
            if identity:
                relations.append({
                    "entity_id": identity,
                    "session_id": session_id,
                    "job_id": str(job["id"]) if job else None,
                    "encrypt_job_id": str(session.get("encrypt_job_id") or "") or None,
                })
        jobs_to_collect = relations if include_job_items else []
        counts = {
            "refreshed_sessions": len(session_ids),
            "sessions_to_sync": len(session_ids),
            "new_sessions_to_sync": 0,
            "related_jobs": len(relations),
            "jobs_to_collect": len(jobs_to_collect),
            "jobs_missing_jd": 0,
            "jobs_missing_evaluation": 0,
            "unresolved_relations": len(session_ids) - len(relations),
        }
        connection.execute(
            """
            INSERT INTO fj_job_hunt_refresh_scopes (
              id, selected_since_time, account_uid, source_url,
              friend_list_synced_at, scope_generated_at, session_ids_json,
              new_session_ids_json, related_jobs_json, jobs_to_collect_json,
              jobs_missing_jd_json, jobs_missing_evaluation_json,
              unresolved_session_ids_json, counts_json, friend_list_result_json,
              created_at
            ) VALUES (?, '2026-09-04T00:00:00Z', 'candidate', 'test', ?, ?, ?, '[]', ?, ?,
                      '[]', '[]', '[]', ?, '{}', ?)
            """,
            (
                scope_id,
                now,
                now,
                json.dumps(session_ids),
                json.dumps(relations),
                json.dumps(jobs_to_collect),
                json.dumps(counts),
                now,
            ),
        )
    return scope_id


def test_scope_discovery_uses_friend_refresh_result_and_selected_platform_time(
    test_db: Database,
    monkeypatch,
) -> None:
    _insert_session(
        test_db,
        session_id="existing-session",
        peer_uid="existing-peer",
        changed_at="2026-09-05T02:00:00Z",
        encrypt_job_id="existing-job",
    )
    _insert_session(
        test_db,
        session_id="existing-job-loaded-session",
        peer_uid="loaded-peer",
        changed_at="2026-09-05T02:00:00Z",
        encrypt_job_id="existing-job",
    )
    with test_db.connect() as connection:
        connection.execute(
            "UPDATE fj_chat_sessions SET platform_latest_msg_id = 'existing-latest' WHERE id = 'existing-session'"
        )
        connection.execute(
            "UPDATE fj_chat_sessions SET platform_latest_msg_id = 'loaded-latest' WHERE id = 'existing-job-loaded-session'"
        )
    _insert_message(
        test_db,
        message_id="loaded-local-message",
        session_id="existing-job-loaded-session",
        platform_message_id="loaded-latest",
        sent_at="2026-09-05T02:00:00Z",
    )
    recent_ms = int(datetime(2026, 9, 5, 2, tzinfo=UTC).timestamp() * 1_000)
    old_ms = int(datetime(2026, 9, 1, 2, tzinfo=UTC).timestamp() * 1_000)
    response = {"zpData": {"result": [
        {
            "uid": "existing-peer", "encryptFriendId": "encrypt-existing-peer",
            "securityId": "security-existing-peer", "encryptJobId": "existing-job",
            "lastMessageInfo": {"msgId": "existing-latest", "msgTime": recent_ms},
        },
        {
            "uid": "new-peer", "encryptFriendId": "encrypt-new-peer",
            "securityId": "security-new-peer", "encryptJobId": "new-job",
            "lastMessageInfo": {"msgId": "new-latest", "msgTime": recent_ms},
        },
        {
            "uid": "loaded-peer", "encryptFriendId": "encrypt-loaded-peer",
            "securityId": "security-loaded-peer", "encryptJobId": "existing-job",
            "lastMessageInfo": {"msgId": "loaded-latest", "msgTime": recent_ms},
        },
        {
            "uid": "old-peer", "encryptFriendId": "encrypt-old-peer",
            "securityId": "security-old-peer", "encryptJobId": "old-job",
            "lastMessageInfo": {"msgId": "old-latest", "msgTime": old_ms},
        },
    ]}}
    monkeypatch.setattr(
        job_hunt_refresh.boss_scraper_service,
        "capture_chat_friend_list",
        lambda: {"account_uid": "candidate", "response": response, "url": "test"},
    )
    table_names = (
        "fj_chat_messages",
        "fj_boss_jobs",
        "fj_job_activity_events",
        "fj_job_pipeline_snapshots",
        "fj_job_hunt_refresh_runs",
        "fj_job_hunt_refresh_items",
    )
    with test_db.connect() as connection:
        before = {
            table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in table_names
        }

    result = job_hunt_refresh.discover_scope(test_db, "2026-09-04T00:00:00Z")

    with test_db.connect() as connection:
        after = {
            table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in table_names
        }
    assert result["counts"]["sessions_to_sync"] == 2
    assert result["counts"]["sessions_in_scope"] == 3
    assert result["scope_source"] == "refresh"
    assert result["counts"]["new_sessions_to_sync"] == 1
    assert result["counts"]["chat_update_jobs"] == 2
    assert result["counts"]["extra_jobs"] == 0
    assert result["counts"]["jobs_to_update"] == 2
    assert result["counts"]["jobs_to_collect"] == 0
    assert result["session_ids_to_sync"][0] == "existing-session"
    assert "old-peer" not in result["session_ids_to_sync"]
    assert len(result["new_session_ids"]) == 2
    assert before == after


def test_latest_local_chat_excludes_synthetic_assistant_placeholder(
    test_db: Database,
) -> None:
    _insert_session(
        test_db,
        session_id="latest-session",
        peer_uid="latest-peer",
        changed_at="2026-09-05T01:00:00Z",
    )
    _insert_message(
        test_db,
        message_id="real-websocket",
        session_id="latest-session",
        platform_message_id="platform-1",
        sent_at="2026-09-05T01:00:00Z",
    )
    _insert_message(
        test_db,
        message_id="real-echo",
        session_id="latest-session",
        platform_message_id="platform-echo",
        sent_at="2026-09-05T02:00:00Z",
        source="assistant",
    )
    _insert_message(
        test_db,
        message_id="synthetic-placeholder",
        session_id="latest-session",
        platform_message_id="assistant:send-action-1",
        sent_at="2026-09-05T03:00:00Z",
        source="assistant",
    )

    context = job_hunt_refresh.get_refresh_context(test_db)

    assert context["latest_local_message_at"] == "2026-09-05T02:00:00Z"


def test_local_scope_does_not_call_platform_and_keeps_synced_session_in_job_scope(
    test_db: Database,
    monkeypatch,
) -> None:
    create_capture_batch(
        test_db,
        capture_id="scope-local-job",
        keyword="本地范围岗位",
        city="",
        pages=1,
        auto_details=False,
        created_at="2026-09-05T00:00:00Z",
    )
    job = record_capture_jobs(
        test_db,
        capture_id="scope-local-job",
        search_keyword="本地范围岗位",
        jobs=[{
            "job_id": "local-source-job",
            "encrypt_job_id": "local-encrypt-job",
            "title": "后端工程师",
            "boss_name": "本地公司",
            "salary": "20-30K",
            "location": "上海",
        }],
        collected_at="2026-09-05T00:10:00Z",
    )[0]
    update_capture_job_detail(
        test_db,
        job={"history_record_id": job["history_record_id"]},
        detail={"description": "负责服务开发。"},
        status="completed",
    )
    _insert_session(
        test_db,
        session_id="local-synced-session",
        peer_uid="local-synced-peer",
        changed_at="2026-09-05T02:00:00Z",
        encrypt_job_id="local-encrypt-job",
    )
    _insert_session(
        test_db,
        session_id="local-stale-session",
        peer_uid="local-stale-peer",
        changed_at="2026-09-05T02:01:00Z",
    )
    _insert_session(
        test_db,
        session_id="local-extra-session",
        peer_uid="local-extra-peer",
        changed_at="2026-09-05T02:02:00Z",
        encrypt_job_id="local-extra-job",
    )
    with test_db.connect() as connection:
        connection.execute(
            """
            UPDATE fj_chat_sessions
            SET platform_latest_msg_id = 'local-platform-message',
                platform_synced_at = '2026-09-05T02:05:00Z'
            WHERE id = 'local-synced-session'
            """
        )
        connection.execute(
            """
            UPDATE fj_chat_sessions
            SET platform_synced_at = '2026-09-05T01:00:00Z'
            WHERE id = 'local-stale-session'
            """
        )
        connection.execute(
            """
            UPDATE fj_chat_sessions
            SET platform_synced_at = '2026-09-05T02:05:00Z'
            WHERE id = 'local-extra-session'
            """
        )
    _insert_message(
        test_db,
        message_id="local-loaded-message",
        session_id="local-synced-session",
        platform_message_id="local-platform-message",
        sent_at="2026-09-05T02:00:00Z",
    )
    _insert_message(
        test_db,
        message_id="local-extra-loaded-message",
        session_id="local-extra-session",
        platform_message_id="local-extra-platform-message",
        sent_at="2026-09-05T02:02:00Z",
    )
    capture = monkeypatch.setattr(
        job_hunt_refresh.boss_scraper_service,
        "capture_chat_friend_list",
        lambda: (_ for _ in ()).throw(AssertionError("local 模式不应访问 BOSS")),
    )

    scope = job_hunt_refresh.discover_scope(
        test_db,
        "2026-09-04T00:00:00Z",
        "local",
    )

    assert capture is None
    assert scope["scope_source"] == "local"
    assert scope["counts"]["sessions_in_scope"] == 2
    assert set(scope["session_ids_in_scope"]) == {
        "local-extra-session",
        "local-synced-session",
    }
    assert scope["counts"]["sessions_to_sync"] == 0
    assert scope["counts"]["related_jobs"] == 2
    assert scope["counts"]["chat_update_jobs"] == 0
    assert scope["counts"]["extra_jobs"] == 1
    assert scope["counts"]["jobs_to_update"] == 1
    assert scope["counts"]["jobs_to_collect"] == 1
    assert scope["counts"]["jobs_missing_evaluation"] == 2


def test_auto_uses_fresh_local_list_and_refreshes_stale_list(
    test_db: Database,
    monkeypatch,
) -> None:
    now = datetime.now(UTC).replace(microsecond=0)
    _insert_session(
        test_db,
        session_id="auto-session",
        peer_uid="auto-peer",
        changed_at=(now - timedelta(minutes=5)).isoformat().replace("+00:00", "Z"),
    )
    with test_db.connect() as connection:
        connection.execute(
            "UPDATE fj_chat_sessions SET platform_synced_at = ? WHERE id = 'auto-session'",
            ((now - timedelta(minutes=10)).isoformat().replace("+00:00", "Z"),),
        )
    capture_calls = 0

    def capture_friend_list():
        nonlocal capture_calls
        capture_calls += 1
        return {"account_uid": "candidate", "response": {"zpData": {"result": []}}, "url": "test"}

    monkeypatch.setattr(
        job_hunt_refresh.boss_scraper_service,
        "capture_chat_friend_list",
        capture_friend_list,
    )
    selected = (now - timedelta(hours=1)).isoformat().replace("+00:00", "Z")

    fresh_scope = job_hunt_refresh.discover_scope(test_db, selected, "auto")
    with test_db.connect() as connection:
        connection.execute(
            "UPDATE fj_chat_sessions SET platform_synced_at = ? WHERE id = 'auto-session'",
            ((now - timedelta(minutes=31)).isoformat().replace("+00:00", "Z"),),
        )
    stale_scope = job_hunt_refresh.discover_scope(test_db, selected, "auto")
    refresh_scope = job_hunt_refresh.discover_scope(test_db, selected, "refresh")

    assert fresh_scope["scope_source"] == "local"
    assert stale_scope["scope_source"] == "refresh"
    assert refresh_scope["requested_source_mode"] == "refresh"
    assert capture_calls == 2


def test_run_and_items_survive_database_reinitialization_and_skip_succeeded(
    test_db: Database,
) -> None:
    for index in range(2):
        _insert_session(
            test_db,
            session_id=f"session-{index}",
            peer_uid=f"peer-{index}",
            changed_at="2026-09-05T02:00:00Z",
        )
    scope_id = _insert_scope(test_db, ["session-0", "session-1"])
    run = job_hunt_refresh.create_run(
        test_db,
        scope_id=scope_id,
        workflow_options=_options(refresh_related_jobs=False),
    )
    succeeded_id = str(run["items"][0]["id"])
    with test_db.connect() as connection:
        connection.execute(
            """
            UPDATE fj_job_hunt_refresh_items
            SET status = 'succeeded', retryable = 0, completed_at = updated_at
            WHERE id = ?
            """,
            (succeeded_id,),
        )

    reopened = Database(test_db.sqlite_path)
    reopened.initialize()
    persisted = job_hunt_refresh.get_run(reopened, str(run["id"]))
    actionable = job_hunt_refresh.list_actionable_items(
        reopened,
        str(run["id"]),
        item_type="chat_session",
    )

    assert len(persisted["items"]) == 2
    assert len(actionable) == 1
    assert actionable[0]["id"] != succeeded_id


def test_run_items_are_copied_from_scope_without_recalculating_database(
    test_db: Database,
) -> None:
    _insert_session(
        test_db,
        session_id="scope-session",
        peer_uid="scope-peer",
        changed_at="2026-09-05T02:00:00Z",
    )
    scope_id = _insert_scope(test_db, ["scope-session"])
    _insert_session(
        test_db,
        session_id="later-session",
        peer_uid="later-peer",
        changed_at="2026-09-05T02:30:00Z",
    )

    run = job_hunt_refresh.create_run(
        test_db,
        scope_id=scope_id,
        workflow_options=_options(refresh_related_jobs=False),
    )

    assert run["scope_id"] == scope_id
    assert run["scope_generated_at"] == "2026-09-05T03:00:00Z"
    assert [item["session_id"] for item in run["items"]] == ["scope-session"]


def test_run_waits_for_prompt_then_can_be_cancelled_without_deleting_scope(
    test_db: Database,
) -> None:
    _insert_session(
        test_db,
        session_id="prompt-session",
        peer_uid="prompt-peer",
        changed_at="2026-09-05T02:00:00Z",
    )
    scope_id = _insert_scope(test_db, ["prompt-session"])
    run = job_hunt_refresh.create_run(
        test_db,
        scope_id=scope_id,
        workflow_options=_options(refresh_related_jobs=False),
    )

    assert run["current_step"] == "waiting_codex"
    attached = job_hunt_refresh.attach_codex_session(
        test_db,
        str(run["id"]),
        "codex-session-ready",
    )
    assert attached["current_step"] == "waiting_codex"
    submitted = job_hunt_refresh.mark_prompt_submitted(test_db, str(run["id"]))
    assert submitted["current_step"] == "waiting_chat_messages"
    assert submitted["prompt_submitted_at"] is not None

    cancelled = job_hunt_refresh.cancel_run(test_db, str(run["id"]))
    assert cancelled["status"] == "cancelled"
    assert cancelled["current_step"] == "cancelled"
    assert cancelled["items"][0]["status"] == "skipped"
    assert job_hunt_refresh.get_scope(test_db, scope_id)["id"] == scope_id


def test_database_upgrade_recovers_legacy_pending_run_waiting_for_codex(
    test_db: Database,
) -> None:
    _insert_session(
        test_db,
        session_id="legacy-waiting-session",
        peer_uid="legacy-waiting-peer",
        changed_at="2026-09-05T02:00:00Z",
    )
    scope_id = _insert_scope(test_db, ["legacy-waiting-session"])
    run = job_hunt_refresh.create_run(
        test_db,
        scope_id=scope_id,
        workflow_options=_options(refresh_related_jobs=False),
    )
    with test_db.connect() as connection:
        connection.execute(
            """
            UPDATE fj_job_hunt_refresh_runs
            SET current_step = 'waiting_chat_messages', codex_session_ref = NULL
            WHERE id = ?
            """,
            (run["id"],),
        )

    reopened = Database(test_db.sqlite_path)
    reopened.initialize()
    recovered = job_hunt_refresh.get_run(reopened, str(run["id"]))

    assert recovered["status"] == "pending"
    assert recovered["current_step"] == "waiting_codex"
    assert recovered["resume_available"] is True


def test_completed_with_errors_can_resume_only_retryable_items(test_db: Database) -> None:
    for index in range(2):
        _insert_session(
            test_db,
            session_id=f"retry-session-{index}",
            peer_uid=f"retry-peer-{index}",
            changed_at="2026-09-05T02:00:00Z",
        )
    scope_id = _insert_scope(test_db, ["retry-session-0", "retry-session-1"])
    run = job_hunt_refresh.create_run(
        test_db,
        scope_id=scope_id,
        workflow_options=_options(refresh_related_jobs=False),
    )
    succeeded_id = str(run["items"][0]["id"])
    failed_id = str(run["items"][1]["id"])
    with test_db.connect() as connection:
        connection.execute(
            "UPDATE fj_job_hunt_refresh_items SET status = 'succeeded', retryable = 0 WHERE id = ?",
            (succeeded_id,),
        )
        connection.execute(
            "UPDATE fj_job_hunt_refresh_items SET status = 'failed', retryable = 1 WHERE id = ?",
            (failed_id,),
        )

    completed = job_hunt_refresh.complete_run(test_db, str(run["id"]))
    resumed = job_hunt_refresh.attach_codex_session(
        test_db,
        str(run["id"]),
        "codex-resume-1",
    )
    submitted = job_hunt_refresh.mark_prompt_submitted(test_db, str(run["id"]))
    actionable = job_hunt_refresh.list_actionable_items(
        test_db,
        str(run["id"]),
        item_type="chat_session",
    )

    assert completed["status"] == "completed_with_errors"
    assert completed["resume_available"] is True
    assert resumed["status"] == "completed_with_errors"
    assert resumed["current_step"] == "waiting_codex"
    assert submitted["status"] == "running"
    assert [item["id"] for item in actionable] == [failed_id]


def test_message_refresh_reuses_existing_single_session_history_sync(
    test_db: Database,
    monkeypatch,
) -> None:
    _insert_session(
        test_db,
        session_id="scoped-session",
        peer_uid="scoped-peer",
        changed_at="2026-09-05T02:00:00Z",
    )
    old_time = int(datetime(2026, 9, 3, tzinfo=UTC).timestamp() * 1_000)
    new_time = int(datetime(2026, 9, 5, tzinfo=UTC).timestamp() * 1_000)

    def capture_chat_history(**_kwargs):
        return {
            "messages": [
                {
                    "mid": "old-message",
                    "time": old_time,
                    "from": {"uid": "scoped-peer"},
                    "to": {"uid": "candidate"},
                    "body": {"type": 1, "text": "旧消息"},
                },
                {
                    "mid": "new-message",
                    "time": new_time,
                    "from": {"uid": "scoped-peer"},
                    "to": {"uid": "candidate"},
                    "body": {"type": 1, "text": "新消息"},
                },
            ],
            "has_more": False,
            "next_cursor": "",
        }

    monkeypatch.setattr(
        job_hunt_refresh.boss_scraper_service,
        "capture_chat_history",
        capture_chat_history,
    )
    scope_id = _insert_scope(test_db, ["scoped-session"])
    run = job_hunt_refresh.create_run(
        test_db,
        scope_id=scope_id,
        workflow_options=_options(refresh_related_jobs=False),
    )
    item_id = str(run["items"][0]["id"])

    first = job_hunt_refresh.refresh_chat_messages(test_db, str(run["id"]), item_id)
    second = job_hunt_refresh.refresh_chat_messages(test_db, str(run["id"]), item_id)

    with test_db.connect() as connection:
        messages = connection.execute(
            "SELECT platform_message_id FROM fj_chat_messages ORDER BY platform_message_id"
        ).fetchall()
    assert first["item"]["result"]["inserted_count"] == 2
    assert second["reused"] is True
    assert [row["platform_message_id"] for row in messages] == ["new-message", "old-message"]


def test_message_batch_refresh_prepares_chat_page_and_uses_batch_manager(
    test_db: Database,
    monkeypatch,
) -> None:
    _insert_session(
        test_db,
        session_id="batch-session",
        peer_uid="batch-peer",
        changed_at="2026-09-05T02:00:00Z",
    )
    scope_id = _insert_scope(test_db, ["batch-session"])
    run = job_hunt_refresh.create_run(
        test_db,
        scope_id=scope_id,
        workflow_options=_options(refresh_related_jobs=False),
    )
    captured_friend_lists: list[bool] = []
    started_batches: list[list[str]] = []
    message_time = int(datetime(2026, 9, 5, 2, tzinfo=UTC).timestamp() * 1_000)
    friend_response = {"zpData": {"result": [{
        "uid": "batch-peer",
        "encryptFriendId": "encrypt-batch-peer",
        "securityId": "security-batch-peer",
        "lastMessageInfo": {"msgId": "batch-message", "msgTime": message_time},
    }]}}

    def capture_chat_friend_list():
        captured_friend_lists.append(True)
        return {"account_uid": "candidate", "response": friend_response, "url": "test"}

    def start_batch(_db, _config, *, batch_size: int, session_ids: list[str] | None = None):
        started_batches.append(list(session_ids or []))
        assert batch_size == 1
        boss_chat.sync_history_messages(
            test_db,
            session_id="batch-session",
            messages=[{
                "mid": "batch-message",
                "time": message_time,
                "from": {"uid": "batch-peer"},
                "to": {"uid": "candidate"},
                "body": {"type": 1, "text": "批量消息"},
            }],
            history_has_more=False,
            history_next_cursor="",
        )
        return {
            "id": "chat_batch_test",
            "status": "completed",
            "total": 1,
            "current": 1,
            "chat_completed": 1,
            "job_completed": 0,
            "job_skipped": 1,
            "failed": 0,
            "current_session_name": "",
            "current_job_title": "",
            "stage": "completed",
            "message": "批量更新已完成。",
            "created_at": "2026-09-05T03:00:00Z",
            "finished_at": "2026-09-05T03:00:01Z",
        }

    monkeypatch.setattr(
        job_hunt_refresh.boss_scraper_service,
        "capture_chat_friend_list",
        capture_chat_friend_list,
    )
    monkeypatch.setattr(
        boss_chat.boss_chat_batch_manager,
        "start",
        start_batch,
    )
    monkeypatch.setattr(
        boss_chat.boss_chat_batch_manager,
        "get",
        lambda _task_id: start_batch(test_db, object(), batch_size=1, session_ids=["batch-session"]),
    )

    started = job_hunt_refresh.refresh_chat_messages_batch(test_db, object(), str(run["id"]))
    finished = job_hunt_refresh.refresh_chat_messages_batch(test_db, object(), str(run["id"]))

    assert captured_friend_lists == [True]
    assert started["operation"] == {"type": "chat_batch", "id": "chat_batch_test"}
    assert finished["status"] == "succeeded"
    assert started_batches[0] == ["batch-session"]
    refreshed = job_hunt_refresh.get_run(test_db, str(run["id"]))
    assert refreshed["items"][0]["status"] == "succeeded"
    assert refreshed["items"][0]["result"]["source"] == "boss_chat_batch"


def test_related_job_refresh_reuses_existing_session_job_flow(test_db: Database) -> None:
    create_capture_batch(
        test_db,
        capture_id="capture-related",
        keyword="聊天岗位补录",
        city="",
        pages=1,
        auto_details=False,
        created_at="2026-09-05T01:00:00Z",
    )
    recorded = record_capture_jobs(
        test_db,
        capture_id="capture-related",
        search_keyword="聊天岗位补录",
        jobs=[{
            "job_id": "source-related",
            "encrypt_job_id": "encrypt-related-job",
            "title": "后端工程师",
            "boss_name": "示例公司",
            "salary": "20-30K",
            "location": "上海",
        }],
        collected_at="2026-09-05T01:00:00Z",
    )[0]
    update_capture_job_detail(
        test_db,
        job={"history_record_id": recorded["history_record_id"]},
        detail={
            "title": "后端工程师",
            "company_name": "示例公司",
            "salary": "20-30K",
            "location": "上海",
            "description": "负责后端服务开发。",
        },
        status="completed",
    )
    _insert_session(
        test_db,
        session_id="related-session",
        peer_uid="related-peer",
        changed_at="2026-09-05T02:00:00Z",
        encrypt_job_id="encrypt-related-job",
    )
    scope_id = _insert_scope(test_db, ["related-session"], include_job_items=True)
    run = job_hunt_refresh.create_run(
        test_db,
        scope_id=scope_id,
        workflow_options=_options(refresh_chat_messages=False),
    )
    item_id = str(run["items"][0]["id"])

    result = job_hunt_refresh.refresh_related_job(
        test_db,
        object(),  # 已完成岗位不会启动详情任务，因此不会读取 AppConfig。
        str(run["id"]),
        item_id,
    )

    assert result["status"] == "succeeded"
    assert result["item"]["job_id"] == recorded["history_record_id"]
    assert result["item"]["result"]["outcome"] == "reused"


def test_unresolved_job_relation_stays_in_scope_summary_without_job_item(
    test_db: Database,
) -> None:
    _insert_session(
        test_db,
        session_id="unresolved-session",
        peer_uid="unresolved-peer",
        changed_at="2026-09-05T02:00:00Z",
    )
    scope_id = _insert_scope(test_db, ["unresolved-session"])
    run = job_hunt_refresh.create_run(
        test_db,
        scope_id=scope_id,
        workflow_options=_options(refresh_chat_messages=False),
    )

    with test_db.connect() as connection:
        job_count = connection.execute("SELECT COUNT(*) FROM fj_boss_jobs").fetchone()[0]
    assert run["items"] == []
    assert run["summary"]["unresolved_jobs"] == 1
    assert job_count == 0
