from __future__ import annotations


def test_save_and_read_delivery_strategy(configured_client) -> None:
    empty_response = configured_client.get("/api/fine-job/delivery-strategy")

    assert empty_response.status_code == 200
    assert empty_response.json()["strategy"] is None

    save_response = configured_client.put(
        "/api/fine-job/delivery-strategy",
        json={
            "automation_level": "semi_auto",
            "auto_greeting_enabled": True,
            "force_contact_verification_enabled": True,
            "daily_greeting_limit": 30,
            "hourly_greeting_limit": 6,
            "min_match_score": 0.75,
            "resume_submit_mode": "manual",
            "contact_share_mode": "manual",
            "interview_accept_mode": "manual",
            "only_online_interview": True,
            "pause_on_risk": True,
            "notes": "先保守运行",
        },
    )

    assert save_response.status_code == 200
    saved = save_response.json()["strategy"]
    assert saved["ready"] is True
    assert saved["automation_level"] == "semi_auto"
    assert saved["daily_greeting_limit"] == 30
    assert saved["force_contact_verification_enabled"] is True
    assert saved["confirmed_at"] is not None

    read_response = configured_client.get("/api/fine-job/delivery-strategy")

    assert read_response.status_code == 200
    assert read_response.json()["strategy"]["auto_greeting_enabled"] is True
