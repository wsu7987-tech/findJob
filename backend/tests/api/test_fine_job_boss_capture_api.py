from __future__ import annotations

from backend.app.services.fine_job.boss_scraper.service import BossBrowserStatus


def _task_payload(**updates):
    value = {
        "id": "task-1",
        "status": "queued",
        "stage": "queued",
        "message": "等待执行",
        "keyword": "Python",
        "city": "上海",
        "pages": 1,
        "auto_details": False,
        "used_current_page": False,
        "source_url": None,
        "progress_current": 0,
        "progress_total": 1,
        "jobs_collected": 0,
        "details_completed": 0,
        "details_failed": 0,
        "duplicate_jobs_count": 0,
        "current_job": None,
        "estimated_seconds_min": 8,
        "estimated_seconds_max": 8,
        "jobs": [],
        "jobs_path": None,
        "details_path": None,
        "created_at": "2026-08-18T10:00:00Z",
        "updated_at": "2026-08-18T10:00:00Z",
        "finished_at": None,
        "error_message": None,
    }
    value.update(updates)
    return value


def test_boss_capture_status(configured_client, monkeypatch) -> None:
    monkeypatch.setattr(
        "backend.app.routers.fine_job.boss_capture.boss_scraper_service.get_browser_status",
        lambda: BossBrowserStatus(
            running=True,
            cdp_port=9222,
            current_url="https://www.zhipin.com/web/geek/job?query=Python&city=101020100",
            current_title="Python招聘",
            is_search_page=True,
        ),
    )

    response = configured_client.get("/api/fine-job/boss-capture/status")

    assert response.status_code == 200
    assert response.json()["running"] is True
    assert response.json()["is_search_page"] is True


def test_boss_city_list_uses_scraper_service(configured_client, monkeypatch) -> None:
    monkeypatch.setattr(
        "backend.app.routers.fine_job.boss_capture.boss_scraper_service.list_cities",
        lambda: [{"name": "上海", "code": "101020100"}],
    )

    response = configured_client.get("/api/fine-job/boss-capture/cities")

    assert response.status_code == 200
    assert response.json() == {"cities": [{"name": "上海", "code": "101020100"}]}


def test_locate_boss_search_page(configured_client, monkeypatch) -> None:
    status = BossBrowserStatus(running=True, cdp_port=9222, is_search_page=True)
    monkeypatch.setattr(
        "backend.app.routers.fine_job.boss_capture.boss_scraper_service.locate_search_page",
        lambda **_: "https://www.zhipin.com/web/geek/job?query=Python&city=101020100&page=1",
    )
    monkeypatch.setattr(
        "backend.app.routers.fine_job.boss_capture.boss_scraper_service.get_browser_status",
        lambda: status,
    )

    response = configured_client.post(
        "/api/fine-job/boss-capture/locate",
        json={"keyword": "Python", "city": "上海"},
    )

    assert response.status_code == 200
    assert response.json()["url"].startswith("https://www.zhipin.com/web/geek/job?")


def test_start_capture_returns_pollable_task(configured_client, monkeypatch) -> None:
    captured = {}
    monkeypatch.setattr(
        "backend.app.routers.fine_job.boss_capture.boss_scraper_service.get_browser_status",
        lambda: BossBrowserStatus(running=True, cdp_port=9222),
    )

    def fake_start(request, *, output_dir, db):
        captured["request"] = request
        captured["output_dir"] = output_dir
        captured["db"] = db
        return _task_payload(auto_details=True)

    monkeypatch.setattr(
        "backend.app.routers.fine_job.boss_capture.boss_capture_task_manager.start_capture",
        fake_start,
    )

    response = configured_client.post(
        "/api/fine-job/boss-capture/capture",
        json={
            "keyword": "Python",
            "city": "上海",
            "pages": 1,
            "include_details": True,
            "prefer_current_page": True,
        },
    )

    assert response.status_code == 202
    assert response.json()["id"] == "task-1"
    assert response.json()["auto_details"] is True
    assert captured["request"].include_details is True
    assert captured["request"].max_details is None


def test_list_boss_capture_history_supports_filters_sort_and_pagination(
    configured_client,
) -> None:
    from backend.app.services.fine_job.boss_capture_history import (
        create_capture_batch,
        record_capture_jobs,
    )

    db = configured_client.app.state.db
    create_capture_batch(
        db,
        capture_id="capture-1",
        keyword="Python",
        city="上海",
        pages=1,
        auto_details=False,
        created_at="2026-08-18T10:00:00Z",
    )
    record_capture_jobs(
        db,
        capture_id="capture-1",
        jobs=[
            {
                "job_id": "job-1",
                "title": "Python 开发",
                "boss_name": "示例科技",
                "company_scale": "100-499人",
                "location": "上海",
            }
        ],
        collected_at="2026-08-18T10:01:00Z",
    )

    response = configured_client.get(
        "/api/fine-job/boss-capture/history",
        params={
            "query": "Python",
            "city": "上海",
            "sort_by": "title",
            "sort_order": "asc",
            "page": 1,
            "page_size": 10,
        },
    )

    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["company_scale"] == "100-499人"


def test_capture_selected_details_returns_same_task(configured_client, monkeypatch) -> None:
    monkeypatch.setattr(
        "backend.app.routers.fine_job.boss_capture.boss_capture_task_manager.start_details",
        lambda task_id, job_ids, force: _task_payload(
            id=task_id,
            status="queued",
            stage="details_queued",
            progress_total=len(job_ids),
        ),
    )

    response = configured_client.post(
        "/api/fine-job/boss-capture/tasks/task-1/details",
        json={"job_ids": ["job-1", "job-2"], "force": True},
    )

    assert response.status_code == 202
    assert response.json()["stage"] == "details_queued"
    assert response.json()["progress_total"] == 2


def test_capture_history_job_details_starts_standalone_task(
    configured_client,
    monkeypatch,
) -> None:
    captured = {}
    monkeypatch.setattr(
        "backend.app.routers.fine_job.boss_capture.get_capture_history_job",
        lambda db, history_job_id: {
            "id": history_job_id,
            "job_id": "job-1",
            "title": "Python 开发",
            "location": "上海",
        },
    )
    monkeypatch.setattr(
        "backend.app.routers.fine_job.boss_capture.boss_scraper_service.get_browser_status",
        lambda: BossBrowserStatus(running=True, cdp_port=9222),
    )

    def fake_start(job, *, output_dir, db):
        captured["job"] = job
        captured["output_dir"] = output_dir
        captured["db"] = db
        return _task_payload(id="history-detail-task", stage="details_queued")

    monkeypatch.setattr(
        "backend.app.routers.fine_job.boss_capture.boss_capture_task_manager.start_history_detail",
        fake_start,
    )

    response = configured_client.post(
        "/api/fine-job/boss-capture/history/history-job-1/details"
    )

    assert response.status_code == 202
    assert response.json()["id"] == "history-detail-task"
    assert captured["job"]["id"] == "history-job-1"


def test_apply_filter_strategy_to_capture_task(configured_client, monkeypatch) -> None:
    strategy = configured_client.post(
        "/api/fine-job/strategies/filters",
        json={
            "name": "Agent 正职",
            "title_include_any": ["Agent"],
            "title_exclude": ["销售"],
            "unknown_value_policy": "review",
        },
    ).json()["strategy"]
    task = _task_payload(
        status="completed",
        jobs=[{"job_id": "job-1", "title": "AI Agent 开发"}],
    )
    monkeypatch.setattr(
        "backend.app.routers.fine_job.boss_capture.boss_capture_task_manager.get_task",
        lambda task_id: task,
    )
    monkeypatch.setattr(
        "backend.app.routers.fine_job.boss_capture.boss_capture_task_manager.apply_filter_results",
        lambda task_id, results: task,
    )

    response = configured_client.post(
        "/api/fine-job/boss-capture/tasks/task-1/filters",
        json={"strategy_id": strategy["id"]},
    )

    assert response.status_code == 200
    assert response.json()["selected_job_ids"] == ["job-1"]
    assert response.json()["results"][0]["status"] == "pass"


def test_rules_delivery_evaluation_endpoint_does_not_require_llm(
    configured_client,
    monkeypatch,
) -> None:
    strategy = configured_client.post(
        "/api/fine-job/strategies/recommendations",
        json={
            "name": "规则投递建议",
            "evaluation_method": "rules",
            "required_skills": ["Python"],
            "preferred_skills": ["LangGraph"],
        },
    ).json()["strategy"]
    task = _task_payload(
        status="completed",
        jobs=[
            {
                "job_id": "job-1",
                "title": "AI Agent 开发",
                "skills": "Python | LangGraph",
                "detail_status": "completed",
                "detail": {"jd": "使用 Python 和 LangGraph 开发 Agent"},
            }
        ],
    )
    monkeypatch.setattr(
        "backend.app.routers.fine_job.boss_capture.boss_capture_task_manager.get_task",
        lambda task_id: task,
    )
    monkeypatch.setattr(
        "backend.app.routers.fine_job.boss_capture.boss_capture_task_manager.apply_delivery_evaluations",
        lambda task_id, evaluations: task,
    )

    response = configured_client.post(
        "/api/fine-job/boss-capture/tasks/task-1/delivery-evaluations",
        json={"recommendation_strategy_id": strategy["id"]},
    )

    assert response.status_code == 200
    assert response.json()["evaluations"][0]["decision"] == "recommend"
    assert response.json()["evaluations"][0]["source"] == "rules"


def test_delivery_evaluation_only_uses_selected_completed_details(
    configured_client,
    monkeypatch,
) -> None:
    strategy = configured_client.post(
        "/api/fine-job/strategies/recommendations",
        json={"name": "按选中岗位评估", "evaluation_method": "rules"},
    ).json()["strategy"]
    task = _task_payload(
        status="completed",
        jobs=[
            {
                "job_id": "job-selected",
                "title": "已选岗位",
                "detail_status": "completed",
                "detail": {"jd": "完整 JD"},
            },
            {
                "job_id": "job-unselected",
                "title": "未选岗位",
                "detail_status": "completed",
                "detail": {"jd": "完整 JD"},
            },
            {
                "job_id": "job-pending",
                "title": "未完成详情岗位",
                "detail_status": "not_collected",
            },
        ],
    )
    evaluated_job_ids = []

    def evaluate(jobs, **_kwargs):
        evaluated_job_ids.extend(job["job_id"] for job in jobs)
        return []

    monkeypatch.setattr(
        "backend.app.routers.fine_job.boss_capture.boss_capture_task_manager.get_task",
        lambda task_id: task,
    )
    monkeypatch.setattr(
        "backend.app.routers.fine_job.boss_capture.evaluate_delivery_jobs",
        evaluate,
    )
    monkeypatch.setattr(
        "backend.app.routers.fine_job.boss_capture.boss_capture_task_manager.apply_delivery_evaluations",
        lambda task_id, evaluations: task,
    )

    response = configured_client.post(
        "/api/fine-job/boss-capture/tasks/task-1/delivery-evaluations",
        json={
            "recommendation_strategy_id": strategy["id"],
            "job_ids": ["job-selected"],
        },
    )

    assert response.status_code == 200
    assert evaluated_job_ids == ["job-selected"]
