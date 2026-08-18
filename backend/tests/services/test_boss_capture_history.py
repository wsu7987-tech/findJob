from __future__ import annotations

from backend.app.services.fine_job.boss_capture_history import (
    create_capture_batch,
    list_capture_history,
    record_capture_jobs,
    update_capture_job_detail,
)


def _create_batch(test_db, capture_id: str, created_at: str) -> None:
    create_capture_batch(
        test_db,
        capture_id=capture_id,
        keyword="Python",
        city="上海",
        pages=1,
        auto_details=False,
        created_at=created_at,
    )


def test_capture_history_deduplicates_across_batches_and_counts_once_per_batch(test_db) -> None:
    _create_batch(test_db, "capture-1", "2026-08-18T10:00:00Z")
    _create_batch(test_db, "capture-2", "2026-08-19T10:00:00Z")
    job = {
        "job_id": "job-1",
        "encrypt_job_id": "encrypted-1",
        "title": "Python 开发",
        "boss_name": "示例科技",
        "company_scale": "100-499人",
        "salary": "20-30K",
        "location": "上海·浦东新区",
        "job_link": "https://www.zhipin.com/job_detail/encrypted-1.html",
        "detail_status": "not_collected",
    }

    first = record_capture_jobs(
        test_db,
        capture_id="capture-1",
        jobs=[job],
        collected_at="2026-08-18T10:01:00Z",
    )
    repeated_same_batch = record_capture_jobs(
        test_db,
        capture_id="capture-1",
        jobs=[job],
        collected_at="2026-08-18T10:02:00Z",
    )
    second = record_capture_jobs(
        test_db,
        capture_id="capture-2",
        jobs=[{**job, "company_scale": "500-999人"}],
        collected_at="2026-08-19T10:01:00Z",
    )

    assert first[0]["is_previously_collected"] is False
    assert repeated_same_batch[0]["collect_count"] == 1
    assert second[0]["is_previously_collected"] is True
    assert second[0]["collect_count"] == 2

    history = list_capture_history(
        test_db,
        repeat_status="repeated",
        sort_by="collect_count",
        sort_order="desc",
    )
    assert history["total"] == 1
    item = history["items"][0]
    assert item["company_scale"] == "500-999人"
    assert item["first_collected_at"] == "2026-08-18T10:01:00Z"
    assert item["last_collected_at"] == "2026-08-19T10:01:00Z"
    assert item["collect_count"] == 2


def test_capture_history_filters_sorts_paginates_and_saves_detail(test_db) -> None:
    _create_batch(test_db, "capture-1", "2026-08-18T10:00:00Z")
    jobs = [
        {
            "job_id": f"job-{index}",
            "title": title,
            "boss_name": company,
            "company_scale": "20-99人",
            "location": city,
            "detail_status": "not_collected",
        }
        for index, (title, company, city) in enumerate(
            [
                ("Python 开发", "甲公司", "上海"),
                ("Java 开发", "乙公司", "杭州"),
                ("Python 工程师", "丙公司", "上海"),
            ],
            start=1,
        )
    ]
    recorded = record_capture_jobs(
        test_db,
        capture_id="capture-1",
        jobs=jobs,
        collected_at="2026-08-18T10:01:00Z",
    )
    update_capture_job_detail(
        test_db,
        job=recorded[0],
        detail={"jd": "负责 Python 服务开发"},
        status="completed",
        collected_at="2026-08-18T10:03:00Z",
    )

    result = list_capture_history(
        test_db,
        query="Python",
        city="上海",
        company_scale="20-99人",
        sort_by="title",
        sort_order="asc",
        page=1,
        page_size=10,
    )

    assert result["total"] == 2
    assert {item["title"] for item in result["items"]} == {"Python 开发", "Python 工程师"}
    completed = next(item for item in result["items"] if item["title"] == "Python 开发")
    assert completed["detail"]["jd"] == "负责 Python 服务开发"
    assert completed["detail_status"] == "completed"
