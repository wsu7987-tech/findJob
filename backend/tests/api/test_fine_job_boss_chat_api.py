from __future__ import annotations

from datetime import datetime, timezone

from backend.app.services.fine_job import boss_chat


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


def _resume_session(client, session_id: str) -> None:
    resumed = client.post(
        f"/api/fine-job/boss-chat/sessions/{session_id}/resume",
        json={"reason": "测试恢复 AI 辅助"},
    )
    assert resumed.status_code == 200
    assert resumed.json()["session"]["status"] == "active"


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


def test_refresh_friend_list_saves_identity_and_detects_latest_message_change(configured_client) -> None:
    from backend.app.services.fine_job.boss_capture_history import (
        create_capture_batch,
        record_capture_jobs,
    )

    create_capture_batch(
        configured_client.app.state.db,
        capture_id="chat-job-capture",
        keyword="Python",
        city="广州",
        pages=1,
        auto_details=False,
        created_at="2026-08-31T10:00:00Z",
    )
    record_capture_jobs(
        configured_client.app.state.db,
        capture_id="chat-job-capture",
        jobs=[{
            "job_id": "internal-job-id",
            "encrypt_job_id": "enc-job-300",
            "title": "Python 后端开发工程师",
            "boss_name": "示例科技",
            "job_link": "https://www.zhipin.com/job_detail/enc-job-300.html",
            "detail_status": "not_collected",
        }],
        collected_at="2026-08-31T10:01:00Z",
    )

    payload = {
        "code": 0,
        "zpData": {
            "result": [{
                "uid": 200,
                "encryptFriendId": "enc-peer-200",
                "securityId": "security-200",
                "encryptJobId": "enc-job-300",
                "jobId": 300,
                "name": "王经理",
                "title": "招聘经理",
                "brandName": "示例科技",
                "lastMsg": "请问方便沟通吗？",
                "lastMessageInfo": {
                    "msgId": 1001,
                    "showText": "请问方便沟通吗？",
                    "fromId": 200,
                    "toId": 100,
                    "status": 0,
                    "msgTime": 1788168981115,
                },
                "relationType": 3,
                "chatStatus": 0,
            }],
        },
    }

    first = boss_chat.sync_friend_list(
        configured_client.app.state.db,
        account_uid="100",
        response=payload,
        source_url="https://www.zhipin.com/wapi/zprelation/friend/getGeekFriendList.json",
    )
    assert first["count"] == 1
    assert first["created_count"] == 1
    assert first["changed_count"] == 0
    assert first["created_session_ids"] == first["session_ids"]

    session = configured_client.get("/api/fine-job/boss-chat/sessions").json()["sessions"][0]
    assert session["encrypt_peer_uid"] == "enc-peer-200"
    assert session["security_id"] == "security-200"
    assert session["peer_title"] == "招聘经理"
    assert session["job_title"] == "Python 后端开发工程师"
    assert session["latest_message_content"] == "请问方便沟通吗？"
    assert session["latest_message_direction"] == "inbound"
    assert session["message_update_required"] is False

    payload["zpData"]["result"][0]["lastMsg"] = "可以，下午沟通。"  # type: ignore[index]
    payload["zpData"]["result"][0]["lastMessageInfo"]["msgId"] = 1002  # type: ignore[index]
    second = boss_chat.sync_friend_list(
        configured_client.app.state.db,
        account_uid="100",
        response=payload,
        source_url="https://www.zhipin.com/wapi/zprelation/friend/getGeekFriendList.json",
    )
    assert second["count"] == 1
    assert second["created_count"] == 0
    assert second["changed_count"] == 1
    assert second["created_session_ids"] == []
    assert second["session_ids"] == first["session_ids"]
    updated = configured_client.get("/api/fine-job/boss-chat/sessions").json()["sessions"][0]
    assert updated["latest_platform_msg_id"] == "1002"
    assert updated["message_update_required"] is True


def test_refresh_history_uses_saved_identity_and_persists_platform_messages(configured_client, monkeypatch) -> None:
    from backend.app.services.fine_job.boss_scraper.service import boss_scraper_service

    friend_payload = {
        "code": 0,
        "zpData": {
            "result": [{
                "uid": 200,
                "encryptUid": "enc-peer-200",
                "securityId": "security-200",
                "encryptJobId": "enc-job-300",
                "name": "王经理",
                "title": "招聘经理",
                "brandName": "示例科技",
                "lastMsg": "收到，我先把简历推送用人部门",
                "lastMessageInfo": {
                    "msgId": 1002,
                    "fromId": 200,
                    "toId": 100,
                    "status": 2,
                    "msgTime": 1788161054529,
                },
            }],
        },
    }
    boss_chat.sync_friend_list(
        configured_client.app.state.db,
        account_uid="100",
        response=friend_payload,
        source_url="https://www.zhipin.com/wapi/zprelation/friend/getGeekFriendList.json",
    )
    session = configured_client.get("/api/fine-job/boss-chat/sessions").json()["sessions"][0]
    captured_args: list[dict[str, str]] = []

    def capture_history(*, boss_id: str, security_id: str, max_message_id: str = "0", **_kwargs):
        captured_args.append({
            "boss_id": boss_id,
            "security_id": security_id,
            "max_message_id": max_message_id,
        })
        if max_message_id == "0":
            return {
                "messages": [{
                    "mid": 1002,
                    "type": 1,
                    "body": {"type": 1, "text": "收到，我先把简历推送用人部门"},
                    "from": {"uid": 200},
                    "to": {"uid": 100},
                    "time": 1788161054529,
                    "status": 2,
                }],
                "has_more": True,
                "next_cursor": "1001",
            }
        return {
            "messages": [{
                "mid": 1001,
                "type": 1,
                "body": {"type": 1, "text": "您好，想了解一下岗位详情"},
                "from": {"uid": 100},
                "to": {"uid": 200},
                "time": 1788160054529,
                "status": 2,
            }],
            "has_more": False,
            "next_cursor": "",
        }

    monkeypatch.setattr(boss_scraper_service, "capture_chat_history", capture_history)
    refreshed = configured_client.post(
        f"/api/fine-job/boss-chat/sessions/{session['id']}/history/refresh"
    )

    assert refreshed.status_code == 200
    assert captured_args == [{
        "boss_id": "enc-peer-200",
        "security_id": "security-200",
        "max_message_id": "0",
    }]
    assert refreshed.json()["inserted_count"] == 1
    assert refreshed.json()["has_more"] is True
    more = configured_client.post(
        f"/api/fine-job/boss-chat/sessions/{session['id']}/history/more"
    )
    assert more.status_code == 200
    assert more.json()["inserted_count"] == 1
    assert more.json()["has_more"] is False
    assert captured_args[-1]["max_message_id"] == "1001"
    detail = configured_client.get(f"/api/fine-job/boss-chat/sessions/{session['id']}").json()
    assert [item["content"] for item in detail["messages"]] == [
        "您好，想了解一下岗位详情",
        "收到，我先把简历推送用人部门",
    ]
    assert detail["session"]["message_update_required"] is False
    listed_ids = {
        item["id"]
        for item in configured_client.get("/api/fine-job/boss-chat/sessions").json()["sessions"]
    }
    assert session["id"] in listed_ids


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
    assert observed.json()["generated"] == 0
    assert observed.json()["processing_deferred"] is False

    pending_detail = configured_client.get("/api/fine-job/boss-chat/sessions").json()["sessions"][0]
    assert pending_detail["status"] == "human_takeover"
    _resume_session(configured_client, pending_detail["id"])
    follow_up = _message_event(
        "event-1-follow-up",
        "message-1-follow-up",
        "这个岗位需要到岗办公吗？我还想了解面试流程。",
    )
    resumed_observed = configured_client.post(
        "/api/fine-job/boss-chat/executor/events/batch",
        headers=headers,
        json={"events": [follow_up]},
    )
    assert resumed_observed.status_code == 200
    assert resumed_observed.json()["processing_deferred"] is True

    pending_detail = configured_client.get("/api/fine-job/boss-chat/sessions").json()["sessions"][0]
    pending_session = configured_client.get(
        f"/api/fine-job/boss-chat/sessions/{pending_detail['id']}"
    ).json()
    assert pending_session["reply_tasks"][0]["status"] == "pending_generation"

    checked = configured_client.post("/api/fine-job/boss-chat/check")
    assert checked.status_code == 200
    assert checked.json()["generated"] == 1

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
    assert task["decision"] == "reply"
    assert task["facts_used"] == []

    edited_text = "您好，可以沟通一下办公地点、面试时间和薪资范围吗？"
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
    assert confirmed.json()["action"]["content_categories"] == [
        "send_chat_reply",
        "send_commitment_reply",
        "send_interview_decision",
    ]

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
    assert [item["direction"] for item in detail["messages"]] == ["inbound", "inbound", "outbound"]

    assistant_echo = _message_event(
        "event-assistant-echo",
        "message-assistant-echo",
        edited_text,
        direction="outbound",
        source="manual",
    )
    assistant_echo["message"]["client_mid"] = "assistant-mid-1"  # type: ignore[index]
    echoed = configured_client.post(
        "/api/fine-job/boss-chat/executor/events/batch",
        headers=headers,
        json={"events": [assistant_echo]},
    )
    assert echoed.status_code == 200
    detail = configured_client.get(f"/api/fine-job/boss-chat/sessions/{session_id}").json()
    assert detail["session"]["status"] == "active"
    assert len(detail["messages"]) == 3


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
    _resume_session(configured_client, session["id"])
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


def test_immediate_mode_debounces_continuous_messages(configured_client) -> None:
    from backend.app.services.fine_job import boss_chat

    _, token = _pair(configured_client)
    headers = {"Authorization": f"Bearer {token}"}
    configured_client.patch(
        "/api/fine-job/boss-chat/runtime",
        json={
            "listen_enabled": True,
            "generation_enabled": True,
            "send_enabled": False,
            "trigger_mode": "immediate",
            "interval_minutes": 0,
        },
    )
    first = _message_event("event-debounce-a", "message-debounce-a", "您好，请问在吗？")
    second = _message_event("event-debounce-b", "message-debounce-b", "这个岗位需要到岗吗？")

    first_response = configured_client.post(
        "/api/fine-job/boss-chat/executor/events/batch",
        headers=headers,
        json={"events": [first]},
    )
    first_session = configured_client.get("/api/fine-job/boss-chat/sessions").json()["sessions"][0]
    _resume_session(configured_client, first_session["id"])
    second_response = configured_client.post(
        "/api/fine-job/boss-chat/executor/events/batch",
        headers=headers,
        json={"events": [second]},
    )
    assert first_response.json()["generated"] == 0
    assert second_response.json()["generated"] == 0
    assert boss_chat.process_due_tasks(
        configured_client.app.state.db,
        configured_client.app.state.config,
    ) == 0

    session = configured_client.get("/api/fine-job/boss-chat/sessions").json()["sessions"][0]
    detail = configured_client.get(f"/api/fine-job/boss-chat/sessions/{session['id']}").json()
    pending_tasks = [item for item in detail["reply_tasks"] if item["status"] == "pending_generation"]
    assert len(pending_tasks) == 1
    assert pending_tasks[0]["based_on_message_id"] == detail["messages"][-1]["id"]
    assert pending_tasks[0]["input_message_ids"] == [
        detail["messages"][1]["id"],
    ]

    with configured_client.app.state.db.connect() as connection:
        connection.execute(
            "UPDATE fj_chat_reply_tasks SET generation_due_at = '2000-01-01T00:00:00Z' WHERE id = ?",
            (pending_tasks[0]["id"],),
        )
    assert boss_chat.process_due_tasks(
        configured_client.app.state.db,
        configured_client.app.state.config,
    ) == 1


def test_pause_cancels_queued_send_action(configured_client) -> None:
    _, token = _pair(configured_client)
    headers = {"Authorization": f"Bearer {token}"}
    configured_client.patch(
        "/api/fine-job/boss-chat/runtime",
        json={"listen_enabled": True, "generation_enabled": False, "send_enabled": True},
    )
    configured_client.post(
        "/api/fine-job/boss-chat/executor/events/batch",
        headers=headers,
        json={"events": [_message_event("event-pause", "message-pause", "方便沟通吗？")]},
    )
    session = configured_client.get("/api/fine-job/boss-chat/sessions").json()["sessions"][0]
    _resume_session(configured_client, session["id"])
    task = configured_client.post(
        f"/api/fine-job/boss-chat/sessions/{session['id']}/generate",
        json={"instruction": "礼貌回复"},
    ).json()["reply_task"]
    confirmed = configured_client.post(
        f"/api/fine-job/boss-chat/reply-tasks/{task['id']}/confirm",
        json={
            "final_text": task["final_text"],
            "based_on_message_id": task["based_on_message_id"],
            "based_on_session_version": task["based_on_session_version"],
        },
    )
    assert confirmed.status_code == 200

    paused = configured_client.post(
        f"/api/fine-job/boss-chat/sessions/{session['id']}/pause",
        json={"reason": "用户暂停"},
    )
    assert paused.status_code == 200
    detail = configured_client.get(f"/api/fine-job/boss-chat/sessions/{session['id']}").json()
    assert detail["reply_tasks"][0]["status"] == "cancelled"
    assert detail["send_actions"][0]["status"] == "cancelled"


def test_dispatch_timeout_becomes_unknown_and_is_not_reclaimed(configured_client) -> None:
    from backend.app.services.fine_job import boss_chat

    _, token = _pair(configured_client)
    headers = {"Authorization": f"Bearer {token}"}
    configured_client.patch(
        "/api/fine-job/boss-chat/runtime",
        json={"listen_enabled": True, "generation_enabled": False, "send_enabled": True},
    )
    configured_client.post(
        "/api/fine-job/boss-chat/executor/heartbeat",
        headers=headers,
        json={"account_uid": "geek-100", "tab_id": "tab-a", "leader_epoch": 1, "is_leader": True},
    )
    configured_client.post(
        "/api/fine-job/boss-chat/executor/events/batch",
        headers=headers,
        json={"events": [_message_event("event-timeout", "message-timeout", "您好")]},
    )
    session = configured_client.get("/api/fine-job/boss-chat/sessions").json()["sessions"][0]
    _resume_session(configured_client, session["id"])
    task = configured_client.post(
        f"/api/fine-job/boss-chat/sessions/{session['id']}/generate",
        json={"instruction": "礼貌回复"},
    ).json()["reply_task"]
    configured_client.post(
        f"/api/fine-job/boss-chat/reply-tasks/{task['id']}/confirm",
        json={
            "final_text": task["final_text"],
            "based_on_message_id": task["based_on_message_id"],
            "based_on_session_version": task["based_on_session_version"],
        },
    )
    action = configured_client.post(
        "/api/fine-job/boss-chat/executor/actions/claim",
        headers=headers,
        json={"account_uid": "geek-100", "tab_id": "tab-a", "leader_epoch": 1},
    ).json()["action"]
    configured_client.post(
        f"/api/fine-job/boss-chat/executor/actions/{action['id']}/dispatch-started",
        headers=headers,
        json={"execution_epoch": action["execution_epoch"]},
    )
    with configured_client.app.state.db.connect() as connection:
        connection.execute(
            "UPDATE fj_chat_send_actions SET dispatch_deadline_at = '2000-01-01T00:00:00Z' WHERE id = ?",
            (action["id"],),
        )
    assert boss_chat.sweep_stale_send_actions(configured_client.app.state.db) == 1
    detail = configured_client.get(f"/api/fine-job/boss-chat/sessions/{session['id']}").json()
    assert detail["send_actions"][0]["status"] == "unknown"
    assert detail["send_actions"][0]["status_code"] == "dispatch_result_timeout"
    reclaimed = configured_client.post(
        "/api/fine-job/boss-chat/executor/actions/claim",
        headers=headers,
        json={"account_uid": "geek-100", "tab_id": "tab-a", "leader_epoch": 1},
    )
    assert reclaimed.status_code == 200
    assert reclaimed.json()["action"] is None


def test_incomplete_session_is_reconciled_when_job_identity_arrives(configured_client) -> None:
    _, token = _pair(configured_client)
    headers = {"Authorization": f"Bearer {token}"}
    configured_client.patch(
        "/api/fine-job/boss-chat/runtime",
        json={"listen_enabled": True, "generation_enabled": False, "send_enabled": True},
    )
    incomplete = _message_event("event-identity-a", "message-identity-a", "您好")
    incomplete["message"]["encrypt_job_id"] = ""  # type: ignore[index]
    configured_client.post(
        "/api/fine-job/boss-chat/executor/events/batch",
        headers=headers,
        json={"events": [incomplete]},
    )
    complete = _message_event("event-identity-b", "message-identity-b", "补充岗位信息")
    configured_client.post(
        "/api/fine-job/boss-chat/executor/events/batch",
        headers=headers,
        json={"events": [complete]},
    )

    sessions = configured_client.get("/api/fine-job/boss-chat/sessions").json()["sessions"]
    assert len(sessions) == 1
    assert sessions[0]["encrypt_job_id"] == "enc-job-300"
    assert sessions[0]["status"] == "active"
