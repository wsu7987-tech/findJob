from __future__ import annotations

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
    allowed = configured_client.post(
        "/api/fine-job/boss-executor/control", headers=headers, json={"command": "allow"}
    )
    assert allowed.status_code == 200
    assert allowed.json()["executor"]["permission_state"] == "allowed"

    opened = configured_client.post(
        "/api/fine-job/boss-navigation/open",
        json={"job_id": job["id"], "source_context": "history"},
    )
    assert opened.status_code == 200
    assert opened.json()["navigation"]["status"] == "opened"
    assert opened.json()["navigation"]["browser_target_id"] == "target-api"
