from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient


def _runtime_client() -> tuple[TestClient, dict[str, str]]:
    from backend.app.main import create_app

    client = TestClient(create_app())
    created = client.post("/api/internal/codex/v1/runtime")
    assert created.status_code == 200
    headers = {
        "Authorization": f"Bearer {created.json()['token']}",
        "X-FineJob-MCP-Contract-Version": "v1",
        "X-FineJob-Internal-API-Version": "v1",
    }
    return client, headers


def test_internal_runtime_handshake_and_capabilities() -> None:
    client, headers = _runtime_client()
    with client:
        handshake = client.post("/api/internal/codex/v1/handshake", headers=headers)
        assert handshake.status_code == 200
        assert handshake.json()["mcp_contract_version"] == "v1"

        capabilities = client.post(
            "/api/internal/codex/v1/tools/get_capabilities",
            headers=headers,
            json={"arguments": {}},
        )
        assert capabilities.status_code == 200
        assert len(capabilities.json()["data"]["registered_tools"]) == 14

        run_id = handshake.json()["run_id"]
        completed = client.post(
            f"/api/internal/codex/v1/runtime/{run_id}/complete",
            headers=headers,
            json={"status": "exited", "reason": "测试正常退出"},
        )
        assert completed.status_code == 200
        with client.app.state.db.connect() as connection:
            row = connection.execute(
                "SELECT status, exit_reason FROM fj_codex_sessions WHERE id = ?",
                (run_id,),
            ).fetchone()
        assert dict(row) == {"status": "exited", "exit_reason": "测试正常退出"}


def test_internal_api_rejects_missing_token_and_version() -> None:
    client, headers = _runtime_client()
    with client:
        assert client.post("/api/internal/codex/v1/handshake").status_code == 409
        wrong = {**headers, "Authorization": "Bearer invalid-token"}
        assert client.post("/api/internal/codex/v1/handshake", headers=wrong).status_code == 401
        assert client.patch("/api/internal/codex/v1/permissions", headers=headers).status_code == 404


def test_new_runtime_does_not_invalidate_existing_runtime() -> None:
    client, first_headers = _runtime_client()
    with client:
        second = client.post("/api/internal/codex/v1/runtime")
        assert second.status_code == 200
        second_headers = {
            "Authorization": f"Bearer {second.json()['token']}",
            "X-FineJob-MCP-Contract-Version": "v1",
            "X-FineJob-Internal-API-Version": "v1",
        }

        first_handshake = client.post(
            "/api/internal/codex/v1/handshake", headers=first_headers
        )
        second_handshake = client.post(
            "/api/internal/codex/v1/handshake", headers=second_headers
        )
        assert first_handshake.status_code == 200
        assert second_handshake.status_code == 200
        assert first_handshake.json()["run_id"] != second_handshake.json()["run_id"]

        first_run_id = first_handshake.json()["run_id"]
        completed = client.post(
            f"/api/internal/codex/v1/runtime/{first_run_id}/complete",
            headers=first_headers,
            json={"status": "exited", "reason": "第一个运行结束"},
        )
        assert completed.status_code == 200
        assert client.post(
            "/api/internal/codex/v1/handshake", headers=first_headers
        ).status_code == 401
        assert client.post(
            "/api/internal/codex/v1/handshake", headers=second_headers
        ).status_code == 200


def test_page_permissions_are_persisted_by_registered_key(client) -> None:
    current = client.get("/api/fine-job/codex/permissions")
    assert current.status_code == 200
    permissions = {key: True for key in current.json()["permissions"]}
    updated = client.patch(
        "/api/fine-job/codex/permissions",
        json={"enabled": True, "permissions": permissions},
    )
    assert updated.status_code == 200
    assert updated.json()["enabled"] is True
    assert all(updated.json()["permissions"].values())


def test_mcp_server_registers_exact_core_tool_set() -> None:
    from backend.app.mcp.fine_job_server import server
    from backend.app.services.fine_job.codex_tools import CORE_TOOLS

    tools = asyncio.run(server.list_tools())
    assert tuple(tool.name for tool in tools) == CORE_TOOLS


def test_sensitive_content_classification_and_authorization(app_paths) -> None:
    from backend.app.config import load_config
    from backend.app.services.fine_job.codex_authorization import (
        classify_outbound_content,
        resolve_codex_authorization,
    )

    classification = classify_outbound_content(
        "可以线上面试，薪资可以再沟通，请加微信。",
        base_operation="send_chat_reply",
    )
    assert classification.categories == [
        "send_chat_reply",
        "send_contact_info",
        "send_commitment_reply",
        "send_interview_decision",
    ]

    config = load_config()
    config.codex_sensitive_auto_authorization_enabled = True
    config.codex_sensitive_operation_permissions = {
        category: True for category in classification.categories
    }
    authorization = resolve_codex_authorization(config, classification=classification)
    assert authorization["authorization_mode"] == "pre_authorized"
    assert authorization["requires_confirmation"] is False

    greeting = classify_outbound_content(
        "您好，可以加微信进一步沟通吗？",
        base_operation="send_greeting",
    )
    assert greeting.categories == ["send_greeting", "send_contact_info"]
