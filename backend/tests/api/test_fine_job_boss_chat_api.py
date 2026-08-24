from __future__ import annotations

from datetime import datetime, timezone


def _pair(client) -> tuple[str, str]:
    code = client.post("/api/fine-job/boss-executor/pairing-code").json()["code"]
    paired = client.post(
        "/api/fine-job/boss-executor/pair",
        json={
            "code": code,
            "plugin_version": "0.2.0",
            "protocol_version": "1.1",
            "capabilities": ["chat_observe", "chat_send"],
        },
    )
    assert paired.status_code == 200
    return paired.json()["executor_id"], paired.json()["token"]


def _message_event(
    event_id: str,
    platform_message_id: str,
    content: str,
    *,
    direction: str = "inbound",
    source: str = "websocket",
) -> dict[str, object]:
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    return {
        "event_id": event_id,
        "event_type": "message",
        "account_uid": "geek-100",
        "leader_epoch": 1,
        "message": {
            "platform_message_id": platform_message_id,
            "direction": direction,
            "message_type": "text",
            "content": content,
            "sender_uid": "boss-200" if direction == "inbound" else "geek-100",
            "receiver_uid": "geek-100" if direction == "inbound" else "boss-200",
            "client_mid": platform_message_id,
            "peer_uid": "boss-200",
            "encrypt_peer_uid": "enc-boss-200",
            "security_id": "security-200",
            "encrypt_job_id": "enc-job-300",
            "job_title": "Python 开发工程师",
            "peer_name": "王经理",
            "company_name": "示例科技",
            "sent_at": now,
            "observed_at": now,
            "source": source,
        },
    }


def test_chat_observe_generate_confirm_and_send(configured_client) -> None:
    _, token = _pair(configured_client)
    headers = {"Authorization": f"Bearer {token}"}

    initial = configured_client.get("/api/fine-job/boss-chat/runtime")
    assert initial.status_code == 200
    assert initial.json()["runtime"]["send_enabled"] is False

    enabled = configured_client.patch(
        "/api/fine-job/boss-chat/runtime",
        json={
            "listen_enabled": True,
            "generation_enabled": True,
            "send_enabled": True,
            "trigger_mode": "immediate",
            "interval_minutes": 0,
        },
    )
    assert enabled.status_code == 200

    heartbeat = configured_client.post(
        "/api/fine-job/boss-chat/executor/heartbeat",
        headers=headers,
        json={
            "account_uid": "geek-100",
            "tab_id": "tab-a",
            "leader_epoch": 1,
            "is_leader": True,
        },
    )
    assert heartbeat.status_code == 200
    assert heartbeat.json()["accepted"] is True

    event = _message_event("event-1", "message-1", "这个岗位需要到岗办公吗？")
    observed = configured_client.post(
        "/api/fine-job/boss-chat/executor/events/batch",
        headers=headers,
        json={"events": [event]},
    )
    assert observed.status_code == 200
    assert observed.json()["accepted"] == 1
    assert observed.json()["generated"] == 1

    duplicate = configured_client.post(
        "/api/fine-job/boss-chat/executor/events/batch",
        headers=headers,
        json={"events": [event]},
    )
    assert duplicate.status_code == 200
    assert duplicate.json()["duplicates"] == 1

    sessions = configured_client.get("/api/fine-job/boss-chat/sessions").json()["sessions"]
    assert len(sessions) == 1
    session_id = sessions[0]["id"]
    detail = configured_client.get(f"/api/fine-job/boss-chat/sessions/{session_id}").json()
    task = detail["reply_tasks"][0]
    assert task["status"] == "awaiting_review"
    assert "到岗办公" in task["draft_text"]

    edited_text = "您好，可以沟通一下办公地点和每周到岗安排吗？"
    edited = configured_client.patch(
        f"/api/fine-job/boss-chat/reply-tasks/{task['id']}",
        json={"final_text": edited_text},
    )
    assert edited.status_code == 200

    confirmed = configured_client.post(
        f"/api/fine-job/boss-chat/reply-tasks/{task['id']}/confirm",
        json={
            "final_text": edited_text,
            "based_on_message_id": task["based_on_message_id"],
            "based_on_session_version": task["based_on_session_version"],
        },
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["action"]["status"] == "queued"

    claimed = configured_client.post(
        "/api/fine-job/boss-chat/executor/actions/claim",
        headers=headers,
        json={"account_uid": "geek-100", "tab_id": "tab-a", "leader_epoch": 1},
    )
    assert claimed.status_code == 200
    action = claimed.json()["action"]
    assert action["text"] == edited_text
    epoch = action["execution_epoch"]

    started = configured_client.post(
        f"/api/fine-job/boss-chat/executor/actions/{action['id']}/dispatch-started",
        headers=headers,
        json={"execution_epoch": epoch},
    )
    assert started.status_code == 200

    completed = configured_client.post(
        f"/api/fine-job/boss-chat/executor/actions/{action['id']}/complete",
        headers=headers,
        json={
            "execution_epoch": epoch,
            "outcome": "accepted",
            "client_mid": "assistant-mid-1",
            "status_code": "publish_no_throw",
            "message": "MQTT publish 已提交",
        },
    )
    assert completed.status_code == 200
    assert completed.json()["action"]["status"] == "accepted"
    detail = configured_client.get(f"/api/fine-job/boss-chat/sessions/{session_id}").json()
    assert [item["direction"] for item in detail["messages"]] == ["inbound", "outbound"]


def test_new_message_invalidates_old_draft_and_manual_send_takes_over(configured_client) -> None:
    _, token = _pair(configured_client)
    headers = {"Authorization": f"Bearer {token}"}
    configured_client.patch(
        "/api/fine-job/boss-chat/runtime",
        json={"listen_enabled": True, "generation_enabled": False, "send_enabled": True},
    )
    first = _message_event("event-a", "message-a", "方便发一份简历吗？")
    configured_client.post(
        "/api/fine-job/boss-chat/executor/events/batch", headers=headers, json={"events": [first]}
    )
    session = configured_client.get("/api/fine-job/boss-chat/sessions").json()["sessions"][0]
    generated = configured_client.post(
        f"/api/fine-job/boss-chat/sessions/{session['id']}/generate", json={"instruction": "先礼貌确认"}
    )
    assert generated.status_code == 200
    old_task = generated.json()["reply_task"]

    second = _message_event("event-b", "message-b", "也请说明一下到岗时间")
    configured_client.post(
        "/api/fine-job/boss-chat/executor/events/batch", headers=headers, json={"events": [second]}
    )
    stale = configured_client.post(
        f"/api/fine-job/boss-chat/reply-tasks/{old_task['id']}/confirm",
        json={
            "final_text": old_task["final_text"],
            "based_on_message_id": old_task["based_on_message_id"],
            "based_on_session_version": old_task["based_on_session_version"],
        },
    )
    assert stale.status_code == 409

    fresh = configured_client.post(
        f"/api/fine-job/boss-chat/sessions/{session['id']}/generate",
        json={"instruction": "回答最新问题"},
    ).json()["reply_task"]
    queued = configured_client.post(
        f"/api/fine-job/boss-chat/reply-tasks/{fresh['id']}/confirm",
        json={
            "final_text": fresh["final_text"],
            "based_on_message_id": fresh["based_on_message_id"],
            "based_on_session_version": fresh["based_on_session_version"],
        },
    )
    assert queued.status_code == 200
    third = _message_event("event-c", "message-c", "补充：下周可以面试吗？")
    configured_client.post(
        "/api/fine-job/boss-chat/executor/events/batch", headers=headers, json={"events": [third]}
    )
    detail = configured_client.get(f"/api/fine-job/boss-chat/sessions/{session['id']}").json()
    assert detail["send_actions"][0]["status"] == "cancelled"

    manual = _message_event(
        "event-d",
        "message-d",
        "我先人工回复这条",
        direction="outbound",
        source="manual",
    )
    observed = configured_client.post(
        "/api/fine-job/boss-chat/executor/events/batch", headers=headers, json={"events": [manual]}
    )
    assert observed.status_code == 200
    detail = configured_client.get(f"/api/fine-job/boss-chat/sessions/{session['id']}").json()
    assert detail["session"]["status"] == "human_takeover"

    resumed = configured_client.post(
        f"/api/fine-job/boss-chat/sessions/{session['id']}/resume",
        json={"reason": "用户恢复 AI 辅助"},
    )
    assert resumed.status_code == 200
    assert resumed.json()["session"]["status"] == "active"
