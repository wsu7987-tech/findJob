from __future__ import annotations


def _create_history_job(configured_client, *, job_id: str, title: str, jd: str) -> dict:
    from backend.app.services.fine_job.boss_capture_history import (
        create_capture_batch,
        record_capture_jobs,
    )

    db = configured_client.app.state.db
    capture_id = f"capture-{job_id}"
    create_capture_batch(
        db,
        capture_id=capture_id,
        keyword="Python",
        city="上海",
        pages=1,
        auto_details=False,
        created_at="2026-08-21T10:00:00Z",
    )
    record_capture_jobs(
        db,
        capture_id=capture_id,
        jobs=[
            {
                "job_id": job_id,
                "title": title,
                "boss_name": "示例科技",
                "job_link": f"https://www.zhipin.com/job_detail/{job_id}.html",
                "detail_status": "completed",
                "detail": {"jd": jd},
            }
        ],
        collected_at="2026-08-21T10:01:00Z",
    )
    return configured_client.get(
        "/api/fine-job/boss-capture/history",
        params={"page": 1, "page_size": 10},
    ).json()["items"][0]


def test_evaluation_v2_routes_to_review_and_persistent_action_queue(
    configured_client,
) -> None:
    strategy = configured_client.post(
        "/api/fine-job/strategies/recommendations",
        json={
            "name": "V2 规则评估",
            "evaluation_method": "rules",
            "required_skills": ["Python"],
        },
    ).json()["strategy"]
    job = _create_history_job(
        configured_client,
        job_id="workflow-python",
        title="Python 开发",
        jd="负责 Python 服务开发",
    )

    evaluation_response = configured_client.post(
        f"/api/fine-job/boss-capture/history/{job['id']}/delivery-evaluations",
        json={"recommendation_strategy_id": strategy["id"]},
    )

    assert evaluation_response.status_code == 200
    evaluation = evaluation_response.json()["evaluation"]
    assert evaluation["evaluation_version"] == "2.0"
    assert evaluation["decision"] == "recommend"
    assert evaluation["greeting_draft"]["status"] == "ready"

    pending_response = configured_client.get(
        "/api/fine-job/review-items", params={"status": "pending"}
    )
    assert pending_response.status_code == 200
    pending = pending_response.json()["items"]
    assert len(pending) == 1
    assert pending[0]["job_title"] == "Python 开发"

    approve_response = configured_client.post(
        f"/api/fine-job/review-items/{pending[0]['id']}/approve",
        json={"message": "您好，我有 Python 项目经验，希望进一步沟通。"},
    )
    assert approve_response.status_code == 200
    action = approve_response.json()["action"]
    assert action["status"] == "queued"
    assert action["payload"]["message"].startswith("您好")
    assert action["payload"]["encrypt_job_id"] == "workflow-python"

    # 队列来自 SQLite；重新查询仍然存在，而不是只保存在进程内存。
    queued = configured_client.get(
        "/api/fine-job/automation-actions", params={"status": "queued"}
    ).json()["actions"]
    assert [item["id"] for item in queued] == [action["id"]]

    # 新默认招呼动作不能被旧通用租约接口领取，必须走BOSS执行器协议。
    claimed = configured_client.post(
        "/api/fine-job/automation-actions/claim",
        json={"worker_id": "test-extension", "lease_seconds": 60},
    ).json()["action"]
    assert claimed is None


def test_rejected_evaluation_requires_explicit_override(configured_client) -> None:
    strategy = configured_client.post(
        "/api/fine-job/strategies/recommendations",
        json={
            "name": "缺少必备技能",
            "evaluation_method": "rules",
            "required_skills": ["Rust"],
        },
    ).json()["strategy"]
    job = _create_history_job(
        configured_client,
        job_id="workflow-reject",
        title="Python 开发",
        jd="负责 Python 服务开发",
    )
    response = configured_client.post(
        f"/api/fine-job/boss-capture/history/{job['id']}/delivery-evaluations",
        json={"recommendation_strategy_id": strategy["id"]},
    )
    assert response.json()["evaluation"]["decision"] == "reject"

    rejected = configured_client.get(
        "/api/fine-job/review-items", params={"status": "rejected"}
    ).json()["items"]
    assert len(rejected) == 1
    review_id = rejected[0]["id"]

    denied = configured_client.post(
        f"/api/fine-job/review-items/{review_id}/approve",
        json={"message": "您好，希望进一步沟通。"},
    )
    assert denied.status_code == 409

    approved = configured_client.post(
        f"/api/fine-job/review-items/{review_id}/approve",
        json={
            "message": "您好，希望进一步沟通。",
            "allow_override": True,
        },
    )
    assert approved.status_code == 200
    assert approved.json()["action"]["status"] == "queued"


def test_recommended_job_is_auto_queued_only_with_confirmed_auto_policy(
    configured_client,
) -> None:
    delivery = configured_client.put(
        "/api/fine-job/delivery-strategy",
        json={
            "automation_level": "auto_greeting",
            "auto_greeting_enabled": True,
            "daily_greeting_limit": 20,
            "hourly_greeting_limit": 5,
            "min_match_score": 0.72,
        },
    )
    assert delivery.status_code == 200
    strategy = configured_client.post(
        "/api/fine-job/strategies/recommendations",
        json={
            "name": "自动路由规则评估",
            "evaluation_method": "rules",
            "required_skills": ["Python"],
        },
    ).json()["strategy"]
    job = _create_history_job(
        configured_client,
        job_id="workflow-auto",
        title="Python 后端",
        jd="负责 Python 服务开发",
    )

    response = configured_client.post(
        f"/api/fine-job/boss-capture/history/{job['id']}/delivery-evaluations",
        json={"recommendation_strategy_id": strategy["id"]},
    )
    assert response.status_code == 200

    assert configured_client.get(
        "/api/fine-job/review-items", params={"status": "pending"}
    ).json()["total"] == 0
    approved = configured_client.get(
        "/api/fine-job/review-items", params={"status": "approved"}
    ).json()["items"]
    assert approved[0]["auto_approved"] is True
    queued = configured_client.get(
        "/api/fine-job/automation-actions", params={"status": "queued"}
    ).json()["actions"]
    assert len(queued) == 1
