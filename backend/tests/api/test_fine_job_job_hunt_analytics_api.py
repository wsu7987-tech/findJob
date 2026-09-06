from __future__ import annotations


def test_job_hunt_analytics_empty_response_contract(configured_client) -> None:
    response = configured_client.get(
        "/api/fine-job/job-hunt/analytics",
        params={"from": "2026-09-01", "to": "2026-09-07"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["range"] == {
        "from": "2026-09-01",
        "to": "2026-09-07",
        "timezone": "Asia/Shanghai",
        "granularity": "day",
        "contact_origin": None,
    }
    assert payload["overview"]["candidate_contacts"] == 0
    assert payload["overview"]["candidate_reply_rate"] is None
    assert payload["current_state"]["waiting_recruiter"] == 0
    assert len(payload["source_performance"]) == 5


def test_job_hunt_analytics_validates_range_and_timezone(configured_client) -> None:
    invalid_range = configured_client.get(
        "/api/fine-job/job-hunt/analytics",
        params={"from": "2026-09-08", "to": "2026-09-07"},
    )
    invalid_timezone = configured_client.get(
        "/api/fine-job/job-hunt/analytics",
        params={
            "from": "2026-09-01",
            "to": "2026-09-07",
            "timezone": "UTC",
        },
    )

    assert invalid_range.status_code == 422
    assert invalid_range.json()["error_category"] == "ANALYTICS_RANGE_INVALID"
    assert invalid_timezone.status_code == 422


def test_recruiter_origin_returns_unavailable_candidate_funnel(configured_client) -> None:
    response = configured_client.get(
        "/api/fine-job/job-hunt/analytics",
        params={
            "from": "2026-09-01",
            "to": "2026-09-07",
            "contact_origin": "recruiter_initiated",
        },
    )

    assert response.status_code == 200
    assert response.json()["funnel"] == {
        "available": False,
        "unavailable_reason": "candidate_contact_cohort_not_applicable",
        "stages": [],
    }


def test_job_hunt_analytics_jobs_empty_response_contract(configured_client) -> None:
    response = configured_client.get(
        "/api/fine-job/job-hunt/analytics/jobs",
        params={
            "metric": "interview_scheduled",
            "from": "2026-09-01",
            "to": "2026-09-07",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "metric": "interview_scheduled",
        "total": 0,
        "jobs": [],
    }


def test_job_hunt_analytics_jobs_validates_metric_filter_combination(
    configured_client,
) -> None:
    response = configured_client.get(
        "/api/fine-job/job-hunt/analytics/jobs",
        params={
            "metric": "resume_submitted",
            "from": "2026-09-01",
            "to": "2026-09-07",
            "rejection_reason_source": "ai_inferred",
        },
    )

    assert response.status_code == 422
    assert response.json()["error_category"] == "ANALYTICS_REJECTION_FILTER_NOT_APPLICABLE"
