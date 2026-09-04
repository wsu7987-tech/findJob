from __future__ import annotations

import asyncio

from backend.app.services.fine_job import boss_executor
from backend.app.services.fine_job.boss_capture_history import create_capture_batch, record_capture_jobs
from backend.app.utils import utc_now


def _create_job(client) -> dict[str, object]:
    db = client.app.state.db
    create_capture_batch(
        db, capture_id="executor-api-capture", keyword="Python", city="上海",
        pages=1, auto_details=False, created_at=utc_now(),
    )
    record_capture_jobs(
        db, capture_id="executor-api-capture",
        jobs=[{
            "job_id": "source-api", "encrypt_job_id": "encrypt-api",
            "title": "API工程师", "boss_name": "示例科技",
            "job_link": "https://www.zhipin.com/job_detail/encrypt-api.html",
        }],
    )
    return client.get(
        "/api/fine-job/boss-capture/history", params={"page": 1, "page_size": 10}
    ).json()["items"][0]


def test_pair_auth_control_and_manual_navigation(configured_client, monkeypatch) -> None:
    job = _create_job(configured_client)
    monkeypatch.setattr(
        "backend.app.services.fine_job.boss_executor.boss_scraper_service.open_job_page",
        lambda _url: "target-api",
    )

    code = configured_client.post("/api/fine-job/boss-executor/pairing-code").json()["code"]
    paired = configured_client.post(
        "/api/fine-job/boss-executor/pair",
        json={
            "code": code, "plugin_version": "0.1.0", "protocol_version": "1.1",
            "capabilities": ["default_greeting"],
        },
    )
    assert paired.status_code == 200
    token = paired.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}

    assert configured_client.get("/api/fine-job/boss-executor/queue").status_code == 401
    started = configured_client.post(
        "/api/fine-job/boss-executor/control", headers=headers, json={"command": "start"}
    )
    assert started.status_code == 200
    assert started.json()["executor"]["queue_state"] == "running"

    paused_from_plugin = configured_client.post(
        "/api/fine-job/boss-executor/control", headers=headers, json={"command": "pause"}
    )
    assert paused_from_plugin.status_code == 200
    assert paused_from_plugin.json()["executor"]["queue_state"] == "paused"

    class DesktopSocket:
        def __init__(self) -> None:
            self.messages: list[dict[str, object]] = []

        async def send_json(self, message: dict[str, object]) -> None:
            self.messages.append(message)

    class PluginSocket:
        async def send_json(self, message: dict[str, object]) -> None:
            if message.get("type") == "executor_control":
                await boss_executor.handle_executor_channel_message(
                    configured_client.app.state.db,
                    paired.json()["executor_id"],
                    {
                        "type": "executor_state_changed",
                        "request_id": message["request_id"],
                        "queue_state": "running",
                    },
                )

        async def close(self, **_kwargs) -> None:
            return None

    async def verify_desktop_and_plugin_state_sync() -> None:
        db = configured_client.app.state.db
        desktop_socket = DesktopSocket()
        plugin_socket = PluginSocket()
        executor_id = paired.json()["executor_id"]
        await boss_executor.register_desktop_channel(db, desktop_socket)
        await boss_executor.register_executor_channel(db, executor_id, plugin_socket)
        runtime = await boss_executor.request_control(db, executor_id, "start")
        assert runtime["executor"]["queue_state"] == "running"
        assert any(
            item.get("runtime", {}).get("executor", {}).get("queue_state") == "running"
            for item in desktop_socket.messages
        )
        await boss_executor.unregister_executor_channel(db, executor_id, plugin_socket)
        await boss_executor.unregister_desktop_channel(desktop_socket)

    asyncio.run(verify_desktop_and_plugin_state_sync())

    opened = configured_client.post(
        "/api/fine-job/boss-navigation/open",
        json={"job_id": job["id"], "source_context": "history"},
    )
    assert opened.status_code == 200
    assert opened.json()["navigation"]["status"] == "opened"
    assert opened.json()["navigation"]["browser_target_id"] == "target-api"


def test_test_jobs_can_be_edited_and_create_delay_task(configured_client, monkeypatch) -> None:
    listed = configured_client.get("/api/fine-job/boss-executor/test-jobs")
    assert listed.status_code == 200
    jobs = listed.json()["jobs"]
    assert len(jobs) == 5
    job = jobs[0]
    assert job["job_link"] == "https://www.zhipin.com/"

    updated = configured_client.put(
        f"/api/fine-job/boss-executor/test-jobs/{job['id']}",
        json={"encrypt_job_id": "editable-test-id", "job_link": "https://www.zhipin.com/"},
    )
    assert updated.status_code == 200
    assert updated.json()["job"]["id"] == job["id"]
    assert updated.json()["job"]["encrypt_job_id"] == "editable-test-id"

    created = configured_client.post(
        "/api/fine-job/boss-executor/test-tasks",
        json={"job_id": job["id"], "close_page_after_completion": True, "delay_seconds": 7},
    )
    assert created.status_code == 200
    task = created.json()["task"]
    assert task["task_type"] == "TEST_DELAY"
    assert task["delay_seconds"] == 7
    assert task["close_page_after_completion"] is True

    monkeypatch.setattr(
        "backend.app.services.fine_job.boss_executor.boss_scraper_service.open_test_page",
        lambda _url: "target-test",
    )
    code = configured_client.post("/api/fine-job/boss-executor/pairing-code").json()["code"]
    paired = configured_client.post(
        "/api/fine-job/boss-executor/pair",
        json={"code": code, "plugin_version": "0.1.0", "protocol_version": "1.1", "capabilities": []},
    )
    headers = {"Authorization": f"Bearer {paired.json()['token']}"}
    configured_client.post("/api/fine-job/boss-executor/control", headers=headers, json={"command": "start"})
    opened = configured_client.post("/api/fine-job/boss-executor/tasks/open-page", headers=headers)
    assert opened.status_code == 200
    assert opened.json()["task"]["id"] == task["id"]
    assert opened.json()["navigation"]["browser_target_id"] == "target-test"
    matched = configured_client.post(
        f"/api/fine-job/boss-executor/tasks/{task['id']}/matched",
        headers=headers,
        json={"execution_epoch": task["execution_epoch"]},
    )
    assert matched.status_code == 200
    assert matched.json()["task"]["status"] == "leased"
    assert matched.json()["task"]["execution_state"] == "running"

    closed_targets: list[str] = []
    monkeypatch.setattr(
        "backend.app.services.fine_job.boss_executor.boss_scraper_service.close_job_page",
        lambda target_id: closed_targets.append(target_id),
    )
    asyncio.run(boss_executor.handle_executor_channel_message(
        configured_client.app.state.db,
        paired.json()["executor_id"],
        {"type": "task_succeeded", "task_id": task["id"], "execution_result": "测试完成"},
    ))
    assert closed_targets == ["target-test"]
    with configured_client.app.state.db.connect() as connection:
        row = connection.execute(
            "SELECT status, execution_state FROM fj_automation_actions WHERE id = ?",
            (task["id"],),
        ).fetchone()
    assert row["status"] == "succeeded"
    assert row["execution_state"] == "succeeded"

    second = configured_client.post(
        "/api/fine-job/boss-executor/test-tasks",
        json={"job_id": job["id"], "close_page_after_completion": False},
    ).json()["task"]
    configured_client.post("/api/fine-job/boss-executor/tasks/open-page", headers=headers)
    asyncio.run(boss_executor.handle_executor_channel_message(
        configured_client.app.state.db,
        paired.json()["executor_id"],
        {"type": "task_succeeded", "task_id": second["id"], "execution_result": "测试完成"},
    ))
    assert closed_targets == ["target-test"]


def test_created_test_task_pushes_queue_to_connected_plugin(configured_client) -> None:
    job = configured_client.get("/api/fine-job/boss-executor/test-jobs").json()["jobs"][0]
    code = configured_client.post("/api/fine-job/boss-executor/pairing-code").json()["code"]
    paired = configured_client.post(
        "/api/fine-job/boss-executor/pair",
        json={"code": code, "plugin_version": "0.1.0", "protocol_version": "1.1", "capabilities": []},
    ).json()

    class PluginSocket:
        def __init__(self) -> None:
            self.messages: list[dict[str, object]] = []

        async def send_json(self, message: dict[str, object]) -> None:
            self.messages.append(message)

        async def close(self, **_kwargs) -> None:
            return None

    async def register(socket: PluginSocket) -> None:
        await boss_executor.register_executor_channel(
            configured_client.app.state.db,
            paired["executor_id"],
            socket,
        )

    socket = PluginSocket()
    asyncio.run(register(socket))
    socket.messages.clear()
    try:
        created = configured_client.post(
            "/api/fine-job/boss-executor/test-tasks",
            json={"job_id": job["id"], "close_page_after_completion": False, "delay_seconds": 5},
        )
        assert created.status_code == 200
        task_id = created.json()["task"]["id"]
        task_queue_messages = [message for message in socket.messages if message.get("type") == "task_queue"]
        assert task_queue_messages
        assert any(
            task.get("id") == task_id and task.get("delay_seconds") == 5
            for task in task_queue_messages[-1].get("tasks", [])
            if isinstance(task, dict)
        )
    finally:
        asyncio.run(boss_executor.unregister_executor_channel(
            configured_client.app.state.db,
            paired["executor_id"],
            socket,
        ))


def test_executor_settings_and_runtime_cooldown_state(configured_client) -> None:
    code = configured_client.post("/api/fine-job/boss-executor/pairing-code").json()["code"]
    paired = configured_client.post(
        "/api/fine-job/boss-executor/pair",
        json={"code": code, "plugin_version": "0.1.0", "protocol_version": "1.1", "capabilities": []},
    ).json()

    updated = configured_client.patch(
        "/api/fine-job/boss-executor/settings",
        json={"task_cooldown_max_seconds": 9, "page_load_wait_max_seconds": 6},
    )
    assert updated.status_code == 200
    assert updated.json()["executor"]["task_cooldown_max_seconds"] == 9
    assert updated.json()["executor"]["page_load_wait_max_seconds"] == 6

    class DesktopSocket:
        def __init__(self) -> None:
            self.messages: list[dict[str, object]] = []

        async def send_json(self, message: dict[str, object]) -> None:
            self.messages.append(message)

    class PluginSocket:
        async def send_json(self, _message: dict[str, object]) -> None:
            return None

        async def close(self, **_kwargs) -> None:
            return None

    async def verify_runtime_state() -> None:
        db = configured_client.app.state.db
        desktop_socket = DesktopSocket()
        plugin_socket = PluginSocket()
        await boss_executor.register_desktop_channel(db, desktop_socket)
        await boss_executor.register_executor_channel(db, paired["executor_id"], plugin_socket)
        await boss_executor.handle_executor_channel_message(
            db,
            paired["executor_id"],
            {
                "type": "runtime_state",
                "phase": "task_cooldown",
                "detail": "任务间隔冷却等待 9 秒",
                "seconds": 9,
                "until_at": "2026-09-04T00:00:09Z",
            },
        )
        assert any(
            message.get("runtime", {}).get("executor", {}).get("runtime_phase") == "task_cooldown"
            for message in desktop_socket.messages
        )
        await boss_executor.unregister_executor_channel(db, paired["executor_id"], plugin_socket)
        await boss_executor.unregister_desktop_channel(desktop_socket)

    asyncio.run(verify_runtime_state())


def test_executor_channel_reports_message_error_without_closing(configured_client) -> None:
    code = configured_client.post("/api/fine-job/boss-executor/pairing-code").json()["code"]
    paired = configured_client.post(
        "/api/fine-job/boss-executor/pair",
        json={"code": code, "plugin_version": "0.1.0", "protocol_version": "1.1", "capabilities": []},
    ).json()

    class PluginSocket:
        def __init__(self) -> None:
            self.messages: list[dict[str, object]] = []
            self.closed = False

        async def send_json(self, message: dict[str, object]) -> None:
            self.messages.append(message)

        async def close(self, **_kwargs) -> None:
            self.closed = True

    async def verify_message_error() -> None:
        db = configured_client.app.state.db
        socket = PluginSocket()
        await boss_executor.register_executor_channel(db, paired["executor_id"], socket)
        await boss_executor.handle_executor_channel_message(
            db,
            paired["executor_id"],
            {"type": "match_task", "task_id": "missing-task"},
        )
        await boss_executor.handle_executor_channel_message(
            db,
            paired["executor_id"],
            {"type": "heartbeat"},
        )
        assert any(message.get("type") == "task_sync_failed" for message in socket.messages)
        assert socket.closed is False
        await boss_executor.unregister_executor_channel(db, paired["executor_id"], socket)

    asyncio.run(verify_message_error())
